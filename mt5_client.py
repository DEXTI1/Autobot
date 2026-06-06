"""
MT5 connection management for Exness.

Wraps the official `MetaTrader5` package (Windows-only) so the rest of the
bot never touches the raw API directly. Handles login, symbol checks,
data fetching and clean shutdown.
"""
from __future__ import annotations

import logging

import MetaTrader5 as mt5
import pandas as pd

log = logging.getLogger(__name__)

# Map our friendly timeframe strings to MT5 constants.
_TIMEFRAMES = {
    "M1": mt5.TIMEFRAME_M1,
    "M5": mt5.TIMEFRAME_M5,
    "M15": mt5.TIMEFRAME_M15,
    "M30": mt5.TIMEFRAME_M30,
    "H1": mt5.TIMEFRAME_H1,
    "H4": mt5.TIMEFRAME_H4,
    "D1": mt5.TIMEFRAME_D1,
}


def timeframe_const(name: str) -> int:
    """Translate a timeframe string (e.g. 'M15') to an MT5 constant."""
    try:
        return _TIMEFRAMES[name.upper()]
    except KeyError as exc:
        raise ValueError(
            f"Unknown timeframe '{name}'. Valid: {list(_TIMEFRAMES)}"
        ) from exc


def connect(login: int, password: str, server: str, terminal_path: str | None = None) -> None:
    """
    Initialize the terminal and log in. Raises RuntimeError on failure.
    """
    kwargs = {"login": login, "password": password, "server": server}
    if terminal_path:
        kwargs["path"] = terminal_path

    if not mt5.initialize(**kwargs):
        code, msg = mt5.last_error()
        raise RuntimeError(f"MT5 initialize() failed ({code}): {msg}")

    account = mt5.account_info()
    if account is None:
        code, msg = mt5.last_error()
        mt5.shutdown()
        raise RuntimeError(f"Login failed ({code}): {msg}")

    log.info(
        "Connected to %s | account=%s | balance=%.2f %s | leverage=1:%s",
        server, account.login, account.balance, account.currency, account.leverage,
    )

    # Loud, deliberate warning so a real account is never a surprise.
    if account.trade_mode != mt5.ACCOUNT_TRADE_MODE_DEMO:
        log.warning("=" * 60)
        log.warning("!! THIS IS NOT A DEMO ACCOUNT - REAL MONEY IS AT RISK !!")
        log.warning("=" * 60)


def ensure_symbol(symbol: str):
    """
    Confirm the symbol exists and is selected in Market Watch.
    Returns the symbol_info object.
    """
    info = mt5.symbol_info(symbol)
    if info is None:
        raise RuntimeError(f"Symbol '{symbol}' not found on this broker.")
    if not info.visible and not mt5.symbol_select(symbol, True):
        raise RuntimeError(f"Could not select symbol '{symbol}' in Market Watch.")
    return mt5.symbol_info(symbol)


def get_rates(symbol: str, timeframe: str, count: int) -> pd.DataFrame:
    """
    Fetch the most recent `count` completed bars as a DataFrame.
    """
    rates = mt5.copy_rates_from_pos(symbol, timeframe_const(timeframe), 0, count)
    if rates is None or len(rates) == 0:
        code, msg = mt5.last_error()
        raise RuntimeError(f"No rate data for {symbol} ({code}): {msg}")
    df = pd.DataFrame(rates)
    df["time"] = pd.to_datetime(df["time"], unit="s")
    return df


def account_info():
    """Return the current account_info object (equity, balance, etc.)."""
    info = mt5.account_info()
    if info is None:
        raise RuntimeError("account_info() returned None - lost connection?")
    return info


def disconnect() -> None:
    """Shut the terminal connection down cleanly."""
    mt5.shutdown()
    log.info("Disconnected from MT5.")
