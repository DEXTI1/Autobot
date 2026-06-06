"""
READY-TO-USE scalp config (aggressive DEMO settings).

Almost everything is pre-filled. You only need to do ONE thing:
    -> set MT5_PASSWORD below to your account password.

(The password is intentionally NOT stored here for security - never commit a
real password to GitHub.)

Then run it in its own window:
    python bot.py --config config.scalp.ready.py

WARNING: these are AGGRESSIVE settings (high risk, unlimited trades, big lots).
Great for learning on a DEMO account - do NOT use these numbers on real money.
"""

# ---- ACCOUNT ----
MT5_LOGIN = 260996883
MT5_PASSWORD = "PUT-YOUR-PASSWORD-HERE"   # <-- type your password between the quotes
MT5_SERVER = "Exness-MT5Trial15"
MT5_TERMINAL_PATH = r"C:\MT5\terminal64.exe"

# ---- INSTRUMENT ----
SYMBOL = "BTCUSD"
TIMEFRAME = "M5"

# ---- STRATEGY ----
STRATEGY = "scalp"
FAST_EMA_PERIOD = 20
SLOW_EMA_PERIOD = 50
TREND_EMA_PERIOD = 200
RSI_PERIOD = 14
RSI_LONG_MAX = 70.0
RSI_SHORT_MIN = 30.0
ATR_PERIOD = 14
BB_PERIOD = 20
BB_STD = 2.0
SCALP_RSI_PERIOD = 14
SCALP_RSI_OVERSOLD = 40.0       # higher = more BUY signals
SCALP_RSI_OVERBOUGHT = 60.0     # lower = more SELL signals
SCALP_BAND_TOUCH_FRAC = 0.7     # 0..1: lower = triggers sooner = MORE trades
SCALP_REQUIRE_BOTH = False      # False = band OR RSI triggers = many more trades

# ---- RISK (aggressive, as requested) ----
RISK_PER_TRADE_PCT = 2.0     # bigger lots per trade (was 0.25)
ATR_SL_MULTIPLIER = 1.0
REWARD_RISK_RATIO = 1.0
MAX_OPEN_POSITIONS = 3       # allow several trades at once
MAX_TRADES_PER_DAY = 0       # 0 = UNLIMITED trades
DAILY_MAX_LOSS_PCT = 20.0    # raised daily loss limit
MIN_LOT = 0.01
MAX_LOT = 10.0               # raised lot ceiling -> bigger lots allowed

USE_TRAILING_STOP = True
TRAIL_ATR_MULTIPLIER = 1.5

# ---- TELEGRAM (optional - paste your token/id to enable) ----
TELEGRAM_ENABLED = False
TELEGRAM_BOT_TOKEN = ""
TELEGRAM_CHAT_ID = ""

# ---- RUNTIME ----
POLL_SECONDS = 15
MAGIC_NUMBER = 20260607       # different from the trend bot
LOG_FILE = "bot_scalp.log"
DRY_RUN = False               # actively trades on your DEMO account
