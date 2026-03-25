"""Discord bot interface for Shipwright.

Conversational interface via Discord. Supports multiple concurrent crews,
persistent state, and all router commands.
"""

from __future__ import annotations

import asyncio
import logging

import discord

from shipwright.config import Config
from shipwright.conversation.router import Router
from shipwright.conversation.session import Session
from shipwright.persistence.store import load_state, save_state

logger = logging.getLogger("shipwright.interfaces.discord")

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


class ShipwrightBot(discord.Client):
    """Discord bot that wraps the Shipwright router."""

    def __init__(self, config: Config):
        intents = discord.Intents.default()
        intents.message_content = True
        super().__init__(intents=intents)
        self.config = config
        self.channel_id = int(config.discord_channel_id) if config.discord_channel_id else None
        self._routers: dict[str, Router] = {}

    def _get_router(self, channel_id: int) -> Router:
        key = f"discord-{channel_id}"
        if key not in self._routers:
            saved = load_state(self.config, session_id=key)
            if saved:
                router = Router.from_dict(saved, self.config)
            else:
                session = Session(id=key)
                router = Router(config=self.config, session=session)
            self._routers[key] = router
        return self._routers[key]

    def _save_router(self, channel_id: int) -> None:
        key = f"discord-{channel_id}"
        if key in self._routers:
            save_state(self._routers[key].to_dict(), self.config, session_id=key)

    async def on_ready(self) -> None:
        logger.info("Discord bot logged in as %s", self.user)
        if self.channel_id:
            channel = self.get_channel(self.channel_id)
            if channel:
                logger.info("Listening on channel: #%s", channel.name)

    async def on_message(self, message: discord.Message) -> None:
        if message.author == self.user:
            return

        if self.channel_id and message.channel.id != self.channel_id:
            if not isinstance(message.channel, discord.DMChannel):
                return

        text = message.content.strip()
        if not text:
            return

        # Strip bot command prefix
        if text.startswith("!"):
            text = text[1:]
            if not text:
                return

        async with message.channel.typing():
            router = self._get_router(message.channel.id)
            response = await router.handle_message(text)
            if response:
                await self._send(message.channel, response)
            self._save_router(message.channel.id)

    async def _send(self, channel: discord.abc.Messageable, text: str) -> None:
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
        self.client = ShipwrightBot(config)

    def run(self) -> None:
        logger.info("Starting Discord bot...")
        self.client.run(self.config.discord_bot_token, log_handler=None)
