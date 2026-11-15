from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field, model_validator

from app.config import get_settings
from app.market.provider import get_provider
from app.models import User
from app.security import get_current_user
from app.services import etf as svc
from app.services.simulation import PEA_CEILING
from app.universe import load_etfs

router = APIRouter(prefix="/etfs", tags=["etfs"])


class PlanIn(BaseModel):
    ticker: str = Field(pattern=r"^[A-Z0-9]{1,8}\.[A-Z]{2}$")
    monthly: float = Field(ge=10, le=12_500)
    initial: float = Field(0, ge=0, le=PEA_CEILING)
    years: int = Field(ge=1, le=15)

    @model_validator(mode="after")
    def _under_ceiling(self):
        if self.initial + self.monthly * 12 * self.years > PEA_CEILING:
            raise ValueError("Le total des versements dépasse le plafond du PEA (150 000 €)")
        return self


@router.get("")
def list_etfs(_: User = Depends(get_current_user)) -> list[dict]:
    return svc.etf_stats()


@router.post("/simulate")
def simulate(body: PlanIn, _: User = Depends(get_current_user)) -> dict:
    etf = load_etfs().get(body.ticker)
    if etf is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "ETF inconnu")
    df = get_provider().history([body.ticker]).get(body.ticker)
    if df is None or df.empty:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Cours indisponible")
    try:
        result = svc.simulate_plan(df["Close"], body.monthly, body.initial, body.years,
                                   get_settings().order_fee_rate)
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    return {"ticker": body.ticker, "name": etf.name, **result}
