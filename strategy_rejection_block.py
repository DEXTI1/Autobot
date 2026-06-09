"""
Rejection-Block Retracement strategy (1H structure -> 5m entry).

The idea (your model):
  1. On the 1-HOUR chart, mark "rejection blocks" - swing candles with a long
     wick that show price was strongly rejected from a level:
        * BULLISH RB (support)   : a swing-LOW candle with a long LOWER wick.
                                   Zone = [low (distal) .. body-bottom (proximal)].
        * BEARISH RB (resistance): a swing-HIGH candle with a long UPPER wick.
                                   Zone = [body-top (proximal) .. high (distal)].
  2. Wait for price to RETRACE back into that block (seen on the 5-minute TF).
  3. ENTER in the block's direction when price touches the proximal edge:
        * buy at a bullish RB, sell at a bearish RB.
  4. TARGET the OPPOSITE rejection block (nearest one on the other side); if
     none exists yet, fall back to a fixed reward:risk multiple.
  5. STOP-LOSS just beyond the block's distal edge (under the low for longs,
     above the high for shorts), with a small buffer.

No-lookahead: a 1H block needs `swing_lookback` bars on EACH side to confirm the
swing, so it only becomes "known" (usable) after its right-side bars have closed.
detect_blocks() stamps each block with `known_time` = the moment it is confirmed,
and the backtest only uses blocks whose known_time <= the current 5m bar time.
"""
from __future__ import annotations

from dataclasses import dataclass

import pandas as pd


@dataclass
class RejBlockParams:
    swing_lookback: int = 2          # 1H bars required on each side of the swing
    wick_ratio: float = 0.5          # wick must be >= this fraction of the candle range
    sl_buffer_frac: float = 0.10     # SL buffer beyond distal, as a fraction of block height
    target_mode: str = "opposite"    # "opposite" (with rr fallback) or "rr"
    rr_fallback: float = 2.0         # reward:risk used when no opposite block exists
    max_block_age_h: int = 48        # ignore blocks older than this many hours
    min_block_points: float = 2.0    # minimum block height (index points) to be valid
    confirm: bool = False            # False = enter on touch; True = need 5m close back out
    use_trend_filter: bool = False   # only take blocks aligned with the 1H trend
    trend_ema: int = 50              # 1H EMA used for the trend filter


@dataclass
class Block:
    kind: str            # "bull" or "bear"
    proximal: float      # edge price re-enters first (entry edge)
    distal: float        # far edge (beyond it = invalidation -> SL side)
    top: float
    bottom: float
    formed_time: pd.Timestamp
    known_time: pd.Timestamp   # when it becomes usable (no lookahead)

    @property
    def height(self) -> float:
        return self.top - self.bottom


def detect_blocks(h1: pd.DataFrame, p: RejBlockParams) -> list[Block]:
    """Find all 1H rejection blocks in an OHLC dataframe (columns: time/open/high/low/close)."""
    L = p.swing_lookback
    o = h1["open"].values
    hi = h1["high"].values
    lo = h1["low"].values
    cl = h1["close"].values
    t = h1["time"].values
    n = len(h1)
    hour = pd.Timedelta(hours=1)
    blocks: list[Block] = []

    for i in range(L, n - L):
        rng = hi[i] - lo[i]
        if rng <= 0:
            continue
        body_top = max(o[i], cl[i])
        body_bot = min(o[i], cl[i])
        lower_wick = body_bot - lo[i]
        upper_wick = hi[i] - body_top

        # swing low? (strict local minimum over the window)
        is_swing_low = all(lo[i] < lo[i - k] for k in range(1, L + 1)) and \
                       all(lo[i] < lo[i + k] for k in range(1, L + 1))
        # swing high?
        is_swing_high = all(hi[i] > hi[i - k] for k in range(1, L + 1)) and \
                        all(hi[i] > hi[i + k] for k in range(1, L + 1))

        known = pd.Timestamp(t[i + L]) + hour   # confirmed after right-side bars close

        if is_swing_low and lower_wick >= p.wick_ratio * rng:
            top, bottom = body_bot, lo[i]       # zone between body-bottom and the low
            if (top - bottom) >= p.min_block_points:
                blocks.append(Block("bull", proximal=top, distal=bottom, top=top,
                                    bottom=bottom, formed_time=pd.Timestamp(t[i]),
                                    known_time=known))

        if is_swing_high and upper_wick >= p.wick_ratio * rng:
            top, bottom = hi[i], body_top       # zone between the high and body-top
            if (top - bottom) >= p.min_block_points:
                blocks.append(Block("bear", proximal=bottom, distal=top, top=top,
                                    bottom=bottom, formed_time=pd.Timestamp(t[i]),
                                    known_time=known))

    blocks.sort(key=lambda b: b.known_time)
    return blocks
