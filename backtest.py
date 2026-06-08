"""
Event-driven backtester with realistic costs.

Why event-driven (bar by bar) instead of vectorized? Because it models the
things that quietly destroy naive backtests:

    * NO LOOKAHEAD: a signal from bar i's CLOSE is executed at bar i+1's OPEN.
    * SPREAD: you enter/exit at the worse side of the market.
    * COMMISSION: charged per lot, per side.
    * SWAP: overnight financing charged when a position is held across a day.
    * INTRABAR STOPS: SL/TP are checked against each bar's high/low, and when a
      bar could have hit both, we PESSIMISTICALLY assume the stop hit first.

It reuses the SAME strategy and RiskManager as the live bot, so the backtest
reflects how the bot would actually behave.

Run:
    python backtest.py --csv data/XAUUSD_M15.csv
    python backtest.py --synthetic            # generate fake data to smoke-test
"""
from __future__ import annotations

import argparse
import logging
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

import strategy as strat
from risk import RiskManager
from strategy import BUY, SELL, HOLD, StrategyParams

log = logging.getLogger("backtest")


# ---------------------------------------------------------------------------
# Instrument + cost description (stand-in for MT5 symbol_info in backtests)
# ---------------------------------------------------------------------------
@dataclass
class InstrumentSpec:
    """
    Contract details. Defaults are reasonable for XAUUSD on a typical Exness
    account, but ALWAYS check your own account's contract specs and override.
    """
    point: float = 0.01                 # smallest price increment
    trade_tick_size: float = 0.01       # price change per tick
    trade_tick_value: float = 1.0       # $ value of one tick per 1.0 lot
    volume_min: float = 0.01
    volume_step: float = 0.01
    volume_max: float = 100.0
    # cost model
    spread_points: float = 20.0         # typical spread in points (round-trip modeled)
    commission_per_lot_side: float = 0.0  # $ per lot per side
    swap_points_per_night: float = 5.0  # points charged per lot per night held

    @property
    def value_per_price_unit_per_lot(self) -> float:
        """$ P&L for a 1.0-lot position per 1.0 of price movement."""
        return self.trade_tick_value / self.trade_tick_size


@dataclass
class Trade:
    direction: int          # +1 long, -1 short
    entry_time: pd.Timestamp
    entry_price: float
    volume: float
    sl: float
    tp: float
    exit_time: pd.Timestamp | None = None
    exit_price: float | None = None
    pnl: float = 0.0
    reason_open: str = ""
    reason_close: str = ""


@dataclass
class BacktestResult:
    trades: list = field(default_factory=list)
    equity_curve: pd.Series | None = None
    start_equity: float = 0.0
    final_equity: float = 0.0
    metrics: dict = field(default_factory=dict)

    def summary(self) -> str:
        m = self.metrics
        lines = [
            "=" * 52,
            "  BACKTEST RESULTS",
            "=" * 52,
            f"  Start equity      : {self.start_equity:,.2f}",
            f"  Final equity      : {self.final_equity:,.2f}",
            f"  Total return      : {m.get('total_return_pct', 0):+.2f}%",
            f"  Max drawdown      : {m.get('max_drawdown_pct', 0):.2f}%",
            f"  Trades            : {m.get('num_trades', 0)}",
            f"  Win rate          : {m.get('win_rate_pct', 0):.1f}%",
            f"  Profit factor     : {m.get('profit_factor', 0):.2f}",
            f"  Avg trade         : {m.get('avg_trade', 0):+.2f}",
            f"  Sharpe (annual)   : {m.get('sharpe', 0):.2f}",
            f"  Total costs paid  : {m.get('total_costs', 0):,.2f}",
            "=" * 52,
        ]
        return "\n".join(lines)


class Backtester:
    def __init__(
        self,
        spec: InstrumentSpec,
        risk: RiskManager,
        params: StrategyParams,
        start_equity: float = 10_000.0,
        use_trailing: bool = True,
        trail_atr_mult: float = 2.0,
        periods_per_year: int = 35_040,   # M15 bars/year (~96*365); override per TF
        strategy_module=strat,            # pluggable strategy (trend/scalp/scalp_pro)
    ):
        self.spec = spec
        self.risk = risk
        self.params = params
        self.start_equity = start_equity
        self.use_trailing = use_trailing
        self.trail_atr_mult = trail_atr_mult
        self.periods_per_year = periods_per_year
        self.strat = strategy_module

    def _evaluate(self, df, i):
        """Call the strategy's evaluate, passing point if it accepts one."""
        try:
            return self.strat.evaluate(df, i, self.params, self.spec.point)
        except TypeError:
            return self.strat.evaluate(df, i, self.params)

    # -- money helpers -------------------------------------------------------
    def _pnl(self, trade: Trade, exit_price: float) -> float:
        gross = (
            (exit_price - trade.entry_price)
            * trade.direction
            * self.spec.value_per_price_unit_per_lot
            * trade.volume
        )
        return gross

    def _commission(self, volume: float) -> float:
        return self.spec.commission_per_lot_side * volume

    def _half_spread(self) -> float:
        return (self.spec.spread_points * self.spec.point) / 2.0

    # -- main loop -----------------------------------------------------------
    def run(self, df: pd.DataFrame) -> BacktestResult:
        df = self.strat.add_indicators(df, self.params).reset_index(drop=True)
        n = len(df)

        balance = self.start_equity
        position: Trade | None = None
        pending: str | None = None          # action queued for next bar's open
        total_costs = 0.0
        equity_points: list[float] = []
        trades: list[Trade] = []

        for i in range(n):
            bar = df.iloc[i]
            price_open = float(bar["open"])
            high, low = float(bar["high"]), float(bar["low"])
            atr_val = float(bar["atr"]) if not pd.isna(bar["atr"]) else 0.0

            # 1) Execute any pending order at THIS bar's open (no lookahead).
            if pending in (BUY, SELL) and position is None and atr_val > 0:
                direction = 1 if pending == BUY else -1
                # entry suffers half the spread
                fill = price_open + direction * self._half_spread()
                sl, tp = self.risk.sl_tp_prices(pending, fill, atr_val)
                stop_dist = self.risk.stop_distance(atr_val)
                vol = self.risk.calc_lot_size(balance, stop_dist, self.spec)
                comm = self._commission(vol)
                balance -= comm
                total_costs += comm
                position = Trade(
                    direction=direction, entry_time=bar["time"], entry_price=fill,
                    volume=vol, sl=sl, tp=tp, reason_open=f"{pending} @open",
                )
            pending = None

            # 2) Manage an open position against this bar's range.
            if position is not None:
                exit_price = None
                reason = ""
                hit_sl = low <= position.sl <= high or (
                    position.direction == 1 and low <= position.sl
                ) or (position.direction == -1 and high >= position.sl)
                hit_tp = low <= position.tp <= high or (
                    position.direction == 1 and high >= position.tp
                ) or (position.direction == -1 and low <= position.tp)

                # Pessimistic: if both could trigger in one bar, assume SL first.
                if hit_sl:
                    exit_price, reason = position.sl, "stop-loss"
                elif hit_tp:
                    exit_price, reason = position.tp, "take-profit"

                if exit_price is not None:
                    exit_fill = exit_price - position.direction * self._half_spread()
                    pnl = self._pnl(position, exit_fill)
                    comm = self._commission(position.volume)
                    balance += pnl - comm
                    total_costs += comm
                    position.exit_time = bar["time"]
                    position.exit_price = exit_fill
                    position.pnl = pnl - comm
                    position.reason_close = reason
                    trades.append(position)
                    position = None
                else:
                    # 2b) Trailing stop: tighten using this bar's close + ATR.
                    if self.use_trailing and atr_val > 0:
                        close = float(bar["close"])
                        if position.direction == 1:
                            new_sl = close - self.trail_atr_mult * atr_val
                            position.sl = max(position.sl, new_sl)
                        else:
                            new_sl = close + self.trail_atr_mult * atr_val
                            position.sl = min(position.sl, new_sl)

            # 3) Swap charge if a position is held into a new day.
            if position is not None and i + 1 < n:
                today = pd.Timestamp(bar["time"]).date()
                nxt = pd.Timestamp(df.iloc[i + 1]["time"]).date()
                if nxt != today:
                    swap = (self.spec.swap_points_per_night * self.spec.point
                            * self.spec.value_per_price_unit_per_lot * position.volume)
                    balance -= swap
                    total_costs += swap

            # 4) Generate signal on this CLOSED bar -> act next bar.
            sig = self._evaluate(df, i)
            if sig.action in (BUY, SELL):
                # If opposing position open, close it at next open via flip logic:
                if position is not None and (
                    (sig.action == BUY and position.direction == -1)
                    or (sig.action == SELL and position.direction == 1)
                ):
                    # close now at current close (conservative), then queue entry
                    close = float(bar["close"])
                    exit_fill = close - position.direction * self._half_spread()
                    pnl = self._pnl(position, exit_fill)
                    comm = self._commission(position.volume)
                    balance += pnl - comm
                    total_costs += comm
                    position.exit_time = bar["time"]
                    position.exit_price = exit_fill
                    position.pnl = pnl - comm
                    position.reason_close = "flip"
                    trades.append(position)
                    position = None
                if position is None:
                    pending = sig.action

            # 5) Mark-to-market equity for the curve.
            equity = balance
            if position is not None:
                equity += self._pnl(position, float(bar["close"]))
            equity_points.append(equity)

        curve = pd.Series(equity_points, index=df["time"])
        result = BacktestResult(
            trades=trades, equity_curve=curve,
            start_equity=self.start_equity, final_equity=float(curve.iloc[-1]),
        )
        result.metrics = self._metrics(curve, trades, total_costs)
        return result

    # -- metrics -------------------------------------------------------------
    def _metrics(self, curve: pd.Series, trades: list, total_costs: float) -> dict:
        final = float(curve.iloc[-1])
        total_return = (final / self.start_equity - 1.0) * 100.0

        # max drawdown off the equity curve
        running_max = curve.cummax()
        drawdown = (curve - running_max) / running_max
        max_dd = abs(float(drawdown.min())) * 100.0 if len(curve) else 0.0

        pnls = np.array([t.pnl for t in trades], dtype=float)
        wins = pnls[pnls > 0]
        losses = pnls[pnls < 0]
        win_rate = (len(wins) / len(pnls) * 100.0) if len(pnls) else 0.0
        gross_win = wins.sum() if len(wins) else 0.0
        gross_loss = abs(losses.sum()) if len(losses) else 0.0
        profit_factor = (gross_win / gross_loss) if gross_loss > 0 else float("inf") if gross_win > 0 else 0.0
        avg_trade = float(pnls.mean()) if len(pnls) else 0.0

        rets = curve.pct_change().dropna()
        if len(rets) > 1 and rets.std() > 0:
            sharpe = (rets.mean() / rets.std()) * np.sqrt(self.periods_per_year)
        else:
            sharpe = 0.0

        return {
            "total_return_pct": total_return,
            "max_drawdown_pct": max_dd,
            "num_trades": len(trades),
            "win_rate_pct": win_rate,
            "profit_factor": profit_factor,
            "avg_trade": avg_trade,
            "sharpe": float(sharpe),
            "total_costs": total_costs,
        }


# ---------------------------------------------------------------------------
# Data loading + synthetic generator
# ---------------------------------------------------------------------------
def save_equity_chart(result: "BacktestResult", path: str = "equity_curve.png") -> str:
    """
    Render the equity curve (with drawdown shaded) to a PNG. matplotlib is
    imported lazily so the backtester still works without it installed.
    Returns the saved file path.
    """
    try:
        import matplotlib
        matplotlib.use("Agg")  # headless: no display needed (works on a VPS)
        import matplotlib.pyplot as plt
    except ImportError:
        log.warning("matplotlib not installed; skipping chart. `pip install matplotlib`")
        return ""

    curve = result.equity_curve
    running_max = curve.cummax()

    fig, (ax1, ax2) = plt.subplots(
        2, 1, figsize=(11, 7), sharex=True, gridspec_kw={"height_ratios": [3, 1]}
    )

    ax1.plot(curve.index, curve.values, color="#1f77b4", linewidth=1.3, label="Equity")
    ax1.plot(curve.index, running_max.values, color="#2ca02c",
             linewidth=0.8, linestyle="--", alpha=0.7, label="Peak")
    ax1.fill_between(curve.index, curve.values, running_max.values,
                     where=curve.values < running_max.values,
                     color="#d62728", alpha=0.15, label="Drawdown")
    m = result.metrics
    ax1.set_title(
        f"Equity Curve  |  Return {m.get('total_return_pct', 0):+.2f}%  "
        f"|  MaxDD {m.get('max_drawdown_pct', 0):.2f}%  "
        f"|  Trades {m.get('num_trades', 0)}  "
        f"|  PF {m.get('profit_factor', 0):.2f}"
    )
    ax1.set_ylabel("Equity")
    ax1.legend(loc="upper left", fontsize=8)
    ax1.grid(alpha=0.3)

    drawdown_pct = (curve - running_max) / running_max * 100.0
    ax2.fill_between(curve.index, drawdown_pct.values, 0,
                     color="#d62728", alpha=0.4)
    ax2.set_ylabel("Drawdown %")
    ax2.set_xlabel("Time")
    ax2.grid(alpha=0.3)

    fig.tight_layout()
    fig.savefig(path, dpi=120)
    plt.close(fig)
    log.info("Saved equity chart to %s", path)
    return path


def load_csv(path: str) -> pd.DataFrame:
    """
    Load OHLC data. Expects columns: time, open, high, low, close (volume optional).
    `time` may be a string/epoch; it is parsed to datetime.
    """
    df = pd.read_csv(path)
    cols = {c.lower(): c for c in df.columns}
    df = df.rename(columns={cols.get(k, k): k for k in ["time", "open", "high", "low", "close"]})
    df["time"] = pd.to_datetime(df["time"])
    return df.sort_values("time").reset_index(drop=True)


def make_synthetic(bars: int = 6000, seed: int = 7, start_price: float = 2000.0) -> pd.DataFrame:
    """
    Generate fake but plausible OHLC data (random walk with mild trend/regime
    shifts) purely to smoke-test the pipeline. NOT a substitute for real data.
    """
    rng = np.random.default_rng(seed)
    times = pd.date_range("2024-01-01", periods=bars, freq="15min")
    # regime-switching drift to create some trends the strategy can find/lose on
    drift = np.zeros(bars)
    regime_len = 400
    for s in range(0, bars, regime_len):
        drift[s:s + regime_len] = rng.normal(0, 0.04)
    steps = rng.normal(0, 1.0, bars) + drift
    close = start_price + np.cumsum(steps)
    close = np.maximum(close, 1.0)
    open_ = np.concatenate([[start_price], close[:-1]])
    noise = np.abs(rng.normal(0, 0.8, bars))
    high = np.maximum(open_, close) + noise
    low = np.minimum(open_, close) - noise
    return pd.DataFrame({"time": times, "open": open_, "high": high,
                         "low": low, "close": close})


def build_default_components():
    """Construct spec/risk/params from sensible defaults for a quick run."""
    spec = InstrumentSpec()
    params = StrategyParams()
    risk = RiskManager(
        risk_per_trade_pct=0.5,
        atr_sl_multiplier=2.0,
        reward_risk_ratio=2.0,
        max_open_positions=1,
        daily_max_loss_pct=3.0,
        min_lot=0.01,
        max_lot=1.0,
    )
    return spec, risk, params


def main() -> None:
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(message)s")
    ap = argparse.ArgumentParser(description="Backtest the MA/trend/RSI strategy.")
    ap.add_argument("--csv", help="Path to OHLC CSV (time,open,high,low,close).")
    ap.add_argument("--synthetic", action="store_true", help="Use generated data.")
    ap.add_argument("--equity", type=float, default=10_000.0)
    ap.add_argument("--no-trailing", action="store_true", help="Disable trailing stop.")
    ap.add_argument("--chart", nargs="?", const="equity_curve.png", default=None,
                    help="Save an equity-curve PNG (optional path; default equity_curve.png).")
    args = ap.parse_args()

    if args.csv:
        df = load_csv(args.csv)
        print(f"Loaded {len(df)} bars from {args.csv}")
    elif args.synthetic:
        df = make_synthetic()
        print(f"Generated {len(df)} synthetic bars")
    else:
        ap.error("Provide --csv PATH or --synthetic")

    spec, risk, params = build_default_components()
    bt = Backtester(spec, risk, params, start_equity=args.equity,
                    use_trailing=not args.no_trailing)
    result = bt.run(df)
    print(result.summary())

    if args.chart is not None:
        saved = save_equity_chart(result, args.chart)
        if saved:
            print(f"Equity chart saved to: {saved}")


if __name__ == "__main__":
    main()
