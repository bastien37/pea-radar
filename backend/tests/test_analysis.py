import numpy as np
import pandas as pd

from app.analysis import indicators as ta
from app.analysis.scoring import estimate, forward_returns, risk_level, score_series
from app.market.provider import SyntheticProvider
from app.services.scanner import (
    analyse_universe,
    apply_context,
    build_opportunity,
    market_regime,
    select_top,
)
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
    out = apply_context(opp, news, {"favorable": False})
    assert out["adjusted_score"] == round(out["score"] - 5, 1)
    assert out["risk_level"] == min(base_risk + 2, 5)  # actualité + marché défavorable
    assert out["details"]["market_unfavorable"] is True


def test_fundamental_score():
    from app.market.fundamentals import fundamental_score
    good = {"forward_pe": 9, "earnings_growth": 0.3, "debt_to_equity": 10, "roe": 0.25}
    bad = {"pe": -4, "earnings_growth": -0.4, "debt_to_equity": 300, "roe": -0.1}
    assert fundamental_score(good)[0] == 100 and fundamental_score(bad)[0] < 5
    assert fundamental_score({"pe": 12})[0] is None  # un seul critère : pas de score
    assert fundamental_score(None) == (None, {})


def test_select_top_blends_and_caps_sectors():
    ranking = [{"ticker": f"T{i}", "sector": "Banque" if i < 4 else f"S{i}", "score": 80 - i}
               for i in range(8)]
    funda = {"T7": {"forward_pe": 9, "earnings_growth": 0.3, "debt_to_equity": 10, "roe": 0.25},
             "T0": {"pe": -4, "earnings_growth": -0.4, "debt_to_equity": 300, "roe": -0.1}}
    picks = select_top(ranking, funda, top_n=5, max_per_sector=2, weight=0.2)
    assert sum(p["sector"] == "Banque" for p in picks) == 2
    assert picks[0]["ticker"] == "T7"  # 73 technique, tiré vers le haut par ses fondamentaux
    assert next(p for p in ranking if p["ticker"] == "T0")["score"] == 80
    assert "T0" not in [p["ticker"] for p in picks]  # 80 technique, plombé puis évincé du secteur


def test_market_regime():
    import pandas as pd
    up = pd.DataFrame({"Close": [float(i) for i in range(1, 301)]})
    up.index = pd.bdate_range("2024-01-01", periods=300)
    assert market_regime(up)["favorable"] is True
    assert market_regime(up.iloc[::-1].set_axis(up.index))["favorable"] is False
    assert market_regime(up.iloc[:50]) is None


def test_backtest_accounting():
    from app.analysis.backtest import BacktestParams, metrics, run_backtest
    p = SyntheticProvider()
    u = dict(list(load_universe().items())[:30])
    r = run_backtest(p.history(list(u)), {t: s.sector for t, s in u.items()},
                     p.history(["^FCHI"])["^FCHI"], None, BacktestParams(years=6, market_filter=False))
    assert r["trades"]["count"] > 20 and 0 <= r["trades"]["exposure_pct"] <= 100
    assert set(r["metrics"]) == {"Stratégie", "Univers équipondéré", "CAC 40 (hors dividendes)"}
    assert all(row["Stratégie"] > 0 for row in r["curve"])
    # sans frais ni stop serré, aucune perte ne peut dépasser l'amplitude des cours
    free = run_backtest(p.history(list(u)), {t: s.sector for t, s in u.items()}, None, None,
                        BacktestParams(years=6, market_filter=False, fee_rate=0.0))
    assert free["metrics"]["Stratégie"]["total_return_pct"] > r["metrics"]["Stratégie"]["total_return_pct"]
    import pandas as pd
    flat = pd.Series([100.0, 110.0, 99.0, 121.0])
    assert metrics(flat)["max_drawdown_pct"] == 10.0 and metrics(flat)["total_return_pct"] == 21.0
