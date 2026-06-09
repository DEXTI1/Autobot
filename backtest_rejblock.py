"""
Event-driven backtest for the Rejection-Block Retracement strategy.

Flow:
  * Resample the 5m base data to 1H and detect rejection blocks (no lookahead -
    each block has a known_time it only becomes usable after).
  * Walk the 5m bars one at a time. When FLAT, look for a retrace into a known,
    unmitigated block:
        - BULL block (support): price was ABOVE the proximal on the prior bar and
          this bar's low crosses DOWN to/through it  -> BUY at the proximal.
        - BEAR block (resistance): mirror -> SELL at the proximal.
  * SL = beyond the distal edge + buffer. TP = the opposite block's proximal
    (nearest one past entry); if none, fall back to rr_fallback * risk.
  * Realistic costs: half-spread on entry & exit, risk-% position sizing,
    pessimistic intrabar (if a bar could hit both SL and TP, assume SL first).

Same economics as the other USTEC backtests: $1/point, 0.5% risk, 1.5pt spread.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from strategy_rejection_block import RejBlockParams, Block, detect_blocks


def resample_1h(df5: pd.DataFrame) -> pd.DataFrame:
    g = (df5.set_index("time")
            .resample("1h")
            .agg(open=("open", "first"), high=("high", "max"),
                 low=("low", "min"), close=("close", "last"))
            .dropna()
            .reset_index())
    return g


def run(df5: pd.DataFrame, p: RejBlockParams, start_equity: float = 10_000.0,
        risk_pct: float = 0.5, spread: float = 1.5, dollars_per_point: float = 1.0,
        min_lot: float = 0.01, max_lot: float = 100.0) -> dict:
    h1 = resample_1h(df5)
    blocks = detect_blocks(h1, p)

    # 1H trend reference (close vs EMA), stamped at each 1H bar's CLOSE time (no lookahead)
    trend_ser = None
    if p.use_trend_filter:
        ema = h1["close"].ewm(span=p.trend_ema, adjust=False).mean()
        sign = np.where(h1["close"].values > ema.values, 1, -1)
        idx = pd.DatetimeIndex(h1["time"].values) + pd.Timedelta(hours=1)
        trend_ser = pd.Series(sign, index=idx).sort_index()

    half = spread / 2.0
    age = pd.Timedelta(hours=p.max_block_age_h)

    balance = start_equity
    equity_curve: list[float] = []
    trades: list[float] = []
    wins = losses = 0
    gross_win = gross_loss = 0.0

    pos = None           # dict(dir, entry, sl, tp, vol)
    used = set()         # ids of mitigated blocks
    bi = 0               # pointer into blocks by known_time

    times = df5["time"].values
    o = df5["open"].values; hi = df5["high"].values
    lo = df5["low"].values; cl = df5["close"].values
    n = len(df5)

    active: list[Block] = []
    prev_close = None

    for i in range(n):
        now = pd.Timestamp(times[i])

        # admit newly-known blocks
        while bi < len(blocks) and blocks[bi].known_time <= now:
            active.append(blocks[bi]); bi += 1
        # drop expired / used blocks
        active = [b for b in active if id(b) not in used and (now - b.known_time) <= age]

        # ---- manage open position (intrabar, pessimistic) ----
        if pos is not None:
            hit_sl = (pos["dir"] == 1 and lo[i] <= pos["sl"]) or (pos["dir"] == -1 and hi[i] >= pos["sl"])
            hit_tp = (pos["dir"] == 1 and hi[i] >= pos["tp"]) or (pos["dir"] == -1 and lo[i] <= pos["tp"])
            exit_px = None
            if hit_sl:
                exit_px = pos["sl"]
            elif hit_tp:
                exit_px = pos["tp"]
            if exit_px is not None:
                fill = exit_px - pos["dir"] * half
                pnl = (fill - pos["entry"]) * pos["dir"] * dollars_per_point * pos["vol"]
                balance += pnl
                trades.append(pnl)
                if pnl >= 0:
                    wins += 1; gross_win += pnl
                else:
                    losses += 1; gross_loss += -pnl
                pos = None

        # ---- look for an entry when flat ----
        if pos is None and prev_close is not None:
            trend = 0
            if trend_ser is not None:
                tv = trend_ser.asof(now)
                trend = int(tv) if tv == tv else 0  # NaN-safe (before first bar)
            for b in active:
                if b.kind == "bull":
                    if p.use_trend_filter and trend < 0:
                        continue
                    # price was above proximal, now dips into it
                    if prev_close > b.proximal and lo[i] <= b.proximal:
                        entry = b.proximal + half
                        sl = b.distal - p.sl_buffer_frac * b.height
                        tp = _target(active, b, entry, p, direction=1)
                        risk_dist = entry - sl
                        if risk_dist > 0 and tp > entry:
                            vol = _lot(balance, risk_pct, risk_dist, dollars_per_point, min_lot, max_lot)
                            pos = dict(dir=1, entry=entry, sl=sl, tp=tp, vol=vol)
                            used.add(id(b))
                            break
                else:  # bear
                    if p.use_trend_filter and trend > 0:
                        continue
                    if prev_close < b.proximal and hi[i] >= b.proximal:
                        entry = b.proximal - half
                        sl = b.distal + p.sl_buffer_frac * b.height
                        tp = _target(active, b, entry, p, direction=-1)
                        risk_dist = sl - entry
                        if risk_dist > 0 and tp < entry:
                            vol = _lot(balance, risk_pct, risk_dist, dollars_per_point, min_lot, max_lot)
                            pos = dict(dir=-1, entry=entry, sl=sl, tp=tp, vol=vol)
                            used.add(id(b))
                            break

        prev_close = cl[i]
        eq = balance
        if pos is not None:
            eq += (cl[i] - pos["entry"]) * pos["dir"] * dollars_per_point * pos["vol"]
        equity_curve.append(eq)

    curve = pd.Series(equity_curve, index=df5["time"])
    final = float(curve.iloc[-1]) if len(curve) else start_equity
    running_max = curve.cummax()
    dd = ((curve - running_max) / running_max).min() if len(curve) else 0.0
    nt = len(trades)
    pf = (gross_win / gross_loss) if gross_loss > 0 else (float("inf") if gross_win > 0 else 0.0)
    return dict(
        final=final,
        ret=(final / start_equity - 1) * 100,
        dd=abs(float(dd)) * 100,
        n=nt,
        wr=(wins / nt * 100) if nt else 0.0,
        pf=pf,
        blocks=len(blocks),
        avg=(float(np.mean(trades)) if nt else 0.0),
    )


def _target(active, entry_block: Block, entry: float, p: RejBlockParams, direction: int) -> float:
    if p.target_mode == "opposite":
        if direction == 1:
            opp = [b for b in active if b.kind == "bear" and b.proximal > entry]
            if opp:
                return min(opp, key=lambda b: b.proximal).proximal
        else:
            opp = [b for b in active if b.kind == "bull" and b.proximal < entry]
            if opp:
                return max(opp, key=lambda b: b.proximal).proximal
    # fallback: fixed reward:risk
    if direction == 1:
        sl = entry_block.distal - p.sl_buffer_frac * entry_block.height
        return entry + p.rr_fallback * (entry - sl)
    else:
        sl = entry_block.distal + p.sl_buffer_frac * entry_block.height
        return entry - p.rr_fallback * (sl - entry)


def _lot(balance, risk_pct, risk_dist, dollars_per_point, min_lot, max_lot) -> float:
    money = balance * (risk_pct / 100.0)
    raw = money / (risk_dist * dollars_per_point)
    return max(min_lot, min(round(raw, 2), max_lot))


if __name__ == "__main__":
    df = pd.read_csv("ustec_nq_5m.csv")
    df["time"] = pd.to_datetime(df["time"], utc=True)
    df = df.sort_values("time").reset_index(drop=True)
    print(f"Dataset: {len(df)} 5m bars | {df['time'].min()} -> {df['time'].max()}\n")

    variants = {
        "touch + opposite-target (defaults)": RejBlockParams(),
        "touch + 2R fixed target": RejBlockParams(target_mode="rr"),
        "stronger wick (0.6) + opposite": RejBlockParams(wick_ratio=0.6),
        "+ 1H TREND FILTER (aligned only)": RejBlockParams(use_trend_filter=True),
        "+ trend filter + stronger wick": RejBlockParams(use_trend_filter=True, wick_ratio=0.6),
        "+ trend filter + 2R target": RejBlockParams(use_trend_filter=True, target_mode="rr"),
    }
    print(f"  {'variant':<38}{'ret%':>8}{'DD%':>7}{'trades':>8}{'win%':>7}{'PF':>7}{'blocks':>8}")
    print("  " + "-" * 83)
    for name, p in variants.items():
        r = run(df, p)
        pf = "inf" if r["pf"] == float("inf") else f"{r['pf']:.2f}"
        print(f"  {name:<38}{r['ret']:>+8.2f}{r['dd']:>7.2f}{r['n']:>8d}{r['wr']:>7.1f}{pf:>7}{r['blocks']:>8d}")
    print("  " + "-" * 83)
