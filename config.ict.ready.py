"""
READY-TO-USE config for the ICT strategy on GOLD (XAUUSD).

The plan it trades:
    NY open -> 15m FVG continuation OR 5m liquidity sweep sets the bias ->
    drop to 1m, wait for an Inverse FVG (IFVG) in that direction ->
    enter, stop just beyond the 1m swing, take-profit at 1:1.

It reads 15m, 5m AND 1m automatically - you do NOT set TIMEFRAME for the logic
(TIMEFRAME below is only a label). It only hunts during the NY killzone, so it
will sit idle most of the day - that is intentional.

DO ONE THING: set MT5_PASSWORD. (Check the gold symbol in Market Watch.)

Run it:
    python bot.py --config config.ict.ready.py

HONEST NOTE: ICT concepts (FVG/IFVG/sweeps) are discretionary in origin; this is
a mechanical interpretation and does NOT guarantee profit. Keep DRY_RUN=True and
watch it on DEMO first, then backtest before trusting it.
"""

# ---- ACCOUNT ----
MT5_LOGIN = 260996883
MT5_PASSWORD = "PUT-YOUR-PASSWORD-HERE"   # <-- type your password here
MT5_SERVER = "Exness-MT5Trial15"
MT5_TERMINAL_PATH = r"C:\MT5\terminal64.exe"

# ---- INSTRUMENT ----
SYMBOL = "XAUUSDm"   # gold on Exness (check Market Watch; could be "XAUUSD")
TIMEFRAME = "M1"     # label only; ICT reads 15m/5m/1m internally

# ---- STRATEGY ----
STRATEGY = "ict"

# NY open killzone (UTC). 13:30-16:00 UTC = 09:30-12:00 New York (EDT).
# In winter (EST) NY is UTC-5, so use 14:30-17:00 instead.
NY_START_HOUR = 13
NY_START_MIN = 30
NY_END_HOUR = 16
NY_END_MIN = 0

# Structure detection
ICT_SWING_LOOKBACK = 3        # bars each side to qualify a swing high/low
ICT_FVG_MIN_SIZE_ATR = 0.10   # ignore FVGs smaller than this * 1m ATR (noise)
ICT_SWEEP_LOOKBACK_5M = 20    # how far back (5m bars) to look for swept swings
ICT_FVG_LOOKBACK_15M = 20     # how far back (15m bars) to look for an FVG
ICT_IFVG_LOOKBACK_1M = 30     # how far back (1m bars) to find the IFVG
ATR_PERIOD = 14
ICT_REWARD_RISK = 1.0         # 1:1 RR as requested
ICT_STOP_BUFFER_ATR = 0.25    # extra cushion beyond the swing for the stop

# ---- RISK MANAGEMENT ----
RISK_PER_TRADE_PCT = 1.0
MAX_OPEN_POSITIONS = 1
MAX_TRADES_PER_DAY = 0        # 0 = unlimited (within the NY window)
DAILY_MAX_LOSS_PCT = 10.0
MIN_LOT = 0.01
MAX_LOT = 5.0

# ICT sets its own SL/TP prices, so these generic modes stay OFF:
USE_PRICE_DIST = False
SL_PRICE = 0.0
TP_PRICE = 0.0
USE_FIXED_PIPS = False
SL_POINTS = 0.0
TP_POINTS = 0.0
ATR_SL_MULTIPLIER = 1.5
REWARD_RISK_RATIO = 1.0

# ICT manages its own exits at fixed SL/TP; trailing off by default.
USE_TRAILING_STOP = False
TRAIL_ATR_MULTIPLIER = 1.5

# ICT already restricts to the NY killzone, so the generic filters stay OFF
# (the strategy's own session gate handles timing).
USE_NEWS_FILTER = False
USE_SESSION_FILTER = False

# ---- TELEGRAM (optional) ----
TELEGRAM_ENABLED = False
TELEGRAM_BOT_TOKEN = ""
TELEGRAM_CHAT_ID = ""

# ---- RUNTIME ----
POLL_SECONDS = 10            # check often - 1m IFVG entries are time-sensitive
MAGIC_NUMBER = 20260609      # unique to this bot
LOG_FILE = "bot_ict.log"
DRY_RUN = True               # keep True until you trust it, THEN set False
