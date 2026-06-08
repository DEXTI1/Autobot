"""
Cross-regime robustness: baseline scalp_pro vs tuned scalp_pro_v2 on ~2 years
of Nasdaq-100 (NQ=F) 1h bars, broken down by calendar quarter.

5m history is capped at 60 days (one regime). 1h reaches ~2 years, covering
bull, bear and choppy quarters - the real test of whether the v2 upgrades are
"safer". Granularity is coarser than the live 5m, so read it for the RELATIVE
baseline-vs-v2 behaviour per regime, not absolute numbers.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import yfinance as yf

import backtest as bt
from risk import RiskManager
from backtest_engine_v2 import BacktesterV2
import strategy_scalp_pro as s_pro
import strategy_scalp_pro_v2 as s_v2

START = 10_000.0
RISK = 0.5
SPREAD = 1.5
PPY = 252 * 24


def fetch():
    df = yf.download("NQ=F", period="730d", interval="1h", progress=False, auto_adjust=False)
    df.columns = [c[0] if isinstance(c, tuple) else c for c in df.columns]
    df = df.reset_index().rename(columns={df.reset_index().columns[0]: "time",
                                          "Open": "open", "High": "high", "Low": "low",
                                          "Close": "close", "Volume": "volume"})
    df = df[["time", "open", "high", "low", "close", "volume"]].dropna()
    df["time"] = pd.to_datetime(df["time"], utc=True)
    return df.sort_values("time").reset_index(drop=True)


def spec():
    return bt.InstrumentSpec(point=1.0, trade_tick_size=1.0, trade_tick_value=1.0,
                             volume_min=0.01, volume_step=0.01, volume_max=100.0,
                             spread_points=SPREAD, swap_points_per_night=2.0)


def run_base(df):
    rk = RiskManager(RISK, 2.0, 2.0, 1, 100.0, 0.01, 100.0)
    bk = bt.Backtester(spec(), rk, s_pro.ScalpProParams(min_atr_points=12.0),
                       start_equity=START, use_trailing=True, trail_atr_mult=2.0,
                       periods_per_year=PPY, strategy_module=s_pro)
    return bk.run(df.copy()).metrics


def run_v2(df):
    rk = RiskManager(RISK, 2.0, 2.0, 1, 3.0, 0.01, 100.0, max_trades_per_day=6)
    bk = BacktesterV2(spec(), rk, s_v2.ScalpProV2Params(),
                      start_equity=START, use_trailing=True, trail_atr_mult=3.0,
                      periods_per_year=PPY, strategy_module=s_v2,
                      use_breakeven=True, use_daily_loss_stop=True, use_trade_cap=True)
    return bk.run(df.copy()).metrics


def regime_label(df):
    c = df["close"].values
    move = (c[-1] / c[0] - 1) * 100
    # choppiness: net move vs total path
    path = np.abs(np.diff(c)).sum()
    eff = abs(c[-1] - c[0]) / path if path else 0
    if move > 6 and eff > 0.12:
        return f"BULL ({move:+.0f}%)"
    if move < -6 and eff > 0.12:
        return f"BEAR ({move:+.0f}%)"
    return f"CHOP ({move:+.0f}%)"


def main():
    df = fetch()
    print(f"NQ 1h: {len(df)} bars | {df['time'].min():%Y-%m-%d} -> {df['time'].max():%Y-%m-%d}\n")
    df["q"] = df["time"].dt.to_period("Q")
    print(f"  {'quarter':<9}{'regime':<14}{'BASE ret%':>11}{'BASE DD%':>10}"
          f"{'V2 ret%':>10}{'V2 DD%':>9}")
    print("  " + "-" * 62)
    bsum = vsum = 0.0
    bdd = vdd = 0.0
    for q, g in df.groupby("q"):
        g = g.reset_index(drop=True)
        if len(g) < 300:
            continue
        b, v = run_base(g), run_v2(g)
        bsum += b["total_return_pct"]; vsum += v["total_return_pct"]
        bdd = max(bdd, b["max_drawdown_pct"]); vdd = max(vdd, v["max_drawdown_pct"])
        print(f"  {str(q):<9}{regime_label(g):<14}{b['total_return_pct']:>+11.2f}"
              f"{b['max_drawdown_pct']:>10.2f}{v['total_return_pct']:>+10.2f}"
              f"{v['max_drawdown_pct']:>9.2f}")
    print("  " + "-" * 62)
    print(f"  {'SUM/agg':<23}{bsum:>+11.2f}{bdd:>10.2f}{vsum:>+10.2f}{vdd:>9.2f}")
    print("\n  (BASE = original scalp_pro | V2 = upgraded, RR2.0/trail3.0 + guards)")


if __name__ == "__main__":
    main()
