# Exness MT5 Advanced Trading Bot

An automated trend-following trading bot for **Exness** via **MetaTrader 5**, in Python.
It combines a **trend filter + EMA crossover + RSI confirmation**, sizes positions by
**volatility (ATR)**, manages risk with **stop-loss / take-profit / trailing stops**, and
ships with a **realistic cost-aware backtester** and **walk-forward validation** so you
can measure how it *actually* behaves before risking money.

It defaults to **dry-run on a demo account**.

> ⚠️ **Trading leveraged CFDs can lose money quickly.** This is example software for
> learning and automation, **not financial advice** and **not a guarantee of profit**.
> No strategy wins forever. Test on demo, understand every parameter, risk only what
> you can afford to lose.

---

## How effective is automated trading, honestly?

- A good backtest is **not** proof of future profit. It is very easy to "curve-fit" —
  tune parameters until the past looks great, then lose live. That's the #1 failure mode.
- **Walk-forward validation** (included here) is the main defense: it optimizes on data,
  then tests on *different* data the optimizer never saw.
- **Costs matter enormously** on CFDs: spread + commission + overnight swap. This
  backtester models all three.
- What a bot reliably gives you is **discipline** — no skipped stops, no emotional
  trades, 24/7 monitoring. That makes a disciplined trader, not automatically a
  profitable one. The edge has to come from the strategy, and durable edges are hard.

---

## Project layout

| File | Purpose |
|------|---------|
| `indicators.py` | EMA, SMA, RSI, ATR, MACD (pure pandas; shared live + backtest). |
| `strategy.py` | Advanced signal: trend filter + EMA cross + RSI confirm + ATR. |
| `risk.py` | ATR-based stops, position sizing, daily kill switch, lot caps. |
| `executor.py` | Places/closes orders, modifies SL, trailing stops; honors `DRY_RUN`. |
| `mt5_client.py` | Connect/login, fetch data, account info. |
| `bot.py` | Live loop tying it together. |
| `backtest.py` | Event-driven backtester with spread/commission/swap + metrics. |
| `walkforward.py` | Walk-forward optimization & out-of-sample validation. |
| `config.example.py` | All settings. Copy to `config.py` and fill in. |

## The strategy in one paragraph

Go **long** only when price is **above** the 200-EMA (uptrend), the fast EMA crosses
**above** the slow EMA, and RSI isn't already overbought. Go **short** on the mirror
conditions in a downtrend. Stops are placed `ATR × multiplier` away (so they widen in
volatile markets and tighten in calm ones), take-profit is a `reward:risk` multiple of
that, and a trailing stop locks in profit as price runs. Swap the body of
`strategy.evaluate()` to use your own logic — everything else keeps working.

---

## Platform requirement

The official `MetaTrader5` Python package **only runs on Windows** (it talks to a locally
installed MT5 terminal). Run the **live bot** on a Windows PC or **Windows VPS** (for
24/7). The **backtester and walk-forward tools run anywhere** (Linux/Mac/Windows) since
they don't need MT5.

> On Linux/Mac and want live trading? Use a Windows VPS, or adapt to the cloud-based
> [MetaApi](https://github.com/metaapi/metaapi-python-sdk) SDK.

---

## Setup

### 1. Install dependencies
```bash
pip install -r requirements.txt
```
(`pandas` + `numpy` are enough for backtesting. `MetaTrader5` is only needed for live
trading on Windows.)

### 2. Backtest first (no account needed)
Smoke-test on generated data:
```bash
python backtest.py --synthetic
```
Then on your own data (export OHLC from MT5 as CSV with columns
`time,open,high,low,close`):
```bash
python backtest.py --csv data/XAUUSD_M15.csv
```

### 3. Walk-forward validation (the honesty check)
```bash
python walkforward.py --synthetic
python walkforward.py --csv data/XAUUSD_M15.csv --splits 5
```
Look at the **out-of-sample** column. If those returns are poor or wildly inconsistent,
the strategy isn't robust — do **not** trade it live.

> ⚠️ **Tune the cost model to your account.** Open `backtest.py` → `InstrumentSpec` and
> set `spread_points`, `commission_per_lot_side`, `swap_points_per_night`, and the tick
> value/size to match your real Exness contract specs. Optimistic costs = fake results.

### 4. Connect to Exness (demo) for live testing
1. In the Exness Personal Area, **open a Demo account**; note login, password, server.
2. Install MT5, log in to the demo account.
3. Enable: **Tools → Options → Expert Advisors → "Allow algorithmic trading" → OK.**
4. Configure the bot:
   ```bash
   cp config.example.py config.py
   ```
   Fill in `MT5_LOGIN`, `MT5_PASSWORD`, `MT5_SERVER`. (`config.py` is git-ignored.)

### 5. Run the live bot in dry-run
```bash
python bot.py
```
With `DRY_RUN = True` (default) it logs the orders it *would* place but sends nothing.
Watch `bot.log`. Only when you trust it, set `DRY_RUN = False` while **still on demo**.
Stop anytime with **Ctrl+C**.

---

## Key settings (`config.py`)

| Setting | Meaning |
|---------|---------|
| `SYMBOL` / `TIMEFRAME` | Instrument (e.g. `XAUUSD`, `BTCUSD`) and bar size. |
| `FAST_EMA_PERIOD` / `SLOW_EMA_PERIOD` | Crossover EMAs (default 20/50). |
| `TREND_EMA_PERIOD` | Trend filter EMA (default 200). |
| `RSI_PERIOD` / `RSI_LONG_MAX` / `RSI_SHORT_MIN` | Momentum confirmation. |
| `ATR_PERIOD` | Volatility window for stops & sizing. |
| `RISK_PER_TRADE_PCT` | % of equity risked per trade (default 0.5%). |
| `ATR_SL_MULTIPLIER` | Stop distance = ATR × this. |
| `REWARD_RISK_RATIO` | Take-profit = stop distance × this. |
| `USE_TRAILING_STOP` / `TRAIL_ATR_MULTIPLIER` | Trailing-stop behavior. |
| `MAX_OPEN_POSITIONS` | Cap on simultaneous positions. |
| `DAILY_MAX_LOSS_PCT` | Daily drawdown that triggers the kill switch. |
| `MAX_LOT` | Absolute lot cap — protects against sizing bugs. |
| `DRY_RUN` | `True` = simulate only; `False` = send real orders. |

---

## Safety checklist before going live (real funds)

- [ ] Backtested on **real** historical data with **accurate costs**.
- [ ] **Walk-forward** out-of-sample results are positive and consistent.
- [ ] Ran on **demo** for a meaningful period; live behavior matched the backtest.
- [ ] Understand `XAUUSD`/`BTCUSD` on Exness are **leveraged CFDs** with spreads + swaps.
- [ ] Small `RISK_PER_TRADE_PCT`, sensible `DAILY_MAX_LOSS_PCT`, set `MAX_LOT`.
- [ ] You can monitor and stop the bot (Ctrl+C / VPS access).

## Disclaimer

Provided as-is for educational purposes. You are solely responsible for any trades it
places and any resulting gains or losses. Past/backtested performance does not predict
future results.
