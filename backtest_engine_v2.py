"""
BacktesterV2 - the base event-driven Backtester plus three safety features the
original engine doesn't model:

  1. BREAKEVEN after +1R : once a trade is +1R in our favour, the stop is moved
     to (just past) entry, so a winner can't turn into a full loser. This is the
     biggest single reducer of drawdown / give-back.
  2. DAILY LOSS KILL-SWITCH : if realised equity drops more than
     daily_max_loss_pct from the day's start, no NEW trades are opened that day
     (open trades still run to their stop). Caps bad-day damage.
  3. MAX TRADES / DAY : optional hard cap on entries per day (anti-overtrading).

It reuses the parent's cost model (spread, commission, swap), no-lookahead
execution, intrabar pessimistic stops, trailing stop, and metrics.
"""
from __future__ import annotations

import pandas as pd

from backtest import Backtester, Trade
from strategy import BUY, SELL


class BacktesterV2(Backtester):
    def __init__(self, *args,
                 use_breakeven: bool = True,
                 be_trigger_r: float = 1.0,    # move to BE once +1R in profit
                 be_lock_r: float = 0.05,      # lock this fraction of R at BE
                 use_daily_loss_stop: bool = True,
                 use_trade_cap: bool = True,
                 **kwargs):
        super().__init__(*args, **kwargs)
        self.use_breakeven = use_breakeven
        self.be_trigger_r = be_trigger_r
        self.be_lock_r = be_lock_r
        self.use_daily_loss_stop = use_daily_loss_stop
        self.use_trade_cap = use_trade_cap

    def run(self, df: pd.DataFrame):
        df = self.strat.add_indicators(df, self.params).reset_index(drop=True)
        n = len(df)

        balance = self.start_equity
        position: Trade | None = None
        pending: str | None = None
        total_costs = 0.0
        equity_points: list[float] = []
        trades: list[Trade] = []

        for i in range(n):
            bar = df.iloc[i]
            price_open = float(bar["open"])
            high, low = float(bar["high"]), float(bar["low"])
            atr_val = float(bar["atr"]) if not pd.isna(bar["atr"]) else 0.0
            today = str(pd.Timestamp(bar["time"]).date())

            # roll the daily guard (resets start-equity each new day)
            self.risk.update_daily_guard(today, balance)

            # 1) Execute pending entry at THIS open (no lookahead).
            if pending in (BUY, SELL) and position is None and atr_val > 0:
                direction = 1 if pending == BUY else -1
                fill = price_open + direction * self._half_spread()
                sl, tp = self.risk.sl_tp_prices(pending, fill, atr_val)
                stop_dist = self.risk.stop_distance(atr_val)
                vol = self.risk.calc_lot_size(balance, stop_dist, self.spec)
                comm = self._commission(vol)
                balance -= comm
                total_costs += comm
                position = Trade(direction=direction, entry_time=bar["time"],
                                 entry_price=fill, volume=vol, sl=sl, tp=tp,
                                 reason_open=f"{pending} @open")
                position.r_dist = stop_dist      # remember 1R distance
                position.be_done = False
                self.risk.record_trade_opened()
            pending = None

            # 2) Manage an open position.
            if position is not None:
                exit_price, reason = None, ""
                hit_sl = (position.direction == 1 and low <= position.sl) or \
                         (position.direction == -1 and high >= position.sl)
                hit_tp = (position.direction == 1 and high >= position.tp) or \
                         (position.direction == -1 and low <= position.tp)
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
                    # 2a) BREAKEVEN: once +be_trigger_r R in favour, lock stop at entry.
                    if self.use_breakeven and not position.be_done and position.r_dist > 0:
                        if position.direction == 1:
                            favour = high - position.entry_price
                            if favour >= self.be_trigger_r * position.r_dist:
                                be = position.entry_price + self.be_lock_r * position.r_dist
                                position.sl = max(position.sl, be)
                                position.be_done = True
                        else:
                            favour = position.entry_price - low
                            if favour >= self.be_trigger_r * position.r_dist:
                                be = position.entry_price - self.be_lock_r * position.r_dist
                                position.sl = min(position.sl, be)
                                position.be_done = True
                    # 2b) Trailing stop (only ever tightens).
                    if self.use_trailing and atr_val > 0:
                        close = float(bar["close"])
                        if position.direction == 1:
                            position.sl = max(position.sl, close - self.trail_atr_mult * atr_val)
                        else:
                            position.sl = min(position.sl, close + self.trail_atr_mult * atr_val)

            # 3) Swap on day change.
            if position is not None and i + 1 < n:
                if str(pd.Timestamp(df.iloc[i + 1]["time"]).date()) != today:
                    swap = (self.spec.swap_points_per_night * self.spec.point
                            * self.spec.value_per_price_unit_per_lot * position.volume)
                    balance -= swap
                    total_costs += swap

            # 4) Signal -> queue next-bar entry, subject to daily guards.
            sig = self._evaluate(df, i)
            if sig.action in (BUY, SELL):
                if position is not None and (
                    (sig.action == BUY and position.direction == -1)
                    or (sig.action == SELL and position.direction == 1)
                ):
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
                # daily guards gate NEW entries only
                blocked = False
                if self.use_daily_loss_stop and self.risk.daily_loss_exceeded(balance):
                    blocked = True
                if self.use_trade_cap and self.risk.daily_trade_limit_reached():
                    blocked = True
                if position is None and not blocked:
                    pending = sig.action

            # 5) Mark-to-market equity.
            equity = balance
            if position is not None:
                equity += self._pnl(position, float(bar["close"]))
            equity_points.append(equity)

        from backtest import BacktestResult
        curve = pd.Series(equity_points, index=df["time"])
        result = BacktestResult(trades=trades, equity_curve=curve,
                                start_equity=self.start_equity,
                                final_equity=float(curve.iloc[-1]))
        result.metrics = self._metrics(curve, trades, total_costs)
        return result
