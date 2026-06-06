"""
Advanced multi-filter trend-following strategy.

This is the single source of truth for trade decisions, used by BOTH the live
bot and the backtester so behavior matches exactly.

Entry logic (all must agree):
    1. TREND FILTER  - price is above a long EMA for longs / below for shorts.
    2. MA CROSS      - fast EMA crosses slow EMA in the trade direction.
    3. MOMENTUM      - RSI confirms (not overbought for longs / not oversold
                       for shorts), keeping us out of exhausted moves.

Stops are VOLATILITY-SCALED via ATR rather than fixed pips, so risk is
consistent whether the market is calm or wild. The strategy reports an ATR
value with each signal; risk.py turns that into stop distance, sizing, and
take-profit.

Decisions are evaluated on the last CLOSED bar (never the forming candle).

To build your own strategy, keep `add_indicators` + `evaluate` returning a
`Signal`, and the rest of the system works unchanged.
"""
from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

import indicators as ind

BUY = "BUY"
SELL = "SELL"
HOLD = "HOLD"


@dataclass
class StrategyParams:
    fast_ema: int = 20
    slow_ema: int = 50
    trend_ema: int = 200      # long-term trend filter
    rsi_period: int = 14
    rsi_long_max: float = 70.0   # don't buy if RSI already this high
    rsi_short_min: float = 30.0  # don't sell if RSI already this low
    atr_period: int = 14


@dataclass
class Signal:
    action: str          # BUY / SELL / HOLD
    price: float         # close price at the decision bar
    atr: float           # ATR at the decision bar (price units)
    reason: str          # human-readable explanation for logs
    # Indicator snapshot, handy for logging/debugging:
    fast: float = 0.0
    slow: float = 0.0
    trend: float = 0.0
    rsi: float = 0.0


def add_indicators(df: pd.DataFrame, p: StrategyParams) -> pd.DataFrame:
    """
    Attach all indicator columns the strategy needs. Returns a copy so the
    caller's DataFrame is never mutated.
    """
    out = df.copy()
    out["fast_ema"] = ind.ema(out["close"], p.fast_ema)
    out["slow_ema"] = ind.ema(out["close"], p.slow_ema)
    out["trend_ema"] = ind.ema(out["close"], p.trend_ema)
    out["rsi"] = ind.rsi(out["close"], p.rsi_period)
    out["atr"] = ind.atr(out["high"], out["low"], out["close"], p.atr_period)
    return out


def evaluate(df: pd.DataFrame, i: int, p: StrategyParams) -> Signal:
    """
    Produce a Signal for bar index `i`, comparing it to bar `i-1` to detect a
    genuine crossover. `df` must already have indicator columns (add_indicators).
    """
    # Need a previous bar and enough warmup for the longest EMA.
    if i < 1 or i < p.trend_ema:
        return Signal(HOLD, 0.0, 0.0, "warmup")

    row = df.iloc[i]
    prev = df.iloc[i - 1]

    # Any NaN indicator => not enough data yet.
    needed = ["fast_ema", "slow_ema", "trend_ema", "rsi", "atr", "close"]
    if row[needed].isna().any() or prev[["fast_ema", "slow_ema"]].isna().any():
        return Signal(HOLD, float(row["close"]), 0.0, "indicators not ready")

    price = float(row["close"])
    atr_val = float(row["atr"])
    fast, slow = float(row["fast_ema"]), float(row["slow_ema"])
    trend, rsi_val = float(row["trend_ema"]), float(row["rsi"])

    crossed_up = prev["fast_ema"] <= prev["slow_ema"] and fast > slow
    crossed_down = prev["fast_ema"] >= prev["slow_ema"] and fast < slow

    uptrend = price > trend
    downtrend = price < trend

    snapshot = dict(fast=fast, slow=slow, trend=trend, rsi=rsi_val)

    if crossed_up and uptrend and rsi_val < p.rsi_long_max:
        return Signal(BUY, price, atr_val,
                      "cross up + uptrend + RSI ok", **snapshot)
    if crossed_down and downtrend and rsi_val > p.rsi_short_min:
        return Signal(SELL, price, atr_val,
                      "cross down + downtrend + RSI ok", **snapshot)

    # Explain why we're holding (useful in live logs).
    if crossed_up and not uptrend:
        reason = "cross up but below trend EMA - skip"
    elif crossed_down and not downtrend:
        reason = "cross down but above trend EMA - skip"
    elif crossed_up and rsi_val >= p.rsi_long_max:
        reason = "cross up but RSI overbought - skip"
    elif crossed_down and rsi_val <= p.rsi_short_min:
        reason = "cross down but RSI oversold - skip"
    else:
        reason = "no crossover"
    return Signal(HOLD, price, atr_val, reason, **snapshot)


def latest_signal(df: pd.DataFrame, p: StrategyParams) -> Signal:
    """Convenience for the live bot: indicators + signal for the last bar."""
    annotated = add_indicators(df, p)
    return evaluate(annotated, len(annotated) - 1, p)
