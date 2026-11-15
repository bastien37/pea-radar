"""Accès aux cours historiques, derrière une interface pour pouvoir changer de source."""

import logging
import threading
import time
import zlib
from typing import Protocol

import numpy as np
import pandas as pd

from app.config import get_settings

log = logging.getLogger(__name__)
COLUMNS = ["Open", "High", "Low", "Close", "Volume"]


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
        for i in range(0, len(tickers), 40):  # lots pour ménager l'API Yahoo
            chunk = tickers[i : i + 40]
            raw = yf.download(
                chunk, period=self.period, interval="1d", auto_adjust=True,
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
        missing = sorted(set(tickers) - set(out))
        if missing:
            log.warning("Cours indisponibles pour : %s", ", ".join(missing))
        return out


class SyntheticProvider:
    """Marche aléatoire déterministe par ticker — démonstration hors ligne et tests uniquement."""

    def __init__(self, days: int = 780) -> None:
        self.days = days

    def history(self, tickers: list[str]) -> dict[str, pd.DataFrame]:
        idx = pd.bdate_range(end=pd.Timestamp.today().normalize(), periods=self.days)
        out = {}
        for t in tickers:
            rng = np.random.default_rng(zlib.crc32(t.encode()))
            drift, vol = rng.uniform(-0.0002, 0.0008), rng.uniform(0.008, 0.03)
            close = rng.uniform(10, 300) * np.exp(np.cumsum(rng.normal(drift, vol, self.days)))
            spread = np.abs(rng.normal(0, vol / 2, self.days))
            out[t] = pd.DataFrame(
                {
                    "Open": close * (1 + rng.normal(0, vol / 3, self.days)),
                    "High": close * (1 + spread),
                    "Low": close * (1 - spread),
                    "Close": close,
                    "Volume": rng.integers(50_000, 2_000_000, self.days).astype(float),
                },
                index=idx,
            )
        return out


class CachedProvider:
    """Cache mémoire avec durée de vie, partagé entre le scan, les fiches et la simulation."""

    def __init__(self, inner: MarketDataProvider, ttl: int) -> None:
        self.inner, self.ttl = inner, ttl
        self._cache: dict[str, tuple[float, pd.DataFrame]] = {}
        self._lock = threading.Lock()

    def history(self, tickers: list[str]) -> dict[str, pd.DataFrame]:
        now = time.monotonic()
        with self._lock:
            fresh = {t: v[1] for t in tickers
                     if (v := self._cache.get(t)) and now - v[0] < self.ttl}
        todo = [t for t in tickers if t not in fresh]
        if todo:
            fetched = self.inner.history(todo)
            with self._lock:
                for t, df in fetched.items():
                    self._cache[t] = (now, df)
            fresh.update(fetched)
        return fresh

    def last_price(self, ticker: str) -> tuple[float, str] | None:
        df = self.history([ticker]).get(ticker)
        if df is None or df.empty:
            return None
        return float(df["Close"].iloc[-1]), df.index[-1].strftime("%Y-%m-%d")


_provider: CachedProvider | None = None


def get_provider() -> CachedProvider:
    global _provider
    if _provider is None:
        s = get_settings()
        inner = SyntheticProvider() if s.data_provider == "synthetic" \
            else YFinanceProvider(s.history_period)
        _provider = CachedProvider(inner, s.price_cache_seconds)
    return _provider
