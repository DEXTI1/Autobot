"""
Generate an ANNOTATED chart of a real Rejection-Block trade from your data.

It scans the data for a clean BULLISH rejection-block setup (a 1H swing-low
candle with a long lower wick), where price later retraced into the block and
rose toward the opposite (bearish) block, then draws:

    * the 5-minute candles around the event
    * the bullish RB zone (green) with its proximal/distal edges
    * the opposite bearish RB zone (red) used as the target
    * ENTRY (at the proximal), STOP-LOSS (below the block), TARGET lines

Run:
    python plot_rejblock_example.py                 # gold (default)
    python plot_rejblock_example.py ustec_nq_5m.csv # USTEC instead

Output: rejblock_example.png
"""
from __future__ import annotations

import sys
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.dates as mdates

from strategy_rejection_block import RejBlockParams, detect_blocks
import backtest_rejblock as bt


def draw_candles(ax, d):
    for _, r in d.iterrows():
        x = mdates.date2num(r["time"])
        up = r["close"] >= r["open"]
        col = "#26a69a" if up else "#ef5350"
        ax.vlines(x, r["low"], r["high"], color=col, linewidth=0.8, zorder=2)
        lo_body, hi_body = sorted([r["open"], r["close"]])
        ax.add_patch(plt.Rectangle((x - 0.0012, lo_body), 0.0024, max(hi_body - lo_body, 1e-6),
                                   facecolor=col, edgecolor=col, zorder=3))


def main():
    csv = sys.argv[1] if len(sys.argv) > 1 else "xauusd_gc_5m.csv"
    label = "XAUUSD (gold)" if "xau" in csv else "USTEC (Nasdaq-100)"
    df = pd.read_csv(csv)
    df["time"] = pd.to_datetime(df["time"], utc=True).dt.tz_localize(None)
    df = df.sort_values("time").reset_index(drop=True)

    p = RejBlockParams()
    h1 = bt.resample_1h(df)
    blocks = detect_blocks(h1, p)
    bulls = [b for b in blocks if b.kind == "bull"]
    bears = [b for b in blocks if b.kind == "bear"]

    # find a bull block that price retraced into, with an opposite bear block above
    chosen = None
    for b in bulls:
        opp = [x for x in bears if x.proximal > b.proximal and x.known_time <= b.known_time + pd.Timedelta(hours=48)]
        if not opp:
            continue
        target = min(opp, key=lambda x: x.proximal)
        after = df[(df["time"] > b.known_time) & (df["time"] <= b.known_time + pd.Timedelta(hours=48))]
        if after.empty:
            continue
        touched = after[after["low"] <= b.proximal]
        if touched.empty:
            continue
        entry_t = touched["time"].iloc[0]
        post = after[after["time"] >= entry_t]
        if (post["high"] >= target.proximal).any():   # a winner -> nice illustration
            chosen = (b, target, entry_t)
            break
    if chosen is None:
        print("No clean example found; try the other CSV.")
        return

    b, target, entry_t = chosen
    win = df[(df["time"] >= b.formed_time - pd.Timedelta(hours=3)) &
             (df["time"] <= entry_t + pd.Timedelta(hours=10))]

    fig, ax = plt.subplots(figsize=(13, 7))
    draw_candles(ax, win)
    x0, x1 = mdates.date2num(win["time"].iloc[0]), mdates.date2num(win["time"].iloc[-1])

    # bullish RB zone (entry)
    ax.axhspan(b.bottom, b.top, xmin=0, xmax=1, color="#26a69a", alpha=0.15)
    ax.axhline(b.proximal, color="#1b8a7a", lw=1.4, ls="-")
    ax.axhline(b.distal, color="#1b8a7a", lw=1.0, ls=":")
    ax.text(x0, b.proximal, "  BULLISH RB proximal = ENTRY", va="bottom", color="#1b8a7a", fontsize=9, weight="bold")
    ax.text(x0, b.distal, "  block low (distal)", va="top", color="#1b8a7a", fontsize=8)

    # stop loss
    sl = b.distal - p.sl_buffer_frac * b.height
    ax.axhline(sl, color="#d32f2f", lw=1.6, ls="--")
    ax.text(x0, sl, "  STOP-LOSS (just below block)", va="top", color="#d32f2f", fontsize=9, weight="bold")

    # opposite bearish RB (target)
    ax.axhspan(target.bottom, target.top, color="#ef5350", alpha=0.15)
    ax.axhline(target.proximal, color="#b71c1c", lw=1.6, ls="--")
    ax.text(x0, target.proximal, "  TARGET = opposite (bearish) RB", va="bottom", color="#b71c1c", fontsize=9, weight="bold")

    # entry marker
    ax.annotate("ENTRY (5m retrace touches block)",
                xy=(mdates.date2num(entry_t), b.proximal),
                xytext=(mdates.date2num(entry_t), b.proximal - b.height * 2),
                arrowprops=dict(arrowstyle="->", color="black"), fontsize=9)

    rr = (target.proximal - b.proximal) / (b.proximal - sl)
    ax.set_title(f"Rejection-Block trade example — {label}\n"
                 f"Buy at 1H bullish RB, stop below it, target opposite bearish RB  (RR ~ {rr:.1f})",
                 fontsize=12)
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%m-%d %H:%M"))
    ax.set_ylabel("Price")
    ax.grid(alpha=0.25)
    fig.autofmt_xdate()
    fig.tight_layout()
    fig.savefig("rejblock_example.png", dpi=130)
    print("Saved rejblock_example.png  | entry", entry_t,
          f"| entry {b.proximal:.2f}  SL {sl:.2f}  TP {target.proximal:.2f}  RR {rr:.1f}")


if __name__ == "__main__":
    main()
