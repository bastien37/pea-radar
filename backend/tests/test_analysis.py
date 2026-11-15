import numpy as np
import pandas as pd

from app.analysis import indicators as ta
from app.analysis.scoring import estimate, forward_returns, risk_level, score_series
from app.market.provider import SyntheticProvider
from app.services.scanner import analyse_universe, apply_news, build_opportunity
from app.universe import load_universe


def _df(ticker="AIR.PA"):
    return SyntheticProvider().history([ticker])[ticker]


def test_rsi_extremes():
    up = pd.Series(np.arange(1.0, 60.0))
    assert ta.rsi(up).iloc[-1] == 100
    assert ta.rsi(up[::-1].reset_index(drop=True)).iloc[-1] < 1


def test_stochastic_bounds_and_definition():
    df = _df()
    k, d = ta.stochastic(df["High"], df["Low"], df["Close"])
    assert k.dropna().between(0, 100).all() and d.dropna().between(0, 100).all()
    raw = 100 * (df["Close"] - df["Low"].rolling(14).min()) / (
        df["High"].rolling(14).max() - df["Low"].rolling(14).min())
    assert np.isclose(k.iloc[-1], raw.iloc[-3:].mean())


def test_macd_sma_atr():
    df = _df()
    line, sig, hist = ta.macd(df["Close"])
    assert np.allclose((line - sig).dropna(), hist.dropna())
    assert np.isclose(ta.sma(df["Close"], 20).iloc[-1], df["Close"].iloc[-20:].mean())
    assert (ta.atr(df["High"], df["Low"], df["Close"]).dropna() > 0).all()


def test_score_in_range_and_no_lookahead():
    df = _df()
    full = score_series(ta.compute_all(df))
    assert full.dropna().between(0, 100).all()
    # Le score d'un jour ne doit dépendre que du passé.
    truncated = score_series(ta.compute_all(df.iloc[:-50]))
    assert np.isclose(full.iloc[-51], truncated.iloc[-1])


def test_risk_level():
    assert risk_level(0.12, 0.05) == 1
    assert risk_level(0.30, 0.10) == 3
    assert risk_level(0.30, 0.50) == 4
    assert risk_level(0.90, 0.90) == 5


def test_estimate_uses_history_then_falls_back():
    ind = ta.compute_all(_df())
    study = forward_returns(ind["Close"]).assign(score=score_series(ind)).dropna(subset=["score"])
    est = estimate(float(study["score"].median()), study, study, 0.02)
    assert est.basis == "titre" and est.samples >= 30 and est.holding_days in (10, 20, 40, 60, 90)
    assert est.gain_low_pct <= est.expected_gain_pct <= est.gain_high_pct
    empty = study.iloc[:0]
    assert estimate(50, empty, empty, 0.02).basis == "volatilité"


def test_universe_excludes_non_pea():
    u = load_universe()
    assert "AIR.PA" in u and "URW.PA" not in u and len(u) > 100


def test_pipeline_and_news_adjustment():
    tickers = list(load_universe())[:15]
    ranking, ctx, pooled = analyse_universe(SyntheticProvider().history(tickers))
    assert [r["score"] for r in ranking] == sorted((r["score"] for r in ranking), reverse=True)
    opp = build_opportunity(ranking[0], ctx[ranking[0]["ticker"]], pooled)
    assert opp["stop_loss"] < opp["price"]
    base_risk = opp["risk_level"]
    news = {"status": "ok", "sentiment": -1.0, "confidence": 0.5, "risk_adjustment": 1}
    out = apply_news(opp, news)
    assert out["adjusted_score"] == round(out["score"] - 5, 1)
    assert out["risk_level"] == min(base_risk + 1, 5)
