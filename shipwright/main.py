"""Entry point for Shipwright.

  # Interactive REPL (main mode)
  shipwright

  # Quick hire
  shipwright hire backend "Add Stripe payments"

  # Manage crews
  shipwright crews
  shipwright status

  # Named sessions
  shipwright --session myproject

  # Bot modes
  shipwright --telegram
  shipwright --discord
"""

from __future__ import annotations

import asyncio
import signal
import sys

from shipwright.config import load_config
from shipwright.utils.logging import setup_logging


def _extract_session_flag(args: list[str]) -> tuple[str, list[str]]:
    """Extract --session <name> from args. Returns (session_name, remaining_args)."""
    if "--session" not in args:
        return "default", args

    idx = args.index("--session")
    if idx + 1 >= len(args):
        print("Error: --session requires a name argument.", file=sys.stderr)
        sys.exit(1)

    session_name = args[idx + 1]
    remaining = args[:idx] + args[idx + 2:]
    return session_name, remaining


def main() -> None:
    setup_logging()

    signal.signal(signal.SIGINT, lambda *_: None)  # Let asyncio handle it
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))

    args = sys.argv[1:]

    # Extract --session flag before processing other args
    session_name, args = _extract_session_flag(args)

    if not args:
        # Interactive REPL mode
        config = load_config()
        from shipwright.interfaces.cli import run_repl
        asyncio.run(run_repl(config, session_name=session_name))
        return

    if args[0] == "--telegram":
        config = load_config()
        if not config.telegram_bot_token:
            print("Error: TELEGRAM_BOT_TOKEN required for Telegram mode.", file=sys.stderr)
            sys.exit(1)
        from shipwright.interfaces.telegram import TelegramBot
        bot = TelegramBot(config)
        bot.run()
        return

    if args[0] == "--discord":
        config = load_config()
        if not config.discord_bot_token:
            print("Error: DISCORD_BOT_TOKEN required for Discord mode.", file=sys.stderr)
            sys.exit(1)
        from shipwright.interfaces.discord import DiscordBot
        bot = DiscordBot(config)
        bot.run()
        return

    if args[0] == "--help" or args[0] == "-h":
        _print_help()
        return

    if args[0] == "sessions":
        config = load_config()
        _list_sessions(config)
        return

    if args[0] == "crews" or args[0] == "status":
        config = load_config()
        _show_status(config, session_name)
        return

    if args[0] == "hire" and len(args) >= 3:
        config = load_config()
        crew_type = args[1]
        objective = " ".join(args[2:])
        from shipwright.interfaces.cli import run_oneshot
        asyncio.run(run_oneshot(config, f"hire {crew_type} {objective}", session_name))
        return

    if args[0] == "talk" and len(args) >= 2:
        config = load_config()
        from shipwright.interfaces.cli import run_oneshot
        crew_id = " ".join(args[1:])
        asyncio.run(run_oneshot(config, f"talk to {crew_id}", session_name))
        return

    if args[0] == "fire" and len(args) >= 2:
        config = load_config()
        crew_id = " ".join(args[1:])
        from shipwright.interfaces.cli import run_oneshot
        asyncio.run(run_oneshot(config, f"fire {crew_id}", session_name))
        return

    # Anything else is treated as a message to the active crew
    config = load_config()
    message = " ".join(args)
    from shipwright.interfaces.cli import run_oneshot
    sys.exit(asyncio.run(run_oneshot(config, message, session_name)))


def _print_help() -> None:
    print("""shipwright — Virtual engineering crews powered by Claude

Usage:
  shipwright                          Interactive REPL (main mode)
  shipwright --session <name>         Use a named session (default: default)
  shipwright hire <type> <objective>  Quick hire a crew
  shipwright status                   Show active crews
  shipwright sessions                 List all saved sessions
  shipwright talk <crew-id>           Talk to a specific crew
  shipwright fire <crew-id>           Dismiss a crew
  shipwright --telegram               Run Telegram bot
  shipwright --discord                Run Discord bot
  shipwright "<message>"              Send a message to active crew

Session management (in REPL):
  sessions                            List all saved sessions
  session save <name>                 Save current state as a named session
  session load <name>                 Load a named session
  session clear                       Clear current session state

Available crew types: fullstack, frontend, backend, qa, devops, security, docs, enterprise

Enterprise mode (3-level hierarchy):
  shipwright hire enterprise "Build a complete billing system"

Examples:
  shipwright hire backend "Add Stripe payments"
  shipwright hire frontend "Redesign the dashboard"
  shipwright --session myproject hire backend "Add payments"
  shipwright "What's the status?"
""")


def _show_status(config: "Config", session_name: str = "default") -> None:
    from shipwright.persistence.store import load_state
    from shipwright.conversation.router import Router

    saved = load_state(config, session_id=session_name)
    if not saved:
        print("No active crews.")
        return

    router = Router.from_dict(saved, config)
    if not router.crews:
        print("No active crews.")
        return

    for crew in router.crews.values():
        print(crew.summary)
        print()


def _list_sessions(config: "Config") -> None:
    from shipwright.persistence.store import list_sessions

    sessions = list_sessions(config)
    if not sessions:
        print("No saved sessions.")
        return

    print("Saved sessions:")
    for name in sorted(sessions):
        print(f"  {name}")


if __name__ == "__main__":
    main()
