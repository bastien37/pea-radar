"""Accès aux cours historiques : sources interchangeables, repli, et conservation en base."""

import logging
import threading
import zlib
from datetime import timedelta
from typing import Protocol

import httpx
import numpy as np
import pandas as pd
from sqlalchemy import delete, insert, select

from app.config import get_settings
from app.db import engine
from app.models import PriceBar, PriceMeta, utcnow

log = logging.getLogger(__name__)
COLUMNS = ["Open", "High", "Low", "Close", "Volume"]
INDEX_PERIOD = "max"  # les indices servent aux tests sur longue durée (crises de 2000 et 2008)


def _by_period(tickers: list[str], period: str) -> dict[str, list[str]]:
    groups: dict[str, list[str]] = {}
    for t in tickers:
        groups.setdefault(INDEX_PERIOD if t.startswith("^") else period, []).append(t)
    return groups


class MarketDataProvider(Protocol):
    def history(self, tickers: list[str]) -> dict[str, pd.DataFrame]:
        """Retourne, par ticker, un DataFrame OHLCV journalier ajusté, indexé par date."""
        ...


class YFinanceProvider:
    def __init__(self, period: str) -> None:
        self.period = period

    def history(self, tickers: list[str]) -> dict[str, pd.DataFrame]:
        import yfinance as yf

        out: dict[str, pd.DataFrame] = {}
        chunks = [(period, group[i : i + 40]) for period, group in _by_period(tickers, self.period).items()
                  for i in range(0, len(group), 40)]  # lots pour ménager l'API Yahoo
        for period, chunk in chunks:
            raw = yf.download(
                chunk, period=period, interval="1d", auto_adjust=True,
                group_by="ticker", threads=True, progress=False,
            )
            if raw is None or raw.empty:
                continue
            for t in chunk:
                try:
                    df = raw[t] if isinstance(raw.columns, pd.MultiIndex) else raw
                    df = df[COLUMNS].dropna(subset=["Close"])
                except KeyError:
                    continue
                if not df.empty:
                    df.index = pd.to_datetime(df.index).tz_localize(None)
                    out[t] = df.astype(float)
        return out


class YahooChartProvider:
    """Secours : interroge directement l'API « chart » de Yahoo, sans passer par yfinance.

    Protège d'une panne de la bibliothèque (fréquente lors des changements côté Yahoo),
    pas d'une indisponibilité de Yahoo lui-même : dans ce cas, les cours en base servent.
    """

    URL = "https://query1.finance.yahoo.com/v8/finance/chart/{ticker}"

    def __init__(self, period: str) -> None:
        self.period = period

    def _one(self, client: httpx.Client, ticker: str) -> pd.DataFrame | None:
        r = client.get(self.URL.format(ticker=ticker),
                       params={"range": INDEX_PERIOD if ticker.startswith("^") else self.period,
                               "interval": "1d", "events": "div,splits"})
        r.raise_for_status()
        res = r.json()["chart"]["result"][0]
        quote = res["indicators"]["quote"][0]
        df = pd.DataFrame({c: quote[c.lower()] for c in COLUMNS},
                          index=pd.to_datetime(res["timestamp"], unit="s").normalize())
        adj = res["indicators"].get("adjclose", [{}])[0].get("adjclose")
        df = df.astype(float)
        if adj:  # ramène OHLC en cours ajustés des dividendes et divisions
            factor = pd.Series(adj, index=df.index, dtype=float) / df["Close"]
            for c in ("Open", "High", "Low", "Close"):
                df[c] = df[c] * factor
        df = df.dropna(subset=["Close"])
        return df[~df.index.duplicated(keep="last")] if not df.empty else None

    def history(self, tickers: list[str]) -> dict[str, pd.DataFrame]:
        out = {}
        with httpx.Client(timeout=20, headers={"User-Agent": "Mozilla/5.0 (pea-radar)"}) as client:
            for t in tickers:
                try:
                    if (df := self._one(client, t)) is not None:
                        out[t] = df
                except (httpx.HTTPError, KeyError, IndexError, TypeError, ValueError) as exc:
                    log.warning("Source de secours : %s indisponible (%s)", t, type(exc).__name__)
        return out


class FallbackProvider:
    """Interroge les sources dans l'ordre ; les suivantes ne traitent que les titres manquants."""

    def __init__(self, providers: list[MarketDataProvider]) -> None:
        self.providers = providers

    def history(self, tickers: list[str]) -> dict[str, pd.DataFrame]:
        out: dict[str, pd.DataFrame] = {}
        for provider in self.providers:
            missing = [t for t in tickers if t not in out]
            if not missing:
                break
            try:
                out.update(provider.history(missing))
            except Exception as exc:  # noqa: BLE001 — une source en panne ne doit pas tout arrêter
                log.warning("%s en échec : %s", type(provider).__name__, exc)
        return out


class SyntheticProvider:
    """Marche aléatoire déterministe par ticker — démonstration hors ligne et tests uniquement."""

    def __init__(self, days: int = 2600, index_days: int = 9000) -> None:
        self.days, self.index_days = days, index_days

    def history(self, tickers: list[str]) -> dict[str, pd.DataFrame]:
        out = {}
        for t in tickers:
            days = self.index_days if t.startswith("^") else self.days
            idx = pd.bdate_range(end=pd.Timestamp.today().normalize(), periods=days)
            rng = np.random.default_rng(zlib.crc32(t.encode()))
            drift, vol = rng.uniform(-0.0002, 0.0006), rng.uniform(0.008, 0.03)
            close = rng.uniform(10, 300) * np.exp(np.cumsum(rng.normal(drift, vol, days)))
            spread = np.abs(rng.normal(0, vol / 2, days))
            out[t] = pd.DataFrame(
                {
                    "Open": close * (1 + rng.normal(0, vol / 3, days)),
                    "High": close * (1 + spread),
                    "Low": close * (1 - spread),
                    "Close": close,
                    "Volume": rng.integers(50_000, 2_000_000, days).astype(float),
                },
                index=idx,
            )
        return out


class StoredProvider:
    """Cache à deux niveaux : mémoire, puis table `prices`.

    Un cours périmé en base est rafraîchi auprès de la source ; si la source ne répond
    pas, le cours périmé est servi quand même plutôt que de faire échouer le scan.
    """

    def __init__(self, inner: MarketDataProvider, ttl: int) -> None:
        self.inner, self.ttl = inner, timedelta(seconds=ttl)
        self._mem: dict[str, pd.DataFrame] = {}
        self._fresh_until: dict[str, object] = {}
        self._lock = threading.Lock()

    def _load_db(self, tickers: list[str]) -> tuple[dict[str, pd.DataFrame], dict]:
        with engine.connect() as conn:
            meta = dict(conn.execute(
                select(PriceMeta.ticker, PriceMeta.fetched_at).where(PriceMeta.ticker.in_(tickers))
            ).all())
            if not meta:
                return {}, {}
            df = pd.read_sql(
                select(PriceBar).where(PriceBar.ticker.in_(list(meta))).order_by(PriceBar.date), conn
            )
        out = {}
        for t, part in df.groupby("ticker"):
            part = part.set_index(pd.to_datetime(part["date"]))
            part.index.name = None
            out[t] = part[["open", "high", "low", "close", "volume"]].set_axis(COLUMNS, axis=1)
        fetched = {t: (ts if ts.tzinfo else ts.replace(tzinfo=utcnow().tzinfo)) for t, ts in meta.items()}
        return out, fetched

    def _save_db(self, data: dict[str, pd.DataFrame]) -> None:
        now = utcnow()
        rows = [
            {"ticker": t, "date": d.strftime("%Y-%m-%d"), "open": o, "high": h, "low": lo,
             "close": c, "volume": v}
            for t, df in data.items()
            for d, o, h, lo, c, v in zip(df.index, *(df[col].tolist() for col in COLUMNS), strict=True)
        ]
        with engine.begin() as conn:
            conn.execute(delete(PriceBar).where(PriceBar.ticker.in_(list(data))))
            conn.execute(delete(PriceMeta).where(PriceMeta.ticker.in_(list(data))))
            for i in range(0, len(rows), 20_000):
                conn.execute(insert(PriceBar), rows[i : i + 20_000])
            conn.execute(insert(PriceMeta), [{"ticker": t, "fetched_at": now} for t in data])

    def history(self, tickers: list[str]) -> dict[str, pd.DataFrame]:
        now = utcnow()
        with self._lock:
            out = {t: self._mem[t] for t in tickers
                   if t in self._mem and self._fresh_until[t] > now}
            todo = [t for t in tickers if t not in out]
            if not todo:
                return out
            stored, fetched_at = self._load_db(todo)
            stale = [t for t in todo if t not in stored or fetched_at[t] + self.ttl <= now]
            fetched = self.inner.history(stale) if stale else {}
            fetched = {t: df for t, df in fetched.items() if not df.empty}
            if fetched:
                self._save_db(fetched)
            for t in todo:
                df = fetched.get(t, stored.get(t))
                if df is None:
                    continue
                if t in stale and t not in fetched:
                    log.warning("%s : source indisponible, cours en base du %s utilisés",
                                t, df.index[-1].date())
                self._mem[t] = out[t] = df
                if t in fetched:
                    self._fresh_until[t] = now + self.ttl
                elif t not in stale:
                    self._fresh_until[t] = fetched_at[t] + self.ttl
                else:  # cours périmé servi faute de mieux : nouvelle tentative dans 10 minutes
                    self._fresh_until[t] = now + timedelta(minutes=10)
            missing = sorted(set(tickers) - set(out))
            if missing:
                log.warning("Cours indisponibles pour : %s", ", ".join(missing))
            return out

    def last_price(self, ticker: str) -> tuple[float, str] | None:
        df = self.history([ticker]).get(ticker)
        if df is None or df.empty:
            return None
        return float(df["Close"].iloc[-1]), df.index[-1].strftime("%Y-%m-%d")

    def clear(self) -> None:
        with self._lock:
            self._mem.clear()
            self._fresh_until.clear()


_provider: StoredProvider | None = None


def get_provider() -> StoredProvider:
    global _provider
    if _provider is None:
        s = get_settings()
        inner: MarketDataProvider = SyntheticProvider() if s.data_provider == "synthetic" \
            else FallbackProvider([YFinanceProvider(s.history_period),
                                   YahooChartProvider(s.history_period)])
        _provider = StoredProvider(inner, s.price_cache_seconds)
    return _provider
