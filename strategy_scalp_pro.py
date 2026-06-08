"""
"Scalp Pro" - a momentum-from-value scalping strategy for M5 gold (XAUUSD).

This is the strategy professionals actually lean on for intraday gold, rather
than a weak two-indicator confluence. It combines THREE aligned, independent
read-outs (per widely-used scalping practice: VWAP for value/direction, a fast
EMA pair for momentum, RSI/ATR for timing and filtering):

    DIRECTION  (VWAP)   : only go long ABOVE the session VWAP, short BELOW it.
                          VWAP is the day's volume-weighted fair value; trading
                          on its correct side keeps us with intraday flow.
    MOMENTUM   (EMA 9/21): fast EMA above slow = bullish push (and vice-versa).
                          We enter on a PULLBACK in the direction of momentum,
                          not by chasing - better entries, better R:R.
    TIMING     (RSI)    : avoid buying when already overbought / selling when
                          oversold; require RSI to be turning UP (longs) /
                          DOWN (shorts) so we enter as momentum resumes.
    QUALITY    (ATR)    : require enough volatility to clear spread+target, and
                          skip dead, rangebound bars.

Entry (LONG; SHORT is the mirror):
    1. close > VWAP                      (with intraday flow)
    2. ema_fast > ema_slow               (bullish momentum)
    3. pullback: price dipped toward ema_fast recently, now closing back up
    4. RSI between rsi_floor and rsi_ceiling AND rising
    5. ATR >= min_atr_points*point       (tradable volatility)

Exits use the bot's risk manager. With fixed-pip mode (USE_FIXED_PIPS) you get
the 30-40 pip targets you asked for; otherwise ATR-scaled SL/TP applies.

Honest note: this raises the QUALITY of entries and the odds of a clean move;
it does NOT guarantee wins, and no setting makes 50%/day realistic. Backtest
with real costs before trusting it.
"""
from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

import indicators as ind
from strategy import BUY, SELL, HOLD, Signal


@dataclass
class ScalpProParams:
    ema_fast: int = 9
    ema_slow: int = 21
    rsi_period: int = 14
    rsi_floor: float = 45.0       # longs: RSI must be above this (momentum intact)
    rsi_ceiling: float = 68.0     # longs: but below this (not overbought)
    atr_period: int = 14
    min_atr_points: float = 80.0  # min volatility in POINTS to bother trading
    pullback_atr: float = 0.6     # how deep a pullback toward ema_fast counts
    use_vwap: bool = True

    @property
    def warmup(self) -> int:
        return max(self.ema_slow, self.rsi_period, self.atr_period) + 5


def add_indicators(df: pd.DataFrame, p: ScalpProParams) -> pd.DataFrame:
    out = df.copy()
    out["ema_fast"] = ind.ema(out["close"], p.ema_fast)
    out["ema_slow"] = ind.ema(out["close"], p.ema_slow)
    out["rsi"] = ind.rsi(out["close"], p.rsi_period)
    out["atr"] = ind.atr(out["high"], out["low"], out["close"], p.atr_period)
    out["vwap"] = ind.session_vwap(out) if p.use_vwap else out["close"]
    return out


def evaluate(df: pd.DataFrame, i: int, p: ScalpProParams, point: float = 0.01) -> Signal:
    if i < 2 or i < p.warmup:
        return Signal(HOLD, 0.0, 0.0, "warmup")

    row = df.iloc[i]
    prev = df.iloc[i - 1]
    needed = ["ema_fast", "ema_slow", "rsi", "atr", "vwap", "close"]
    if row[needed].isna().any() or prev[["rsi"]].isna().any():
        return Signal(HOLD, float(row["close"]), 0.0, "indicators not ready")

    price = float(row["close"])
    atr_val = float(row["atr"])
    ema_f, ema_s = float(row["ema_fast"]), float(row["ema_slow"])
    vwap = float(row["vwap"])
    rsi_now, rsi_prev = float(row["rsi"]), float(prev["rsi"])
    low, high = float(row["low"]), float(row["high"])

    # log snapshot: fast=ema_fast, slow=ema_slow, trend=vwap, rsi=rsi
    snap = dict(fast=ema_f, slow=ema_s, trend=vwap, rsi=rsi_now)

    # 5) volatility gate
    if atr_val < p.min_atr_points * point:
        return Signal(HOLD, price, atr_val, "ATR too low (dead market)", **snap)

    bull_momo = ema_f > ema_s
    bear_momo = ema_f < ema_s
    above_vwap = (price > vwap) or not p.use_vwap
    below_vwap = (price < vwap) or not p.use_vwap

    # 3) pullback: this bar's low dipped near/below ema_fast (longs), or high
    #    poked above ema_fast (shorts), i.e. a dip we can buy / pop we can sell.
    pull_depth = p.pullback_atr * atr_val
    long_pullback = low <= ema_f + pull_depth and price > ema_f
    short_pullback = high >= ema_f - pull_depth and price < ema_f

    # 4) RSI in the momentum band AND turning the right way
    rsi_ok_long = p.rsi_floor <= rsi_now <= p.rsi_ceiling and rsi_now > rsi_prev
    rsi_ok_short = (100 - p.rsi_ceiling) <= rsi_now <= (100 - p.rsi_floor) and rsi_now < rsi_prev

    long_ok = bull_momo and above_vwap and long_pullback and rsi_ok_long
    short_ok = bear_momo and below_vwap and short_pullback and rsi_ok_short

    if long_ok:
        return Signal(BUY, price, atr_val, "long: VWAP+EMA momentum pullback + RSI rising", **snap)
    if short_ok:
        return Signal(SELL, price, atr_val, "short: VWAP+EMA momentum pullback + RSI falling", **snap)

    # explain the hold (handy in live logs)
    if bull_momo and above_vwap and not long_pullback:
        reason = "bull setup, waiting for pullback"
    elif bear_momo and below_vwap and not short_pullback:
        reason = "bear setup, waiting for pullback"
    else:
        reason = "no aligned setup"
    return Signal(HOLD, price, atr_val, reason, **snap)


def latest_signal(df: pd.DataFrame, p: ScalpProParams, point: float = 0.01) -> Signal:
    annotated = add_indicators(df, p)
    return evaluate(annotated, len(annotated) - 1, p, point)
