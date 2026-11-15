"""Données fondamentales (valorisation, croissance, dette, prochaine publication).

Récupérées titre par titre auprès de Yahoo : lent, donc limité à la présélection du
scan et conservé en base quelques jours. La couverture des valeurs françaises est
inégale : chaque champ peut être absent, et le score s'adapte à ce qui est disponible.
"""

import logging
import zlib
from datetime import date, timedelta

import numpy as np
from sqlalchemy import select

from app.config import get_settings
from app.db import SessionLocal
from app.models import Fundamental, utcnow

log = logging.getLogger(__name__)
FIELDS = ["pe", "forward_pe", "earnings_growth", "debt_to_equity", "roe", "dividend_yield_pct",
          "next_earnings"]


def _num(x) -> float | None:
    try:
        x = float(x)
    except (TypeError, ValueError):
        return None
    return round(x, 4) if np.isfinite(x) else None


def _from_yfinance(ticker: str) -> dict:
    import yfinance as yf

    t = yf.Ticker(ticker)
    info = t.info or {}
    dy = _num(info.get("dividendYield"))
    if dy is not None and dy < 0.25:  # selon la version de yfinance : fraction ou pourcentage
        dy = round(dy * 100, 2)
    next_earnings = None
    try:
        dates = (t.calendar or {}).get("Earnings Date") or []
        upcoming = sorted(d for d in dates if isinstance(d, date) and d >= date.today())
        next_earnings = upcoming[0].isoformat() if upcoming else None
    except Exception:  # noqa: BLE001 — le calendrier est facultatif
        log.debug("Calendrier indisponible pour %s", ticker)
    return {
        "pe": _num(info.get("trailingPE")), "forward_pe": _num(info.get("forwardPE")),
        "earnings_growth": _num(info.get("earningsGrowth")),
        "debt_to_equity": _num(info.get("debtToEquity")),  # en % des fonds propres
        "roe": _num(info.get("returnOnEquity")), "dividend_yield_pct": dy,
        "next_earnings": next_earnings,
    }


def _synthetic(ticker: str) -> dict:
    rng = np.random.default_rng(zlib.crc32(ticker.encode()) + 1)
    return {
        "pe": round(float(rng.uniform(6, 40)), 1), "forward_pe": round(float(rng.uniform(6, 30)), 1),
        "earnings_growth": round(float(rng.uniform(-0.3, 0.4)), 3),
        "debt_to_equity": round(float(rng.uniform(0, 220)), 1),
        "roe": round(float(rng.uniform(-0.05, 0.3)), 3),
        "dividend_yield_pct": round(float(rng.uniform(0, 7)), 2),
        "next_earnings": (date.today() + timedelta(days=int(rng.integers(1, 90)))).isoformat(),
    }


def get_fundamentals(tickers: list[str]) -> dict[str, dict]:
    s = get_settings()
    limit = utcnow() - timedelta(days=s.fundamentals_cache_days)
    out: dict[str, dict] = {}
    with SessionLocal() as db:
        cached = {f.ticker: f for f in db.scalars(select(Fundamental)
                                                  .where(Fundamental.ticker.in_(tickers)))}
        for t in tickers:
            row = cached.get(t)
            fetched = row.fetched_at if row else None
            if fetched and (fetched if fetched.tzinfo else fetched.replace(tzinfo=limit.tzinfo)) > limit:
                out[t] = row.data
                continue
            try:
                data = _synthetic(t) if s.data_provider == "synthetic" else _from_yfinance(t)
            except Exception as exc:  # noqa: BLE001 — les fondamentaux sont facultatifs
                log.warning("Fondamentaux indisponibles pour %s : %s", t, type(exc).__name__)
                if row:
                    out[t] = row.data  # mieux vaut une donnée ancienne que rien
                continue
            out[t] = data
            if row:
                row.data, row.fetched_at = data, utcnow()
            else:
                db.add(Fundamental(ticker=t, data=data, fetched_at=utcnow()))
        db.commit()
    return out


def _scale(x: float | None, worst: float, best: float) -> float | None:
    return None if x is None else float(np.clip((x - worst) / (best - worst), 0, 1))


def fundamental_score(f: dict | None) -> tuple[float | None, dict]:
    """Score 0–100 sur les critères disponibles (None s'il y en a moins de deux)."""
    if not f:
        return None, {}
    pe = f.get("forward_pe") or f.get("pe")
    parts = {
        # PER : 10 ou moins = très bon marché, 35 ou plus = très cher ; négatif = pertes
        "valuation": 0.1 if (pe is not None and pe <= 0) else _scale(pe, 35, 10),
        "growth": _scale(f.get("earnings_growth"), -0.2, 0.2),
        "debt": _scale(f.get("debt_to_equity"), 200, 20),
        "profitability": _scale(f.get("roe"), 0.0, 0.2),
    }
    parts = {k: round(v * 100, 1) for k, v in parts.items() if v is not None}
    if len(parts) < 2:
        return None, parts
    return round(float(np.mean(list(parts.values()))), 1), parts


def days_to_earnings(f: dict | None) -> int | None:
    try:
        return (date.fromisoformat(f["next_earnings"]) - date.today()).days
    except (KeyError, TypeError, ValueError):
        return None
