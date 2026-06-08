"""
Unified USTEC (Nasdaq-100) backtest harness.

Runs all five Autobot strategies on the SAME recent intraday dataset with
USTEC-appropriate costs, so they can be compared fairly.

Data : NQ=F (E-mini Nasdaq-100 futures) 5-minute bars, ~2 months, near-24h.
       This is the closest free proxy for the USTEC / US100 CFD (same index,
       ~same price level, nearly round-the-clock).

Fairness notes:
  * Same starting equity ($10,000) and same risk per trade (0.5%) for all.
  * Same instrument economics ($1 / index point per 1.0 lot) and spread
    (~1.5 index points). Because position size is risk-based, the % return is
    largely invariant to the $/point multiplier; only spread-vs-volatility
    matters, which is modelled consistently.
  * ICT/MMXM use the 5m bars as their entry timeframe (live they'd use 1m), so
    their entry timing is coarser here - a known, documented limitation.
"""
from __future__ import annotations

import pandas as pd

# generic engine + strategies
import backtest as bt
from risk import RiskManager
import strategy as s_trend
import strategy_scalp as s_scalp
import strategy_scalp_pro as s_pro
# dedicated multi-TF engines
import backtest_ict as bt_ict
import backtest_mmxm as bt_mmxm
import strategy_ict as ict
import strategy_mmxm as mm

CSV = "ustec_nq_5m.csv"
START_EQUITY = 10_000.0
RISK_PCT = 0.5
SPREAD_PTS = 1.5            # index points (round-trip-ish)
DOLLARS_PER_POINT = 1.0     # $ per 1.0 index point per 1.0 lot (USTEC CFD-like)
PERIODS_PER_YEAR = 252 * 288  # 5m bars/yr (~24h futures, 252 trading days)


def load() -> pd.DataFrame:
    df = pd.read_csv(CSV)
    df["time"] = pd.to_datetime(df["time"], utc=True)
    return df.sort_values("time").reset_index(drop=True)


def ustec_spec() -> bt.InstrumentSpec:
    # value_per_price_unit_per_lot = tick_value/tick_size = 1.0  -> $1/point/lot
    return bt.InstrumentSpec(
        point=1.0, trade_tick_size=1.0, trade_tick_value=1.0,
        volume_min=0.01, volume_step=0.01, volume_max=100.0,
        spread_points=SPREAD_PTS,   # round-trip = spread_points*point = SPREAD_PTS
        commission_per_lot_side=0.0,
        swap_points_per_night=2.0,
    )


def risk_mgr() -> RiskManager:
    return RiskManager(
        risk_per_trade_pct=RISK_PCT, atr_sl_multiplier=2.0, reward_risk_ratio=2.0,
        max_open_positions=1, daily_max_loss_pct=100.0,  # don't kill mid-backtest
        min_lot=0.01, max_lot=100.0,
    )


def run_generic(df, module, params, label):
    spec = ustec_spec()
    bk = bt.Backtester(spec, risk_mgr(), params, start_equity=START_EQUITY,
                       use_trailing=True, trail_atr_mult=2.0,
                       periods_per_year=PERIODS_PER_YEAR, strategy_module=module)
    r = bk.run(df.copy())
    m = r.metrics
    return dict(name=label, ret=m["total_return_pct"], trades=m["num_trades"],
                wr=m["win_rate_pct"], pf=m["profit_factor"], dd=m["max_drawdown_pct"],
                final=r.final_equity)


def run_ict(df):
    r = bt_ict.run_ict_backtest(df.copy(), ict.ICTParams(), start_equity=START_EQUITY,
                                risk_pct=RISK_PCT, spread=SPREAD_PTS,
                                dollars_per_point_per_lot=DOLLARS_PER_POINT,
                                min_lot=0.01, max_lot=100.0)
    return dict(name="ict", ret=r["return_pct"], trades=r["num_trades"],
                wr=r["win_rate"], pf=r["profit_factor"], dd=r["max_drawdown_pct"],
                final=r["final_equity"])


def run_mmxm(df):
    r = bt_mmxm.run(df.copy(), mm.MMXMParams(), start_equity=START_EQUITY,
                    risk_pct=RISK_PCT, spread=SPREAD_PTS,
                    dollars_per_point_per_lot=DOLLARS_PER_POINT,
                    min_lot=0.01, max_lot=100.0)
    return dict(name="mmxm", ret=r["ret"], trades=r["n"], wr=r["wr"],
                pf=r["pf"], dd=r["dd"], final=r["final"])


def main():
    df = load()
    print(f"Dataset: {len(df)} 5m bars | {df['time'].min()} -> {df['time'].max()}\n")

    rows = []
    print(">> trend ...");     rows.append(run_generic(df, s_trend, s_trend.StrategyParams(), "trend"))
    print(">> scalp ...");     rows.append(run_generic(df, s_scalp, s_scalp.ScalpParams(), "scalp"))
    print(">> scalp_pro ..."); rows.append(run_generic(df, s_pro, s_pro.ScalpProParams(min_atr_points=12.0), "scalp_pro"))
    print(">> ict ...");       rows.append(run_ict(df))
    print(">> mmxm ...");      rows.append(run_mmxm(df))

    rows.sort(key=lambda x: x["ret"], reverse=True)
    print("\n" + "=" * 78)
    print(f"  USTEC (NQ=F 5m, ~2 months) | start ${START_EQUITY:,.0f} | risk {RISK_PCT}%/trade")
    print("=" * 78)
    print(f"  {'strategy':<10} {'return%':>9} {'final$':>10} {'trades':>7} {'win%':>6} {'PF':>6} {'maxDD%':>7}")
    print("  " + "-" * 74)
    for r in rows:
        pf = "inf" if r["pf"] == float("inf") else f"{r['pf']:.2f}"
        print(f"  {r['name']:<10} {r['ret']:>+8.2f} {r['final']:>10,.0f} {r['trades']:>7d} "
              f"{r['wr']:>5.1f} {pf:>6} {r['dd']:>6.2f}")
    print("=" * 78)


if __name__ == "__main__":
    main()



def run_all_on(df):
    rows = []
    rows.append(run_generic(df, s_trend, s_trend.StrategyParams(), "trend"))
    rows.append(run_generic(df, s_scalp, s_scalp.ScalpParams(), "scalp"))
    rows.append(run_generic(df, s_pro, s_pro.ScalpProParams(min_atr_points=12.0), "scalp_pro"))
    rows.append(run_ict(df))
    rows.append(run_mmxm(df))
    return {r["name"]: r for r in rows}


def split_check():
    df = load()
    mid = len(df) // 2
    h1, h2 = df.iloc[:mid].reset_index(drop=True), df.iloc[mid:].reset_index(drop=True)
    print("\nROBUSTNESS: return% per half (consistency check)")
    print(f"  half1 {h1['time'].min():%m-%d} -> {h1['time'].max():%m-%d} | "
          f"half2 {h2['time'].min():%m-%d} -> {h2['time'].max():%m-%d}")
    a, b = run_all_on(h1), run_all_on(h2)
    print(f"  {'strategy':<10} {'half1%':>8} {'half2%':>8}")
    for k in ["scalp_pro", "ict", "trend", "mmxm", "scalp"]:
        print(f"  {k:<10} {a[k]['ret']:>+7.2f} {b[k]['ret']:>+7.2f}")


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "split":
        split_check()
