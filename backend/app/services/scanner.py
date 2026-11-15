"""Scan de l'univers.

cours → indicateurs → score technique → présélection → fondamentaux → score global
→ plafond par secteur → top N → actualités analysées par IA → alertes et suivi auto.
"""

import logging
import time
from dataclasses import asdict

import numpy as np
import pandas as pd

from app.analysis.indicators import compute_all, sma
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
from app.market.fundamentals import days_to_earnings, fundamental_score, get_fundamentals
from app.market.provider import get_provider
from app.models import Opportunity, Scan, utcnow
from app.news.fetch import fetch_headlines
from app.news.llm import analyse_news
from app.universe import INDEX_TICKER, load_etfs, load_universe

log = logging.getLogger(__name__)
MIN_HISTORY = 260
INDICATOR_KEYS = ["rsi", "stoch_k", "stoch_d", "macd_hist", "adx", "pct_b", "atr_pct",
                  "roc63", "roc126", "vol_ratio", "volatility", "drawdown_1y",
                  "sma20", "sma50", "sma200"]


def _r(x: float, n: int = 2) -> float | None:
    return None if x is None or not np.isfinite(x) else round(float(x), n)


def analyse_universe(histories: dict[str, pd.DataFrame]) -> tuple[list[dict], dict, pd.DataFrame]:
    """Retourne (classement technique, contexte par ticker, échantillon score/rendements)."""
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
            "ticker": ticker, "name": universe[ticker].name, "sector": universe[ticker].sector,
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


def market_regime(index: pd.DataFrame | None) -> dict | None:
    """Tendance de fond du CAC 40 : au-dessus ou en dessous de sa moyenne 200 séances."""
    if index is None or len(index) < 200:
        return None
    close, avg = float(index["Close"].iloc[-1]), float(sma(index["Close"], 200).iloc[-1])
    return {"index": "CAC 40", "close": _r(close), "sma200": _r(avg), "favorable": close > avg,
            "distance_pct": _r((close / avg - 1) * 100, 1),
            "date": index.index[-1].strftime("%Y-%m-%d")}


def etf_table(histories: dict[str, pd.DataFrame]) -> list[dict]:
    rows = []
    for ticker, etf in load_etfs().items():
        df = histories.get(ticker)
        if df is None or len(df) < 70:
            continue
        c = df["Close"]
        rows.append({
            "ticker": ticker, "name": etf.name, "index": etf.sector.removeprefix("ETF "),
            "price": _r(c.iloc[-1]), "perf_3m_pct": _r((c.iloc[-1] / c.iloc[-64] - 1) * 100, 1),
            "perf_1y_pct": _r((c.iloc[-1] / c.iloc[-253] - 1) * 100, 1) if len(c) > 253 else None,
            "volatility_pct": _r(np.log(c).diff().tail(60).std() * np.sqrt(252) * 100, 1),
        })
    return rows


def select_top(ranking: list[dict], fundamentals: dict[str, dict], top_n: int,
               max_per_sector: int, weight: float) -> list[dict]:
    """Score global = technique ± bonus fondamental, puis top N plafonné par secteur.

    Le bonus est centré sur 50 : des fondamentaux moyens ou inconnus ne changent rien,
    ce qui évite d'avantager les titres dont les données manquent.
    """
    candidates = []
    for row in ranking:
        f = fundamentals.get(row["ticker"])
        f_score, parts = fundamental_score(f)
        global_score = row["score"] if f_score is None \
            else round(row["score"] + weight * (f_score - 50), 1)
        candidates.append({**row, "global_score": global_score, "fundamentals": f or {},
                           "fundamental_score": f_score, "fundamental_parts": parts})
    candidates.sort(key=lambda x: x["global_score"], reverse=True)
    picks, per_sector = [], {}
    for c in candidates:
        if per_sector.get(c["sector"], 0) >= max_per_sector:
            continue
        per_sector[c["sector"]] = per_sector.get(c["sector"], 0) + 1
        picks.append(c)
        if len(picks) == top_n:
            break
    return picks


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
            "sector": row.get("sector"),
            "indicators": {k: _r(last[k], 4) for k in INDICATOR_KEYS},
            "subscores": {k: _r(sub[k] * 100, 1) for k in WEIGHTS},
            "weights": WEIGHTS,
            "estimate": asdict(est),
            "global_score": row.get("global_score", row["score"]),
            "fundamentals": row.get("fundamentals", {}),
            "fundamental_score": row.get("fundamental_score"),
            "fundamental_parts": row.get("fundamental_parts", {}),
            "earnings_in_days": days_to_earnings(row.get("fundamentals")),
        },
    }


def apply_context(opp: dict, news: dict, market: dict | None = None) -> dict:
    """Ajuste le score (±10 points max) et le risque selon l'actualité, le marché et le calendrier."""
    s = get_settings()
    d = opp["details"]
    opp["news"] = news
    opp["adjusted_score"] = d.get("global_score", opp["score"])
    risk = opp["risk_level"]
    if news.get("status") == "ok":
        opp["adjusted_score"] = round(opp["adjusted_score"] + 10 * news["sentiment"] * news["confidence"], 1)
        risk += news["risk_adjustment"]
    d["market_unfavorable"] = bool(market and not market["favorable"])
    if d["market_unfavorable"]:
        risk += 1
    days = d.get("earnings_in_days")
    d["earnings_warning"] = days is not None and 0 <= days <= s.earnings_warning_days
    opp["risk_level"] = int(np.clip(risk, 1, 5))
    d["risk_label"] = RISK_LABELS[opp["risk_level"]]
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
        universe, etfs = load_universe(), load_etfs()
        _set(scan_id, status="running", progress=f"Chargement des cours ({len(universe)} titres)")
        histories = get_provider().history([*universe, *etfs, INDEX_TICKER])
        if not any(t in histories for t in universe):
            raise RuntimeError("Aucun cours récupéré auprès du fournisseur de données")

        _set(scan_id, progress="Calcul des indicateurs et des scores")
        ranking, ctx, pooled = analyse_universe(histories)
        if not ranking:
            raise RuntimeError("Historique insuffisant pour calculer les scores")
        market = market_regime(histories.get(INDEX_TICKER))

        _set(scan_id, progress="Données fondamentales de la présélection")
        shortlist = ranking[: 2 * s.top_n]
        fundamentals = get_fundamentals([r["ticker"] for r in shortlist])
        picks = select_top(shortlist, fundamentals, s.top_n, s.max_per_sector, s.fundamental_weight)

        opps = []
        for i, row in enumerate(picks, 1):
            _set(scan_id, progress=f"Analyse des actualités ({i}/{len(picks)}) : {row['name']}")
            opp = build_opportunity(row, ctx[row["ticker"]], pooled)
            headlines = fetch_headlines(row["name"], s.news_max_headlines)
            news = analyse_news(row["name"], row["ticker"], headlines)
            opps.append(apply_context(opp, news, market))
            if news["status"] in ("ok", "error"):  # un appel IA a réellement eu lieu
                time.sleep(s.news_pause_seconds)
        opps.sort(key=lambda o: o["adjusted_score"], reverse=True)

        with SessionLocal() as db:
            scan = db.get(Scan, scan_id)
            scan.ranking = ranking
            scan.market = {"regime": market, "etfs": etf_table(histories)}
            scan.opportunities = [Opportunity(rank=i, **o) for i, o in enumerate(opps, 1)]
            scan.status, scan.progress, scan.finished_at = "done", "Terminé", utcnow()
            db.commit()
    except Exception as exc:
        log.exception("Scan %s en échec", scan_id)
        _set(scan_id, status="failed", error=str(exc)[:500], finished_at=utcnow())
        return

    try:  # alertes et suivi automatique : un échec ici n'invalide pas le scan
        from app.services.alerts import after_scan

        after_scan(scan_id)
    except Exception:
        log.exception("Traitements d'après-scan en échec (scan %s)", scan_id)
