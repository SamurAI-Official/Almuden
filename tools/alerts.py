"""Alerting — notifications for fills, risk events, and kill switch.

Supports Discord webhooks and Telegram bots.
"""
from __future__ import annotations

import logging
from typing import Optional

import aiohttp

from config import Settings

log = logging.getLogger(__name__)


class AlertManager:
    """Manages alerts and notifications."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._webhook_url = getattr(settings, 'alert_webhook_url', '')
        self._telegram_token = getattr(settings, 'telegram_bot_token', '')
        self._telegram_chat_id = getattr(settings, 'telegram_chat_id', '')

    async def send_alert(self, message: str, level: str = "info") -> None:
        """Send an alert via configured channels."""
        if self._webhook_url:
            await self._send_discord(message, level)

        if self._telegram_token and self._telegram_chat_id:
            await self._send_telegram(message, level)

        # Always log locally
        log_func = getattr(log, level, log.info)
        log_func("ALERT: %s", message)

    async def _send_discord(self, message: str, level: str) -> None:
        """Send alert to Discord webhook."""
        try:
            color = {"info": 0x00ff00, "warning": 0xffaa00, "error": 0xff0000}.get(level, 0x888888)
            payload = {
                "embeds": [{
                    "title": f"AlMuden Alert ({level.upper()})",
                    "description": message,
                    "color": color,
                }]
            }

            async with aiohttp.ClientSession() as session:
                async with session.post(self._webhook_url, json=payload) as resp:
                    if resp.status != 204:
                        log.warning("Discord webhook error: %s", resp.status)
        except Exception as exc:
            log.debug("Discord alert failed: %s", exc)

    async def _send_telegram(self, message: str, level: str) -> None:
        """Send alert to Telegram."""
        try:
            url = f"https://api.telegram.org/bot{self._telegram_token}/sendMessage"
            payload = {
                "chat_id": self._telegram_chat_id,
                "text": f"[{level.upper()}] AlMuden: {message}",
                "parse_mode": "HTML",
            }

            async with aiohttp.ClientSession() as session:
                async with session.post(url, json=payload) as resp:
                    if resp.status != 200:
                        log.warning("Telegram error: %s", resp.status)
        except Exception as exc:
            log.debug("Telegram alert failed: %s", exc)

    async def notify_fill(self, symbol: str, side: str, size: float, price: float, pnl: float) -> None:
        """Notify about a fill."""
        emoji = "🟢" if side == "buy" else "🔴"
        await self.send_alert(
            f"{emoji} {side.upper()} {size:.4f} {symbol} @ {price:.6f} (PnL: {pnl:+.4f})",
            level="info",
        )

    async def notify_risk_event(self, reason: str) -> None:
        """Notify about a risk event."""
        await self.send_alert(f"⚠️ Risk Event: {reason}", level="warning")

    async def notify_kill_switch(self, reason: str) -> None:
        """Notify about kill switch engagement."""
        await self.send_alert(f"🛑 KILL SWITCH: {reason}", level="error")