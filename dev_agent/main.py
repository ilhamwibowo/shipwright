"""Entry point for the dev-agent.

  # Telegram bot -- talk to your team from your phone
  dev-agent

  # CLI -- one-off message
  dev-agent "Add cancellation reasons to order flow"
"""

from __future__ import annotations

import asyncio
import logging
import sys

from dev_agent.config import load_config
from dev_agent.coordinator import handle_message

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("dev-agent")


async def run_cli(message: str) -> int:
    config = load_config()

    print(f"\nMessage: {message}")
    print(f"Model: {config.agent_model}")
    print(f"Repo: {config.repo_root}\n")

    def on_reply(text: str) -> None:
        print(f"\nTeam Lead: {text}")

    def on_update(text: str) -> None:
        print(f"  [{text}]")

    await handle_message(
        chat_id="cli",
        message=message,
        config=config,
        on_reply=on_reply,
        on_update=on_update,
    )

    # Wait for background tasks to finish (tasks are asyncio tasks now)
    pending = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
    if pending:
        print(f"\nWaiting for {len(pending)} background task(s)...")
        await asyncio.gather(*pending, return_exceptions=True)

    return 0


def run_bot() -> None:
    from dev_agent.telegram_bot import TelegramBot

    config = load_config()

    if not config.telegram_bot_token:
        logger.error("TELEGRAM_BOT_TOKEN required. Or: dev-agent \"your message\"")
        sys.exit(1)

    bot = TelegramBot(config)
    bot.run()


def main() -> None:
    if len(sys.argv) > 1:
        message = " ".join(sys.argv[1:])
        sys.exit(asyncio.run(run_cli(message)))
    else:
        run_bot()


if __name__ == "__main__":
    main()
