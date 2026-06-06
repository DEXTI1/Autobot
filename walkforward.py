"""
Walk-forward validation - the single most important defense against fooling
yourself with an over-fit backtest.

The idea:
    1. Split the history into consecutive windows.
    2. On each IN-SAMPLE window, grid-search strategy parameters and pick the
       best by an objective (default: profit factor, tie-broken by return).
    3. Apply those "best" parameters to the FOLLOWING OUT-OF-SAMPLE window the
       optimizer never saw, and record that result.
    4. Stitch all out-of-sample results together.

If the strategy only looks good in-sample but the stitched out-of-sample curve
is poor, the edge was curve-fit noise, not real. A strategy that holds up
out-of-sample is far more trustworthy (still never guaranteed).

Run:
    python walkforward.py --synthetic
    python walkforward.py --csv data/XAUUSD_M15.csv
"""
from __future__ import annotations

import argparse
import itertools
import logging

import numpy as np
import pandas as pd

from backtest import Backtester, InstrumentSpec, build_default_components, load_csv, make_synthetic
from risk import RiskManager
from strategy import StrategyParams

log = logging.getLogger("walkforward")


# Parameter grid to search in-sample. Keep it modest: a huge grid is itself a
# form of overfitting (you'll always find SOMETHING that worked by luck).
PARAM_GRID = {
    "fast_ema": [10, 20, 30],
    "slow_ema": [50, 100],
    "trend_ema": [200],
}


def _objective(metrics: dict) -> tuple:
    """
    Rank candidates. Require a minimum number of trades so we don't crown a
    parameter set that only traded once and got lucky.
    """
    if metrics["num_trades"] < 5:
        return (-1e9, -1e9)
    pf = metrics["profit_factor"]
    pf = 100.0 if pf == float("inf") else pf
    return (pf, metrics["total_return_pct"])


def optimize(df: pd.DataFrame, spec: InstrumentSpec, risk: RiskManager,
             base: StrategyParams, start_equity: float) -> tuple[StrategyParams, dict]:
    """Grid-search PARAM_GRID on `df`; return the best params and their metrics."""
    best_params, best_metrics, best_score = None, None, (-1e18, -1e18)

    keys = list(PARAM_GRID.keys())
    for combo in itertools.product(*PARAM_GRID.values()):
        trial = {k: v for k, v in zip(keys, combo)}
        if trial["fast_ema"] >= trial["slow_ema"]:
            continue  # fast must be faster than slow
        params = StrategyParams(
            fast_ema=trial["fast_ema"],
            slow_ema=trial["slow_ema"],
            trend_ema=trial["trend_ema"],
            rsi_period=base.rsi_period,
            rsi_long_max=base.rsi_long_max,
            rsi_short_min=base.rsi_short_min,
            atr_period=base.atr_period,
        )
        bt = Backtester(spec, risk, params, start_equity=start_equity)
        result = bt.run(df)
        score = _objective(result.metrics)
        if score > best_score:
            best_score, best_params, best_metrics = score, params, result.metrics

    if best_params is None:  # nothing met the minimum-trades bar
        best_params, best_metrics = base, {"num_trades": 0}
    return best_params, best_metrics


def walk_forward(df: pd.DataFrame, n_splits: int = 5, oos_fraction: float = 0.3,
                 start_equity: float = 10_000.0) -> dict:
    """
    Run the walk-forward procedure and return a report dict.

    Each split's window is divided into an in-sample head and an out-of-sample
    tail (oos_fraction). Out-of-sample equity is compounded across splits so the
    stitched curve reflects sequentially trading the chosen parameters.
    """
    spec, risk, base = build_default_components()
    n = len(df)
    window = n // n_splits
    if window < 600:
        raise ValueError("Not enough data for the requested number of splits.")

    equity = start_equity
    folds = []

    for s in range(n_splits):
        start = s * window
        end = n if s == n_splits - 1 else (s + 1) * window
        win = df.iloc[start:end].reset_index(drop=True)

        split_at = int(len(win) * (1 - oos_fraction))
        in_sample = win.iloc[:split_at].reset_index(drop=True)
        out_sample = win.iloc[split_at:].reset_index(drop=True)
        if len(in_sample) < 300 or len(out_sample) < 150:
            continue

        best_params, is_metrics = optimize(in_sample, spec, risk, base, equity)

        # Trade the chosen params forward on unseen data, compounding equity.
        oos_bt = Backtester(spec, risk, best_params, start_equity=equity)
        oos_result = oos_bt.run(out_sample)
        equity = oos_result.final_equity

        folds.append({
            "fold": s + 1,
            "params": (best_params.fast_ema, best_params.slow_ema, best_params.trend_ema),
            "in_sample_pf": is_metrics.get("profit_factor", 0),
            "in_sample_return": is_metrics.get("total_return_pct", 0),
            "oos_return": oos_result.metrics["total_return_pct"],
            "oos_trades": oos_result.metrics["num_trades"],
            "oos_win_rate": oos_result.metrics["win_rate_pct"],
            "oos_max_dd": oos_result.metrics["max_drawdown_pct"],
            "equity_after": equity,
        })

    total_oos_return = (equity / start_equity - 1.0) * 100.0
    return {"folds": folds, "start_equity": start_equity,
            "final_equity": equity, "total_oos_return_pct": total_oos_return}


def print_report(report: dict) -> None:
    print("=" * 78)
    print("  WALK-FORWARD (out-of-sample) REPORT")
    print("=" * 78)
    header = f"{'Fold':>4} {'Params(f/s/t)':>16} {'IS PF':>7} {'OOS ret%':>9} {'OOS WR%':>8} {'OOS DD%':>8} {'Equity':>12}"
    print(header)
    print("-" * 78)
    for f in report["folds"]:
        p = "/".join(str(x) for x in f["params"])
        pf = f["in_sample_pf"]
        pf_s = "inf" if pf == float("inf") else f"{pf:.2f}"
        print(f"{f['fold']:>4} {p:>16} {pf_s:>7} {f['oos_return']:>+9.2f} "
              f"{f['oos_win_rate']:>8.1f} {f['oos_max_dd']:>8.2f} {f['equity_after']:>12,.0f}")
    print("-" * 78)
    print(f"  Start equity : {report['start_equity']:,.2f}")
    print(f"  Final equity : {report['final_equity']:,.2f}")
    print(f"  TOTAL OUT-OF-SAMPLE RETURN : {report['total_oos_return_pct']:+.2f}%")
    print("=" * 78)
    print("  Reminder: positive OOS != guaranteed future profit. It only means")
    print("  the edge wasn't obviously curve-fit on this data. Costs, slippage,")
    print("  and regime change can still turn it negative live.")


def main() -> None:
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(message)s")
    ap = argparse.ArgumentParser(description="Walk-forward validation.")
    ap.add_argument("--csv", help="Path to OHLC CSV.")
    ap.add_argument("--synthetic", action="store_true")
    ap.add_argument("--splits", type=int, default=5)
    ap.add_argument("--equity", type=float, default=10_000.0)
    args = ap.parse_args()

    if args.csv:
        df = load_csv(args.csv)
    elif args.synthetic:
        df = make_synthetic(bars=12000)
    else:
        ap.error("Provide --csv PATH or --synthetic")

    report = walk_forward(df, n_splits=args.splits, start_equity=args.equity)
    print_report(report)


if __name__ == "__main__":
    main()
