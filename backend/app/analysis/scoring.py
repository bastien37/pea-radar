"""Score composite 0–100, niveau de risque, et estimation gain / durée de détention.

Le score est calculé pour chaque jour de l'historique (et pas seulement le dernier) :
cela permet de mesurer ce qui s'est réellement passé après les configurations
passées comparables, plutôt que d'inventer un objectif de cours.
"""

from dataclasses import dataclass

import numpy as np
import pandas as pd

WEIGHTS = {"trend": 0.30, "momentum": 0.25, "timing": 0.25, "volume": 0.10, "stability": 0.10}
HORIZONS = [10, 20, 40, 60, 90]  # jours de bourse
MIN_SAMPLES = 30
SCORE_TOLERANCE = 5.0
RISK_LABELS = {1: "Faible", 2: "Modéré", 3: "Moyen", 4: "Élevé", 5: "Très élevé"}


def _clip01(s: pd.Series) -> pd.Series:
    return s.clip(0, 1)


def subscores(ind: pd.DataFrame) -> pd.DataFrame:
    c = ind["Close"]
    out = pd.DataFrame(index=ind.index)

    # Tendance : alignement cours / moyennes mobiles, pondéré par la force (ADX).
    align = ((c > ind["sma50"]).astype(float) + (ind["sma50"] > ind["sma200"]).astype(float)
             + (c > ind["sma200"]).astype(float)) / 3
    out["trend"] = 0.75 * align + 0.25 * _clip01((ind["adx"] - 10) / 30) * align

    # Momentum : histogramme MACD positif et croissant, performance 3 et 6 mois.
    hist = ind["macd_hist"]
    macd_part = 0.5 * (hist > 0).astype(float) + 0.5 * (hist > hist.shift(3)).astype(float)
    roc_part = _clip01(0.5 + ind["roc63"] / 0.40) * 0.6 + _clip01(0.5 + ind["roc126"] / 0.60) * 0.4
    out["momentum"] = 0.5 * macd_part + 0.5 * roc_part

    # Point d'entrée : stochastique bas qui repasse au-dessus de %D, RSI ni suracheté
    # ni effondré, cours dans la moitié basse des bandes de Bollinger.
    k, d = ind["stoch_k"], ind["stoch_d"]
    stoch_part = 0.6 * _clip01(1 - k / 100) + 0.4 * (k > d).astype(float)
    rsi_part = _clip01(1 - (ind["rsi"] - 50).abs() / 30)
    boll_part = _clip01(1 - ind["pct_b"])
    out["timing"] = 0.45 * stoch_part + 0.30 * rsi_part + 0.25 * boll_part

    out["volume"] = _clip01(ind["vol_ratio"].rolling(5).mean() / 2)
    out["stability"] = _clip01(1 - (ind["volatility"] - 0.15) / 0.35)

    required = ["sma200", "adx", "macd_hist", "roc126", "stoch_d", "rsi", "pct_b", "volatility"]
    return out.where(ind[required].notna().all(axis=1))


def score_series(ind: pd.DataFrame) -> pd.Series:
    sub = subscores(ind)
    return 100 * sum(sub[k] * w for k, w in WEIGHTS.items())


def risk_level(volatility: float, drawdown_1y: float) -> int:
    level = 1 + int(np.searchsorted([0.18, 0.25, 0.33, 0.45], volatility))
    if drawdown_1y > 0.35:
        level += 1
    return min(level, 5)


def forward_returns(close: pd.Series) -> pd.DataFrame:
    return pd.DataFrame({h: close.shift(-h) / close - 1 for h in HORIZONS})


@dataclass
class Estimate:
    expected_gain_pct: float  # médiane historique
    gain_low_pct: float  # 1er quartile
    gain_high_pct: float  # 3e quartile
    win_rate: float
    holding_days: int
    samples: int
    basis: str  # "titre" | "univers" | "volatilité"
    by_horizon: list[dict]


def estimate(score: float, own: pd.DataFrame, pooled: pd.DataFrame, atr_pct: float) -> Estimate:
    """`own` / `pooled` : colonnes `score` + un rendement futur par horizon."""
    for basis, data in (("titre", own), ("univers", pooled)):
        similar = data[data["score"] >= score - SCORE_TOLERANCE]
        rows = []
        for h in HORIZONS:
            r = similar[h].dropna()
            if len(r) >= MIN_SAMPLES:
                rows.append({
                    "days": h, "samples": int(len(r)),
                    "median_pct": round(float(r.median()) * 100, 2),
                    "low_pct": round(float(r.quantile(0.25)) * 100, 2),
                    "high_pct": round(float(r.quantile(0.75)) * 100, 2),
                    "win_rate": round(float((r > 0).mean()), 3),
                })
        if rows:
            # Horizon retenu : meilleur gain médian rapporté à la racine du temps immobilisé.
            best = max(rows, key=lambda x: x["median_pct"] / np.sqrt(x["days"]))
            return Estimate(best["median_pct"], best["low_pct"], best["high_pct"],
                            best["win_rate"], best["days"], best["samples"], basis, rows)
    # Repli sans historique suffisant : amplitude typique sur 20 séances (ATR × √20).
    swing = round(atr_pct * np.sqrt(20) * 100, 2)
    return Estimate(round(swing / 2, 2), -swing, swing, 0.5, 20, 0, "volatilité", [])
