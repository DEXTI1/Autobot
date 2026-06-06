"""
Mean-reversion scalping strategy (Bollinger Bands + RSI).

Designed for fast timeframes (M1/M5). Unlike the trend strategy, this one bets
that short, sharp moves to an extreme tend to SNAP BACK toward the average:

    - Price closes BELOW the lower Bollinger Band AND RSI is oversold  -> BUY
      (expecting a bounce back up to the middle band)
    - Price closes ABOVE the upper Bollinger Band AND RSI is overbought -> SELL
      (expecting a drop back down)

Exits are handled by the bot's ATR-based stop-loss / take-profit and the
trailing stop, exactly like the trend strategy - so the rest of the system
(risk.py, executor.py, bot.py) works unchanged.

Same interface as strategy.py: a Params dataclass, add_indicators(), evaluate(),
and latest_signal(). This is what makes the two strategies swappable.

NOTE: Scalping is genuinely hard - spread + commission eat tiny profits. Test on
DEMO and backtest with realistic costs before ever using real money.
"""
from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

import indicators as ind
from strategy import BUY, SELL, HOLD, Signal  # reuse the shared Signal/constants


@dataclass
class ScalpParams:
    bb_period: int = 20        # Bollinger Band lookback (the moving average)
    bb_std: float = 2.0        # how many standard deviations for the bands
    rsi_period: int = 14
    rsi_oversold: float = 30.0   # BUY only when RSI is below this
    rsi_overbought: float = 70.0 # SELL only when RSI is above this
    atr_period: int = 14
    # Alias so generic code that expects a warmup length still works:
    @property
    def warmup(self) -> int:
        return max(self.bb_period, self.rsi_period, self.atr_period) + 5


def add_indicators(df: pd.DataFrame, p: ScalpParams) -> pd.DataFrame:
    """Attach Bollinger Bands, RSI and ATR. Returns a copy."""
    out = df.copy()
    mid = ind.sma(out["close"], p.bb_period)
    std = out["close"].rolling(p.bb_period).std(ddof=0)
    out["bb_mid"] = mid
    out["bb_upper"] = mid + p.bb_std * std
    out["bb_lower"] = mid - p.bb_std * std
    out["rsi"] = ind.rsi(out["close"], p.rsi_period)
    out["atr"] = ind.atr(out["high"], out["low"], out["close"], p.atr_period)
    return out


def evaluate(df: pd.DataFrame, i: int, p: ScalpParams) -> Signal:
    """Produce a Signal for bar index `i` (must already have indicators)."""
    if i < 1 or i < p.warmup:
        return Signal(HOLD, 0.0, 0.0, "warmup")

    row = df.iloc[i]
    needed = ["bb_mid", "bb_upper", "bb_lower", "rsi", "atr", "close"]
    if row[needed].isna().any():
        return Signal(HOLD, float(row["close"]), 0.0, "indicators not ready")

    price = float(row["close"])
    atr_val = float(row["atr"])
    upper, lower, mid = float(row["bb_upper"]), float(row["bb_lower"]), float(row["bb_mid"])
    rsi_val = float(row["rsi"])

    # Map to the shared Signal's snapshot fields for logging:
    #   fast=upper band, slow=lower band, trend=middle band, rsi=rsi
    snap = dict(fast=upper, slow=lower, trend=mid, rsi=rsi_val)

    below_lower = price < lower
    above_upper = price > upper

    if below_lower and rsi_val <= p.rsi_oversold:
        return Signal(BUY, price, atr_val, "below lower band + RSI oversold", **snap)
    if above_upper and rsi_val >= p.rsi_overbought:
        return Signal(SELL, price, atr_val, "above upper band + RSI overbought", **snap)

    if below_lower:
        reason = "below lower band but RSI not oversold - skip"
    elif above_upper:
        reason = "above upper band but RSI not overbought - skip"
    else:
        reason = "inside bands"
    return Signal(HOLD, price, atr_val, reason, **snap)


def latest_signal(df: pd.DataFrame, p: ScalpParams) -> Signal:
    """Convenience for the live bot: indicators + signal for the last bar."""
    annotated = add_indicators(df, p)
    return evaluate(annotated, len(annotated) - 1, p)
