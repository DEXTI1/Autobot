"""
Backtester for the ICT multi-timeframe strategy (strategy_ict.py).

It reproduces the LIVE logic bar-by-bar on real 1-minute data:
    - resample 1m -> 5m and 15m (the strategy reads all three)
    - at each CLOSED 1m bar inside the NY killzone, call evaluate_mtf using ONLY
      data up to that bar (no lookahead)
    - when a signal fires, simulate the trade against subsequent 1m bars:
        * exit at stop (under/over the swing) or take-profit (1:1), whichever
          the price reaches first (pessimistic: if a bar spans both, stop wins)
        * include spread cost on entry and exit
    - one position at a time

Usage:
    python backtest_ict.py --csv /tmp/gold_1m.csv
"""
from __future__ import annotations

import argparse

import numpy as np
import pandas as pd

import strategy_ict as ict


def load_yf_1m(path: str) -> pd.DataFrame:
    """Load a yfinance CSV (multi-row header) into time/open/high/low/close."""
    raw = pd.read_csv(path)
    # yfinance writes 3 header rows: Price/Ticker/Date. Detect & skip.
    # The first column holds the datetime once we drop the ticker rows.
    # Re-read robustly:
    df = pd.read_csv(path, skiprows=[1, 2])
    df = df.rename(columns={df.columns[0]: "time"})
    df.columns = [str(c).lower() for c in df.columns]
    df["time"] = pd.to_datetime(df["time"], utc=True)
    for col in ["open", "high", "low", "close"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df = df.dropna(subset=["open", "high", "low", "close"]).reset_index(drop=True)
    return df[["time", "open", "high", "low", "close"]]


def resample(df1: pd.DataFrame, rule: str) -> pd.DataFrame:
    """Resample 1m OHLC to a higher timeframe (e.g. '5min','15min')."""
    s = df1.set_index("time")
    agg = s.resample(rule, label="right", closed="right").agg(
        {"open": "first", "high": "max", "low": "min", "close": "last"}
    ).dropna().reset_index()
    return agg


def run_ict_backtest(
    df1: pd.DataFrame,
    params: ict.ICTParams,
    start_equity: float = 10_000.0,
    risk_pct: float = 1.0,
    spread: float = 0.30,          # round-trip-ish spread on gold in price ($)
    dollars_per_point_per_lot: float = 100.0,  # gold: 1.0 price move = $100 per 1.0 lot
    min_lot: float = 0.01,
    max_lot: float = 5.0,
):
    df5 = resample(df1, "5min")
    df15 = resample(df1, "15min")

    equity = start_equity
    trades = []
    equity_curve = []
    in_pos = False
    pos = None

    # Pre-index higher TFs by time for "data up to now" slicing (UTC aware).
    t5 = pd.to_datetime(df5["time"], utc=True)
    t15 = pd.to_datetime(df15["time"], utc=True)

    warm = max(params.ifvg_lookback_1m, 40) + 5
    for i in range(warm, len(df1)):
        bar = df1.iloc[i]
        now = bar["time"].to_pydatetime()

        # --- manage open position against this bar ---
        if in_pos:
            hit_sl = bar["low"] <= pos["sl"] <= bar["high"] or (
                pos["dir"] == 1 and bar["low"] <= pos["sl"]) or (
                pos["dir"] == -1 and bar["high"] >= pos["sl"])
            hit_tp = (pos["dir"] == 1 and bar["high"] >= pos["tp"]) or (
                pos["dir"] == -1 and bar["low"] <= pos["tp"])
            exit_price = None
            reason = ""
            if hit_sl:  # pessimistic: stop first
                exit_price, reason = pos["sl"], "stop"
            elif hit_tp:
                exit_price, reason = pos["tp"], "target"
            if exit_price is not None:
                gross = (exit_price - pos["entry"]) * pos["dir"] * dollars_per_point_per_lot * pos["lot"]
                cost = spread * dollars_per_point_per_lot * pos["lot"]
                pnl = gross - cost
                equity += pnl
                pos["exit"] = exit_price
                pos["pnl"] = pnl
                pos["reason"] = reason
                trades.append(pos)
                in_pos = False
                pos = None
            equity_curve.append(equity)
            continue

        equity_curve.append(equity)

        # --- look for a new signal (no lookahead: slice up to current time) ---
        if not ict.in_ny_killzone(params, now):
            continue
        d1 = df1.iloc[:i + 1]
        d5 = df5[(t5 <= bar["time"]).values]
        d15 = df15[(t15 <= bar["time"]).values]
        if len(d5) < params.sweep_lookback_5m + 5 or len(d15) < params.fvg_lookback_15m + 5:
            continue

        sig = ict.evaluate_mtf(d15.reset_index(drop=True),
                               d5.reset_index(drop=True),
                               d1.reset_index(drop=True), params, now=now)
        if sig.action in ("BUY", "SELL"):
            direction = 1 if sig.action == "BUY" else -1
            stop_dist = abs(sig.price - sig.sl_price)
            if stop_dist <= 0:
                continue
            money_risk = equity * (risk_pct / 100.0)
            lot = money_risk / (stop_dist * dollars_per_point_per_lot)
            lot = max(min_lot, min(lot, max_lot))
            entry = sig.price + direction * spread / 2.0  # pay half spread on entry
            pos = {"dir": direction, "entry": entry, "sl": sig.sl_price,
                   "tp": sig.tp_price, "lot": round(lot, 2), "time": now,
                   "reason_open": sig.reason}
            in_pos = True

    # --- metrics ---
    curve = pd.Series(equity_curve)
    pnls = np.array([t["pnl"] for t in trades], dtype=float)
    wins = pnls[pnls > 0]
    losses = pnls[pnls < 0]
    win_rate = (len(wins) / len(pnls) * 100.0) if len(pnls) else 0.0
    gross_win = wins.sum() if len(wins) else 0.0
    gross_loss = abs(losses.sum()) if len(losses) else 0.0
    pf = (gross_win / gross_loss) if gross_loss > 0 else (float("inf") if gross_win > 0 else 0.0)
    if len(curve):
        run_max = curve.cummax()
        dd = ((curve - run_max) / run_max).min() * 100.0
    else:
        dd = 0.0

    return {
        "trades": trades,
        "start_equity": start_equity,
        "final_equity": equity,
        "return_pct": (equity / start_equity - 1) * 100.0,
        "num_trades": len(trades),
        "win_rate": win_rate,
        "profit_factor": pf,
        "max_drawdown_pct": abs(dd),
        "avg_trade": float(pnls.mean()) if len(pnls) else 0.0,
        "best": float(pnls.max()) if len(pnls) else 0.0,
        "worst": float(pnls.min()) if len(pnls) else 0.0,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", required=True, help="1-minute OHLC CSV (yfinance format)")
    ap.add_argument("--equity", type=float, default=10_000.0)
    ap.add_argument("--risk", type=float, default=1.0)
    ap.add_argument("--spread", type=float, default=0.30, help="gold spread in price $")
    args = ap.parse_args()

    df1 = load_yf_1m(args.csv)
    print(f"Loaded {len(df1)} 1m bars | {df1['time'].min()} -> {df1['time'].max()}")

    params = ict.ICTParams()
    res = run_ict_backtest(df1, params, start_equity=args.equity,
                           risk_pct=args.risk, spread=args.spread)

    print("=" * 56)
    print("  ICT GOLD BACKTEST (real GC=F data, NY killzone only)")
    print("=" * 56)
    print(f"  Period bars       : {len(df1)} 1m")
    print(f"  Start equity      : {res['start_equity']:,.2f}")
    print(f"  Final equity      : {res['final_equity']:,.2f}")
    print(f"  Return            : {res['return_pct']:+.2f}%")
    print(f"  Trades            : {res['num_trades']}")
    print(f"  Win rate          : {res['win_rate']:.1f}%")
    print(f"  Profit factor     : {res['profit_factor']:.2f}")
    print(f"  Max drawdown      : {res['max_drawdown_pct']:.2f}%")
    print(f"  Avg / best / worst: {res['avg_trade']:+.2f} / {res['best']:+.2f} / {res['worst']:+.2f}")
    print("=" * 56)
    for t in res["trades"]:
        d = "BUY " if t["dir"] == 1 else "SELL"
        print(f"  {t['time']:%Y-%m-%d %H:%M} {d} entry={t['entry']:.2f} sl={t['sl']:.2f} "
              f"tp={t['tp']:.2f} lot={t['lot']:.2f} -> {t['reason']:6} {t['pnl']:+.2f}")


if __name__ == "__main__":
    main()
