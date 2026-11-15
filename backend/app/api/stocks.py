import numpy as np
from fastapi import APIRouter, Depends, HTTPException, Path, Query, status

from app.analysis.indicators import compute_all
from app.analysis.scoring import score_series
from app.market.provider import get_provider
from app.models import User
from app.security import get_current_user
from app.universe import load_universe

router = APIRouter(prefix="/stocks", tags=["stocks"])
TICKER = Path(pattern=r"^[A-Z0-9]{1,8}\.[A-Z]{2}$")
SERIES = ["Close", "sma20", "sma50", "sma200", "rsi", "stoch_k", "stoch_d", "macd_hist", "score"]


@router.get("")
def list_stocks(_: User = Depends(get_current_user)) -> list[dict]:
    return [{"ticker": s.ticker, "name": s.name} for s in load_universe().values()]


@router.get("/{ticker}")
def stock_detail(ticker: str = TICKER, days: int = Query(250, ge=30, le=750),
                 _: User = Depends(get_current_user)) -> dict:
    stock = load_universe().get(ticker)
    if stock is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Titre hors de l'univers éligible PEA")
    df = get_provider().history([ticker]).get(ticker)
    if df is None or df.empty:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Cours indisponible")
    ind = compute_all(df)
    ind["score"] = score_series(ind)
    tail = ind[SERIES].tail(days).round(3).replace({np.nan: None})
    return {
        "ticker": ticker, "name": stock.name,
        "series": [{"date": d.strftime("%Y-%m-%d"), **{k.lower(): row[k] for k in SERIES}}
                   for d, row in tail.iterrows()],
    }
