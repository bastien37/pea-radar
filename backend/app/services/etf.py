"""ETF et indices : statistiques historiques, plans de versements programmés, mode objectif."""

import math

import numpy as np
import pandas as pd

from app.analysis.indicators import TRADING_DAYS
from app.config import get_settings
from app.fees import FeeModel
from app.market.provider import get_provider
from app.universe import load_etfs, load_indices

MIN_WINDOWS = 12


def instruments() -> dict:
    return {**load_etfs(), **load_indices()}


def _annualized(close: pd.Series, years: int) -> float | None:
    start = close.index[-1] - pd.DateOffset(years=years)
    if close.index[0] > start + pd.Timedelta(days=10):
        return None  # historique trop court pour cette durée
    past = close[close.index <= start]
    base = past.iloc[-1] if len(past) else close.iloc[0]
    return round(float((close.iloc[-1] / base) ** (1 / years) - 1) * 100, 1)


def etf_stats() -> list[dict]:
    items = instruments()
    histories = get_provider().history(list(items))
    rows = []
    for ticker, item in items.items():
        kind = "etf" if item.is_etf else "indice"
        base = {"ticker": ticker, "name": item.name, "kind": kind,
                "index": item.sector.removeprefix("ETF ").removeprefix("Indice ")}
        df = histories.get(ticker)
        if df is None or len(df) < 60:
            rows.append({**base, "available": False})
            continue
        c = df["Close"]
        span = (c.index[-1] - c.index[0]).days / 365.25
        rows.append({
            **base, "available": True, "price": round(float(c.iloc[-1]), 2),
            "since": c.index[0].strftime("%Y-%m-%d"), "history_years": round(span, 1),
            "perf_1y_pct": _annualized(c, 1), "perf_3y_pct": _annualized(c, 3),
            "perf_5y_pct": _annualized(c, 5), "perf_10y_pct": _annualized(c, 10),
            "perf_20y_pct": _annualized(c, 20),
            "volatility_pct": round(float(np.log(c).diff().std() * np.sqrt(TRADING_DAYS)) * 100, 1),
            "max_drawdown_pct": round(float((1 - c / c.cummax()).max()) * 100, 1),
        })
    return rows


def aligned_closes(tickers: list[str]) -> pd.DataFrame:
    """Cours de clôture sur la période commune à tous les supports choisis."""
    histories = get_provider().history(tickers)
    missing = [t for t in tickers if t not in histories or histories[t].empty]
    if missing:
        raise LookupError(f"Cours indisponibles pour {', '.join(missing)}")
    return pd.DataFrame({t: histories[t]["Close"] for t in tickers}).dropna()


def _monthly_buys(closes: pd.DataFrame) -> pd.DataFrame:
    return closes.groupby([closes.index.year, closes.index.month]).head(1)


def _run(prices: np.ndarray, amounts: np.ndarray, weights: np.ndarray, fees: FeeModel,
         rebalance_every: int | None) -> tuple[np.ndarray, np.ndarray, float]:
    """Un plan sur une suite de dates d'achat : valeur après chaque versement, parts, frais."""
    units, paid_fees, values = np.zeros(len(weights)), 0.0, np.empty(len(amounts))
    for i, row in enumerate(prices):
        if rebalance_every and i and i % rebalance_every == 0:
            held = units * row
            total = held.sum()
            cost = sum(fees.fee(abs(x)) for x in total * weights - held if abs(x) > 0.01)
            paid_fees += cost
            units = (total - cost) * weights / row  # retour à la répartition cible
        for j, w in enumerate(weights):
            order = amounts[i] * w
            if order > 0:
                fee = fees.fee(order)
                paid_fees += fee
                units[j] += (order - fee) / row[j]
        values[i] = units @ row
    return values, units, paid_fees


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


def _tax_rate(years: float) -> float:
    s = get_settings()
    return s.pea_social_rate + (s.pea_income_tax_rate if years < 5 else 0.0)


def simulate_plan(closes: pd.DataFrame | pd.Series, monthly: float, initial: float, years: int,
                  fees: FeeModel, weights: list[float] | None = None, rebalance: bool = False) -> dict:
    """Rejoue un plan de versements mensuels (premier jour de bourse du mois) sur l'historique."""
    closes = closes.to_frame() if isinstance(closes, pd.Series) else closes
    w = np.array(weights or [1.0], dtype=float)
    w = w / w.sum()
    every = 12 if rebalance and len(w) > 1 else None
    buys = _monthly_buys(closes)
    available = len(buys) - 1  # le mois en cours sert de date d'arrivée
    if available < 12:
        raise ValueError("Historique insuffisant pour simuler un plan sur ces supports")
    months = min(years * 12, available)
    amounts = np.full(months, float(monthly))
    amounts[0] += initial
    invested = float(amounts.sum())
    grid = buys.to_numpy()

    # Plan le plus récent : il se termine au dernier cours connu.
    start = len(buys) - 1 - months
    window = buys.iloc[start : start + months]
    values, units, paid_fees = _run(grid[start : start + months], amounts, w, fees, every)
    final_value = float(units @ closes.iloc[-1].to_numpy())
    paid = np.cumsum(amounts)
    curve = [{"date": d.strftime("%Y-%m-%d"), "invested": round(float(p), 2), "value": round(float(v), 2)}
             for d, p, v in zip(window.index, paid, values, strict=True)]
    curve.append({"date": closes.index[-1].strftime("%Y-%m-%d"), "invested": round(invested, 2),
                  "value": round(final_value, 2)})
    gap = values / paid - 1
    worst = int(np.argmin(gap))
    horizon = (closes.index[-1] - window.index[0]).days / 365.25
    times = np.array([(d - window.index[0]).days / 365.25 for d in window.index])
    gain = final_value - invested
    rate = _tax_rate(horizon)
    tax = max(gain, 0.0) * rate

    # Tous les plans de même durée qu'on aurait pu démarrer dans l'historique.
    series = []
    for first in range(len(buys) - months):
        _, u, _ = _run(grid[first : first + months], amounts, w, fees, every)
        series.append({"start": buys.index[first].strftime("%Y-%m"),
                       "pct": round((float(u @ grid[first + months]) / invested - 1) * 100, 1)})
    windows = None
    if len(series) >= MIN_WINDOWS:
        pcts = np.array([x["pct"] for x in series])
        windows = {
            "count": len(series), "worst_pct": float(pcts.min()),
            "median_pct": round(float(np.median(pcts)), 1), "best_pct": float(pcts.max()),
            "loss_share": round(float((pcts < 0).mean()), 3),
            "worst_start": series[int(pcts.argmin())]["start"],
            "best_start": series[int(pcts.argmax())]["start"],
            "series": series,
        }
    return {
        "months": months, "requested_months": years * 12,
        "start": window.index[0].strftime("%Y-%m-%d"), "end": closes.index[-1].strftime("%Y-%m-%d"),
        "invested": round(invested, 2), "final_value": round(final_value, 2),
        "gain": round(gain, 2), "gain_pct": round(gain / invested * 100, 1),
        "annual_return_pct": _irr(amounts, times, final_value, horizon),
        "fees": round(paid_fees, 2), "fee_label": fees.label(), "rebalanced": every is not None,
        "tax_rate_pct": round(rate * 100, 1), "tax": round(tax, 2),
        "net_gain": round(gain - tax, 2), "net_value": round(final_value - tax, 2),
        "after_5_years": horizon >= 5,
        "worst_point": {"date": window.index[worst].strftime("%Y-%m-%d"),
                        "gap_pct": round(float(gap[worst]) * 100, 1)},
        "windows": windows, "curve": curve,
    }


def goal_plan(closes: pd.DataFrame, target: float, years: int, initial: float, fees: FeeModel,
              inflation_pct: float, weights: list[float] | None = None, rebalance: bool = False) -> dict:
    """Versement mensuel nécessaire pour atteindre une somme nette d'impôt, selon trois scénarios
    tirés de tous les plans de même durée que l'historique permet de rejouer."""
    w = np.array(weights or [1.0], dtype=float)
    w = w / w.sum()
    every = 12 if rebalance and len(w) > 1 else None
    buys = _monthly_buys(closes)
    months = years * 12
    count = len(buys) - months
    if count < MIN_WINDOWS:
        raise ValueError("Historique trop court pour cette durée : choisissez un indice à longue "
                         "histoire ou une durée plus courte")
    grid = buys.to_numpy()
    probe = np.full(months, 100.0)  # la valeur finale est quasi proportionnelle au versement
    lump = np.zeros(months)
    lump[0] = max(initial, 1.0)
    rate = _tax_rate(years)
    nominal = target * (1 + inflation_pct / 100) ** years  # même pouvoir d'achat à l'échéance
    needed = []
    for first in range(count):
        end = grid[first + months]
        per_euro = float(_run(grid[first : first + months], probe, w, fees, every)[1] @ end) / 100
        growth = float(_run(grid[first : first + months], lump, w, fees, every)[1] @ end) / lump[0]
        # net = valeur − taux × (valeur − versements) ; on résout pour le versement mensuel
        m = ((nominal - (1 - rate) * initial * growth - rate * initial)
             / ((1 - rate) * per_euro + rate * months))
        if m * per_euro + initial * growth < m * months + initial:  # plan en perte : pas d'impôt
            m = (nominal - initial * growth) / per_euro
        needed.append(max(m, 0.0))
    needed = np.array(needed)

    def scenario(label: str, q: float, note: str) -> dict:
        monthly = math.ceil(float(np.percentile(needed, q)))
        return {"label": label, "note": note, "monthly": monthly,
                "invested": round(monthly * months + initial, 2)}

    return {
        "target": target, "target_nominal": round(nominal, 2), "years": years, "initial": initial,
        "inflation_pct": inflation_pct, "tax_rate_pct": round(rate * 100, 1),
        "windows": count, "history_start": buys.index[0].strftime("%Y-%m"),
        "fee_label": fees.label(),
        "scenarios": [
            scenario("Défavorable", 90, "suffisant dans 9 périodes passées sur 10"),
            scenario("Médian", 50, "suffisant dans une période passée sur deux"),
            scenario("Favorable", 10, "suffisant seulement dans les meilleures périodes"),
        ],
        "worst_case_monthly": math.ceil(float(needed.max())),
    }
