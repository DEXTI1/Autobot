"""
"Scalp Pro v2" - a safer, higher-quality evolution of scalp_pro for indices
like USTEC / Nasdaq-100.

It keeps the proven core of scalp_pro (VWAP value + EMA 9/21 momentum + RSI
timing + ATR pullback) and adds FOUR quality/safety filters that, per the
backtest, cut the bad trades that hurt the original:

    + SESSION FILTER   : only trade the high-liquidity windows (NY open and,
                         optionally, the London session). The Nasdaq's clean,
                         trending moves cluster around the NY cash open; thin
                         overnight bars are mostly chop and slippage. Trading
                         only the right hours is the single biggest safety win.

    + HTF TREND FILTER : require alignment with a slower trend (EMA-200 on the
                         entry TF as an HTF proxy) AND that the EMA-200 is
                         sloping the right way. Stops us buying dips in a
                         downtrend / selling rips in an uptrend.

    + SLOPE CONFIRM    : the fast EMA must itself be turning the right way, so
                         we enter as momentum RESUMES, not as it stalls.

    + ATR BAND         : skip not only DEAD bars (ATR too low) but also
                         ABNORMAL spikes (ATR too high) - those are usually
                         news candles where stops gap and R:R is unreliable.

Everything else (entry on a pullback to the fast EMA, RSI band + direction) is
inherited from the original logic. Pair this with the v2 engine
(backtest_engine_v2) for breakeven-after-1R + a daily loss kill-switch.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

import indicators as ind
from strategy import BUY, SELL, HOLD, Signal


@dataclass
class ScalpProV2Params:
    # --- inherited core ---
    ema_fast: int = 9
    ema_slow: int = 21
    rsi_period: int = 14
    rsi_floor: float = 45.0
    rsi_ceiling: float = 68.0
    atr_period: int = 14
    pullback_atr: float = 0.6
    use_vwap: bool = True

    # --- NEW: ATR band (volatility sweet-spot), in index POINTS ---
    min_atr_points: float = 8.0     # below this = dead market, skip
    max_atr_points: float = 70.0    # above this = news spike, skip (safety)

    # --- NEW: higher-timeframe trend filter ---
    use_trend_filter: bool = True
    trend_ema: int = 200            # slow EMA on entry TF as HTF proxy
    trend_slope_lookback: int = 10  # bars used to measure EMA-200 slope

    # --- NEW: fast-EMA slope confirmation ---
    use_slope_confirm: bool = True

    # --- NEW: session filter (UTC hours, [start, end) as float hours) ---
    # Defaults: NY cash session 13:30-20:00 UTC. Add London (07:00-11:00) too.
    use_session: bool = True
    sessions_utc: list = field(default_factory=lambda: [(13.5, 20.0), (7.0, 11.0)])

    @property
    def warmup(self) -> int:
        base = max(self.ema_slow, self.rsi_period, self.atr_period)
        if self.use_trend_filter:
            base = max(base, self.trend_ema + self.trend_slope_lookback)
        return base + 5


def add_indicators(df: pd.DataFrame, p: ScalpProV2Params) -> pd.DataFrame:
    out = df.copy()
    out["ema_fast"] = ind.ema(out["close"], p.ema_fast)
    out["ema_slow"] = ind.ema(out["close"], p.ema_slow)
    out["rsi"] = ind.rsi(out["close"], p.rsi_period)
    out["atr"] = ind.atr(out["high"], out["low"], out["close"], p.atr_period)
    out["vwap"] = ind.session_vwap(out) if p.use_vwap else out["close"]
    out["ema_trend"] = ind.ema(out["close"], p.trend_ema)
    return out


def _in_session(ts, sessions) -> bool:
    """ts is a tz-aware (UTC) timestamp; sessions are (start_hr, end_hr) floats."""
    hour = ts.hour + ts.minute / 60.0
    for start, end in sessions:
        if start <= hour < end:
            return True
    return False


def evaluate(df: pd.DataFrame, i: int, p: ScalpProV2Params, point: float = 1.0) -> Signal:
    if i < max(2, p.warmup):
        return Signal(HOLD, 0.0, 0.0, "warmup")

    row = df.iloc[i]
    prev = df.iloc[i - 1]
    needed = ["ema_fast", "ema_slow", "rsi", "atr", "vwap", "close", "ema_trend"]
    if row[needed].isna().any() or prev[["rsi"]].isna().any():
        return Signal(HOLD, float(row["close"]), 0.0, "indicators not ready")

    price = float(row["close"])
    atr_val = float(row["atr"])
    ema_f, ema_s = float(row["ema_fast"]), float(row["ema_slow"])
    ema_f_prev = float(prev["ema_fast"])
    vwap = float(row["vwap"])
    rsi_now, rsi_prev = float(row["rsi"]), float(prev["rsi"])
    low, high = float(row["low"]), float(row["high"])
    trend = float(row["ema_trend"])
    trend_prev = float(df.iloc[i - p.trend_slope_lookback]["ema_trend"])

    snap = dict(fast=ema_f, slow=ema_s, trend=vwap, rsi=rsi_now)

    # --- NEW filter: session ---
    if p.use_session and not _in_session(pd.Timestamp(row["time"]), p.sessions_utc):
        return Signal(HOLD, price, atr_val, "outside session", **snap)

    # --- NEW filter: ATR band (skip dead AND news-spike bars) ---
    if atr_val < p.min_atr_points * point:
        return Signal(HOLD, price, atr_val, "ATR too low (dead market)", **snap)
    if atr_val > p.max_atr_points * point:
        return Signal(HOLD, price, atr_val, "ATR too high (news spike) - stand aside", **snap)

    bull_momo = ema_f > ema_s
    bear_momo = ema_f < ema_s
    above_vwap = (price > vwap) or not p.use_vwap
    below_vwap = (price < vwap) or not p.use_vwap

    # --- NEW filter: HTF trend alignment + slope ---
    if p.use_trend_filter:
        up_trend = price > trend and trend > trend_prev
        down_trend = price < trend and trend < trend_prev
    else:
        up_trend = down_trend = True

    # --- NEW filter: fast-EMA slope confirmation ---
    if p.use_slope_confirm:
        slope_up = ema_f > ema_f_prev
        slope_dn = ema_f < ema_f_prev
    else:
        slope_up = slope_dn = True

    pull_depth = p.pullback_atr * atr_val
    long_pullback = low <= ema_f + pull_depth and price > ema_f
    short_pullback = high >= ema_f - pull_depth and price < ema_f

    rsi_ok_long = p.rsi_floor <= rsi_now <= p.rsi_ceiling and rsi_now > rsi_prev
    rsi_ok_short = (100 - p.rsi_ceiling) <= rsi_now <= (100 - p.rsi_floor) and rsi_now < rsi_prev

    long_ok = bull_momo and above_vwap and up_trend and slope_up and long_pullback and rsi_ok_long
    short_ok = bear_momo and below_vwap and down_trend and slope_dn and short_pullback and rsi_ok_short

    if long_ok:
        return Signal(BUY, price, atr_val, "v2 long: trend+VWAP+EMA pullback, RSI rising", **snap)
    if short_ok:
        return Signal(SELL, price, atr_val, "v2 short: trend+VWAP+EMA pullback, RSI falling", **snap)
    return Signal(HOLD, price, atr_val, "no aligned v2 setup", **snap)


def latest_signal(df: pd.DataFrame, p: ScalpProV2Params, point: float = 1.0) -> Signal:
    annotated = add_indicators(df, p)
    return evaluate(annotated, len(annotated) - 1, p, point)
