"""
SCALP bot configuration - run this alongside the trend bot on a SECOND account.

HOW TO USE:
    1. Copy this file to `config.scalp.py`:  copy config.scalp.example.py config.scalp.py
    2. Fill in your SECOND demo account credentials below.
    3. Run it in its own window:  python bot.py --config config.scalp.py

This is set up so it never clashes with the trend bot:
    - STRATEGY    = "scalp"      (Bollinger Band + RSI mean-reversion)
    - MAGIC_NUMBER is different  (so each bot only manages its own trades)
    - LOG_FILE    is different   (separate log)
    - MT5_TERMINAL_PATH points to the SECOND MT5 install

NEVER commit config.scalp.py with real credentials (it is git-ignored).
"""

# ---------------------------------------------------------------------------
# ACCOUNT (use your SECOND DEMO account)
# ---------------------------------------------------------------------------
MT5_LOGIN = 0000000               # your SECOND demo account number (int)
MT5_PASSWORD = "your-password"    # that account's password
MT5_SERVER = "Exness-MT5Trial"    # that account's server name

# Point to the SECOND MT5 install you set up:
MT5_TERMINAL_PATH = r"C:\MT5\terminal64.exe"

# ---------------------------------------------------------------------------
# INSTRUMENT & TIMEFRAME
# ---------------------------------------------------------------------------
SYMBOL = "BTCUSD"   # 24/7 crypto - good for scalping anytime
TIMEFRAME = "M5"    # M5 (calmer) or M1 (very active)

# ---------------------------------------------------------------------------
# STRATEGY SELECTION
# ---------------------------------------------------------------------------
STRATEGY = "scalp"

# Trend settings are unused in scalp mode but kept so the file is self-contained.
FAST_EMA_PERIOD = 20
SLOW_EMA_PERIOD = 50
TREND_EMA_PERIOD = 200
RSI_PERIOD = 14
RSI_LONG_MAX = 70.0
RSI_SHORT_MIN = 30.0
ATR_PERIOD = 14

# ---------------------------------------------------------------------------
# SCALP STRATEGY settings
# ---------------------------------------------------------------------------
BB_PERIOD = 20
BB_STD = 2.0
SCALP_RSI_PERIOD = 14
SCALP_RSI_OVERSOLD = 35.0
SCALP_RSI_OVERBOUGHT = 65.0
SCALP_BAND_TOUCH_FRAC = 0.85    # 0..1: lower = triggers sooner = more trades
SCALP_REQUIRE_BOTH = False      # False = band OR RSI triggers (more trades)

# ---------------------------------------------------------------------------
# RISK MANAGEMENT  (tighter for scalping)
# ---------------------------------------------------------------------------
RISK_PER_TRADE_PCT = 0.25   # small risk - scalps are frequent
ATR_SL_MULTIPLIER = 1.0     # tight stop for quick scalps
REWARD_RISK_RATIO = 1.0     # 1:1 - aim for many small wins
MAX_OPEN_POSITIONS = 1
MAX_TRADES_PER_DAY = 0      # 0 = unlimited
DAILY_MAX_LOSS_PCT = 3.0
MIN_LOT = 0.01
MAX_LOT = 1.0

USE_TRAILING_STOP = True
TRAIL_ATR_MULTIPLIER = 1.5

# ---------------------------------------------------------------------------
# TELEGRAM ALERTS (optional - can reuse the same bot token/chat as the trend bot)
# ---------------------------------------------------------------------------
TELEGRAM_ENABLED = False
TELEGRAM_BOT_TOKEN = ""
TELEGRAM_CHAT_ID = ""

# ---------------------------------------------------------------------------
# RUNTIME
# ---------------------------------------------------------------------------
POLL_SECONDS = 15           # check more often on a fast timeframe
MAGIC_NUMBER = 20260607     # DIFFERENT from the trend bot (20260606)
LOG_FILE = "bot_scalp.log"  # separate log file
DRY_RUN = True              # keep True until you trust it
