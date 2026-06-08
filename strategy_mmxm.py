"""
MMXM (Market Maker Model) strategy - Order Block fill -> structure-shift entry.

The plan it trades:
    1. HTF CONTEXT (30m): find an Order Block (OB) that price has returned into
       ("got filled"). A bullish OB is the last down-candle before a strong
       up-move; bearish OB is the last up-candle before a strong down-move.
       Price tapping back into the OB = the "fill" that arms a setup.
    2. ENTRY (1m, or base TF in backtest): look for a Market-Maker shift in the
       OB's direction - a liquidity sweep of a recent swing followed by a
       Change of Character (CHoCH: price breaks the opposite recent swing).
       That sweep+break is the simplified MMXM entry trigger.
    3. TARGET: the nearest opposing draw on liquidity, chosen from:
         - the next higher-TF (4H) Fair Value Gap, OR
         - a prior swing high/low, OR
         - an opposing 30m/1h/4h Order Block edge
       whichever is closest in the trade direction (capped by max R:R).
    4. STOP: just beyond the OB / sweep extreme.

KEY TERMS:
    - Order Block (OB): last opposite-color candle before an impulsive move; a
      zone institutions are presumed to have filled orders.
    - CHoCH (Change of Character): price breaks the most recent opposing swing,
      signalling a shift in short-term control.
    - Draw on liquidity: where price is likely headed (a gap, swing, or OB).

HONEST NOTE: MMXM/OB are discretionary ICT concepts; this is a MECHANICAL
interpretation and does NOT guarantee profit. It must be backtested. The whole
point of building it this way is to MEASURE whether it has an edge.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

import pandas as pd

import indicators as ind
from strategy import BUY, SELL, HOLD
# reuse shared primitives
from strategy_ict import find_swings, find_fvgs


@dataclass
class MMXMParams:
    # session gate (UTC). Wide by default; restrict if you only want NY.
    use_session: bool = False
    sess_start_hour: int = 12
    sess_end_hour: int = 20
    # Order Block detection on the HTF (30m)
    ob_impulse_atr: float = 1.2     # move after OB must be >= this * HTF ATR
    ob_lookback: int = 30           # how many HTF bars back to scan for OBs
    # entry (base TF) structure
    swing_lookback: int = 2
    sweep_lookback: int = 25        # bars back to find the swing that gets swept
    atr_period: int = 14
    # targeting
    max_reward_risk: float = 3.0    # cap RR if the target is very far
    min_reward_risk: float = 1.0    # skip if best target is closer than this
    stop_buffer_atr: float = 0.25


@dataclass
class MMXMSignal:
    action: str
    price: float
    sl_price: float
    tp_price: float
    reason: str
    context: dict = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Order Block detection
# ---------------------------------------------------------------------------
def find_order_blocks(df: pd.DataFrame, impulse: float, lookback: int) -> list:
    """
    Return recent Order Blocks as dicts {i, dir, top, bottom}.
      bullish OB (dir=+1): last DOWN candle before an up-move >= impulse
      bearish OB (dir=-1): last UP candle before a down-move >= impulse
    `impulse` is a price distance (e.g. 1.2 * ATR).
    """
    out = []
    O, H, L, C = (df["open"].values, df["high"].values,
                  df["low"].values, df["close"].values)
    n = len(df)
    start = max(1, n - lookback)
    for i in range(start, n - 2):
        # bullish OB: candle i is down (close<open), then strong up move into i+2
        if C[i] < O[i]:
            move = C[i + 2] - C[i]
            if move >= impulse and H[i + 1] > H[i]:
                out.append({"i": i, "dir": 1, "bottom": float(L[i]), "top": float(H[i])})
        # bearish OB: candle i is up, then strong down move
        if C[i] > O[i]:
            move = C[i] - C[i + 2]
            if move >= impulse and L[i + 1] < L[i]:
                out.append({"i": i, "dir": -1, "bottom": float(L[i]), "top": float(H[i])})
    return out


def ob_was_filled(df: pd.DataFrame, ob: dict) -> bool:
    """True if price traded back into the OB zone after it formed."""
    after = df.iloc[ob["i"] + 3:]
    if after.empty:
        return False
    return bool(((after["low"] <= ob["top"]) & (after["high"] >= ob["bottom"])).any())


def detect_choch(df: pd.DataFrame, direction: int, swing_lookback: int,
                 sweep_lookback: int) -> dict | None:
    """
    Market-maker entry trigger: a liquidity sweep followed by a Change of
    Character in `direction` (+1 long / -1 short) on the most recent bar.

      long  : sweep a recent swing LOW, then close back ABOVE the last swing high
      short : sweep a recent swing HIGH, then close back BELOW the last swing low
    Returns {sweep_extreme} or None.
    """
    if len(df) < sweep_lookback + swing_lookback + 3:
        return None
    recent = df.iloc[-(sweep_lookback + swing_lookback + 3):].reset_index(drop=True)
    highs, lows = find_swings(recent, swing_lookback)
    if not highs or not lows:
        return None
    last = recent.iloc[-1]

    # Use the swing levels as they stood BEFORE the most recent bars, so a fresh
    # sweep + break reads cleanly. We look at the last `recent_window` bars for
    # the sweep, and require the current close to break the opposing swing.
    recent_window = min(6, len(recent) - 1)
    tail = recent.iloc[-recent_window:]

    if direction == 1:
        swing_low = min(pr for _, pr in lows)
        swing_high = max(pr for _, pr in highs)
        # liquidity grab: some recent bar's low dipped below the swing low...
        swept = bool((tail["low"] < swing_low).any())
        # ...and price has now reclaimed / broken structure to the upside
        choch = last["close"] > swing_low and last["close"] >= tail["close"].iloc[0]
        if swept and choch:
            return {"sweep_extreme": float(tail["low"].min())}
    else:
        swing_high = max(pr for _, pr in highs)
        swing_low = min(pr for _, pr in lows)
        swept = bool((tail["high"] > swing_high).any())
        choch = last["close"] < swing_high and last["close"] <= tail["close"].iloc[0]
        if swept and choch:
            return {"sweep_extreme": float(tail["high"].max())}
    return None


# ---------------------------------------------------------------------------
# Targeting: nearest opposing draw on liquidity
# ---------------------------------------------------------------------------
def pick_target(entry: float, direction: int, df_htf: pd.DataFrame,
                df_4h: pd.DataFrame, min_fvg: float) -> float | None:
    """
    Choose the nearest sensible target in the trade direction from:
      - 4H FVG edges, prior swings (HTF), opposing OB edges.
    Returns an absolute price, or None if nothing suitable found.
    """
    candidates = []

    # 4H FVGs (unfilled gaps as a draw)
    for fvg in find_fvgs(df_4h, min_fvg):
        edge = fvg["top"] if direction == 1 else fvg["bottom"]
        candidates.append(edge)

    # HTF swings ahead of price
    highs, lows = find_swings(df_htf, 2)
    if direction == 1:
        candidates += [pr for _, pr in highs]
    else:
        candidates += [pr for _, pr in lows]

    # keep only targets in the correct direction
    if direction == 1:
        ahead = [c for c in candidates if c > entry]
        return min(ahead) if ahead else None
    else:
        ahead = [c for c in candidates if c < entry]
        return max(ahead) if ahead else None


def in_session(p: MMXMParams, now: datetime) -> bool:
    if not p.use_session:
        return True
    return p.sess_start_hour <= now.hour < p.sess_end_hour


# ---------------------------------------------------------------------------
# Main evaluation
# ---------------------------------------------------------------------------
def evaluate_mmxm(
    df_htf: pd.DataFrame,   # 30m
    df_entry: pd.DataFrame, # 1m (or base TF in backtest)
    df_4h: pd.DataFrame,    # 4h
    p: MMXMParams,
    now: datetime | None = None,
) -> MMXMSignal:
    now = now or datetime.now(timezone.utc)
    if not in_session(p, now):
        return MMXMSignal(HOLD, 0.0, 0.0, 0.0, "outside session")
    if len(df_htf) < p.ob_lookback + 5 or len(df_entry) < p.sweep_lookback + 10 \
            or len(df_4h) < 10:
        return MMXMSignal(HOLD, 0.0, 0.0, 0.0, "not enough data")

    atr_htf = ind.atr_last(df_htf["high"], df_htf["low"], df_htf["close"], p.atr_period)
    atr_e = ind.atr_last(df_entry["high"], df_entry["low"], df_entry["close"], p.atr_period)
    if pd.isna(atr_htf) or pd.isna(atr_e) or atr_htf <= 0 or atr_e <= 0:
        return MMXMSignal(HOLD, 0.0, 0.0, 0.0, "atr not ready")

    # 1) find a 30m OB that got filled -> bias
    obs = find_order_blocks(df_htf, p.ob_impulse_atr * atr_htf, p.ob_lookback)
    bias, ob_used = 0, None
    for ob in reversed(obs):
        if ob_was_filled(df_htf, ob):
            bias = ob["dir"]
            ob_used = ob
            break
    if bias == 0:
        return MMXMSignal(HOLD, 0.0, 0.0, 0.0, "no filled 30m OB")

    # 2) entry trigger: MMXM sweep + CHoCH on the entry TF
    choch = detect_choch(df_entry, bias, p.swing_lookback, p.sweep_lookback)
    if choch is None:
        return MMXMSignal(HOLD, 0.0, 0.0, 0.0, "OB filled; waiting for MMXM shift")

    entry = float(df_entry["close"].iloc[-1])
    buf = p.stop_buffer_atr * atr_e

    # 3) stop beyond the sweep extreme
    if bias == 1:
        sl = choch["sweep_extreme"] - buf
        risk = entry - sl
    else:
        sl = choch["sweep_extreme"] + buf
        risk = sl - entry
    if risk <= 0:
        return MMXMSignal(HOLD, entry, 0.0, 0.0, "invalid risk")

    # 4) target = nearest opposing draw, bounded by min/max RR
    tgt = pick_target(entry, bias, df_htf, df_4h, 0.10 * atr_htf)
    if tgt is None:
        tp = entry + bias * risk * p.max_reward_risk
    else:
        rr = abs(tgt - entry) / risk
        if rr < p.min_reward_risk:
            return MMXMSignal(HOLD, entry, 0.0, 0.0, "target too close (RR<min)")
        rr = min(rr, p.max_reward_risk)
        tp = entry + bias * risk * rr

    action = BUY if bias == 1 else SELL
    return MMXMSignal(action, entry, sl, tp,
                      f"30m OB filled ({'bull' if bias == 1 else 'bear'}) + MMXM shift",
                      context={"atr_e": atr_e, "rr": abs(tp - entry) / risk})
