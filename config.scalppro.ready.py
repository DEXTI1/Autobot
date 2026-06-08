"""
READY-TO-USE config for the "scalp_pro" strategy on GOLD (XAUUSD), M5.

This is the higher-quality scalper: VWAP + EMA(9/21) momentum pullback + RSI,
with a volatility gate, FIXED 30-40 pip targets, and a NEWS + SESSION filter
that pauses trading around high-impact news and outside active hours.

DO ONE THING: set MT5_PASSWORD below. (Gold name on Exness is often "XAUUSDm" -
check Market Watch and adjust SYMBOL if needed.)

Run it:
    python bot.py --config config.scalppro.ready.py

HONEST NOTE: this improves entry QUALITY and win odds; it does NOT guarantee
profit, and 50%/day is not a realistic or survivable target. Keep DRY_RUN/demo
until you've backtested it. Risk settings here are moderate on purpose.
"""

# ---- ACCOUNT ----
MT5_LOGIN = 260996883
MT5_PASSWORD = "PUT-YOUR-PASSWORD-HERE"   # <-- type your password here
MT5_SERVER = "Exness-MT5Trial15"
MT5_TERMINAL_PATH = r"C:\MT5\terminal64.exe"

# ---- INSTRUMENT ----
SYMBOL = "XAUUSDm"   # gold on Exness (check Market Watch; could be "XAUUSD")
TIMEFRAME = "M5"

# ---- STRATEGY ----
STRATEGY = "scalp_pro"
PRO_EMA_FAST = 9
PRO_EMA_SLOW = 21
PRO_RSI_PERIOD = 14
PRO_RSI_FLOOR = 45.0       # longs need RSI above this (momentum intact)
PRO_RSI_CEILING = 68.0     # but below this (not overbought)
ATR_PERIOD = 14
PRO_MIN_ATR_POINTS = 80.0  # require this much volatility (points) to trade
PRO_PULLBACK_ATR = 0.6     # how deep a pullback toward fast EMA counts
PRO_USE_VWAP = True

# Unused-by-scalp_pro settings kept so the file is self-contained:
FAST_EMA_PERIOD = 20
SLOW_EMA_PERIOD = 50
TREND_EMA_PERIOD = 200
RSI_PERIOD = 14
RSI_LONG_MAX = 70.0
RSI_SHORT_MIN = 30.0

# ---- FIXED TARGETS ----
# Gold "pips" are confusing and depend on the broker's point size, which can
# make point-based stops come out wrong. The CLEAREST way is to set the stop
# and target DIRECTLY as a price move in dollars:
#   on gold, price 4030 -> 4033 is a $3.00 move = "30 pips" in common terms.
USE_PRICE_DIST = True
SL_PRICE = 3.0   # stop  = $3.00 move  (e.g. 4030.00 -> 4027.00)
TP_PRICE = 4.0   # target = $4.00 move  (e.g. 4030.00 -> 4034.00)   R:R ~ 1.33

# (Old point-based mode - left off. 1 "pip" gold = 10 points, point=0.01.)
USE_FIXED_PIPS = False
SL_POINTS = 300.0
TP_POINTS = 400.0

# ---- RISK MANAGEMENT ----
RISK_PER_TRADE_PCT = 1.0    # % of equity risked per trade
ATR_SL_MULTIPLIER = 1.5     # (used only if USE_FIXED_PIPS = False)
REWARD_RISK_RATIO = 1.3     # (used only if USE_FIXED_PIPS = False)
MAX_OPEN_POSITIONS = 2
MAX_TRADES_PER_DAY = 0      # 0 = unlimited
DAILY_MAX_LOSS_PCT = 25.0   # your requested 25% daily loss cap (aggressive!)
MIN_LOT = 0.01
MAX_LOT = 5.0

USE_TRAILING_STOP = True
TRAIL_ATR_MULTIPLIER = 1.5

# ---- NEWS + SESSION FILTER (improves win odds) ----
USE_NEWS_FILTER = True       # pause around high-impact news (uses MT5 calendar)
NEWS_MINUTES_BEFORE = 15
NEWS_MINUTES_AFTER = 15
NEWS_MIN_IMPACT = 3          # 3 = high impact only
# Optional manual events if MT5 calendar is unavailable (UTC), e.g.:
# NEWS_EVENTS = ["2026-06-09 12:30", "2026-06-10 18:00"]
NEWS_EVENTS = []

USE_SESSION_FILTER = True    # only trade active hours (UTC)
SESSION_START_HOUR = 7       # ~London open
SESSION_END_HOUR = 20        # ~NY afternoon

# ---- TELEGRAM (optional) ----
TELEGRAM_ENABLED = False
TELEGRAM_BOT_TOKEN = ""
TELEGRAM_CHAT_ID = ""

# ---- RUNTIME ----
POLL_SECONDS = 15
MAGIC_NUMBER = 20260608      # unique to this bot
LOG_FILE = "bot_scalppro.log"
DRY_RUN = True               # keep True until you trust it, THEN set False
