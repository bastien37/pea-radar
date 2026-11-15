"""Backtest : rejoue la stratégie du score sur l'historique, frais inclus.

Règles simulées
- À la clôture d'un jour de revue, on classe les titres par score ; les meilleurs non
  détenus (score minimal, plafond par secteur) sont achetés à l'ouverture suivante.
- Chaque ligne reçoit une part égale du portefeuille ; stop à N × ATR sous le cours
  d'entrée ; sortie à la clôture une fois la durée de détention atteinte.
- Filtre de marché optionnel : aucun achat quand le CAC 40 est sous sa moyenne 200 séances.

Ce que le backtest ne voit pas : les fondamentaux et l'actualité (pas d'historique),
et les sociétés sorties de l'indice (biais du survivant, qui flatte tous les résultats).
"""

from dataclasses import asdict, dataclass

import numpy as np
import pandas as pd

from app.analysis.indicators import TRADING_DAYS, compute_all, sma
from app.analysis.scoring import score_series

MIN_HISTORY = 260


@dataclass
class BacktestParams:
    years: int = 10
    positions: int = 5
    holding_days: int = 40
    stop_atr: float = 2.0
    min_score: float = 60.0
    rebalance_days: int = 5
    max_per_sector: int = 2
    market_filter: bool = True
    fee_rate: float = 0.005


def metrics(equity: pd.Series) -> dict:
    ret = equity.pct_change().dropna()
    years = max(len(equity) / TRADING_DAYS, 1e-9)
    total = equity.iloc[-1] / equity.iloc[0] - 1
    vol = float(ret.std() * np.sqrt(TRADING_DAYS)) if len(ret) > 1 else 0.0
    cagr = float((1 + total) ** (1 / years) - 1) if total > -1 else -1.0
    drawdown = float((1 - equity / equity.cummax()).max())
    return {
        "total_return_pct": round(float(total) * 100, 1), "cagr_pct": round(cagr * 100, 2),
        "max_drawdown_pct": round(drawdown * 100, 1), "volatility_pct": round(vol * 100, 1),
        "sharpe": round(float(ret.mean() / ret.std() * np.sqrt(TRADING_DAYS)), 2)
        if len(ret) > 1 and ret.std() > 0 else 0.0,
    }


def run_backtest(histories: dict[str, pd.DataFrame], sectors: dict[str, str],
                 index: pd.DataFrame | None, etf: pd.DataFrame | None,
                 params: BacktestParams) -> dict:
    cols: dict[str, dict[str, pd.Series]] = {k: {} for k in ("close", "open", "low", "atr", "score")}
    for ticker, df in histories.items():
        if ticker not in sectors or len(df) < MIN_HISTORY:
            continue
        ind = compute_all(df)
        cols["close"][ticker], cols["open"][ticker] = ind["Close"], ind["Open"]
        cols["low"][ticker], cols["atr"][ticker] = ind["Low"], ind["atr"]
        cols["score"][ticker] = score_series(ind)
    if not cols["close"]:
        raise ValueError("Historique insuffisant pour le backtest")

    close = pd.DataFrame(cols["close"]).sort_index()
    start = close.index[-1] - pd.DateOffset(years=params.years)
    close = close[close.index >= start]
    dates = close.index
    frames = {k: pd.DataFrame(v).reindex(dates) for k, v in cols.items() if k != "close"}
    score = frames["score"]
    scored = score.notna().sum(axis=1)
    first = int(np.argmax(scored.to_numpy() >= min(20, close.shape[1])))
    dates, close = dates[first:], close.iloc[first:]
    opn, low, atr, score = (frames[k].iloc[first:] for k in ("open", "low", "atr", "score"))
    mark = close.ffill()

    market_ok = pd.Series(True, index=dates)
    notes = []
    if params.market_filter:
        if index is not None and len(index) > 200:
            trend = (index["Close"] > sma(index["Close"], 200)).reindex(dates).ffill()
            market_ok = trend.fillna(True).astype(bool)
        else:
            notes.append("Indice CAC 40 indisponible : filtre de marché non appliqué.")

    cash, held, pending, trades, equity, invested = 1.0, {}, [], [], [], []
    for i, day in enumerate(dates):
        # 1) achats décidés la veille, exécutés à l'ouverture
        for ticker, stop_dist in pending:
            price = opn.at[day, ticker]
            if not np.isfinite(price) or ticker in held or len(held) >= params.positions:
                continue
            total = cash + sum(p["qty"] * mark.at[day, t] for t, p in held.items())
            budget = min(total / params.positions, cash)
            if budget <= 0:
                continue
            qty = budget / (price * (1 + params.fee_rate))
            cash -= budget
            held[ticker] = {"qty": qty, "entry": price, "cost": budget, "stop": price - stop_dist,
                            "day": i, "date": day}
        pending = []

        # 2) sorties : stop touché en séance, sinon échéance à la clôture
        for ticker in list(held):
            p = held[ticker]
            lo, op = low.at[day, ticker], opn.at[day, ticker]
            exit_price = reason = None
            if np.isfinite(lo) and lo <= p["stop"]:
                gap = np.isfinite(op) and op < p["stop"] and i > p["day"]
                exit_price, reason = (op if gap else p["stop"]), "stop"
            elif i - p["day"] >= params.holding_days and np.isfinite(close.at[day, ticker]):
                exit_price, reason = close.at[day, ticker], "échéance"
            if exit_price is None:
                continue
            proceeds = p["qty"] * exit_price * (1 - params.fee_rate)
            cash += proceeds
            trades.append({"ticker": ticker, "entry": p["date"].strftime("%Y-%m-%d"),
                           "exit": day.strftime("%Y-%m-%d"), "days": i - p["day"], "reason": reason,
                           "pnl_pct": round((proceeds / p["cost"] - 1) * 100, 2)})
            del held[ticker]

        equity.append(cash + sum(p["qty"] * mark.at[day, t] for t, p in held.items()))
        invested.append(1 - cash / equity[-1] if equity[-1] > 0 else 0.0)

        # 3) décision d'achat à la clôture, pour l'ouverture suivante
        if i % params.rebalance_days == 0 and market_ok.at[day] and len(held) < params.positions:
            per_sector: dict[str, int] = {}
            for t in held:
                per_sector[sectors[t]] = per_sector.get(sectors[t], 0) + 1
            free = params.positions - len(held)
            ranked = score.loc[day].dropna().sort_values(ascending=False)
            for ticker, value in ranked.items():
                if free == 0 or value < params.min_score:
                    break
                sec = sectors[ticker]
                dist = params.stop_atr * atr.at[day, ticker]
                if ticker in held or per_sector.get(sec, 0) >= params.max_per_sector \
                        or not np.isfinite(dist):
                    continue
                pending.append((ticker, dist))
                per_sector[sec] = per_sector.get(sec, 0) + 1
                free -= 1

    equity = pd.Series(equity, index=dates)
    universe_returns = close.pct_change(fill_method=None).mean(axis=1).fillna(0)
    curves = {"Stratégie": equity, "Univers équipondéré": (1 + universe_returns).cumprod()}
    if etf is not None and etf.index[0] <= dates[0] + pd.Timedelta(days=10):
        curves["ETF CAC 40 (dividendes réinvestis)"] = etf["Close"].reindex(dates).ffill().bfill()
    elif index is not None and not index.empty:
        curves["CAC 40 (hors dividendes)"] = index["Close"].reindex(dates).ffill().bfill()
        notes.append("L'ETF CAC 40 ne couvre pas toute la période : l'indice affiché ignore "
                     "les dividendes, ce qui le désavantage d'environ 3 points par an.")
    curves = {k: v / v.iloc[0] for k, v in curves.items()}

    pnl = [t["pnl_pct"] for t in trades]
    yearly = pd.DataFrame({k: v.groupby(v.index.year).last() / v.groupby(v.index.year).first() - 1
                           for k, v in curves.items()})
    weekly = pd.DataFrame(curves).iloc[::5]
    return {
        "params": asdict(params),
        "period": {"start": dates[0].strftime("%Y-%m-%d"), "end": dates[-1].strftime("%Y-%m-%d"),
                   "tickers": int(close.shape[1])},
        "metrics": {k: metrics(v) for k, v in curves.items()},
        "trades": {
            "count": len(trades),
            "exposure_pct": round(float(np.mean(invested)) * 100, 1),
            "win_rate": round(float(np.mean([p > 0 for p in pnl])), 3) if pnl else None,
            "avg_pnl_pct": round(float(np.mean(pnl)), 2) if pnl else None,
            "avg_days": round(float(np.mean([t["days"] for t in trades])), 1) if trades else None,
            "stopped_pct": round(float(np.mean([t["reason"] == "stop" for t in trades])) * 100, 1)
            if trades else None,
            "best": sorted(trades, key=lambda t: t["pnl_pct"], reverse=True)[:5],
            "worst": sorted(trades, key=lambda t: t["pnl_pct"])[:5],
        },
        "yearly": [{"year": int(y), **{k: round(float(v) * 100, 1) for k, v in row.items()}}
                   for y, row in yearly.iterrows()],
        "curve": [{"date": d.strftime("%Y-%m-%d"), **{k: round(float(v) * 100, 1) for k, v in row.items()}}
                  for d, row in weekly.iterrows()],
        "notes": notes + [
            "Biais du survivant : seules les sociétés aujourd'hui dans l'indice sont testées, "
            "ce qui embellit la stratégie comme l'univers équipondéré.",
            "Fondamentaux et actualités ne sont pas rejoués : seul le score technique est testé.",
            "Exécution à l'ouverture sans décalage de cours ; frais de "
            + f"{params.fee_rate * 100:g}".replace(".", ",") + " % par ordre.",
        ],
    }
