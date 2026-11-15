from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field, model_validator

from app.fees import FeeModel, default_fees
from app.models import User
from app.security import get_current_user
from app.services import etf as svc
from app.services.simulation import PEA_CEILING

router = APIRouter(prefix="/etfs", tags=["etfs"])
TICKER = r"^\^?[A-Z0-9]{1,9}(\.[A-Z]{2})?$"


class Allocation(BaseModel):
    ticker: str = Field(pattern=TICKER)
    weight: float = Field(gt=0, le=100)


class FeesIn(BaseModel):
    rate: float = Field(ge=0, le=0.05)
    minimum: float = Field(0, ge=0, le=50)
    fixed: float = Field(0, ge=0, le=50)


class Support(BaseModel):
    allocations: list[Allocation] = Field(min_length=1, max_length=4)
    rebalance: bool = False
    fees: FeesIn | None = None  # sinon, les frais configurés côté serveur

    @model_validator(mode="after")
    def _weights(self):
        if len({a.ticker for a in self.allocations}) != len(self.allocations):
            raise ValueError("Un même support ne peut figurer qu'une fois")
        if abs(sum(a.weight for a in self.allocations) - 100) > 0.01:
            raise ValueError("La somme des pourcentages doit faire 100")
        return self


class PlanIn(Support):
    monthly: float = Field(ge=10, le=12_500)
    initial: float = Field(0, ge=0, le=PEA_CEILING)
    years: int = Field(ge=1, le=30)

    @model_validator(mode="after")
    def _under_ceiling(self):
        if self.initial + self.monthly * 12 * self.years > PEA_CEILING:
            raise ValueError("Le total des versements dépasse le plafond du PEA (150 000 €)")
        return self


class GoalIn(Support):
    target: float = Field(ge=1_000, le=5_000_000)
    initial: float = Field(0, ge=0, le=PEA_CEILING)
    years: int = Field(ge=5, le=30)
    inflation_pct: float = Field(2.0, ge=0, le=10)


def _prepare(body: Support):
    known = svc.instruments()
    unknown = [a.ticker for a in body.allocations if a.ticker not in known]
    if unknown:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Support inconnu : {', '.join(unknown)}")
    try:
        closes = svc.aligned_closes([a.ticker for a in body.allocations])
    except LookupError as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(exc)) from exc
    fees = FeeModel(**body.fees.model_dump()) if body.fees else default_fees()
    name = " + ".join(f"{a.weight:g} % {known[a.ticker].name}" if len(body.allocations) > 1
                      else known[a.ticker].name for a in body.allocations)
    return closes, [a.weight for a in body.allocations], fees, name


@router.get("")
def list_etfs(_: User = Depends(get_current_user)) -> list[dict]:
    return svc.etf_stats()


@router.post("/simulate")
def simulate(body: PlanIn, _: User = Depends(get_current_user)) -> dict:
    closes, weights, fees, name = _prepare(body)
    try:
        result = svc.simulate_plan(closes, body.monthly, body.initial, body.years, fees, weights,
                                   body.rebalance)
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    return {"name": name, **result}


@router.post("/goal")
def goal(body: GoalIn, _: User = Depends(get_current_user)) -> dict:
    closes, weights, fees, name = _prepare(body)
    try:
        result = svc.goal_plan(closes, body.target, body.years, body.initial, fees, body.inflation_pct,
                               weights, body.rebalance)
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    return {"name": name, **result}
