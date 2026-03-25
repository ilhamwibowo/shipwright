"""Discord bot -- conversational interface to the team lead.

Same conversational UX as the Telegram bot: talk to it like a CTO,
manage multiple tasks concurrently, get progress updates.
"""

from __future__ import annotations

import asyncio
import logging

import discord

from dev_agent.config import Config
from dev_agent.coordinator import get_state, handle_message

logger = logging.getLogger(__name__)

DISCORD_MAX = 2000  # Discord max message length
DISCORD_CHUNK = 1900


def _chunk_message(text: str) -> list[str]:
    """Split long messages into Discord-safe chunks."""
    if len(text) <= DISCORD_CHUNK:
        return [text]

    chunks: list[str] = []
    remaining = text

    while remaining:
        if len(remaining) <= DISCORD_CHUNK:
            chunks.append(remaining)
            break

        cut = remaining.rfind("\n\n", 0, DISCORD_CHUNK)
        if cut < DISCORD_CHUNK // 3:
            cut = remaining.rfind("\n", 0, DISCORD_CHUNK)
        if cut < DISCORD_CHUNK // 3:
            cut = DISCORD_CHUNK

        chunk = remaining[:cut].rstrip()
        remaining = remaining[cut:].lstrip()
        if chunk:
            chunks.append(chunk)

    return chunks


class DevAgentBot(discord.Client):
    """Discord bot that wraps the dev-agent coordinator."""

    def __init__(self, config: Config):
        intents = discord.Intents.default()
        intents.message_content = True
        super().__init__(intents=intents)
        self.config = config
        self.channel_id = int(config.discord_channel_id) if config.discord_channel_id else None

    async def on_ready(self) -> None:
        logger.info("Discord bot logged in as %s", self.user)
        if self.channel_id:
            channel = self.get_channel(self.channel_id)
            if channel:
                logger.info("Listening on channel: #%s", channel.name)

    async def on_message(self, message: discord.Message) -> None:
        # Ignore own messages
        if message.author == self.user:
            return

        # If channel_id is set, only respond in that channel
        if self.channel_id and message.channel.id != self.channel_id:
            # Also respond to DMs
            if not isinstance(message.channel, discord.DMChannel):
                return

        text = message.content.strip()
        if not text:
            return

        # Handle commands
        if text == "!status":
            state = get_state(f"discord-{message.channel.id}")
            await self._send(message.channel, f"**Task Board**\n\n{state.summary or 'No tasks yet.'}")
            return

        if text == "!help":
            await self._send(
                message.channel,
                "**dev-agent -- your engineering team**\n\n"
                "Talk to me like a CTO. Examples:\n\n"
                "- `Add cancellation reasons to orders`\n"
                "- `What's the architecture of the auth system?`\n"
                "- `Run E2E tests on the dashboard`\n"
                "- `What's the team doing?`\n"
                "- `Fix the failing CI tests`\n\n"
                "Commands: `!status` `!help`\n"
                "I handle multiple tasks at once. Just keep talking.",
            )
            return

        if text.startswith("!"):
            return

        # Send typing indicator
        async with message.channel.typing():
            pass

        chat_id = f"discord-{message.channel.id}"

        async def on_reply(reply_text: str) -> None:
            await self._send(message.channel, reply_text)

        async def on_update(update_text: str) -> None:
            await self._send(message.channel, update_text)

        # Wrap async callbacks as sync for the coordinator (which may call
        # from background threads via asyncio.to_thread).
        loop = asyncio.get_running_loop()

        def sync_reply(text: str) -> None:
            asyncio.run_coroutine_threadsafe(on_reply(text), loop)

        def sync_update(text: str) -> None:
            asyncio.run_coroutine_threadsafe(on_update(text), loop)

        try:
            await handle_message(
                chat_id=chat_id,
                message=text,
                config=self.config,
                on_reply=sync_reply,
                on_update=sync_update,
            )
        except Exception:
            logger.exception("handle_message raised an exception")
            await self._send(message.channel, "Sorry, something went wrong. Please try again.")

    async def _send(self, channel: discord.abc.Messageable, text: str) -> None:
        """Send a message, splitting into chunks if needed."""
        chunks = _chunk_message(text)
        for chunk in chunks:
            try:
                await channel.send(chunk)
            except discord.HTTPException:
                logger.warning("Failed to send Discord message", exc_info=True)


class DiscordBot:
    """Wrapper to match the TelegramBot interface."""

    def __init__(self, config: Config):
        self.config = config
        self.client = DevAgentBot(config)

    def run(self) -> None:
        logger.info("Starting Discord bot...")
        self.client.run(self.config.discord_bot_token, log_handler=None)
