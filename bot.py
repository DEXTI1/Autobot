"""
Main entry point for the Exness MT5 trading bot.

Supports TWO strategies, chosen via the `STRATEGY` setting in the config:
    - "trend"  -> strategy.py        (trend-following EMA crossover + RSI)
    - "scalp"  -> strategy_scalp.py  (Bollinger Band + RSI mean-reversion)

You can run a different config file with --config, which lets you run two bots
side by side (e.g. trend on one account, scalp on another):
    python bot.py                          # uses config.py
    python bot.py --config config.scalp.py # uses a second config

Flow each cycle:
    1. Refresh account equity + daily guard.
    2. If daily loss limit hit -> close everything and stop trading for the day.
    3. Update trailing stops on any open position.
    4. Fetch bars, compute the signal for the selected strategy.
    5. On a flip signal: close opposite positions, then (if allowed) open a new
       ATR-sized position with SL/TP attached.

Defaults to DRY_RUN and a DEMO account. Stop at any time with Ctrl+C.
"""
from __future__ import annotations

import argparse
import importlib.util
import logging
import os
import sys
import time
from datetime import datetime, timezone

import mt5_client
import strategy as strat_trend
import strategy_scalp as strat_scalp
import strategy_scalp_pro as strat_scalp_pro
from executor import OrderExecutor
from news_filter import NewsSessionFilter
from notifier import TelegramNotifier
from risk import RiskManager
from strategy import BUY, SELL, HOLD

# Set in main() once we know which config file to load.
config = None
log = logging.getLogger("bot")


def setup_logging(log_file: str) -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
        handlers=[
            logging.StreamHandler(sys.stdout),
            logging.FileHandler(log_file, encoding="utf-8"),
        ],
    )


def load_config(path: str):
    """Load a config .py file from a path as a module."""
    if not os.path.exists(path):
        sys.exit(f"ERROR: config file '{path}' not found. "
                 f"Copy config.example.py to config.py and fill it in.")
    spec = importlib.util.spec_from_file_location("botconfig", path)
    cfg = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cfg)
    return cfg


def build_strategy():
    """
    Return (signal_fn, params, bars_needed) for the configured strategy.
    All strategies expose latest_signal(df, params[, point]).
    """
    name = getattr(config, "STRATEGY", "trend").lower()
    if name == "scalp_pro":
        params = strat_scalp_pro.ScalpProParams(
            ema_fast=getattr(config, "PRO_EMA_FAST", 9),
            ema_slow=getattr(config, "PRO_EMA_SLOW", 21),
            rsi_period=getattr(config, "PRO_RSI_PERIOD", 14),
            rsi_floor=getattr(config, "PRO_RSI_FLOOR", 45.0),
            rsi_ceiling=getattr(config, "PRO_RSI_CEILING", 68.0),
            atr_period=getattr(config, "ATR_PERIOD", 14),
            min_atr_points=getattr(config, "PRO_MIN_ATR_POINTS", 80.0),
            pullback_atr=getattr(config, "PRO_PULLBACK_ATR", 0.6),
            use_vwap=getattr(config, "PRO_USE_VWAP", True),
        )
        return ("scalp_pro", params, params.warmup + 60)
    if name == "scalp":
        params = strat_scalp.ScalpParams(
            bb_period=getattr(config, "BB_PERIOD", 20),
            bb_std=getattr(config, "BB_STD", 2.0),
            rsi_period=getattr(config, "SCALP_RSI_PERIOD", 14),
            rsi_oversold=getattr(config, "SCALP_RSI_OVERSOLD", 35.0),
            rsi_overbought=getattr(config, "SCALP_RSI_OVERBOUGHT", 65.0),
            atr_period=getattr(config, "ATR_PERIOD", 14),
            band_touch_frac=getattr(config, "SCALP_BAND_TOUCH_FRAC", 0.85),
            require_both=getattr(config, "SCALP_REQUIRE_BOTH", False),
        )
        return ("scalp", params, params.warmup + 50)
    # default: trend
    params = strat_trend.StrategyParams(
        fast_ema=config.FAST_EMA_PERIOD,
        slow_ema=config.SLOW_EMA_PERIOD,
        trend_ema=config.TREND_EMA_PERIOD,
        rsi_period=config.RSI_PERIOD,
        rsi_long_max=config.RSI_LONG_MAX,
        rsi_short_min=config.RSI_SHORT_MIN,
        atr_period=config.ATR_PERIOD,
    )
    return ("trend", params, params.trend_ema + 50)


def compute_signal(strat_name, params, df, point):
    """Dispatch to the right strategy's latest_signal."""
    if strat_name == "scalp_pro":
        return strat_scalp_pro.latest_signal(df, params, point)
    if strat_name == "scalp":
        return strat_scalp.latest_signal(df, params)
    return strat_trend.latest_signal(df, params)


def opposite_open_positions(executor: OrderExecutor, action: str) -> list:
    """Return open positions that point against the new signal."""
    import MetaTrader5 as mt5
    want_type = mt5.POSITION_TYPE_SELL if action == BUY else mt5.POSITION_TYPE_BUY
    return [p for p in executor.open_positions() if p.type == want_type]


def run_cycle(executor, risk, params, spec, notifier, strat_name, bars_needed, nfilter) -> None:
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

    # 2. Signal from the selected strategy.
    df = mt5_client.get_rates(config.SYMBOL, config.TIMEFRAME, bars_needed)
    point = getattr(spec, "point", 0.01)
    sig = compute_signal(strat_name, params, df, point)
    log.info(
        "Signal=%s | price=%.5f atr=%.5f a=%.5f b=%.5f mid=%.5f rsi=%.1f | %s",
        sig.action, sig.price, sig.atr, sig.fast, sig.slow, sig.trend, sig.rsi, sig.reason,
    )

    # 3. Trailing stops on existing positions (uses latest ATR).
    if config.USE_TRAILING_STOP and sig.atr > 0:
        executor.update_trailing_stops(sig.atr, config.TRAIL_ATR_MULTIPLIER)

    if sig.action == HOLD:
        return

    # 3b. News / session filter - skip entries in bad windows.
    if nfilter is not None:
        allowed, reason = nfilter.can_trade()
        if not allowed:
            log.info("Entry blocked: %s.", reason)
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
    if sig.atr <= 0 and not risk.use_fixed_pips:
        log.warning("ATR not ready; skipping entry.")
        return

    sl_price, tp_price = risk.sl_tp_prices(sig.action, sig.price, sig.atr)
    stop_dist = risk.stop_distance(sig.atr)
    lot = risk.calc_lot_size(acct.equity, stop_dist, spec)
    if lot <= 0:
        log.warning("Lot size computed as 0 - skipping trade.")
        return

    result = executor.open_market_order(sig.action, lot, sl_price, tp_price)
    if config.DRY_RUN or result is not None:
        risk.record_trade_opened()
        notifier.trade_opened(sig.action, config.SYMBOL, lot, sig.price, sl_price, tp_price)


def main() -> None:
    global config
    ap = argparse.ArgumentParser(description="Exness MT5 trading bot.")
    ap.add_argument("--config", default="config.py",
                    help="Path to config file (default: config.py).")
    args = ap.parse_args()

    config = load_config(args.config)

    # Separate log file per config so two bots don't overwrite each other's log.
    log_file = getattr(config, "LOG_FILE", "bot.log")
    setup_logging(log_file)

    strategy_name = getattr(config, "STRATEGY", "trend")
    log.info("Starting bot | strategy=%s symbol=%s tf=%s dry_run=%s",
             strategy_name, config.SYMBOL, config.TIMEFRAME, config.DRY_RUN)

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
        strat_name, params, bars_needed = build_strategy()
        point = getattr(spec, "point", 0.01)
        risk = RiskManager(
            risk_per_trade_pct=config.RISK_PER_TRADE_PCT,
            atr_sl_multiplier=config.ATR_SL_MULTIPLIER,
            reward_risk_ratio=config.REWARD_RISK_RATIO,
            max_open_positions=config.MAX_OPEN_POSITIONS,
            daily_max_loss_pct=config.DAILY_MAX_LOSS_PCT,
            min_lot=config.MIN_LOT,
            max_lot=config.MAX_LOT,
            max_trades_per_day=getattr(config, "MAX_TRADES_PER_DAY", 0),
            use_fixed_pips=getattr(config, "USE_FIXED_PIPS", False),
            sl_points=getattr(config, "SL_POINTS", 0.0),
            tp_points=getattr(config, "TP_POINTS", 0.0),
            point=point,
        )
        executor = OrderExecutor(
            symbol=config.SYMBOL,
            magic=config.MAGIC_NUMBER,
            dry_run=config.DRY_RUN,
        )

        # News + session filter (optional; on by default for scalp_pro).
        nfilter = None
        if getattr(config, "USE_NEWS_FILTER", False) or getattr(config, "USE_SESSION_FILTER", False):
            nfilter = NewsSessionFilter(
                use_news_filter=getattr(config, "USE_NEWS_FILTER", True),
                minutes_before=getattr(config, "NEWS_MINUTES_BEFORE", 15),
                minutes_after=getattr(config, "NEWS_MINUTES_AFTER", 15),
                min_impact=getattr(config, "NEWS_MIN_IMPACT", 3),
                manual_events=getattr(config, "NEWS_EVENTS", None),
                use_session_filter=getattr(config, "USE_SESSION_FILTER", True),
                session_start_hour=getattr(config, "SESSION_START_HOUR", 7),
                session_end_hour=getattr(config, "SESSION_END_HOUR", 20),
            )

        notifier.startup(config.SYMBOL, config.TIMEFRAME, config.DRY_RUN)

        while True:
            try:
                run_cycle(executor, risk, params, spec, notifier, strat_name, bars_needed, nfilter)
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
