"""
News + trading-session filter.

Two protections that genuinely improve a scalper's win probability:

1. NEWS BLACKOUT: around high-impact economic releases, spreads blow out and
   price whipsaws - stops get hit by noise, not direction. Professionals AVOID
   trading then. This pauses entries for a window before/after each event.

   Data source: MetaTrader5's BUILT-IN economic calendar when available
   (mt5.calendar_*). No external API or key needed. If the calendar isn't
   available (older terminal / not Windows), you can supply events manually in
   config as NEWS_EVENTS = ["2026-06-08 12:30", ...] (UTC), or disable it.

   Reference: trading through high-impact news is a known way to get stopped by
   spread widening rather than real movement, so filtering it out helps.

2. SESSION FILTER: gold (XAUUSD) moves best during the London/New York hours.
   Outside active hours, ranges are thin and scalps die on spread. This blocks
   entries outside the configured UTC hour window.

NOTE: This does NOT predict the market from news. It avoids the worst moments.
Avoiding bad trades is a real edge; predicting news direction is not reliable.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

log = logging.getLogger("news_filter")


class NewsSessionFilter:
    def __init__(
        self,
        use_news_filter: bool = True,
        minutes_before: int = 15,
        minutes_after: int = 15,
        min_impact: int = 3,                 # 3 = high impact only
        currencies: tuple = ("USD", "XAU", "ALL"),
        manual_events: list | None = None,   # list of "YYYY-MM-DD HH:MM" UTC
        use_session_filter: bool = True,
        session_start_hour: int = 7,         # UTC - London open-ish
        session_end_hour: int = 20,          # UTC - covers London + NY
    ):
        self.use_news_filter = use_news_filter
        self.minutes_before = minutes_before
        self.minutes_after = minutes_after
        self.min_impact = min_impact
        self.currencies = tuple(c.upper() for c in currencies)
        self.use_session_filter = use_session_filter
        self.session_start_hour = session_start_hour
        self.session_end_hour = session_end_hour

        # Parse any manually-supplied events.
        self._manual_events = []
        for s in (manual_events or []):
            try:
                dt = datetime.strptime(s, "%Y-%m-%d %H:%M").replace(tzinfo=timezone.utc)
                self._manual_events.append(dt)
            except ValueError:
                log.warning("Bad NEWS_EVENTS entry ignored: %r (use 'YYYY-MM-DD HH:MM' UTC)", s)

    # -- session -------------------------------------------------------------
    def in_session(self, now: datetime | None = None) -> bool:
        if not self.use_session_filter:
            return True
        now = now or datetime.now(timezone.utc)
        h = now.hour
        if self.session_start_hour <= self.session_end_hour:
            return self.session_start_hour <= h < self.session_end_hour
        # window wraps past midnight
        return h >= self.session_start_hour or h < self.session_end_hour

    # -- news ----------------------------------------------------------------
    def _upcoming_mt5_events(self, now: datetime) -> list:
        """Pull high-impact events near `now` from MT5's built-in calendar."""
        try:
            import MetaTrader5 as mt5
        except Exception:
            return []
        try:
            frm = now - timedelta(minutes=self.minutes_after + 1)
            to = now + timedelta(minutes=self.minutes_before + 1)
            values = mt5.calendar_value_history(frm, to)  # type: ignore[attr-defined]
            if not values:
                return []
            events = []
            for v in values:
                # Map importance: mt5 uses 0..3 (none/low/moderate/high)
                importance = getattr(v, "importance", 0)
                if importance < self.min_impact:
                    continue
                ts = getattr(v, "time", None)
                if ts is None:
                    continue
                events.append(datetime.fromtimestamp(ts, tz=timezone.utc))
            return events
        except Exception as exc:
            log.debug("MT5 calendar unavailable (%s); relying on manual events.", exc)
            return []

    def in_news_blackout(self, now: datetime | None = None) -> bool:
        if not self.use_news_filter:
            return False
        now = now or datetime.now(timezone.utc)
        window_events = list(self._manual_events) + self._upcoming_mt5_events(now)
        for ev in window_events:
            start = ev - timedelta(minutes=self.minutes_before)
            end = ev + timedelta(minutes=self.minutes_after)
            if start <= now <= end:
                log.info("News blackout active around %s UTC - skipping entries.",
                         ev.strftime("%Y-%m-%d %H:%M"))
                return True
        return False

    # -- combined ------------------------------------------------------------
    def can_trade(self, now: datetime | None = None) -> tuple[bool, str]:
        """Return (allowed, reason). Used by the bot before opening a trade."""
        now = now or datetime.now(timezone.utc)
        if not self.in_session(now):
            return False, "outside trading session"
        if self.in_news_blackout(now):
            return False, "high-impact news blackout"
        return True, "ok"
