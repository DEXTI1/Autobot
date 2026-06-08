"""
Mean-reversion scalping strategy (Bollinger Bands + RSI) - tunable sensitivity.

It bets that stretched moves snap back toward the average:
    - Price near/below the LOWER band (and/or RSI low)  -> BUY  (expect bounce up)
    - Price near/above the UPPER band (and/or RSI high) -> SELL (expect drop)

WHY IT MIGHT TRADE RARELY: requiring BOTH "price beyond the band" AND "RSI at an
extreme" at the same instant is uncommon. The settings below let you loosen it:

    band_touch_frac : how close to the band counts as a trigger.
                      1.0 = must reach the band exactly (strict, few trades)
                      0.8 = trigger at 80% of the way to the band (more trades)
    require_both    : True  = need band AND RSI (strict)
                      False = band OR RSI is enough (many more trades)
    rsi_oversold / rsi_overbought : loosen these (e.g. 40/60) for more signals.

Exits use the bot's ATR stop-loss / take-profit / trailing stop, so the rest of
the system (risk.py, executor.py, bot.py) works unchanged.

NOTE: more trades != more profit. Faster, looser scalping takes MORE hits from
spread + commission. Test on DEMO and backtest with realistic costs.
"""
from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

import indicators as ind
from strategy import BUY, SELL, HOLD, Signal  # reuse the shared Signal/constants


@dataclass
class ScalpParams:
    bb_period: int = 20          # Bollinger Band lookback (the moving average)
    bb_std: float = 2.0          # how many standard deviations for the bands
    rsi_period: int = 14
    rsi_oversold: float = 35.0   # BUY side RSI threshold (raise -> more trades)
    rsi_overbought: float = 65.0 # SELL side RSI threshold (lower -> more trades)
    atr_period: int = 14
    # --- sensitivity knobs (the "make it trade more" controls) ---
    band_touch_frac: float = 0.85  # 0..1: fraction of the way to the band that
                                   # counts as a touch. <1.0 = more trades.
    require_both: bool = False     # False = band OR RSI triggers (more trades);
                                   # True  = need BOTH (strict, fewer trades).

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

    # snapshot for logging: fast=upper, slow=lower, trend=mid, rsi=rsi
    snap = dict(fast=upper, slow=lower, trend=mid, rsi=rsi_val)

    # "Touch" levels sit partway between the middle band and the outer band.
    # band_touch_frac=1.0 -> the outer band; 0.85 -> 85% of the way out.
    lower_touch = mid - (mid - lower) * p.band_touch_frac
    upper_touch = mid + (upper - mid) * p.band_touch_frac

    near_lower = price <= lower_touch
    near_upper = price >= upper_touch
    rsi_low = rsi_val <= p.rsi_oversold
    rsi_high = rsi_val >= p.rsi_overbought

    if p.require_both:
        buy = near_lower and rsi_low
        sell = near_upper and rsi_high
    else:
        buy = near_lower or rsi_low
        sell = near_upper or rsi_high

    # If both somehow trigger, prefer the stronger stretch (distance from mid).
    if buy and sell:
        buy = (mid - price) >= (price - mid)

    if buy:
        return Signal(BUY, price, atr_val, "stretched low -> mean-revert up", **snap)
    if sell:
        return Signal(SELL, price, atr_val, "stretched high -> mean-revert down", **snap)

    return Signal(HOLD, price, atr_val, "inside bands / RSI neutral", **snap)


def latest_signal(df: pd.DataFrame, p: ScalpParams) -> Signal:
    """Convenience for the live bot: indicators + signal for the last bar."""
    annotated = add_indicators(df, p)
    return evaluate(annotated, len(annotated) - 1, p)
