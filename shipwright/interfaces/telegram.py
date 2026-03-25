"""Telegram bot interface for Shipwright.

Conversational interface via Telegram. Supports multiple concurrent crews,
persistent state, and all router commands.
"""

from __future__ import annotations

import asyncio
import html
import logging
import re
import threading
import time

import httpx

from shipwright.config import Config
from shipwright.conversation.router import Router
from shipwright.conversation.session import Session
from shipwright.persistence.store import load_state, save_state

logger = logging.getLogger("shipwright.interfaces.telegram")

API = "https://api.telegram.org/bot{token}/{method}"
TG_CHUNK = 4000


def _escape_html(text: str) -> str:
    return html.escape(text, quote=False)


def _format_message(text: str) -> str:
    """Convert markdown-ish text into Telegram HTML."""
    parts = re.split(r"(```[\s\S]*?```)", text)
    result_parts: list[str] = []

    for part in parts:
        if part.startswith("```") and part.endswith("```"):
            inner = part[3:-3].strip()
            if inner and "\n" in inner:
                first_line, rest = inner.split("\n", 1)
                if re.match(r"^[a-zA-Z]+$", first_line.strip()):
                    inner = rest
            result_parts.append(f"<pre>{_escape_html(inner)}</pre>")
        else:
            escaped = _escape_html(part)
            escaped = re.sub(r"`([^`]+)`", r"<code>\1</code>", escaped)
            escaped = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", escaped)
            escaped = re.sub(r"(?<!\*)\*([^*]+?)\*(?!\*)", r"<i>\1</i>", escaped)
            result_parts.append(escaped)

    return "".join(result_parts)


def _chunk_message(text: str) -> list[str]:
    """Split a long message into Telegram-safe chunks."""
    if len(text) <= TG_CHUNK:
        return [text]

    chunks: list[str] = []
    remaining = text
    while remaining:
        if len(remaining) <= TG_CHUNK:
            chunks.append(remaining)
            break
        cut = remaining.rfind("\n\n", 0, TG_CHUNK)
        if cut < TG_CHUNK // 3:
            cut = remaining.rfind("\n", 0, TG_CHUNK)
        if cut < TG_CHUNK // 3:
            cut = TG_CHUNK
        chunk = remaining[:cut].rstrip()
        remaining = remaining[cut:].lstrip()
        if chunk:
            chunks.append(chunk)
    return chunks


class TelegramBot:
    def __init__(self, config: Config):
        self.config = config
        self.token = config.telegram_bot_token
        self.allowed_users = config.telegram_allowed_users
        self.offset = 0
        self._loop = asyncio.new_event_loop()
        self._loop_thread = threading.Thread(
            target=self._loop.run_forever, daemon=True, name="async-loop"
        )
        self._http = httpx.Client(timeout=60)
        self._routers: dict[int, Router] = {}

    def _get_router(self, chat_id: int) -> Router:
        if chat_id not in self._routers:
            session_id = f"telegram-{chat_id}"
            saved = load_state(self.config, session_id=session_id)
            if saved:
                router = Router.from_dict(saved, self.config)
            else:
                session = Session(id=session_id)
                router = Router(config=self.config, session=session)
            self._routers[chat_id] = router
        return self._routers[chat_id]

    def _save_router(self, chat_id: int) -> None:
        if chat_id in self._routers:
            session_id = f"telegram-{chat_id}"
            save_state(self._routers[chat_id].to_dict(), self.config, session_id=session_id)

    def _call(self, method: str, **kwargs) -> dict | None:
        try:
            resp = self._http.post(
                API.format(token=self.token, method=method), json=kwargs,
            )
            data = resp.json()
            if not data.get("ok"):
                logger.warning("Telegram API error: %s", data.get("description"))
                return None
            return data.get("result")
        except Exception:
            logger.warning("Telegram call failed", exc_info=True)
            return None

    def _send(self, chat_id: int, text: str, reply_to: int | None = None) -> None:
        formatted = _format_message(text)
        chunks = _chunk_message(formatted)
        plain_chunks = _chunk_message(text)

        for i, chunk in enumerate(chunks):
            kwargs: dict = {"chat_id": chat_id, "text": chunk, "parse_mode": "HTML"}
            if reply_to and i == 0:
                kwargs["reply_to_message_id"] = reply_to
            result = self._call("sendMessage", **kwargs)
            if result is None:
                kwargs.pop("parse_mode", None)
                plain = plain_chunks[i] if i < len(plain_chunks) else chunk
                kwargs["text"] = plain
                self._call("sendMessage", **kwargs)

    def _is_allowed(self, user_id: int, username: str | None) -> bool:
        if not self.allowed_users:
            return True
        allowed = {u.strip().lower() for u in self.allowed_users.split(",")}
        return str(user_id) in allowed or (username and username.lower() in allowed)

    def _handle_message(self, message: dict) -> None:
        chat_id = message["chat"]["id"]
        user = message.get("from", {})
        user_id = user.get("id", 0)
        username = user.get("username")
        text = message.get("text", "").strip()
        message_id = message["message_id"]

        if not self._is_allowed(user_id, username):
            self._send(chat_id, "Not authorized.", message_id)
            return

        if text.startswith("/start"):
            self._send(
                chat_id,
                "<b>Shipwright — Virtual Engineering Crews</b>\n\n"
                "Talk to me like a CTO. Examples:\n\n"
                "- <code>hire backend Add Stripe payments</code>\n"
                "- <code>hire frontend Redesign the dashboard</code>\n"
                "- <code>status</code>\n"
                "- <code>help</code>\n\n"
                "I manage multiple crews at once.",
                message_id,
            )
            return

        if text.startswith("/"):
            # Convert /command to command
            text = text.lstrip("/")

        if not text:
            return

        self._send(chat_id, "...")

        def on_reply(response: str) -> None:
            try:
                self._send(chat_id, response)
            except Exception:
                logger.warning("Send failed", exc_info=True)

        async def _process() -> None:
            router = self._get_router(chat_id)
            response = await router.handle_message(text)
            if response:
                on_reply(response)
            self._save_router(chat_id)

        future = asyncio.run_coroutine_threadsafe(_process(), self._loop)
        future.add_done_callback(self._handle_future_error)

    @staticmethod
    def _handle_future_error(future: asyncio.Future) -> None:
        try:
            future.result()
        except Exception:
            logger.exception("handle_message raised an exception")

    def run(self) -> None:
        logger.info("Telegram bot starting...")

        me = self._call("getMe")
        if me:
            logger.info("Bot: @%s", me.get("username"))
        else:
            logger.error("Failed to connect. Check TELEGRAM_BOT_TOKEN.")
            return

        self._loop_thread.start()

        while True:
            try:
                updates = self._call(
                    "getUpdates", offset=self.offset, timeout=30,
                    allowed_updates=["message"],
                )
                if not updates:
                    time.sleep(1)
                    continue

                for update in updates:
                    self.offset = update["update_id"] + 1
                    if "message" in update:
                        self._handle_message(update["message"])

            except KeyboardInterrupt:
                logger.info("Bot stopped.")
                self._loop.call_soon_threadsafe(self._loop.stop)
                self._http.close()
                break
            except Exception:
                logger.warning("Polling error", exc_info=True)
                time.sleep(5)
