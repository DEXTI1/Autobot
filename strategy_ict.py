"""
ICT-style multi-timeframe strategy: NY-open continuation -> 1m IFVG reversal.

This implements the plan you described:

    1. NEW YORK OPEN: only hunt for setups during the NY killzone (configurable,
       default 13:30-16:00 UTC = 09:30-12:00 New York time).
    2. HIGHER-TF CONTEXT (15m): a Fair Value Gap (FVG) on the 15m got "hit"
       (price traded back into it)  -> bias = continuation in the FVG direction.
    3. LIQUIDITY (5m): OR price swept a recent 5m swing high/low (took the liquidity
       then rejected) -> bias = reversal away from the swept side.
    4. ENTRY (1m): on the chosen bias, wait for an INVERSE FVG (IFVG) - an FVG
       that gets traded through and then flips to act as support/resistance.
       Enter in the bias direction when the 1m IFVG confirms.
    5. RISK: stop just beyond the swing that defined the move; take-profit at
       1:1 (configurable) reward:risk.

KEY ICT TERMS (so the code is readable):
    - FVG (Fair Value Gap / imbalance): a 3-candle pattern with a price gap.
        Bullish FVG: candle[0].high < candle[2].low  (gap below, support)
        Bearish FVG: candle[0].low  > candle[2].high (gap above, resistance)
    - Swing high/low: a local high/low with `swing_lookback` lower highs / higher
        lows on each side.
    - Liquidity sweep: price pokes BEYOND a prior swing (grabs stops) then closes
        back, signalling a likely reversal.
    - IFVG (Inverse FVG): an FVG that gets fully traded through; the broken gap
        then acts in the OPPOSITE direction - a classic ICT reversal entry.

NOTE: this is multi-timeframe and reads 15m/5m/1m live from MT5. It is selective
by design (it should mostly do nothing outside the NY open). ICT concepts are
discretionary in origin; this is a mechanical interpretation, NOT a guarantee of
profit. Test on DEMO and review every entry.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

import pandas as pd

import indicators as ind
from strategy import BUY, SELL, HOLD, Signal


@dataclass
class ICTParams:
    # session (UTC hours) - NY open killzone
    ny_start_hour: int = 13
    ny_start_min: int = 30
    ny_end_hour: int = 16
    ny_end_min: int = 0
    # structure detection
    swing_lookback: int = 3        # bars each side to qualify a swing point
    fvg_min_size_atr: float = 0.10  # min FVG size as fraction of 1m ATR (noise filter)
    sweep_lookback_5m: int = 20    # how many 5m bars back to find swings to sweep
    fvg_lookback_15m: int = 20     # how many 15m bars back to find an FVG
    ifvg_lookback_1m: int = 30     # how many 1m bars back to find the IFVG
    atr_period: int = 14
    reward_risk: float = 1.0       # 1:1 RR as requested
    stop_buffer_atr: float = 0.25  # extra buffer beyond the swing for the stop


@dataclass
class ICTSignal:
    action: str
    price: float
    sl_price: float          # absolute stop price (under/over the swing)
    tp_price: float          # absolute take-profit price (1:1 by default)
    reason: str
    context: dict = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Structure primitives (pure functions on OHLC DataFrames)
# ---------------------------------------------------------------------------
def find_swings(df: pd.DataFrame, lookback: int) -> tuple[list, list]:
    """
    Return (swing_highs, swing_lows) as lists of (index, price).
    A swing high has `lookback` strictly-lower highs on both sides; mirror for lows.

    Vectorized with numpy so it stays fast when called many times in a backtest.
    """
    import numpy as np
    h = df["high"].to_numpy()
    l = df["low"].to_numpy()
    n = len(h)
    if n < 2 * lookback + 1:
        return [], []
    # Build sliding windows of width (2*lookback+1) via stride tricks.
    w = 2 * lookback + 1
    sh = np.lib.stride_tricks.sliding_window_view(h, w)
    sl = np.lib.stride_tricks.sliding_window_view(l, w)
    centre = lookback
    # a swing high: centre is the unique max of its window
    is_high = (sh[:, centre] == sh.max(axis=1)) & ((sh == sh[:, centre:centre + 1]).sum(axis=1) == 1)
    is_low = (sl[:, centre] == sl.min(axis=1)) & ((sl == sl[:, centre:centre + 1]).sum(axis=1) == 1)
    high_idx = np.nonzero(is_high)[0] + centre
    low_idx = np.nonzero(is_low)[0] + centre
    highs = [(int(i), float(h[i])) for i in high_idx]
    lows = [(int(i), float(l[i])) for i in low_idx]
    return highs, lows


def find_fvgs(df: pd.DataFrame, min_size: float) -> list:
    """
    Detect 3-candle Fair Value Gaps. Returns list of dicts:
        {i, dir, top, bottom}
    where dir = +1 (bullish gap = support) / -1 (bearish gap = resistance),
    top/bottom are the gap edges, i is the index of the 3rd candle.
    """
    out = []
    H, L = df["high"].values, df["low"].values
    for i in range(2, len(df)):
        # bullish FVG: candle[i-2].high < candle[i].low
        if H[i - 2] < L[i]:
            size = L[i] - H[i - 2]
            if size >= min_size:
                out.append({"i": i, "dir": 1, "bottom": float(H[i - 2]), "top": float(L[i])})
        # bearish FVG: candle[i-2].low > candle[i].high
        elif L[i - 2] > H[i]:
            size = L[i - 2] - H[i]
            if size >= min_size:
                out.append({"i": i, "dir": -1, "bottom": float(H[i]), "top": float(L[i - 2])})
    return out


def fvg_was_hit(df: pd.DataFrame, fvg: dict) -> bool:
    """True if, AFTER it formed, price traded back into the FVG zone."""
    after = df.iloc[fvg["i"] + 1:]
    if after.empty:
        return False
    return bool(((after["low"] <= fvg["top"]) & (after["high"] >= fvg["bottom"])).any())


def detect_sweep(df: pd.DataFrame, lookback: int, swing_lookback: int) -> dict | None:
    """
    Detect a liquidity sweep on the most recent CLOSED bar:
    price pokes beyond a prior swing then closes back inside.
    Returns {dir, swing_price} or None. dir=+1 means bullish reversal (low swept),
    dir=-1 means bearish reversal (high swept).
    """
    if len(df) < lookback + swing_lookback + 2:
        return None
    recent = df.iloc[-(lookback + swing_lookback + 2):].reset_index(drop=True)
    highs, lows = find_swings(recent, swing_lookback)
    last = recent.iloc[-1]

    # swept a swing LOW (grabbed sell-side liquidity) then closed back above it -> bullish
    for idx, price in reversed(lows):
        if idx < len(recent) - 1 and last["low"] < price <= last["close"]:
            return {"dir": 1, "swing_price": price}
    # swept a swing HIGH (grabbed buy-side liquidity) then closed back below -> bearish
    for idx, price in reversed(highs):
        if idx < len(recent) - 1 and last["high"] > price >= last["close"]:
            return {"dir": -1, "swing_price": price}
    return None


def detect_ifvg(df: pd.DataFrame, bias_dir: int, min_size: float) -> dict | None:
    """
    Find an Inverse FVG confirming `bias_dir` (+1 long / -1 short) on recent bars.

    An IFVG: an opposite-direction FVG that price has CLOSED THROUGH, so the broken
    gap now acts as support (for longs) / resistance (for shorts). We confirm when
    the latest close is on the correct side of that broken gap.
    Returns {entry, gap_top, gap_bottom} or None.
    """
    fvgs = find_fvgs(df, min_size)
    last_close = float(df["close"].iloc[-1])
    for fvg in reversed(fvgs):
        # For a LONG bias we want a BEARISH fvg (dir=-1) that got broken to the upside.
        if bias_dir == 1 and fvg["dir"] == -1:
            broke_up = (df["close"].iloc[fvg["i"] + 1:] > fvg["top"]).any()
            if broke_up and last_close > fvg["top"]:
                return {"entry": last_close, "gap_top": fvg["top"], "gap_bottom": fvg["bottom"]}
        # For a SHORT bias we want a BULLISH fvg (dir=+1) broken to the downside.
        if bias_dir == -1 and fvg["dir"] == 1:
            broke_dn = (df["close"].iloc[fvg["i"] + 1:] < fvg["bottom"]).any()
            if broke_dn and last_close < fvg["bottom"]:
                return {"entry": last_close, "gap_top": fvg["top"], "gap_bottom": fvg["bottom"]}
    return None


# ---------------------------------------------------------------------------
# Session check
# ---------------------------------------------------------------------------
def in_ny_killzone(p: ICTParams, now: datetime | None = None) -> bool:
    now = now or datetime.now(timezone.utc)
    start = now.replace(hour=p.ny_start_hour, minute=p.ny_start_min, second=0, microsecond=0)
    end = now.replace(hour=p.ny_end_hour, minute=p.ny_end_min, second=0, microsecond=0)
    return start <= now <= end


# ---------------------------------------------------------------------------
# Main multi-timeframe evaluation (live)
# ---------------------------------------------------------------------------
def evaluate_mtf(
    df15: pd.DataFrame,
    df5: pd.DataFrame,
    df1: pd.DataFrame,
    p: ICTParams,
    now: datetime | None = None,
) -> ICTSignal:
    """
    Combine the three timeframes into one decision. DataFrames must have
    columns: time, open, high, low, close (most-recent row last, CLOSED bars).
    """
    if not in_ny_killzone(p, now):
        return ICTSignal(HOLD, 0.0, 0.0, 0.0, "outside NY killzone")

    if len(df1) < p.ifvg_lookback_1m + 5 or len(df5) < p.sweep_lookback_5m + 5 \
            or len(df15) < p.fvg_lookback_15m + 5:
        return ICTSignal(HOLD, 0.0, 0.0, 0.0, "not enough data")

    atr1 = ind.atr_last(df1["high"], df1["low"], df1["close"], p.atr_period)
    if pd.isna(atr1) or atr1 <= 0:
        return ICTSignal(HOLD, 0.0, 0.0, 0.0, "atr not ready")
    min_fvg = p.fvg_min_size_atr * atr1

    # --- determine bias from 15m FVG continuation OR 5m sweep ---
    bias = 0
    bias_reason = ""

    # 15m: most recent FVG that has been hit -> continuation in its direction
    recent15 = df15.iloc[-(p.fvg_lookback_15m + 3):].reset_index(drop=True)
    fvgs15 = find_fvgs(recent15, min_fvg)
    for fvg in reversed(fvgs15):
        if fvg_was_hit(recent15, fvg):
            bias = fvg["dir"]
            bias_reason = f"15m {'bullish' if bias == 1 else 'bearish'} FVG hit (continuation)"
            break

    # 5m: a liquidity sweep overrides/sets a reversal bias if no clean 15m FVG
    sweep = detect_sweep(df5, p.sweep_lookback_5m, p.swing_lookback)
    if bias == 0 and sweep is not None:
        bias = sweep["dir"]
        bias_reason = f"5m swept {'low' if bias == 1 else 'high'} (reversal)"

    if bias == 0:
        return ICTSignal(HOLD, 0.0, 0.0, 0.0, "no 15m FVG hit / no 5m sweep")

    # --- 1m: confirm with an Inverse FVG in the bias direction ---
    recent1 = df1.iloc[-(p.ifvg_lookback_1m + 3):].reset_index(drop=True)
    ifvg = detect_ifvg(recent1, bias, min_fvg)
    if ifvg is None:
        return ICTSignal(HOLD, 0.0, 0.0, 0.0, f"{bias_reason}; waiting for 1m IFVG")

    # --- build the trade: stop beyond the defining 1m swing, TP at RR ---
    highs1, lows1 = find_swings(recent1, p.swing_lookback)
    entry = float(recent1["close"].iloc[-1])
    buf = p.stop_buffer_atr * atr1

    if bias == 1:  # long
        swing_low = min((pr for _, pr in lows1), default=entry - atr1)
        sl = swing_low - buf
        risk = entry - sl
        if risk <= 0:
            return ICTSignal(HOLD, entry, 0.0, 0.0, "invalid risk (long)")
        tp = entry + risk * p.reward_risk
        return ICTSignal(BUY, entry, sl, tp,
                         f"{bias_reason} + 1m IFVG long",
                         context={"swing_low": swing_low, "atr": atr1})
    else:  # short
        swing_high = max((pr for _, pr in highs1), default=entry + atr1)
        sl = swing_high + buf
        risk = sl - entry
        if risk <= 0:
            return ICTSignal(HOLD, entry, 0.0, 0.0, "invalid risk (short)")
        tp = entry - risk * p.reward_risk
        return ICTSignal(SELL, entry, sl, tp,
                         f"{bias_reason} + 1m IFVG short",
                         context={"swing_high": swing_high, "atr": atr1})


def to_signal(ict: ICTSignal) -> Signal:
    """Adapt an ICTSignal to the shared Signal type for logging."""
    return Signal(ict.action, ict.price, 0.0, ict.reason,
                  fast=ict.sl_price, slow=ict.tp_price, trend=0.0, rsi=0.0)
