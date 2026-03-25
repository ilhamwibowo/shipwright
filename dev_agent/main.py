"""Entry point for dev-agent.

  # Telegram bot
  dev-agent --telegram

  # Discord bot
  dev-agent --discord

  # CLI -- one-off message
  dev-agent "Add cancellation reasons to order flow"
"""

from __future__ import annotations

import asyncio
import logging
import signal
import sys

from dev_agent.config import load_config
from dev_agent.coordinator import handle_message

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("dev-agent")


# ---------------------------------------------------------------------------
# Graceful shutdown
# ---------------------------------------------------------------------------

_shutdown_event = asyncio.Event()


def _handle_signal(sig: int, frame: object) -> None:
    logger.info("Received signal %s, shutting down gracefully...", sig)
    _shutdown_event.set()


# ---------------------------------------------------------------------------
# CLI mode
# ---------------------------------------------------------------------------

async def run_cli(message: str) -> int:
    config = load_config()

    if not config.anthropic_api_key:
        logger.error("ANTHROPIC_API_KEY is required. Set it in .env or environment.")
        return 1

    print(f"\nMessage: {message}")
    print(f"Model:   {config.agent_model}")
    print(f"Repo:    {config.repo_root}\n")

    # Restore persisted state
    from dev_agent.persistence import load_state
    from dev_agent.coordinator import set_state, TeamState
    saved = load_state(config)
    if saved:
        state = TeamState.from_dict(saved)
        set_state("cli", state)
        print(f"Restored {len(state.tasks)} previous task(s)\n")

    def on_reply(text: str) -> None:
        print(f"\nTeam Lead: {text}")

    def on_update(text: str) -> None:
        # Strip markdown for CLI display
        clean = text.replace("**", "").replace("*", "").replace("`", "")
        print(f"  [{clean}]")

    await handle_message(
        chat_id="cli",
        message=message,
        config=config,
        on_reply=on_reply,
        on_update=on_update,
    )

    # Wait for background tasks to finish
    pending = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
    if pending:
        print(f"\nWaiting for {len(pending)} background task(s)...")
        await asyncio.gather(*pending, return_exceptions=True)

    return 0


# ---------------------------------------------------------------------------
# Bot modes
# ---------------------------------------------------------------------------

def run_telegram() -> None:
    from dev_agent.telegram_bot import TelegramBot

    config = load_config()
    if not config.telegram_bot_token:
        logger.error("TELEGRAM_BOT_TOKEN required for Telegram mode.")
        sys.exit(1)
    if not config.anthropic_api_key:
        logger.error("ANTHROPIC_API_KEY is required.")
        sys.exit(1)

    bot = TelegramBot(config)
    bot.run()


def run_discord() -> None:
    from dev_agent.discord_bot import DiscordBot

    config = load_config()
    if not config.discord_bot_token:
        logger.error("DISCORD_BOT_TOKEN required for Discord mode.")
        sys.exit(1)
    if not config.anthropic_api_key:
        logger.error("ANTHROPIC_API_KEY is required.")
        sys.exit(1)

    bot = DiscordBot(config)
    bot.run()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)

    args = sys.argv[1:]

    if not args:
        print("Usage:")
        print('  dev-agent "your request"    # CLI mode')
        print("  dev-agent --telegram        # Telegram bot")
        print("  dev-agent --discord         # Discord bot")
        sys.exit(0)

    if args[0] == "--telegram":
        run_telegram()
    elif args[0] == "--discord":
        run_discord()
    else:
        message = " ".join(args)
        sys.exit(asyncio.run(run_cli(message)))


if __name__ == "__main__":
    main()
