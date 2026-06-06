"""
Order execution against MT5.

Places market orders with attached SL/TP, lists/closes positions opened by
this bot (identified by MAGIC_NUMBER), and decodes MT5 return codes into
readable log messages. Respects DRY_RUN: when on, it logs the order it WOULD
have sent but never contacts the broker.
"""
from __future__ import annotations

import logging

import MetaTrader5 as mt5

from strategy import BUY, SELL

log = logging.getLogger(__name__)


class OrderExecutor:
    def __init__(self, symbol: str, magic: int, dry_run: bool, deviation: int = 20):
        self.symbol = symbol
        self.magic = magic
        self.dry_run = dry_run
        self.deviation = deviation  # max slippage in points

    # -- queries -------------------------------------------------------------
    def open_positions(self) -> list:
        """Positions on our symbol that were opened by THIS bot."""
        positions = mt5.positions_get(symbol=self.symbol)
        if positions is None:
            return []
        return [p for p in positions if p.magic == self.magic]

    # -- open ----------------------------------------------------------------
    def open_market_order(self, action: str, lot: float, sl_price: float, tp_price: float):
        """
        Send a market BUY or SELL with attached SL/TP.
        Returns the MT5 result object, or None in dry-run / on failure.
        """
        tick = mt5.symbol_info_tick(self.symbol)
        if tick is None:
            log.error("No tick for %s; cannot determine price.", self.symbol)
            return None

        order_type = mt5.ORDER_TYPE_BUY if action == BUY else mt5.ORDER_TYPE_SELL
        price = tick.ask if action == BUY else tick.bid

        request = {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": self.symbol,
            "volume": lot,
            "type": order_type,
            "price": price,
            "sl": sl_price,
            "tp": tp_price,
            "deviation": self.deviation,
            "magic": self.magic,
            "comment": "ma-crossover-bot",
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": mt5.ORDER_FILLING_IOC,
        }

        if self.dry_run:
            log.info(
                "[DRY_RUN] Would send %s %.2f lots @ %.5f SL=%.5f TP=%.5f",
                action, lot, price, sl_price, tp_price,
            )
            return None

        result = mt5.order_send(request)
        self._log_result(action, lot, result)
        return result

    # -- close ---------------------------------------------------------------
    def close_position(self, position) -> None:
        """Close a single open position with an opposite market order."""
        tick = mt5.symbol_info_tick(position.symbol)
        if tick is None:
            log.error("No tick for %s; cannot close position %s.", position.symbol, position.ticket)
            return

        is_buy = position.type == mt5.POSITION_TYPE_BUY
        close_type = mt5.ORDER_TYPE_SELL if is_buy else mt5.ORDER_TYPE_BUY
        price = tick.bid if is_buy else tick.ask

        request = {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": position.symbol,
            "volume": position.volume,
            "type": close_type,
            "position": position.ticket,
            "price": price,
            "deviation": self.deviation,
            "magic": self.magic,
            "comment": "ma-crossover-bot close",
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": mt5.ORDER_FILLING_IOC,
        }

        if self.dry_run:
            log.info("[DRY_RUN] Would close position %s (%.2f lots).",
                     position.ticket, position.volume)
            return

        result = mt5.order_send(request)
        self._log_result("CLOSE", position.volume, result)

    def close_all(self) -> None:
        """Close every position opened by this bot (used by the kill switch)."""
        for pos in self.open_positions():
            self.close_position(pos)

    # -- modify / trailing stop ---------------------------------------------
    def modify_sl(self, position, new_sl: float) -> None:
        """Move a position's stop-loss (keeps existing take-profit)."""
        request = {
            "action": mt5.TRADE_ACTION_SLTP,
            "symbol": position.symbol,
            "position": position.ticket,
            "sl": new_sl,
            "tp": position.tp,
            "magic": self.magic,
        }
        if self.dry_run:
            log.info("[DRY_RUN] Would move SL of position %s to %.5f.",
                     position.ticket, new_sl)
            return
        result = mt5.order_send(request)
        self._log_result("MODIFY_SL", position.volume, result)

    def update_trailing_stops(self, atr_value: float, trail_atr_mult: float) -> None:
        """
        Tighten the stop on each open position using ATR * multiplier from the
        latest close. Only ever moves the stop in the favorable direction
        (never loosens it), locking in profit as price runs.
        """
        if atr_value <= 0:
            return
        tick = mt5.symbol_info_tick(self.symbol)
        if tick is None:
            return
        trail_dist = trail_atr_mult * atr_value

        for pos in self.open_positions():
            if pos.type == mt5.POSITION_TYPE_BUY:
                candidate = tick.bid - trail_dist
                if candidate > pos.sl:
                    self.modify_sl(pos, candidate)
            else:  # SELL
                candidate = tick.ask + trail_dist
                if candidate < pos.sl or pos.sl == 0.0:
                    self.modify_sl(pos, candidate)

    # -- helpers -------------------------------------------------------------
    def _log_result(self, action: str, lot: float, result) -> None:
        if result is None:
            code, msg = mt5.last_error()
            log.error("order_send returned None for %s (%s): %s", action, code, msg)
            return
        if result.retcode == mt5.TRADE_RETCODE_DONE:
            log.info("OK %s %.2f lots | deal=%s price=%.5f", action, lot, result.deal, result.price)
        else:
            log.error(
                "FAILED %s %.2f lots | retcode=%s comment=%s",
                action, lot, result.retcode, result.comment,
            )
