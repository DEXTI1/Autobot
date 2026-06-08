"""
Telegram notifications for the bot.

Sends alerts on trades, the daily kill switch, startup/shutdown, and errors so
you can monitor a bot running on a VPS from your phone. Uses only the Python
standard library (urllib) - no extra dependency.

SETUP:
    1. In Telegram, message @BotFather -> /newbot -> copy the bot TOKEN.
    2. Message your new bot once (say "hi") so it can reply to you.
    3. Get your chat id: open
         https://api.telegram.org/bot<TOKEN>/getUpdates
       and read "chat":{"id": ...}. Put TOKEN + chat id in config.py.

If TELEGRAM_ENABLED is False or credentials are blank, every call is a no-op,
so the bot runs fine without Telegram configured.
"""
from __future__ import annotations

import json
import logging
import urllib.parse
import urllib.request

log = logging.getLogger("notifier")


class TelegramNotifier:
    def __init__(self, enabled: bool, token: str, chat_id: str, timeout: int = 10):
        self.enabled = bool(enabled and token and chat_id)
        self.token = token
        self.chat_id = str(chat_id)
        self.timeout = timeout
        if enabled and not self.enabled:
            log.warning("Telegram enabled but token/chat_id missing - alerts OFF.")

    def send(self, text: str) -> None:
        """Fire-and-forget a message. Never raises - notifications must not
        crash the trading loop."""
        if not self.enabled:
            return
        url = f"https://api.telegram.org/bot{self.token}/sendMessage"
        data = urllib.parse.urlencode({
            "chat_id": self.chat_id,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": "true",
        }).encode()
        try:
            req = urllib.request.Request(url, data=data)
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                payload = json.loads(resp.read().decode())
                if not payload.get("ok"):
                    log.warning("Telegram API returned not-ok: %s", payload)
        except Exception as exc:  # network hiccup, bad token, etc.
            log.warning("Telegram send failed: %s", exc)

    # -- convenience wrappers with consistent formatting --------------------
    def startup(self, symbol: str, timeframe: str, dry_run: bool) -> None:
        mode = "DRY-RUN (no real orders)" if dry_run else "LIVE orders"
        self.send(f"🤖 <b>Bot started</b>\nSymbol: {symbol} {timeframe}\nMode: {mode}")

    def shutdown(self) -> None:
        self.send("🛑 <b>Bot stopped.</b>")

    def trade_opened(self, action: str, symbol: str, lot: float,
                     price: float, sl: float, tp: float) -> None:
        emoji = "🟢" if action == "BUY" else "🔴"
        self.send(
            f"{emoji} <b>{action} {symbol}</b>\n"
            f"Lots: {lot:.2f}\nEntry: {price:.5f}\nSL: {sl:.5f}\nTP: {tp:.5f}"
        )

    def trade_closed(self, symbol: str, reason: str, volume: float) -> None:
        self.send(f"⚪ <b>Closed {symbol}</b> ({volume:.2f} lots)\nReason: {reason}")

    def kill_switch(self, equity: float) -> None:
        self.send(f"⛔ <b>DAILY LOSS LIMIT HIT</b>\nTrading halted. Equity: {equity:,.2f}")

    def error(self, message: str) -> None:
        self.send(f"⚠️ <b>Bot error</b>\n{message}")
