"""
Backtester for the MMXM strategy (strategy_mmxm.py) on real gold data.

Reproduces the live logic bar-by-bar with NO lookahead:
    - base TF = entry TF (5m for the 2-month test; would be 1m live)
    - resample base -> 30m (HTF order blocks) and 4h (targets)
    - at each closed base bar, call evaluate_mmxm with data only up to now
    - simulate each trade vs subsequent base bars (stop first if a bar spans both)
    - spread cost on entry+exit, one position at a time

Usage:
    python backtest_mmxm.py --csv gold_5m.csv
"""
from __future__ import annotations

import argparse

import numpy as np
import pandas as pd

import strategy_mmxm as mm
from backtest_ict import load_yf_1m, resample


def run(df_base: pd.DataFrame, p: mm.MMXMParams, start_equity=10_000.0,
        risk_pct=1.0, spread=0.30, dollars_per_point_per_lot=100.0,
        min_lot=0.01, max_lot=5.0):
    df30 = resample(df_base, "30min")
    df4h = resample(df_base, "4h")
    t30 = pd.to_datetime(df30["time"], utc=True)
    t4h = pd.to_datetime(df4h["time"], utc=True)

    equity = start_equity
    trades, curve = [], []
    in_pos, pos = False, None
    warm = max(p.sweep_lookback, 40) + 5

    # PERFORMANCE: only the most recent N bars of each timeframe matter, so we
    # use trailing windows instead of re-slicing the whole growing history every
    # bar (that was O(n^2) and hung on 100k bars). These caps are generous.
    base_win = max(p.sweep_lookback + p.swing_lookback + 10, 120)
    htf_win = p.ob_lookback + 10
    h4_win = 60

    # numpy arrays of higher-TF timestamps for fast searchsorted slicing.
    t30_ns = t30.values.astype("datetime64[ns]")
    t4h_ns = t4h.values.astype("datetime64[ns]")

    n = len(df_base)
    next_report = 10
    for i in range(warm, n):
        bar = df_base.iloc[i]

        # progress indicator so you can see it working on big files
        pct = int((i - warm) / max(1, n - warm) * 100)
        if pct >= next_report:
            print(f"  ...backtesting {pct}%", flush=True)
            next_report += 10

        if in_pos:
            hit_sl = (pos["dir"] == 1 and bar["low"] <= pos["sl"]) or \
                     (pos["dir"] == -1 and bar["high"] >= pos["sl"])
            hit_tp = (pos["dir"] == 1 and bar["high"] >= pos["tp"]) or \
                     (pos["dir"] == -1 and bar["low"] <= pos["tp"])
            ex, reason = None, ""
            if hit_sl:
                ex, reason = pos["sl"], "stop"
            elif hit_tp:
                ex, reason = pos["tp"], "target"
            if ex is not None:
                gross = (ex - pos["entry"]) * pos["dir"] * dollars_per_point_per_lot * pos["lot"]
                cost = spread * dollars_per_point_per_lot * pos["lot"]
                pnl = gross - cost
                equity += pnl
                pos.update(exit=ex, pnl=pnl, reason=reason)
                trades.append(pos)
                in_pos, pos = False, None
            curve.append(equity)
            continue
        curve.append(equity)

        bar_t = bar["time"].to_datetime64()
        now = bar["time"].to_pydatetime()

        # trailing base-TF window
        d_base = df_base.iloc[max(0, i - base_win):i + 1]
        # higher-TF: index of last closed bar at/<= now, then take a trailing slice
        k30 = int(np.searchsorted(t30_ns, bar_t, side="right"))
        k4h = int(np.searchsorted(t4h_ns, bar_t, side="right"))
        if k30 < p.ob_lookback + 5 or k4h < 10:
            continue
        d30 = df30.iloc[max(0, k30 - htf_win):k30]
        d4h = df4h.iloc[max(0, k4h - h4_win):k4h]

        sig = mm.evaluate_mmxm(d30.reset_index(drop=True), d_base.reset_index(drop=True),
                               d4h.reset_index(drop=True), p, now=now)
        if sig.action in ("BUY", "SELL"):
            direction = 1 if sig.action == "BUY" else -1
            stop_dist = abs(sig.price - sig.sl_price)
            if stop_dist <= 0:
                continue
            money_risk = equity * (risk_pct / 100.0)
            lot = max(min_lot, min(money_risk / (stop_dist * dollars_per_point_per_lot), max_lot))
            entry = sig.price + direction * spread / 2.0
            pos = {"dir": direction, "entry": entry, "sl": sig.sl_price, "tp": sig.tp_price,
                   "lot": round(lot, 2), "time": now, "reason_open": sig.reason}
            in_pos = True

    curve = pd.Series(curve)
    pnls = np.array([t["pnl"] for t in trades], dtype=float)
    wins, losses = pnls[pnls > 0], pnls[pnls < 0]
    wr = (len(wins) / len(pnls) * 100) if len(pnls) else 0.0
    gw, gl = (wins.sum() if len(wins) else 0.0), (abs(losses.sum()) if len(losses) else 0.0)
    pf = (gw / gl) if gl > 0 else (float("inf") if gw > 0 else 0.0)
    dd = abs(((curve - curve.cummax()) / curve.cummax()).min() * 100) if len(curve) else 0.0
    return {"trades": trades, "final": equity,
            "ret": (equity / start_equity - 1) * 100, "n": len(trades),
            "wr": wr, "pf": pf, "dd": dd,
            "avg": float(pnls.mean()) if len(pnls) else 0.0,
            "best": float(pnls.max()) if len(pnls) else 0.0,
            "worst": float(pnls.min()) if len(pnls) else 0.0}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", required=True)
    ap.add_argument("--equity", type=float, default=10_000.0)
    ap.add_argument("--risk", type=float, default=1.0)
    ap.add_argument("--spread", type=float, default=0.30)
    ap.add_argument("--resample", default="5min",
                    help="Resample the base data to this TF before testing "
                         "(default 5min - fast and correct for MMXM). Use 'none' "
                         "to run on the raw timeframe (slow on 100k+ 1m bars).")
    args = ap.parse_args()

    df = load_yf_1m(args.csv)  # auto-detects yfinance / MT5 format
    if args.resample.lower() != "none":
        df = resample(df, args.resample)
        print(f"Loaded & resampled to {args.resample}: {len(df)} bars "
              f"| {df['time'].min()} -> {df['time'].max()}")
    else:
        print(f"Loaded {len(df)} base bars | {df['time'].min()} -> {df['time'].max()}")
    res = run(df, mm.MMXMParams(), start_equity=args.equity,
              risk_pct=args.risk, spread=args.spread)
    print("=" * 56)
    print("  MMXM GOLD BACKTEST (real gold, OB-fill + MMXM shift)")
    print("=" * 56)
    print(f"  Final equity   : {res['final']:,.2f}")
    print(f"  Return         : {res['ret']:+.2f}%")
    print(f"  Trades         : {res['n']}")
    print(f"  Win rate       : {res['wr']:.1f}%")
    print(f"  Profit factor  : {res['pf']:.2f}")
    print(f"  Max drawdown   : {res['dd']:.2f}%")
    print(f"  Avg/best/worst : {res['avg']:+.2f} / {res['best']:+.2f} / {res['worst']:+.2f}")
    print("=" * 56)


if __name__ == "__main__":
    main()
