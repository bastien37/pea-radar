"""ETF éligibles PEA : statistiques historiques et simulation de versements programmés."""

import numpy as np
import pandas as pd

from app.analysis.indicators import TRADING_DAYS
from app.config import get_settings
from app.market.provider import get_provider
from app.universe import load_etfs


def _annualized(close: pd.Series, years: int) -> float | None:
    start = close.index[-1] - pd.DateOffset(years=years)
    if close.index[0] > start + pd.Timedelta(days=10):
        return None  # historique trop court pour cette durée
    past = close[close.index <= start]
    base = past.iloc[-1] if len(past) else close.iloc[0]
    return round(float((close.iloc[-1] / base) ** (1 / years) - 1) * 100, 1)


def etf_stats() -> list[dict]:
    etfs = load_etfs()
    histories = get_provider().history(list(etfs))
    rows = []
    for ticker, etf in etfs.items():
        df = histories.get(ticker)
        if df is None or len(df) < 60:
            rows.append({"ticker": ticker, "name": etf.name, "index": etf.sector.removeprefix("ETF "),
                         "available": False})
            continue
        c = df["Close"]
        span = (c.index[-1] - c.index[0]).days / 365.25
        rows.append({
            "ticker": ticker, "name": etf.name, "index": etf.sector.removeprefix("ETF "),
            "available": True, "price": round(float(c.iloc[-1]), 2),
            "since": c.index[0].strftime("%Y-%m-%d"), "history_years": round(span, 1),
            "perf_1y_pct": _annualized(c, 1), "perf_3y_pct": _annualized(c, 3),
            "perf_5y_pct": _annualized(c, 5), "perf_10y_pct": _annualized(c, 10),
            "perf_all_pct": round(float((c.iloc[-1] / c.iloc[0]) ** (1 / span) - 1) * 100, 1)
            if span >= 1 else None,
            "volatility_pct": round(float(np.log(c).diff().std() * np.sqrt(TRADING_DAYS)) * 100, 1),
            "max_drawdown_pct": round(float((1 - c / c.cummax()).max()) * 100, 1),
        })
    return rows


def _irr(amounts: np.ndarray, times: np.ndarray, final_value: float, horizon: float) -> float | None:
    """Rendement annuel qui égalise les versements et la valeur finale (pondéré par les dates)."""
    if final_value <= 0:
        return None

    def npv(rate: float) -> float:
        return float(final_value / (1 + rate) ** horizon - (amounts / (1 + rate) ** times).sum())

    lo, hi = -0.99, 5.0
    if npv(lo) < 0 or npv(hi) > 0:
        return None
    for _ in range(80):
        mid = (lo + hi) / 2
        lo, hi = (mid, hi) if npv(mid) > 0 else (lo, mid)
    return round((lo + hi) / 2 * 100, 2)


def simulate_plan(close: pd.Series, monthly: float, initial: float, years: int,
                  fee_rate: float) -> dict:
    """Rejoue un plan de versements mensuels (premier jour de bourse du mois) sur l'historique."""
    s = get_settings()
    buys = close.groupby([close.index.year, close.index.month]).head(1)
    months = years * 12
    available = len(buys) - 1  # le mois en cours sert de date d'arrivée
    if available < 12:
        raise ValueError("Historique insuffisant pour simuler un plan sur cet ETF")
    months = min(months, available)
    amounts = np.full(months, float(monthly))
    amounts[0] += initial
    invested_total = float(amounts.sum())

    def run(start: int, end_price: float) -> tuple[float, np.ndarray]:
        prices = buys.iloc[start : start + months].to_numpy()
        units = amounts * (1 - fee_rate) / prices
        return float(units.sum() * end_price), units

    # Plan le plus récent : il se termine au dernier cours connu.
    start = len(buys) - 1 - months
    window = buys.iloc[start : start + months]
    final_value, units = run(start, float(close.iloc[-1]))
    held, paid = np.cumsum(units), np.cumsum(amounts)
    values = held * window.to_numpy()
    curve = [{"date": d.strftime("%Y-%m-%d"), "invested": round(float(p), 2), "value": round(float(v), 2)}
             for d, p, v in zip(window.index, paid, values, strict=True)]
    curve.append({"date": close.index[-1].strftime("%Y-%m-%d"), "invested": round(invested_total, 2),
                  "value": round(final_value, 2)})
    gap = values / paid - 1
    worst = int(np.argmin(gap))

    horizon = (close.index[-1] - window.index[0]).days / 365.25
    times = np.array([(d - window.index[0]).days / 365.25 for d in window.index])
    gain = final_value - invested_total
    rate = s.pea_social_rate + (s.pea_income_tax_rate if horizon < 5 else 0.0)
    tax = max(gain, 0.0) * rate

    # Tous les plans de même durée qu'on aurait pu démarrer dans l'historique.
    ratios = []
    for first in range(len(buys) - months):
        end_price = float(buys.iloc[first + months])
        ratios.append(run(first, end_price)[0] / invested_total - 1)
    ratios = np.array(ratios) * 100

    return {
        "months": months, "requested_months": years * 12,
        "start": window.index[0].strftime("%Y-%m-%d"), "end": close.index[-1].strftime("%Y-%m-%d"),
        "invested": round(invested_total, 2), "final_value": round(final_value, 2),
        "gain": round(gain, 2), "gain_pct": round(gain / invested_total * 100, 1),
        "annual_return_pct": _irr(amounts, times, final_value, horizon),
        "fees": round(invested_total * fee_rate, 2), "fee_rate": fee_rate,
        "tax_rate_pct": round(rate * 100, 1), "tax": round(tax, 2),
        "net_gain": round(gain - tax, 2), "net_value": round(final_value - tax, 2),
        "after_5_years": horizon >= 5,
        "worst_point": {"date": window.index[worst].strftime("%Y-%m-%d"),
                        "gap_pct": round(float(gap[worst]) * 100, 1)},
        "windows": {
            "count": int(len(ratios)),
            "worst_pct": round(float(ratios.min()), 1), "median_pct": round(float(np.median(ratios)), 1),
            "best_pct": round(float(ratios.max()), 1),
            "loss_share": round(float((ratios < 0).mean()), 3),
        } if len(ratios) >= 12 else None,
        "curve": curve,
    }
