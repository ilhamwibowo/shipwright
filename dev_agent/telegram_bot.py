"""Telegram bot -- conversational interface to the team lead.

Not a one-task-at-a-time bot. You talk to it like a CTO talks to their
team lead. Multiple tasks run concurrently. It remembers context.
"""

from __future__ import annotations

import asyncio
import html
import logging
import re
import threading
import time

import httpx
from dev_agent.config import Config
from dev_agent.coordinator import handle_message

logger = logging.getLogger(__name__)

API = "https://api.telegram.org/bot{token}/{method}"

# Telegram max message length
TG_MAX = 4096
# Leave room for overhead
TG_CHUNK = 4000


def _escape_html(text: str) -> str:
    """Escape text for Telegram HTML parse mode, preserving our own tags."""
    return html.escape(text, quote=False)


def _format_message(text: str) -> str:
    """Convert lightweight markdown-ish text into Telegram HTML.

    Supports: **bold**, *italic*, `code`, ```code blocks```, and preserves
    newlines.  Falls back gracefully -- if conversion produces broken HTML
    we return plain-escaped text.
    """
    # Code blocks first (``` ... ```)
    parts = re.split(r"(```[\s\S]*?```)", text)
    result_parts: list[str] = []

    for part in parts:
        if part.startswith("```") and part.endswith("```"):
            inner = part[3:-3].strip()
            # Strip optional language tag on first line
            if inner and "\n" in inner:
                first_line, rest = inner.split("\n", 1)
                if re.match(r"^[a-zA-Z]+$", first_line.strip()):
                    inner = rest
            result_parts.append(f"<pre>{_escape_html(inner)}</pre>")
        else:
            # Escape HTML in non-code parts
            escaped = _escape_html(part)
            # Inline code
            escaped = re.sub(r"`([^`]+)`", r"<code>\1</code>", escaped)
            # Bold **text**
            escaped = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", escaped)
            # Italic *text* (but not inside bold)
            escaped = re.sub(r"(?<!\*)\*([^*]+?)\*(?!\*)", r"<i>\1</i>", escaped)
            result_parts.append(escaped)

    return "".join(result_parts)


def _chunk_message(text: str) -> list[str]:
    """Split a long message into Telegram-safe chunks.

    Tries to break at paragraph boundaries, then line boundaries, then
    hard-cuts.
    """
    if len(text) <= TG_CHUNK:
        return [text]

    chunks: list[str] = []
    remaining = text

    while remaining:
        if len(remaining) <= TG_CHUNK:
            chunks.append(remaining)
            break

        # Try to break at a double-newline (paragraph)
        cut = remaining.rfind("\n\n", 0, TG_CHUNK)
        if cut < TG_CHUNK // 3:
            # Try single newline
            cut = remaining.rfind("\n", 0, TG_CHUNK)
        if cut < TG_CHUNK // 3:
            # Hard cut
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
        # Persistent event loop for ALL async work (coordinator + agents)
        self._loop = asyncio.new_event_loop()
        self._loop_thread = threading.Thread(
            target=self._loop.run_forever, daemon=True, name="async-loop"
        )
        # HTTP client with connection pooling -- thread-safe for sends
        self._http = httpx.Client(timeout=60)

    def _call(self, method: str, **kwargs) -> dict | None:
        try:
            resp = self._http.post(
                API.format(token=self.token, method=method),
                json=kwargs,
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
        """Send a message, splitting into chunks if needed.

        Uses HTML parse_mode for reliable formatting.
        Falls back to plain text (stripping HTML tags) if the API rejects it.
        """
        formatted = _format_message(text)
        chunks = _chunk_message(formatted)
        # Keep plain-text chunks for fallback (strip HTML tags)
        plain_chunks = _chunk_message(text)

        for i, chunk in enumerate(chunks):
            kwargs: dict = {
                "chat_id": chat_id,
                "text": chunk,
                "parse_mode": "HTML",
            }
            if reply_to and i == 0:
                kwargs["reply_to_message_id"] = reply_to

            result = self._call("sendMessage", **kwargs)
            if result is None:
                # Fallback: send corresponding plain-text chunk without parse_mode
                kwargs.pop("parse_mode", None)
                plain = plain_chunks[i] if i < len(plain_chunks) else chunk
                kwargs["text"] = plain
                self._call("sendMessage", **kwargs)

    def _send_threadsafe(self, chat_id: int, text: str) -> None:
        """Send from any thread safely. Used by background task callbacks."""
        # _send uses synchronous httpx so it is already thread-safe,
        # but we wrap it to catch and log any exceptions.
        try:
            self._send(chat_id, text)
        except Exception:
            logger.warning("Background send failed", exc_info=True)

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
                "<b>dev-agent -- your engineering team</b>\n\n"
                "Talk to me like a CTO. Examples:\n\n"
                "- <code>Add cancellation reasons to orders</code>\n"
                "- <code>What's the architecture of agent-runner?</code>\n"
                "- <code>Run E2E tests on the admin dashboard</code>\n"
                "- <code>What's the team doing?</code>\n"
                "- <code>Fix the failing CI tests</code>\n\n"
                "I handle multiple tasks at once. Just keep talking.",
                message_id,
            )
            return

        if text.startswith("/status"):
            from dev_agent.coordinator import get_state
            state = get_state(chat_id)
            self._send(chat_id, f"<b>Task Board</b>\n\n{state.summary or 'No tasks yet.'}")
            return

        if text.startswith("/"):
            return

        if not text:
            return

        # Acknowledge receipt
        self._send(chat_id, "...")

        def on_reply(reply_text: str) -> None:
            self._send_threadsafe(chat_id, reply_text)

        def on_update(update_text: str) -> None:
            self._send_threadsafe(chat_id, update_text)

        # Schedule on the persistent event loop
        future = asyncio.run_coroutine_threadsafe(
            handle_message(
                chat_id=chat_id,
                message=text,
                config=self.config,
                on_reply=on_reply,
                on_update=on_update,
            ),
            self._loop,
        )
        # Log errors from the future (don't block)
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

        # Start the persistent async loop
        self._loop_thread.start()

        while True:
            try:
                updates = self._call(
                    "getUpdates",
                    offset=self.offset,
                    timeout=30,
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
