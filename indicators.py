"""
Technical indicators as pure pandas/numpy functions.

No MetaTrader5 dependency on purpose: this module is shared by BOTH the live
bot and the backtester, so signals computed offline match signals computed
live, bar-for-bar. Each function takes/returns pandas Series so they compose
cleanly and stay vectorized (fast over long histories).
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def sma(series: pd.Series, period: int) -> pd.Series:
    """Simple moving average."""
    return series.rolling(period).mean()


def ema(series: pd.Series, period: int) -> pd.Series:
    """Exponential moving average (adjust=False matches platform EMAs)."""
    return series.ewm(span=period, adjust=False).mean()


def rsi(series: pd.Series, period: int = 14) -> pd.Series:
    """
    Wilder's Relative Strength Index, 0..100.

    Uses Wilder smoothing (an EMA with alpha = 1/period), which matches how
    MT5 and most charting platforms compute RSI.
    """
    delta = series.diff()
    gain = delta.clip(lower=0.0)
    loss = -delta.clip(upper=0.0)

    avg_gain = gain.ewm(alpha=1.0 / period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1.0 / period, adjust=False).mean()

    rs = avg_gain / avg_loss.replace(0.0, np.nan)
    out = 100.0 - (100.0 / (1.0 + rs))
    # When there were no losses at all, RSI is 100 by definition.
    out = out.where(avg_loss != 0.0, 100.0)
    return out


def atr(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14) -> pd.Series:
    """
    Average True Range (Wilder smoothing). Measures volatility in price units.
    Used for volatility-scaled stops and position sizing.
    """
    prev_close = close.shift(1)
    true_range = pd.concat(
        [
            (high - low),
            (high - prev_close).abs(),
            (low - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    return true_range.ewm(alpha=1.0 / period, adjust=False).mean()


def atr_last(high, low, close, period: int = 14) -> float:
    """
    Fast ATR of the LAST bar only, using numpy (no pandas EWM overhead).
    Used in hot backtest loops where we just need the latest value.
    """
    import numpy as np
    h = np.asarray(high, dtype=float)
    l = np.asarray(low, dtype=float)
    c = np.asarray(close, dtype=float)
    if len(h) < period + 1:
        return float("nan")
    prev_c = c[:-1]
    tr = np.maximum.reduce([
        h[1:] - l[1:],
        np.abs(h[1:] - prev_c),
        np.abs(l[1:] - prev_c),
    ])
    # Wilder smoothing via simple recursive pass on the true-range series.
    alpha = 1.0 / period
    a = tr[0]
    for x in tr[1:]:
        a = a + alpha * (x - a)
    return float(a)


def macd(
    series: pd.Series,
    fast: int = 12,
    slow: int = 26,
    signal: int = 9,
) -> pd.DataFrame:
    """
    MACD line, signal line, and histogram.
    Returns a DataFrame with columns: macd, signal, hist.
    """
    macd_line = ema(series, fast) - ema(series, slow)
    signal_line = ema(macd_line, signal)
    hist = macd_line - signal_line
    return pd.DataFrame({"macd": macd_line, "signal": signal_line, "hist": hist})


def session_vwap(df: pd.DataFrame) -> pd.Series:
    """
    Volume-Weighted Average Price, reset each calendar day (a "session VWAP").

    VWAP is the average price weighted by volume - widely used by intraday
    traders as the day's "fair value". Price above VWAP = buyers in control;
    below = sellers. Requires columns: time, high, low, close, tick_volume
    (falls back to a flat volume of 1 if no volume column is present, which
    turns this into a simple typical-price average).
    """
    typical = (df["high"] + df["low"] + df["close"]) / 3.0
    if "tick_volume" in df.columns:
        vol = df["tick_volume"].astype(float).clip(lower=1.0)
    elif "volume" in df.columns:
        vol = df["volume"].astype(float).clip(lower=1.0)
    else:
        vol = pd.Series(1.0, index=df.index)

    day = pd.to_datetime(df["time"]).dt.date
    pv = typical * vol
    cum_pv = pv.groupby(day).cumsum()
    cum_vol = vol.groupby(day).cumsum()
    return cum_pv / cum_vol
