"""Indicateurs techniques vectorisés (pandas), sans dépendance externe."""

import numpy as np
import pandas as pd

TRADING_DAYS = 252


def sma(s: pd.Series, n: int) -> pd.Series:
    return s.rolling(n, min_periods=n).mean()


def ema(s: pd.Series, n: int) -> pd.Series:
    return s.ewm(span=n, adjust=False, min_periods=n).mean()


def _wilder(s: pd.Series, n: int) -> pd.Series:
    return s.ewm(alpha=1 / n, adjust=False, min_periods=n).mean()


def rsi(close: pd.Series, n: int = 14) -> pd.Series:
    delta = close.diff()
    gain, loss = _wilder(delta.clip(lower=0), n), _wilder(-delta.clip(upper=0), n)
    out = 100 - 100 / (1 + gain / loss.replace(0, np.nan))
    return out.where(loss != 0, 100.0).where(gain.notna())


def macd(close: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9):
    line = ema(close, fast) - ema(close, slow)
    sig = line.ewm(span=signal, adjust=False, min_periods=signal).mean()
    return line, sig, line - sig


def stochastic(high, low, close, n: int = 14, smooth_k: int = 3, d: int = 3):
    """Stochastique lent : %K lissé et sa moyenne %D."""
    lo, hi = low.rolling(n).min(), high.rolling(n).max()
    raw = 100 * (close - lo) / (hi - lo).replace(0, np.nan)
    k = raw.rolling(smooth_k).mean()
    return k, k.rolling(d).mean()


def bollinger_pct_b(close: pd.Series, n: int = 20, k: float = 2.0) -> pd.Series:
    mid, std = sma(close, n), close.rolling(n).std(ddof=0)
    return (close - (mid - k * std)) / (2 * k * std).replace(0, np.nan)


def true_range(high, low, close) -> pd.Series:
    prev = close.shift()
    return pd.concat([high - low, (high - prev).abs(), (low - prev).abs()], axis=1).max(axis=1)


def atr(high, low, close, n: int = 14) -> pd.Series:
    return _wilder(true_range(high, low, close), n)


def adx(high, low, close, n: int = 14) -> pd.Series:
    up, down = high.diff(), -low.diff()
    plus_dm = up.where((up > down) & (up > 0), 0.0)
    minus_dm = down.where((down > up) & (down > 0), 0.0)
    tr = _wilder(true_range(high, low, close), n).replace(0, np.nan)
    plus_di, minus_di = 100 * _wilder(plus_dm, n) / tr, 100 * _wilder(minus_dm, n) / tr
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)
    return _wilder(dx, n)


def compute_all(df: pd.DataFrame) -> pd.DataFrame:
    """Ajoute tous les indicateurs à un DataFrame OHLCV."""
    h, low, c, v = df["High"], df["Low"], df["Close"], df["Volume"]
    out = df.copy()
    out["sma20"], out["sma50"], out["sma200"] = sma(c, 20), sma(c, 50), sma(c, 200)
    out["rsi"] = rsi(c)
    out["macd"], out["macd_signal"], out["macd_hist"] = macd(c)
    out["stoch_k"], out["stoch_d"] = stochastic(h, low, c)
    out["pct_b"] = bollinger_pct_b(c)
    out["atr"] = atr(h, low, c)
    out["atr_pct"] = out["atr"] / c
    out["adx"] = adx(h, low, c)
    out["roc63"], out["roc126"] = c.pct_change(63), c.pct_change(126)
    out["vol_ratio"] = v / sma(v, 20).replace(0, np.nan)
    ret = np.log(c).diff()
    out["volatility"] = ret.rolling(60).std() * np.sqrt(TRADING_DAYS)
    out["drawdown_1y"] = 1 - c / c.rolling(TRADING_DAYS, min_periods=60).max()
    return out
