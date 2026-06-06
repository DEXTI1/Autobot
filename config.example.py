"""
Configuration for the Exness MT5 advanced trading bot.

HOW TO USE:
    1. Copy this file to `config.py`:  cp config.example.py config.py
    2. Fill in your Exness DEMO account credentials.
    3. NEVER commit config.py with real credentials (it is git-ignored).

Get demo credentials from the Exness Personal Area -> open a "Demo" account.
"""

# ---------------------------------------------------------------------------
# ACCOUNT (use a DEMO account first!)
# ---------------------------------------------------------------------------
MT5_LOGIN = 0000000              # your Exness demo account number (int)
MT5_PASSWORD = "your-password"   # the account password
MT5_SERVER = "Exness-MT5Trial"   # demo server name shown in MT5 login dialog

# Optional: full path to terminal64.exe. Leave as None to auto-detect.
#   r"C:\Program Files\MetaTrader 5 EXNESS\terminal64.exe"
MT5_TERMINAL_PATH = None

# ---------------------------------------------------------------------------
# INSTRUMENT & TIMEFRAME
# ---------------------------------------------------------------------------
SYMBOL = "XAUUSD"   # gold vs USD CFD on Exness. For BTC try "BTCUSD".
TIMEFRAME = "M15"   # one of: M1, M5, M15, M30, H1, H4, D1

# ---------------------------------------------------------------------------
# STRATEGY (trend filter + EMA crossover + RSI confirmation)
# ---------------------------------------------------------------------------
FAST_EMA_PERIOD = 20    # fast EMA (bars)
SLOW_EMA_PERIOD = 50    # slow EMA (bars)
TREND_EMA_PERIOD = 200  # long-term trend filter: only trade with the trend
RSI_PERIOD = 14
RSI_LONG_MAX = 70.0     # don't BUY if RSI already >= this (overbought)
RSI_SHORT_MIN = 30.0    # don't SELL if RSI already <= this (oversold)
ATR_PERIOD = 14         # volatility measure used for stops & sizing

# ---------------------------------------------------------------------------
# RISK MANAGEMENT  (all safety rails live here)
# ---------------------------------------------------------------------------
RISK_PER_TRADE_PCT = 0.5    # risk this % of account equity per trade
ATR_SL_MULTIPLIER = 2.0     # stop-loss distance = ATR * this (volatility-scaled)
REWARD_RISK_RATIO = 2.0     # take-profit distance = stop distance * this
MAX_OPEN_POSITIONS = 1      # never hold more than this many positions at once
MAX_TRADES_PER_DAY = 0      # max NEW trades opened per day (0 = unlimited). Set to 1 for one trade/day.
DAILY_MAX_LOSS_PCT = 3.0    # if equity drops this % below today's start -> STOP
MIN_LOT = 0.01              # broker minimum lot fallback
MAX_LOT = 1.0               # hard cap on position size, safety against bugs

# Trailing stop: tighten the stop as price moves in your favor.
USE_TRAILING_STOP = True
TRAIL_ATR_MULTIPLIER = 2.0  # trailing distance = ATR * this

# ---------------------------------------------------------------------------
# TELEGRAM ALERTS (optional - leave disabled if you don't want notifications)
# ---------------------------------------------------------------------------
TELEGRAM_ENABLED = False         # set True to receive alerts on your phone
TELEGRAM_BOT_TOKEN = ""          # from @BotFather (e.g. "123456:ABC-DEF...")
TELEGRAM_CHAT_ID = ""            # your chat id (see notifier.py setup notes)

# ---------------------------------------------------------------------------
# RUNTIME
# ---------------------------------------------------------------------------
POLL_SECONDS = 30           # how often the main loop checks the market
MAGIC_NUMBER = 20260606     # identifies orders placed by THIS bot
DRY_RUN = True              # True = log decisions but DO NOT place real orders
