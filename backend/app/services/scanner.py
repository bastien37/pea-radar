"""Scan de l'univers : indicateurs → score → top N → actualités analysées par IA."""

import logging
import time
from dataclasses import asdict

import numpy as np
import pandas as pd

from app.analysis.indicators import compute_all
from app.analysis.scoring import (
    RISK_LABELS,
    WEIGHTS,
    estimate,
    forward_returns,
    risk_level,
    score_series,
    subscores,
)
from app.config import get_settings
from app.db import SessionLocal
from app.market.provider import get_provider
from app.models import Opportunity, Scan, utcnow
from app.news.fetch import fetch_headlines
from app.news.llm import analyse_news
from app.universe import load_universe

log = logging.getLogger(__name__)
MIN_HISTORY = 260
INDICATOR_KEYS = ["rsi", "stoch_k", "stoch_d", "macd_hist", "adx", "pct_b", "atr_pct",
                  "roc63", "roc126", "vol_ratio", "volatility", "drawdown_1y",
                  "sma20", "sma50", "sma200"]


def _r(x: float, n: int = 2) -> float | None:
    return None if x is None or not np.isfinite(x) else round(float(x), n)


def analyse_universe(histories: dict[str, pd.DataFrame]) -> tuple[list[dict], dict, pd.DataFrame]:
    """Retourne (classement, contexte par ticker, échantillon mutualisé score/rendements)."""
    universe = load_universe()
    ranking, ctx, pooled = [], {}, []
    for ticker, df in histories.items():
        if ticker not in universe or len(df) < MIN_HISTORY:
            continue
        ind = compute_all(df)
        scores = score_series(ind)
        if not np.isfinite(scores.iloc[-1]):
            continue
        study = forward_returns(ind["Close"]).assign(score=scores).dropna(subset=["score"])
        pooled.append(study)
        last, sub = ind.iloc[-1], subscores(ind).iloc[-1]
        ctx[ticker] = {"last": last, "sub": sub, "study": study}
        ranking.append({
            "ticker": ticker, "name": universe[ticker].name,
            "price": _r(last["Close"]), "score": _r(scores.iloc[-1], 1),
            "risk_level": risk_level(last["volatility"], last["drawdown_1y"]),
            "rsi": _r(last["rsi"], 1), "stoch_k": _r(last["stoch_k"], 1),
            "perf_3m_pct": _r(last["roc63"] * 100, 1),
            "volatility_pct": _r(last["volatility"] * 100, 1),
        })
    ranking.sort(key=lambda x: x["score"], reverse=True)
    for i, row in enumerate(ranking, 1):
        row["rank"] = i
    return ranking, ctx, (pd.concat(pooled) if pooled else pd.DataFrame())


def build_opportunity(row: dict, ctx: dict, pooled: pd.DataFrame) -> dict:
    last, sub, study = ctx["last"], ctx["sub"], ctx["study"]
    est = estimate(row["score"], study, pooled, float(last["atr_pct"]))
    return {
        "ticker": row["ticker"], "name": row["name"], "price": row["price"],
        "price_date": last.name.strftime("%Y-%m-%d"), "score": row["score"],
        "risk_level": row["risk_level"],
        "expected_gain_pct": est.expected_gain_pct, "gain_low_pct": est.gain_low_pct,
        "gain_high_pct": est.gain_high_pct, "win_rate": est.win_rate,
        "holding_days": est.holding_days,
        "stop_loss": _r(last["Close"] - 2 * last["atr"]),
        "details": {
            "indicators": {k: _r(last[k], 4) for k in INDICATOR_KEYS},
            "subscores": {k: _r(sub[k] * 100, 1) for k in WEIGHTS},
            "weights": WEIGHTS,
            "estimate": asdict(est),
        },
    }


def apply_news(opp: dict, news: dict) -> dict:
    """Ajuste le score (±10 points max) et le risque (±1 cran) selon l'analyse IA."""
    opp["news"] = news
    opp["adjusted_score"] = opp["score"]
    if news.get("status") == "ok":
        opp["adjusted_score"] = round(opp["score"] + 10 * news["sentiment"] * news["confidence"], 1)
        opp["risk_level"] = int(np.clip(opp["risk_level"] + news["risk_adjustment"], 1, 5))
    opp["details"]["risk_label"] = RISK_LABELS[opp["risk_level"]]
    return opp


def _set(scan_id: int, **fields) -> None:
    with SessionLocal() as db:
        scan = db.get(Scan, scan_id)
        for k, v in fields.items():
            setattr(scan, k, v)
        db.commit()


def run_scan(scan_id: int) -> None:
    s = get_settings()
    try:
        universe = load_universe()
        _set(scan_id, status="running", progress=f"Téléchargement des cours ({len(universe)} titres)")
        histories = get_provider().history(list(universe))
        if not histories:
            raise RuntimeError("Aucun cours récupéré auprès du fournisseur de données")

        _set(scan_id, progress="Calcul des indicateurs et des scores")
        ranking, ctx, pooled = analyse_universe(histories)
        if not ranking:
            raise RuntimeError("Historique insuffisant pour calculer les scores")

        opps = []
        for i, row in enumerate(ranking[: s.top_n], 1):
            _set(scan_id, progress=f"Analyse des actualités ({i}/{s.top_n}) : {row['name']}")
            opp = build_opportunity(row, ctx[row["ticker"]], pooled)
            headlines = fetch_headlines(row["name"], s.news_max_headlines)
            news = analyse_news(row["name"], row["ticker"], headlines)
            opps.append(apply_news(opp, news))
            if news["status"] in ("ok", "error"):  # un appel IA a réellement eu lieu
                time.sleep(s.news_pause_seconds)
        opps.sort(key=lambda o: o["adjusted_score"], reverse=True)

        with SessionLocal() as db:
            scan = db.get(Scan, scan_id)
            scan.ranking = ranking
            scan.opportunities = [Opportunity(rank=i, **o) for i, o in enumerate(opps, 1)]
            scan.status, scan.progress, scan.finished_at = "done", "Terminé", utcnow()
            db.commit()
    except Exception as exc:
        log.exception("Scan %s en échec", scan_id)
        _set(scan_id, status="failed", error=str(exc)[:500], finished_at=utcnow())
