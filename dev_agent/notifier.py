"""Telegram notifier — used by CLI mode to post updates."""

from __future__ import annotations

import logging

import httpx
from dev_agent.config import Config

logger = logging.getLogger(__name__)

API = "https://api.telegram.org/bot{token}/{method}"


class TelegramNotifier:
    def __init__(self, config: Config, chat_id: int | str | None = None):
        self.token = config.telegram_bot_token
        self.chat_id = chat_id or config.telegram_chat_id
        self.enabled = bool(self.token and self.chat_id)

    def send(self, text: str) -> None:
        if not self.enabled:
            return
        try:
            httpx.post(
                API.format(token=self.token, method="sendMessage"),
                json={"chat_id": self.chat_id, "text": text, "parse_mode": "Markdown"},
                timeout=10,
            )
        except Exception:
            logger.warning("Telegram send failed", exc_info=True)
