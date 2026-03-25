"""Interactive CLI REPL — the primary interface for Shipwright.

Provides a conversational terminal interface where users can hire crews,
chat with them, check status, and manage their virtual engineering company.
"""

from __future__ import annotations

import asyncio
import sys

from shipwright.config import Config
from shipwright.conversation.router import Router
from shipwright.conversation.session import Session
from shipwright.crew.registry import list_crew_types
from shipwright.persistence.store import load_state, save_state
from shipwright.utils.logging import get_logger

logger = get_logger("interfaces.cli")

BANNER = """\
\033[1m  _____ _     _                      _       _     _
 / ____| |   (_)                    (_)     | |   | |
| (___ | |__  _ _ __  __      ___ __ _  __ _| |__ | |_
 \\___ \\| '_ \\| | '_ \\ \\ \\ /\\ / / '__| |/ _` | '_ \\| __|
 ____) | | | | | |_) | \\ V  V /| |  | | (_| | | | | |_
|_____/|_| |_|_| .__/   \\_/\\_/ |_|  |_|\\__, |_| |_|\\__|
               | |                       __/ |
               |_|                      |___/\033[0m
  Virtual engineering crews powered by Claude

  Type \033[1mhelp\033[0m for commands, or \033[1mhire <type> <objective>\033[0m to get started.
"""


def _print_response(text: str) -> None:
    """Print a response with basic formatting."""
    # Convert markdown bold to terminal bold
    formatted = text.replace("**", "\033[1m")
    # Simple toggle — every other occurrence closes the bold
    parts = formatted.split("\033[1m")
    output_parts = [parts[0]]
    for i, part in enumerate(parts[1:], 1):
        if i % 2 == 1:
            output_parts.append(f"\033[1m{part}")
        else:
            output_parts.append(f"\033[0m{part}")
    output = "".join(output_parts)
    if output.count("\033[1m") > output.count("\033[0m"):
        output += "\033[0m"
    print(output)


async def run_repl(config: Config) -> None:
    """Run the interactive REPL."""
    print(BANNER)

    session = Session(id="cli")

    # Restore state if available
    saved = load_state(config, session_id="cli")
    if saved:
        router = Router.from_dict(saved, config)
        if router.crews:
            print(f"  Restored {len(router.crews)} crew(s) from previous session.\n")
    else:
        router = Router(config=config, session=session)

    # Show available crew types
    types = list_crew_types(config)
    print(f"  Available crews: {', '.join(types)}\n")

    def on_text(text: str) -> None:
        """Stream text to terminal as it arrives."""
        sys.stdout.write(text)
        sys.stdout.flush()

    while True:
        try:
            # Show active crew in prompt
            active = router.active_crew
            if active:
                prompt = f"\033[36m[{active.id}]\033[0m > "
            else:
                prompt = "\033[36mshipwright\033[0m > "

            line = input(prompt).strip()

            if not line:
                continue

            if line.lower() in ("quit", "exit", "q"):
                # Save state before exiting
                save_state(router.to_dict(), config, session_id="cli")
                print("\nState saved. Goodbye!")
                break

            # Handle the message
            response = await router.handle_message(line, on_text=on_text)
            if response:
                print()  # newline after streaming
                _print_response(response)
            print()

            # Auto-save after each interaction
            save_state(router.to_dict(), config, session_id="cli")

        except KeyboardInterrupt:
            print("\n")
            save_state(router.to_dict(), config, session_id="cli")
            print("State saved. Goodbye!")
            break
        except EOFError:
            print()
            save_state(router.to_dict(), config, session_id="cli")
            break
        except Exception as exc:
            logger.error("Error: %s", exc, exc_info=True)
            print(f"\n  Error: {exc}\n")


async def run_oneshot(config: Config, message: str) -> int:
    """Run a single message through the router (non-interactive mode)."""
    session = Session(id="cli")

    saved = load_state(config, session_id="cli")
    if saved:
        router = Router.from_dict(saved, config)
    else:
        router = Router(config=config, session=session)

    print(f"\n  Message: {message}")
    print(f"  Model:   {config.model}")
    print(f"  Repo:    {config.repo_root}\n")

    def on_text(text: str) -> None:
        sys.stdout.write(text)
        sys.stdout.flush()

    response = await router.handle_message(message, on_text=on_text)
    if response:
        print()
        _print_response(response)
    print()

    save_state(router.to_dict(), config, session_id="cli")
    return 0
