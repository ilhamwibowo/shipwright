"""Entry point for Shipwright.

  # Interactive REPL (main mode)
  shipwright

  # Quick hire
  shipwright hire backend-dev

  # Manage employees
  shipwright status
  shipwright team

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

    if args[0] in ("team", "status"):
        config = load_config()
        _show_status(config, session_name)
        return

    if args[0] == "hire" and len(args) >= 2:
        config = load_config()
        role = args[1]
        # Optional: hire <role> as "Name"
        rest = " ".join(args[2:]) if len(args) > 2 else ""
        from shipwright.interfaces.cli import run_oneshot
        asyncio.run(run_oneshot(config, f"hire {role} {rest}".strip(), session_name))
        return

    if args[0] == "talk" and len(args) >= 2:
        config = load_config()
        from shipwright.interfaces.cli import run_oneshot
        employee_name = args[1]
        asyncio.run(run_oneshot(config, f"talk {employee_name}", session_name))
        return

    if args[0] == "fire" and len(args) >= 2:
        config = load_config()
        target = args[1]
        from shipwright.interfaces.cli import run_oneshot
        asyncio.run(run_oneshot(config, f"fire {target}", session_name))
        return

    if args[0] == "assign" and len(args) >= 3:
        config = load_config()
        target = args[1]
        task = " ".join(args[2:])
        from shipwright.interfaces.cli import run_oneshot
        asyncio.run(run_oneshot(config, f'assign {target} "{task}"', session_name))
        return

    # Anything else is treated as a message to the active employee
    config = load_config()
    message = " ".join(args)
    from shipwright.interfaces.cli import run_oneshot
    sys.exit(asyncio.run(run_oneshot(config, message, session_name)))


def _print_help() -> None:
    print("""shipwright — Your AI Engineering Company

Usage:
  shipwright                              Interactive REPL (main mode)
  shipwright --session <name>             Use a named session (default: default)
  shipwright hire <role>                  Hire an employee with a role
  shipwright hire <role> as "Name"        Hire with a custom name
  shipwright assign <name> "<task>"       Assign work to an employee or team
  shipwright talk <name>                  Talk to a specific employee
  shipwright fire <name>                  Fire an employee
  shipwright status                       Show company status
  shipwright team                         Show team overview
  shipwright sessions                     List all saved sessions
  shipwright --telegram                   Run Telegram bot
  shipwright --discord                    Run Discord bot
  shipwright "<message>"                  Send a message to active employee

REPL commands:
  roles                                   List available roles to hire
  hire <role>                             Hire an employee
  fire <name>                             Fire an employee or team
  team                                    Show company overview
  team create <name>                      Create a team
  promote <name> to lead of <team>        Promote to team lead
  assign <name|team> "<task>"             Assign work
  assign <name> to <team>                 Add employee to team
  talk <name>                             Switch conversation to employee
  status                                  Company overview
  costs                                   Budget/token usage per employee
  history <name>                          Task history for an employee
  ship                                    Open PR for all work
  ship <team>                             Open PR for team's work
  save                                    Save current state
  sessions                                List all saved sessions
  session save <name>                     Save as named session
  session load <name>                     Load a named session
  session clear                           Clear current session

Available roles: architect, backend-dev, frontend-dev, fullstack-dev,
  db-engineer, qa-engineer, devops-engineer, security-auditor,
  tech-writer, designer, team-lead, vp-engineering

Examples:
  shipwright hire backend-dev
  shipwright assign Alex "Add Stripe payments"
  shipwright --session myproject hire architect
  shipwright "What's the status?"
""")


def _show_status(config: "Config", session_name: str = "default") -> None:
    from shipwright.persistence.store import load_state
    from shipwright.conversation.router import Router

    saved = load_state(config, session_id=session_name)
    if not saved:
        print("No active employees.")
        return

    router = Router.from_dict(saved, config)
    if not router.company or not router.company.employees:
        print("No active employees.")
        return

    company = router.company
    n_employees = len(company.employees)
    n_teams = len(company.teams)
    header = f"Your Company ({n_employees} employee(s)"
    if n_teams:
        header += f", {n_teams} team(s)"
    header += ")"
    print(header)
    print(company.status_summary)
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
