from datetime import date
from typing import Literal

from fastapi import APIRouter, Depends, status
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import User
from app.security import get_current_user
from app.services import simulation as sim

router = APIRouter(prefix="/portfolio", tags=["portfolio"])


class OrderIn(BaseModel):
    ticker: str = Field(pattern=r"^[A-Z0-9]{1,8}\.[A-Z]{2}$")
    side: Literal["buy", "sell"]
    quantity: int = Field(gt=0, le=1_000_000)


class ResetIn(BaseModel):
    initial_cash: float = Field(ge=100, le=sim.PEA_CEILING)


class SettingsIn(BaseModel):
    auto_follow: bool | None = None
    pea_opened_on: date | None = None

    @field_validator("pea_opened_on")
    @classmethod
    def _not_future(cls, v: date | None) -> date | None:
        if v is not None and not (date(1992, 9, 14) <= v <= date.today()):  # création du PEA
            raise ValueError("Date d'ouverture du PEA invalide")
        return v


@router.post("/settings")
def settings(body: SettingsIn, user: User = Depends(get_current_user),
             db: Session = Depends(get_db)) -> dict:
    pf = sim.get_portfolio(db, user)
    if body.auto_follow is not None:
        pf.auto_follow = body.auto_follow
    if body.pea_opened_on is not None:
        pf.pea_opened_on = body.pea_opened_on.isoformat()
    db.commit()
    return sim.portfolio_view(db, user)


@router.get("")
def view(user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> dict:
    return sim.portfolio_view(db, user)


@router.post("/orders", status_code=status.HTTP_201_CREATED)
def order(body: OrderIn, user: User = Depends(get_current_user),
          db: Session = Depends(get_db)) -> dict:
    sim.place_order(db, user, body.ticker, body.side, body.quantity)
    return sim.portfolio_view(db, user)


@router.get("/trades")
def trades(user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> list[dict]:
    pf = sim.get_portfolio(db, user)
    return [{"id": t.id, "ticker": t.ticker, "side": t.side, "quantity": t.quantity,
             "price": t.price, "fee": t.fee, "realized_pnl": t.realized_pnl,
             "executed_at": t.executed_at}
            for t in sorted(pf.trades, key=lambda t: t.id, reverse=True)]


@router.post("/reset")
def reset(body: ResetIn, user: User = Depends(get_current_user),
          db: Session = Depends(get_db)) -> dict:
    sim.reset_portfolio(db, user, body.initial_cash)
    return sim.portfolio_view(db, user)
