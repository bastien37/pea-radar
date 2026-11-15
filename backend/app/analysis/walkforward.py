"""Validation « walk-forward » de stratégies simples face à l'ETF CAC 40.

Principe : on choisit le meilleur réglage d'une stratégie sur une fenêtre d'apprentissage
(3 ans par défaut), on l'applique tel quel à l'année suivante, qu'il n'a jamais vue, puis on
avance d'un an et on recommence. Seules les années de test, mises bout à bout, sont jugées :
un réglage qui n'a brillé que par hasard sur le passé ne tient pas cette épreuve.

Les stratégies sont des portefeuilles équipondérés des N titres les mieux classés selon un
signal, rééquilibrés à intervalle fixe, frais prélevés sur chaque mouvement.
"""

from dataclasses import dataclass
from itertools import product

import numpy as np
import pandas as pd

from app.analysis.backtest import metrics
from app.analysis.indicators import TRADING_DAYS, compute_all
from app.analysis.scoring import score_series

# Règle de verdict, fixée avant de regarder les résultats.
MIN_WIN_SHARE = 0.6  # la stratégie doit battre la référence au moins 6 années de test sur 10
TOP_N_GRID = (10, 20)
REBALANCE_GRID = (21, 63)  # mensuel, trimestriel
STRATEGIES = {
    "momentum": "Momentum 12 mois",
    "low_vol": "Faible volatilité",
    "combo": "Momentum + faible volatilité",
    "score": "Score PEA Radar",
}
AUTO = "Sélection automatique"


@dataclass
class WalkForwardParams:
    years: int = 15
    train_years: int = 3
    fee_rate: float = 0.005


def build_signals(histories: dict[str, pd.DataFrame], with_score: bool = True) -> tuple[pd.DataFrame, dict]:
    """Cours de clôture alignés et, par stratégie, un tableau de signaux (plus haut = mieux)."""
    close = pd.DataFrame({t: df["Close"] for t, df in histories.items() if len(df) > 300}).sort_index()
    log_ret = np.log(close).diff()
    momentum = close.shift(21) / close.shift(TRADING_DAYS) - 1  # 12 mois, dernier mois ignoré
    low_vol = -log_ret.rolling(126, min_periods=100).std()
    signals = {
        "momentum": momentum,
        "low_vol": low_vol,
        "combo": (momentum.rank(axis=1, pct=True) + low_vol.rank(axis=1, pct=True)) / 2,
    }
    if with_score:
        signals["score"] = pd.DataFrame(
            {t: score_series(compute_all(histories[t])) for t in close.columns}).reindex(close.index)
    return close, signals


def simulate(signal: pd.DataFrame, close: pd.DataFrame, top_n: int, rebalance: int,
             fee_rate: float) -> tuple[pd.Series, pd.Series]:
    """Rendements quotidiens nets de frais, et rotation (part du portefeuille échangée) par jour."""
    rets = close.pct_change(fill_method=None).fillna(0.0).to_numpy()
    sig, px = signal.to_numpy(), close.to_numpy()
    n_days, n = rets.shape
    weights = np.zeros(n)
    out, turnover = np.zeros(n_days), np.zeros(n_days)
    for t in range(n_days):
        if t:
            day = float(weights @ rets[t])
            weights = weights * (1 + rets[t]) / (1 + day)  # les poids dérivent avec les cours
            out[t] = day
        if t % rebalance == 0:
            valid = np.isfinite(sig[t]) & np.isfinite(px[t])
            if valid.sum() >= top_n:
                ranked = np.where(valid, sig[t], -np.inf)
                target = np.zeros(n)
                target[np.argpartition(ranked, -top_n)[-top_n:]] = 1 / top_n
                turnover[t] = np.abs(target - weights).sum()
                out[t] -= fee_rate * turnover[t]
                weights = target
    return pd.Series(out, index=close.index), pd.Series(turnover, index=close.index)


def _sharpe(r: pd.Series) -> float:
    return float(r.mean() / r.std()) if len(r) > 20 and r.std() > 0 else -np.inf


def _equity(r: pd.Series) -> pd.Series:
    return (1 + r).cumprod()


def run_walkforward(histories: dict[str, pd.DataFrame], benchmark: pd.DataFrame | None,
                    benchmark_name: str, params: WalkForwardParams) -> dict:
    close, signals = build_signals(histories)
    start = close.index[-1] - pd.DateOffset(years=params.years)
    grid = list(product(TOP_N_GRID, REBALANCE_GRID))
    runs, turns = {}, {}
    for name, signal in signals.items():
        for top_n, reb in grid:
            runs[name, top_n, reb], turns[name, top_n, reb] = simulate(
                signal, close, top_n, reb, params.fee_rate)

    # Première date où toutes les stratégies sont investies, pour un apprentissage équitable.
    live = max(next((d for d, v in r.items() if v != 0), close.index[-1]) for r in runs.values())
    first = max(start, live)
    first_test_year = (first + pd.DateOffset(years=params.train_years)).year + 1
    test_years = list(range(first_test_year, close.index[-1].year + 1))
    if len(test_years) < 3:
        raise ValueError("Historique insuffisant pour une validation walk-forward")

    oos = {name: [] for name in [*signals, AUTO]}
    oos_turn = {name: 0.0 for name in oos}
    choices = []
    previous: dict[str, tuple] = {}
    for year in test_years:
        train = slice(f"{year - params.train_years}-01-01", f"{year - 1}-12-31")
        test = slice(f"{year}-01-01", f"{year}-12-31")
        best = {name: max(((name, n, r) for n, r in grid), key=lambda k: _sharpe(runs[k][train]))
                for name in signals}
        best[AUTO] = max(best.values(), key=lambda k: _sharpe(runs[k][train]))
        row = {"year": year}
        for name, key in best.items():
            r = runs[key][test].copy()
            if previous.get(name) not in (None, key) and len(r):
                r.iloc[0] -= 2 * params.fee_rate  # changement de réglage : tout vendre, tout racheter
            previous[name] = key
            oos[name].append(r)
            oos_turn[name] += float(turns[key][test].sum())
            label = STRATEGIES[key[0]] if name == AUTO else ""
            setting = f"{key[1]} titres, tous les {key[2]} j"
            row[STRATEGIES.get(name, name)] = f"{label} · {setting}" if label else setting
        choices.append(row)

    series = {(STRATEGIES.get(k, k)): pd.concat(v) for k, v in oos.items()}
    dates = series[AUTO].index
    universe = close.pct_change(fill_method=None).mean(axis=1).fillna(0).reindex(dates)
    refs = {"Univers équipondéré": universe}
    if benchmark is not None and not benchmark.empty:
        etf_close = benchmark["Close"].reindex(close.index).ffill()
        refs[benchmark_name] = etf_close.pct_change().reindex(dates).fillna(0)
    ref_name = benchmark_name if benchmark_name in refs else "Univers équipondéré"
    all_series = {**series, **refs}

    yearly = pd.DataFrame({k: (1 + v).groupby(v.index.year).prod() - 1
                           for k, v in all_series.items()})
    n_years = len(dates) / TRADING_DAYS
    rows = []
    best_ref = max(metrics(_equity(r))["cagr_pct"] for r in refs.values())
    for name, r in all_series.items():
        m = metrics(_equity(r))
        wins = int((yearly[name] > yearly[ref_name]).sum())
        is_ref = name in refs
        beats = not is_ref and m["cagr_pct"] > best_ref and wins >= MIN_WIN_SHARE * len(yearly)
        key = next((k for k, v in STRATEGIES.items() if v == name), name)
        rows.append({
            "name": name, "reference": is_ref, **m,
            "years_won": None if is_ref else wins, "years": len(yearly),
            "turnover_pct_per_year": None if is_ref else round(oos_turn[key] / n_years * 100),
            "beats_reference": None if is_ref else beats,
        })

    weekly = pd.DataFrame({k: _equity(v) for k, v in all_series.items()}).iloc[::5]
    winners = [r["name"] for r in rows if r["beats_reference"]]
    return {
        "params": {"years": params.years, "train_years": params.train_years, "fee_rate": params.fee_rate},
        "period": {"start": dates[0].strftime("%Y-%m-%d"), "end": dates[-1].strftime("%Y-%m-%d"),
                   "tickers": int(close.shape[1]), "test_years": len(test_years)},
        "reference": ref_name,
        "rule": "Une stratégie est retenue si son rendement annualisé sur les années de test dépasse à la "
                f"fois celui de l'ETF et celui de l'univers équipondéré, et qu'elle bat « {ref_name} » au "
                f"moins {int(MIN_WIN_SHARE * 100)} % des années.",
        "strategies": rows,
        "winners": winners,
        "yearly": [{"year": int(y), **{k: round(float(v) * 100, 1) for k, v in row.items()}}
                   for y, row in yearly.iterrows()],
        "choices": choices,
        "curve": [{"date": d.strftime("%Y-%m-%d"), **{k: round(float(v) * 100, 1) for k, v in row.items()}}
                  for d, row in weekly.iterrows()],
        "notes": [
            "L'univers équipondéré profite du même biais du survivant que les stratégies : c'est pourquoi "
            "il faut le battre lui aussi, et pas seulement l'ETF.",
            "Biais du survivant : seules les sociétés aujourd'hui dans l'indice sont testées. Il avantage "
            "surtout le momentum, puisque les titres qui ont le plus monté sont ceux qui y sont restés.",
            "Un changement de réglage d'une année sur l'autre est facturé comme une vente et un rachat "
            "complets du portefeuille.",
            "Frais de " + f"{params.fee_rate * 100:g}".replace(".", ",") + " % par mouvement, sans "
            "décalage de cours à l'exécution.",
            "« Sélection automatique » choisit chaque année la stratégie la mieux classée sur les "
            f"{params.train_years} années précédentes : c'est ce qu'aurait donné le fait de suivre le "
            "meilleur backtest du moment.",
        ],
    }
