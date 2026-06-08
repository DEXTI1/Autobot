"""
Ablation: show how each upgrade changes scalp_pro on USTEC (NQ=F 5m, ~2 months).

We add one improvement at a time so you can SEE which lever does what, judging
both profitability (return, PF) and safety (max drawdown, return/DD, Sharpe).
"""
from __future__ import annotations

import pandas as pd

import backtest as bt
from risk import RiskManager
from backtest_engine_v2 import BacktesterV2
import strategy_scalp_pro as s_pro
import strategy_scalp_pro_v2 as s_v2

CSV = "ustec_nq_5m.csv"
START = 10_000.0
RISK = 0.5
SPREAD = 1.5
PPY = 252 * 288


def load():
    df = pd.read_csv(CSV)
    df["time"] = pd.to_datetime(df["time"], utc=True)
    return df.sort_values("time").reset_index(drop=True)


def spec():
    return bt.InstrumentSpec(point=1.0, trade_tick_size=1.0, trade_tick_value=1.0,
                             volume_min=0.01, volume_step=0.01, volume_max=100.0,
                             spread_points=SPREAD, commission_per_lot_side=0.0,
                             swap_points_per_night=2.0)


def risk(daily_max=100.0, max_trades=0):
    return RiskManager(risk_per_trade_pct=RISK, atr_sl_multiplier=2.0,
                       reward_risk_ratio=2.0, max_open_positions=1,
                       daily_max_loss_pct=daily_max, min_lot=0.01, max_lot=100.0,
                       max_trades_per_day=max_trades)


def metrics(r):
    m = r.metrics
    ret, dd = m["total_return_pct"], m["max_drawdown_pct"]
    rdd = (ret / dd) if dd > 0 else float("inf")
    return dict(ret=ret, dd=dd, rdd=rdd, tr=m["num_trades"], wr=m["win_rate_pct"],
                pf=m["profit_factor"], sharpe=m["sharpe"])


def run(df, module, params, engine="v1", daily_max=100.0, max_trades=0, **eng):
    rk = risk(daily_max, max_trades)
    common = dict(start_equity=START, use_trailing=True, trail_atr_mult=2.0,
                  periods_per_year=PPY, strategy_module=module)
    if engine == "v1":
        bk = bt.Backtester(spec(), rk, params, **common)
    else:
        bk = BacktesterV2(spec(), rk, params, **common, **eng)
    return metrics(bk.run(df.copy()))


def main():
    df = load()
    print(f"Dataset: {len(df)} 5m bars | {df['time'].min()} -> {df['time'].max()}\n")

    rows = []

    # 0) baseline = original scalp_pro
    rows.append(("0 baseline (scalp_pro)",
                 run(df, s_pro, s_pro.ScalpProParams(min_atr_points=12.0))))

    # 1) + session filter only
    p = s_v2.ScalpProV2Params(use_trend_filter=False, use_slope_confirm=False,
                              max_atr_points=1e9, use_session=True)
    rows.append(("1 + session filter", run(df, s_v2, p)))

    # 2) + HTF trend filter
    p = s_v2.ScalpProV2Params(use_trend_filter=True, use_slope_confirm=False,
                              max_atr_points=1e9, use_session=True)
    rows.append(("2 + HTF trend filter", run(df, s_v2, p)))

    # 3) + slope confirm + ATR band (all strategy filters)
    p = s_v2.ScalpProV2Params()  # all on, sane defaults
    rows.append(("3 + slope + ATR band", run(df, s_v2, p)))

    # 4) + breakeven (engine v2, BE only)
    rows.append(("4 + breakeven@1R",
                 run(df, s_v2, s_v2.ScalpProV2Params(), engine="v2",
                     use_breakeven=True, use_daily_loss_stop=False, use_trade_cap=False)))

    # 5) FULL v2: breakeven + daily-loss stop (3%) + max 6 trades/day
    rows.append(("5 FULL v2 (+guards)",
                 run(df, s_v2, s_v2.ScalpProV2Params(), engine="v2",
                     daily_max=3.0, max_trades=6,
                     use_breakeven=True, use_daily_loss_stop=True, use_trade_cap=True)))

    print("=" * 92)
    print("  USTEC upgrade ablation  (return% | maxDD% | return/DD | trades | win% | PF | Sharpe)")
    print("=" * 92)
    print(f"  {'variant':<26}{'ret%':>8}{'DD%':>8}{'ret/DD':>8}{'trades':>8}{'win%':>7}{'PF':>7}{'Sharpe':>8}")
    print("  " + "-" * 88)
    for name, m in rows:
        pf = "inf" if m["pf"] == float("inf") else f"{m['pf']:.2f}"
        rdd = "inf" if m["rdd"] == float("inf") else f"{m['rdd']:.2f}"
        print(f"  {name:<26}{m['ret']:>+8.2f}{m['dd']:>8.2f}{rdd:>8}{m['tr']:>8d}"
              f"{m['wr']:>7.1f}{pf:>7}{m['sharpe']:>8.2f}")
    print("=" * 92)


if __name__ == "__main__":
    main()



def sweep():
    """Tune reward:risk and trail on FULL v2 to recover return while keeping
    the drawdown/quality gains (winners run, protected by breakeven)."""
    df = load()
    print(f"Dataset: {len(df)} 5m bars | {df['time'].min()} -> {df['time'].max()}\n")
    print("FULL-v2 tuning: let winners run (breakeven + guards always on)")
    print(f"  {'RR':>4}{'trail':>7}{'ret%':>9}{'DD%':>8}{'ret/DD':>8}{'trades':>8}{'win%':>7}{'PF':>7}{'Sharpe':>8}")
    print("  " + "-" * 64)
    best = None
    for rr in (2.0, 2.5, 3.0, 4.0):
        for trail in (2.0, 3.0, 4.0):
            rk = RiskManager(risk_per_trade_pct=RISK, atr_sl_multiplier=2.0,
                             reward_risk_ratio=rr, max_open_positions=1,
                             daily_max_loss_pct=3.0, min_lot=0.01, max_lot=100.0,
                             max_trades_per_day=6)
            bk = BacktesterV2(spec(), rk, s_v2.ScalpProV2Params(),
                              start_equity=START, use_trailing=True, trail_atr_mult=trail,
                              periods_per_year=PPY, strategy_module=s_v2,
                              use_breakeven=True, use_daily_loss_stop=True, use_trade_cap=True)
            m = metrics(bk.run(df.copy()))
            pf = "inf" if m["pf"] == float("inf") else f"{m['pf']:.2f}"
            rdd = "inf" if m["rdd"] == float("inf") else f"{m['rdd']:.2f}"
            print(f"  {rr:>4.1f}{trail:>7.1f}{m['ret']:>+9.2f}{m['dd']:>8.2f}{rdd:>8}"
                  f"{m['tr']:>8d}{m['wr']:>7.1f}{pf:>7}{m['sharpe']:>8.2f}")
            score = m["ret"] / m["dd"] if m["dd"] > 0 else m["ret"]
            if best is None or score > best[0]:
                best = (score, rr, trail, m)
    print("  " + "-" * 64)
    _, rr, trail, m = best
    print(f"  BEST by return/DD: RR={rr} trail={trail} -> ret {m['ret']:+.2f}%  "
          f"DD {m['dd']:.2f}%  win {m['wr']:.1f}%  PF {m['pf']:.2f}")
