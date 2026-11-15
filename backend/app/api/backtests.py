from datetime import timedelta

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import get_db
from app.models import Backtest, User, utcnow
from app.security import get_current_user
from app.services.backtester import run_backtest_job

router = APIRouter(prefix="/backtests", tags=["backtests"])


class BacktestIn(BaseModel):
    years: int = Field(10, ge=1, le=15)
    positions: int = Field(5, ge=1, le=20)
    holding_days: int = Field(40, ge=5, le=250)
    stop_atr: float = Field(2.0, ge=0.5, le=10)
    min_score: float = Field(60, ge=0, le=100)
    rebalance_days: int = Field(5, ge=1, le=60)
    max_per_sector: int = Field(2, ge=1, le=20)
    market_filter: bool = True


def _out(bt: Backtest) -> dict:
    return {"id": bt.id, "status": bt.status, "error": bt.error, "params": bt.params,
            "started_at": bt.started_at, "finished_at": bt.finished_at, "result": bt.result}


@router.post("", status_code=status.HTTP_202_ACCEPTED)
def start(body: BacktestIn, tasks: BackgroundTasks, user: User = Depends(get_current_user),
          db: Session = Depends(get_db)) -> dict:
    for bt in db.scalars(select(Backtest).where(Backtest.user_id == user.id,
                                                Backtest.status.in_(("pending", "running")))):
        started = bt.started_at if bt.started_at.tzinfo else bt.started_at.replace(tzinfo=utcnow().tzinfo)
        if utcnow() - started < timedelta(minutes=20):
            raise HTTPException(status.HTTP_409_CONFLICT, "Un backtest est déjà en cours")
        bt.status, bt.error = "failed", "Backtest interrompu"
    bt = Backtest(user_id=user.id, params={**body.model_dump(),
                                           "fee_rate": get_settings().order_fee_rate})
    db.add(bt)
    db.commit()
    tasks.add_task(run_backtest_job, bt.id)
    return _out(bt)


@router.get("/latest")
def latest(user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> dict | None:
    bt = db.scalar(select(Backtest).where(Backtest.user_id == user.id).order_by(Backtest.id.desc()))
    return _out(bt) if bt else None
