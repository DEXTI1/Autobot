"""
Main entry point for the Exness MT5 advanced trend-following bot.

Flow each cycle:
    1. Refresh account equity + daily guard.
    2. If daily loss limit hit -> close everything and stop trading for the day.
    3. Update trailing stops on any open position.
    4. Fetch bars, compute the advanced signal (trend + cross + RSI).
    5. On a flip signal: close opposite positions, then (if allowed) open a new
       ATR-sized position with SL/TP attached.

Defaults to DRY_RUN and a DEMO account. Read the README before going live.
Stop the bot at any time with Ctrl+C.
"""
from __future__ import annotations

import logging
import sys
import time
from datetime import datetime, timezone

import mt5_client
from executor import OrderExecutor
from notifier import TelegramNotifier
from risk import RiskManager
from strategy import BUY, SELL, HOLD, StrategyParams, latest_signal

try:
    import config
except ImportError:
    sys.exit("ERROR: config.py not found. Copy config.example.py to config.py and fill it in.")


def setup_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
        handlers=[
            logging.StreamHandler(sys.stdout),
            logging.FileHandler("bot.log", encoding="utf-8"),
        ],
    )


log = logging.getLogger("bot")


def build_params() -> StrategyParams:
    return StrategyParams(
        fast_ema=config.FAST_EMA_PERIOD,
        slow_ema=config.SLOW_EMA_PERIOD,
        trend_ema=config.TREND_EMA_PERIOD,
        rsi_period=config.RSI_PERIOD,
        rsi_long_max=config.RSI_LONG_MAX,
        rsi_short_min=config.RSI_SHORT_MIN,
        atr_period=config.ATR_PERIOD,
    )


def opposite_open_positions(executor: OrderExecutor, action: str) -> list:
    """Return open positions that point against the new signal."""
    import MetaTrader5 as mt5
    want_type = mt5.POSITION_TYPE_SELL if action == BUY else mt5.POSITION_TYPE_BUY
    return [p for p in executor.open_positions() if p.type == want_type]


def run_cycle(executor: OrderExecutor, risk: RiskManager, params: StrategyParams,
              spec, notifier: TelegramNotifier) -> None:
    acct = mt5_client.account_info()
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    risk.update_daily_guard(today, acct.equity)

    # 1. Kill switch first.
    if risk.daily_loss_exceeded(acct.equity):
        if executor.open_positions():
            log.warning("Kill switch: closing all bot positions.")
            executor.close_all()
            notifier.kill_switch(acct.equity)
        return

    # 2. Signal (advanced strategy needs enough warmup for the trend EMA).
    bars_needed = params.trend_ema + 50
    df = mt5_client.get_rates(config.SYMBOL, config.TIMEFRAME, bars_needed)
    sig = latest_signal(df, params)
    log.info(
        "Signal=%s | price=%.5f atr=%.5f fast=%.5f slow=%.5f trend=%.5f rsi=%.1f | %s",
        sig.action, sig.price, sig.atr, sig.fast, sig.slow, sig.trend, sig.rsi, sig.reason,
    )

    # 3. Trailing stops on existing positions (uses latest ATR).
    if config.USE_TRAILING_STOP and sig.atr > 0:
        executor.update_trailing_stops(sig.atr, config.TRAIL_ATR_MULTIPLIER)

    if sig.action == HOLD:
        return

    # 4. On a flip, exit positions facing the wrong way first.
    against = opposite_open_positions(executor, sig.action)
    if against:
        log.info("Closing %d opposing position(s) before flipping to %s.", len(against), sig.action)
        for pos in against:
            executor.close_position(pos)
            notifier.trade_closed(config.SYMBOL, "flip", pos.volume)

    # 5. Open a new position if risk rules allow.
    if risk.daily_trade_limit_reached():
        return
    if not risk.can_open_new_position(len(executor.open_positions())):
        return
    if sig.atr <= 0:
        log.warning("ATR not ready; skipping entry.")
        return

    sl_price, tp_price = risk.sl_tp_prices(sig.action, sig.price, sig.atr)
    stop_dist = risk.stop_distance(sig.atr)
    lot = risk.calc_lot_size(acct.equity, stop_dist, spec)
    if lot <= 0:
        log.warning("Lot size computed as 0 - skipping trade.")
        return

    result = executor.open_market_order(sig.action, lot, sl_price, tp_price)
    # Notify when an order was actually placed (or would be, in dry-run).
    if config.DRY_RUN or result is not None:
        risk.record_trade_opened()
        notifier.trade_opened(sig.action, config.SYMBOL, lot, sig.price, sl_price, tp_price)


def main() -> None:
    setup_logging()
    log.info("Starting advanced bot | symbol=%s tf=%s dry_run=%s",
             config.SYMBOL, config.TIMEFRAME, config.DRY_RUN)

    mt5_client.connect(
        login=config.MT5_LOGIN,
        password=config.MT5_PASSWORD,
        server=config.MT5_SERVER,
        terminal_path=config.MT5_TERMINAL_PATH,
    )

    notifier = TelegramNotifier(
        enabled=config.TELEGRAM_ENABLED,
        token=config.TELEGRAM_BOT_TOKEN,
        chat_id=config.TELEGRAM_CHAT_ID,
    )

    try:
        spec = mt5_client.ensure_symbol(config.SYMBOL)
        params = build_params()
        risk = RiskManager(
            risk_per_trade_pct=config.RISK_PER_TRADE_PCT,
            atr_sl_multiplier=config.ATR_SL_MULTIPLIER,
            reward_risk_ratio=config.REWARD_RISK_RATIO,
            max_open_positions=config.MAX_OPEN_POSITIONS,
            daily_max_loss_pct=config.DAILY_MAX_LOSS_PCT,
            min_lot=config.MIN_LOT,
            max_lot=config.MAX_LOT,
            max_trades_per_day=getattr(config, "MAX_TRADES_PER_DAY", 0),
        )
        executor = OrderExecutor(
            symbol=config.SYMBOL,
            magic=config.MAGIC_NUMBER,
            dry_run=config.DRY_RUN,
        )

        notifier.startup(config.SYMBOL, config.TIMEFRAME, config.DRY_RUN)

        while True:
            try:
                run_cycle(executor, risk, params, spec, notifier)
            except Exception as exc:  # never let one bad cycle kill the bot
                log.exception("Error during cycle - continuing after pause.")
                notifier.error(str(exc))
            time.sleep(config.POLL_SECONDS)

    except KeyboardInterrupt:
        log.info("Stopped by user (Ctrl+C).")
    finally:
        notifier.shutdown()
        mt5_client.disconnect()


if __name__ == "__main__":
    main()
