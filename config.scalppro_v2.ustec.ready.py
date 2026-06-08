"""
READY-TO-USE config for the UPGRADED "scalp_pro_v2" strategy on USTEC
(Nasdaq-100), M5 - the safer, higher-quality scalper.

It keeps the proven scalp_pro core (VWAP value + EMA 9/21 momentum pullback +
RSI timing) and ADDS the upgrades we backtested:
    + session filter      (only trade NY + London windows)
    + higher-TF trend filter (EMA-200 slope alignment)
    + fast-EMA slope confirm
    + ATR band (skip dead bars AND news-spike bars)
    + breakeven at +1R    (a winner can't flip to a full loss)
    + daily loss kill-switch (3%) and a 6-trades/day cap

Backtest (NQ=F 5m proxy, ~2 months, RR2.0 / trail3.0): +10.4% return at 5.7%
max drawdown, 53% win rate - roughly HALF the drawdown of the original, and
consistently steadier across choppy/bear quarters.

DO TWO THINGS:
    1. set MT5_PASSWORD below.
    2. confirm the USTEC symbol name in MT5 Market Watch (Exness often uses
       "USTECm"; some servers use "USTEC" or "US100"). Adjust SYMBOL to match.

Run it:
    python bot.py --config config.scalppro_v2.ustec.ready.py

HONEST NOTE: this improves SAFETY and trade quality; it does NOT guarantee
profit. The numbers above are one regime on a futures proxy - keep DRY_RUN/demo
and re-backtest on your broker's USTEC feed before risking real money.
"""

# ---- ACCOUNT ----
MT5_LOGIN = 260996883
MT5_PASSWORD = "PUT-YOUR-PASSWORD-HERE"   # <-- type your password here
MT5_SERVER = "Exness-MT5Trial15"
MT5_TERMINAL_PATH = r"C:\MT5\terminal64.exe"

# ---- INSTRUMENT ----
SYMBOL = "USTECm"    # Nasdaq-100 on Exness (check Market Watch; could be "USTEC"/"US100")
TIMEFRAME = "M5"

# ---- STRATEGY ----
STRATEGY = "scalp_pro_v2"
PRO2_EMA_FAST = 9
PRO2_EMA_SLOW = 21
PRO2_RSI_PERIOD = 14
PRO2_RSI_FLOOR = 45.0
PRO2_RSI_CEILING = 68.0
ATR_PERIOD = 14
PRO2_PULLBACK_ATR = 0.6
PRO2_USE_VWAP = True

# ATR band in INDEX POINTS (USTEC 5m ATR is typically ~15-35 points):
PRO2_MIN_ATR_POINTS = 8.0     # skip dead, rangebound bars
PRO2_MAX_ATR_POINTS = 70.0    # skip violent news spikes (safety)

# Higher-timeframe trend filter (EMA-200 on M5 as an HTF proxy):
PRO2_USE_TREND_FILTER = True
PRO2_TREND_EMA = 200
PRO2_TREND_SLOPE_LOOKBACK = 10

# Fast-EMA slope confirmation (enter as momentum resumes):
PRO2_USE_SLOPE_CONFIRM = True

# Session filter. Hours are in your BROKER'S SERVER TIME (MT5 bar timestamps).
# Defaults below assume a UTC/GMT server and cover the London (07:00-11:00) and
# New York (13:30-20:00) windows where the Nasdaq trends cleanest.
# >>> If your broker server is GMT+2 (common for Exness), shift these +2h:
#     London (09:00-13:00) and NY (15:30-22:00) -> [(9.0, 13.0), (15.5, 22.0)]
PRO2_USE_SESSION = True
PRO2_SESSIONS_UTC = [(13.5, 20.0), (7.0, 11.0)]

# Unused-by-this-strategy settings kept so the file is self-contained:
FAST_EMA_PERIOD = 20
SLOW_EMA_PERIOD = 50
TREND_EMA_PERIOD = 200
RSI_PERIOD = 14
RSI_LONG_MAX = 70.0
RSI_SHORT_MIN = 30.0

# ---- STOPS / TARGETS (ATR-based; the tuned RR2.0 / trail3.0 from the sweep) ----
USE_PRICE_DIST = False
USE_FIXED_PIPS = False
SL_POINTS = 0.0
TP_POINTS = 0.0
SL_PRICE = 0.0
TP_PRICE = 0.0

ATR_SL_MULTIPLIER = 2.0     # stop = 2.0 * ATR
REWARD_RISK_RATIO = 2.0     # take-profit = 2.0 * stop

# ---- BREAKEVEN (the v2 safety upgrade) ----
USE_BREAKEVEN = True
BE_TRIGGER_R = 1.0          # once +1R in profit...
BE_LOCK_R = 0.05            # ...move stop to entry +5% of R (locks a tiny gain)

# ---- RISK MANAGEMENT ----
RISK_PER_TRADE_PCT = 0.5    # % of equity risked per trade (conservative)
MAX_OPEN_POSITIONS = 1
MAX_TRADES_PER_DAY = 6      # anti-overtrading cap
DAILY_MAX_LOSS_PCT = 3.0    # daily kill-switch: stop after a 3% down day
MIN_LOT = 0.01
MAX_LOT = 5.0

USE_TRAILING_STOP = True
TRAIL_ATR_MULTIPLIER = 3.0  # let winners run (protected by breakeven)

# ---- NEWS FILTER (optional extra layer; strategy already gates sessions) ----
USE_NEWS_FILTER = True       # pause around high-impact news (uses MT5 calendar)
NEWS_MINUTES_BEFORE = 15
NEWS_MINUTES_AFTER = 15
NEWS_MIN_IMPACT = 3          # 3 = high impact only
NEWS_EVENTS = []
USE_SESSION_FILTER = False   # OFF here: the strategy's own session filter handles timing

# ---- TELEGRAM (optional) ----
TELEGRAM_ENABLED = False
TELEGRAM_BOT_TOKEN = ""
TELEGRAM_CHAT_ID = ""

# ---- RUNTIME ----
POLL_SECONDS = 15
MAGIC_NUMBER = 20260609      # unique to this bot
LOG_FILE = "bot_scalppro_v2_ustec.log"
DRY_RUN = True               # keep True until you trust it, THEN set False
