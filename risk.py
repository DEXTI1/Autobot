"""
Risk management - the safety core of the bot.

Pure Python/math: NO MetaTrader5 import, so the exact same risk logic runs in
the live bot AND the backtester. Responsibilities:

    - ATR-based stop distance (volatility-scaled, not fixed pips).
    - Take-profit as a reward:risk multiple of the stop.
    - Position sizing so a stop-out loses ~RISK_PER_TRADE_PCT of equity.
    - Daily max-loss kill switch.
    - Hard cap on number of open positions and absolute lot size.

When anything is uncertain, this module refuses to trade or sizes down.
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from typing import Protocol

log = logging.getLogger(__name__)


class SymbolSpec(Protocol):
    """
    Minimal contract description. MT5's symbol_info satisfies this via duck
    typing; the backtester passes a small stand-in with the same attributes.
    """
    point: float
    trade_tick_value: float
    trade_tick_size: float
    volume_min: float
    volume_step: float
    volume_max: float


@dataclass
class DailyGuard:
    day: str
    start_equity: float
    trades_opened: int = 0   # how many trades this bot opened today


class RiskManager:
    def __init__(
        self,
        risk_per_trade_pct: float,
        atr_sl_multiplier: float,
        reward_risk_ratio: float,
        max_open_positions: int,
        daily_max_loss_pct: float,
        min_lot: float,
        max_lot: float,
        max_trades_per_day: int = 0,   # 0 = unlimited
        use_fixed_pips: bool = False,  # if True, use fixed SL/TP point distances
        sl_points: float = 0.0,        # stop distance in POINTS (fixed-pip mode)
        tp_points: float = 0.0,        # target distance in POINTS (fixed-pip mode)
        point: float = 0.01,          # symbol point size (price per 1 point)
        use_price_dist: bool = False,  # if True, SL/TP given DIRECTLY in price $
        sl_price: float = 0.0,         # stop distance in PRICE units (e.g. 3.0 = $3 on gold)
        tp_price: float = 0.0,         # target distance in PRICE units (e.g. 4.0 = $4 on gold)
    ):
        self.risk_per_trade_pct = risk_per_trade_pct
        self.atr_sl_multiplier = atr_sl_multiplier
        self.reward_risk_ratio = reward_risk_ratio
        self.max_open_positions = max_open_positions
        self.daily_max_loss_pct = daily_max_loss_pct
        self.min_lot = min_lot
        self.max_lot = max_lot
        self.max_trades_per_day = max_trades_per_day
        self.use_fixed_pips = use_fixed_pips
        self.sl_points = sl_points
        self.tp_points = tp_points
        self.point = point
        self.use_price_dist = use_price_dist
        self.sl_price = sl_price
        self.tp_price = tp_price
        self._guard: DailyGuard | None = None

    # -- daily kill switch ---------------------------------------------------
    def update_daily_guard(self, today: str, equity: float) -> None:
        if self._guard is None or self._guard.day != today:
            self._guard = DailyGuard(day=today, start_equity=equity, trades_opened=0)
            log.info("Daily guard set for %s | start equity=%.2f", today, equity)

    # -- daily trade-count limit --------------------------------------------
    def daily_trade_limit_reached(self) -> bool:
        """True if we've already opened the max allowed trades today.
        max_trades_per_day == 0 means unlimited."""
        if self.max_trades_per_day <= 0 or self._guard is None:
            return False
        if self._guard.trades_opened >= self.max_trades_per_day:
            log.info("Daily trade limit reached (%d/%d) - no more trades today.",
                     self._guard.trades_opened, self.max_trades_per_day)
            return True
        return False

    def record_trade_opened(self) -> None:
        """Call this right after a trade is successfully opened."""
        if self._guard is not None:
            self._guard.trades_opened += 1
            log.info("Trades opened today: %d", self._guard.trades_opened)

    def daily_loss_exceeded(self, equity: float) -> bool:
        if self._guard is None:
            return False
        dd = (self._guard.start_equity - equity) / self._guard.start_equity * 100.0
        if dd >= self.daily_max_loss_pct:
            log.warning("DAILY LOSS LIMIT HIT: drawdown %.2f%% >= %.2f%% -> halt.",
                        dd, self.daily_max_loss_pct)
            return True
        return False

    # -- position limit ------------------------------------------------------
    def can_open_new_position(self, open_count: int) -> bool:
        if open_count >= self.max_open_positions:
            log.info("Position cap reached (%d/%d).", open_count, self.max_open_positions)
            return False
        return True

    # -- stop distance & SL/TP prices ---------------------------------------
    def stop_distance(self, atr_value: float) -> float:
        """Stop distance in PRICE units.
        Priority: price-distance mode > fixed-pip mode > ATR-based."""
        if self.use_price_dist and self.sl_price > 0:
            return self.sl_price
        if self.use_fixed_pips and self.sl_points > 0:
            return self.sl_points * self.point
        return atr_value * self.atr_sl_multiplier

    def sl_tp_prices(self, action: str, entry_price: float, atr_value: float) -> tuple[float, float]:
        """
        SL/TP prices.
          - price-distance mode: SL/TP are exact price moves (e.g. 3.0 = $3 on gold)
          - fixed-pip mode: SL=sl_points*point, TP=tp_points*point
          - otherwise: stop = atr*mult, TP = stop*reward_risk
        Returns (sl_price, tp_price).
        """
        sl_dist = self.stop_distance(atr_value)
        if self.use_price_dist and self.tp_price > 0:
            tp_dist = self.tp_price
        elif self.use_fixed_pips and self.tp_points > 0:
            tp_dist = self.tp_points * self.point
        else:
            tp_dist = sl_dist * self.reward_risk_ratio
        if action == "BUY":
            return entry_price - sl_dist, entry_price + tp_dist
        return entry_price + sl_dist, entry_price - tp_dist

    # -- position sizing -----------------------------------------------------
    def calc_lot_size(self, equity: float, stop_distance_price: float, spec: SymbolSpec) -> float:
        """
        Lot size so hitting the stop loses ~risk_per_trade_pct of equity.

            money_risk = equity * risk%/100
            loss_per_lot = (stop_distance / tick_size) * tick_value
            lot = money_risk / loss_per_lot

        Clamped to broker min/step/max and our own MAX_LOT, rounded DOWN.
        """
        if stop_distance_price <= 0:
            log.warning("Non-positive stop distance; using MIN_LOT.")
            return self._clamp_lot(self.min_lot, spec)

        money_risk = equity * (self.risk_per_trade_pct / 100.0)
        tick_value = getattr(spec, "trade_tick_value", 0.0) or 0.0
        tick_size = getattr(spec, "trade_tick_size", 0.0) or getattr(spec, "point", 0.0)
        if tick_value <= 0 or tick_size <= 0:
            log.warning("Missing tick value/size; using MIN_LOT.")
            return self._clamp_lot(self.min_lot, spec)

        loss_per_lot = (stop_distance_price / tick_size) * tick_value
        if loss_per_lot <= 0:
            log.warning("Non-positive loss-per-lot; using MIN_LOT.")
            return self._clamp_lot(self.min_lot, spec)

        raw_lot = money_risk / loss_per_lot
        clamped = self._clamp_lot(raw_lot, spec)
        log.info("Sizing: equity=%.2f risk=%.2f%% money_risk=%.2f loss/lot=%.2f -> lot=%.2f",
                 equity, self.risk_per_trade_pct, money_risk, loss_per_lot, clamped)
        return clamped

    def _clamp_lot(self, lot: float, spec: SymbolSpec) -> float:
        broker_min = getattr(spec, "volume_min", self.min_lot) or self.min_lot
        broker_max = getattr(spec, "volume_max", self.max_lot) or self.max_lot
        step = getattr(spec, "volume_step", 0.01) or 0.01

        lot = max(lot, broker_min)
        lot = min(lot, broker_max, self.max_lot)
        steps = math.floor(lot / step)          # round DOWN so we never over-risk
        lot = max(steps * step, broker_min)
        return round(lot, 2)
