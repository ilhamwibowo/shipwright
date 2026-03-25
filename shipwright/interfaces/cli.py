"""Interactive CLI REPL — the primary interface for Shipwright.

Provides a conversational terminal interface where users can hire crews,
chat with them, check status, and manage their virtual engineering company.
"""

from __future__ import annotations

import asyncio
import re
import signal
import sys
import time

from shipwright.config import Config
from shipwright.conversation.router import Router
from shipwright.conversation.session import Session
from shipwright.crew.crew import CrewStatus
from shipwright.crew.registry import list_crew_types
from shipwright.persistence.store import load_state, save_state
from shipwright.utils.logging import get_logger

logger = get_logger("interfaces.cli")

# Try to import readline for history/completion support
try:
    import readline

    HAS_READLINE = True
except ImportError:
    HAS_READLINE = False

# ---------------------------------------------------------------------------
# ANSI helpers
# ---------------------------------------------------------------------------
BOLD = "\033[1m"
DIM = "\033[2m"
RESET = "\033[0m"
CYAN = "\033[36m"
GREEN = "\033[32m"
YELLOW = "\033[33m"
RED = "\033[31m"

BANNER = f"""\
{BOLD}{CYAN}  _____ _     _                      _       _     _
 / ____| |   (_)                    (_)     | |   | |
| (___ | |__  _ _ __  __      ___ __ _  __ _| |__ | |_
 \\___ \\| '_ \\| | '_ \\ \\ \\ /\\ / / '__| |/ _` | '_ \\| __|
 ____) | | | | | |_) | \\ V  V /| |  | | (_| | | | | |_
|_____/|_| |_|_| .__/   \\_/\\_/ |_|  |_|\\__, |_| |_|\\__|
               | |                       __/ |
               |_|                      |___/{RESET}
  {DIM}Virtual engineering crews powered by Claude{RESET}

  Type {BOLD}help{RESET} for commands, or {BOLD}hire <type> <objective>{RESET} to get started.
"""

# Commands recognised by the REPL (for tab completion)
_COMMANDS = [
    "crews",
    "exit",
    "fire",
    "help",
    "hire",
    "log",
    "pr",
    "quit",
    "ship",
    "status",
    "switch to",
    "talk to",
]


# ---------------------------------------------------------------------------
# Spinner
# ---------------------------------------------------------------------------
class Spinner:
    """Async braille-pattern spinner shown while waiting for SDK responses."""

    _FRAMES = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"

    def __init__(self) -> None:
        self._active = False
        self._task: asyncio.Task | None = None

    @property
    def active(self) -> bool:
        return self._active

    def start(self, message: str = "Thinking...") -> None:
        self._active = True
        try:
            loop = asyncio.get_running_loop()
            self._task = loop.create_task(self._run(message))
        except RuntimeError:
            self._active = False

    def stop(self) -> None:
        self._active = False
        if self._task and not self._task.done():
            self._task.cancel()
        sys.stdout.write("\r\033[K")
        sys.stdout.flush()

    async def _run(self, message: str) -> None:
        i = 0
        try:
            while self._active:
                frame = self._FRAMES[i % len(self._FRAMES)]
                sys.stdout.write(f"\r  {DIM}{frame} {message}{RESET}")
                sys.stdout.flush()
                i += 1
                await asyncio.sleep(0.08)
        except asyncio.CancelledError:
            pass
        finally:
            sys.stdout.write("\r\033[K")
            sys.stdout.flush()


# ---------------------------------------------------------------------------
# CLIOutput — coordinates spinner, streaming text, and delegation messages
# ---------------------------------------------------------------------------
class CLIOutput:
    """Manages all terminal output during a single request/response cycle."""

    def __init__(self) -> None:
        self.spinner = Spinner()
        self._got_text = False
        self._start_time: float = 0.0

    # -- callbacks ----------------------------------------------------------

    def on_text(self, text: str) -> None:
        """Stream lead text — stops spinner on first chunk, prints in cyan."""
        if self.spinner.active:
            self.spinner.stop()
        if not self._got_text:
            self._got_text = True
            sys.stdout.write(f"\n  {CYAN}")
        sys.stdout.write(text)
        sys.stdout.flush()

    def on_delegation_start(
        self, member_name: str, task: str, round_num: int, max_rounds: int
    ) -> None:
        """Called when a crew member starts working."""
        if self.spinner.active:
            self.spinner.stop()
        # Ensure we reset color from any prior lead text
        sys.stdout.write(RESET)
        role = member_name.replace("_", " ").title()
        short_task = task.split("\n")[0][:60]
        round_tag = f" [round {round_num}]" if round_num > 1 else ""
        sys.stdout.write(
            f"\n  {YELLOW}⚙️{round_tag} {role} is working: "
            f"{short_task}...{RESET}\n"
        )
        sys.stdout.flush()
        self.spinner.start(f"{role} working...")

    def on_delegation_end(
        self, member_name: str, duration_s: float, is_error: bool
    ) -> None:
        """Called when a crew member finishes."""
        if self.spinner.active:
            self.spinner.stop()
        role = member_name.replace("_", " ").title()
        if is_error:
            sys.stdout.write(f"  {RED}✗ {role} failed ({duration_s:.1f}s){RESET}\n")
        else:
            sys.stdout.write(
                f"  {GREEN}✓ {role} done ({duration_s:.1f}s){RESET}\n"
            )
        sys.stdout.flush()

    def on_progress(self, message: str) -> None:
        """Show a progress status line."""
        if self.spinner.active:
            self.spinner.stop()
        sys.stdout.write(f"  {DIM}{message}{RESET}\n")
        sys.stdout.flush()
        self.spinner.start(message)

    # -- lifecycle ----------------------------------------------------------

    def start_thinking(self) -> None:
        """Call before sending a message to the router."""
        self._got_text = False
        self._start_time = time.time()
        self.spinner.start("Thinking...")

    def finish_response(self) -> None:
        """Call after the full response cycle completes."""
        if self.spinner.active:
            self.spinner.stop()
        if self._got_text:
            sys.stdout.write(RESET)
            sys.stdout.flush()

    @property
    def elapsed(self) -> float:
        """Wall-clock seconds since start_thinking()."""
        if self._start_time == 0.0:
            return 0.0
        return time.time() - self._start_time

    @property
    def streamed(self) -> bool:
        """Whether any text was streamed via on_text during this cycle."""
        return self._got_text


# ---------------------------------------------------------------------------
# Markdown rendering
# ---------------------------------------------------------------------------
def render_markdown(text: str) -> str:
    """Render markdown to terminal-friendly output with ANSI codes."""
    lines = text.split("\n")
    output: list[str] = []
    in_code_block = False

    for line in lines:
        stripped = line.strip()

        # Code block toggle
        if stripped.startswith("```"):
            in_code_block = not in_code_block
            output.append(f"  {DIM}{'─' * 50}{RESET}")
            continue

        if in_code_block:
            output.append(f"  {DIM}  {line}{RESET}")
            continue

        # Horizontal rule
        if stripped and len(stripped) >= 3 and all(c in "-*_" for c in stripped):
            output.append(f"  {DIM}{'─' * 50}{RESET}")
            continue

        # Headers
        if stripped.startswith("### "):
            output.append(f"  {BOLD}{stripped[4:]}{RESET}")
            continue
        if stripped.startswith("## "):
            output.append(f"  {BOLD}{CYAN}{stripped[3:]}{RESET}")
            continue
        if stripped.startswith("# "):
            output.append(f"  {BOLD}{CYAN}{stripped[2:]}{RESET}")
            continue

        # Inline formatting
        formatted = re.sub(r"\*\*(.+?)\*\*", rf"{BOLD}\1{RESET}", line)
        formatted = re.sub(r"`([^`]+)`", rf"{DIM}\1{RESET}", formatted)
        output.append(formatted)

    return "\n".join(output)


# ---------------------------------------------------------------------------
# Readline setup
# ---------------------------------------------------------------------------
def _setup_readline(config: Config) -> None:
    """Configure readline with persistent history."""
    if not HAS_READLINE:
        return

    history_path = config.data_dir / "history"
    history_path.parent.mkdir(parents=True, exist_ok=True)

    try:
        readline.read_history_file(str(history_path))
    except (FileNotFoundError, OSError):
        pass

    readline.set_history_length(1000)

    import atexit

    atexit.register(readline.write_history_file, str(history_path))


def _setup_completer(router: Router) -> None:
    """Set up tab completion for commands and crew names."""
    if not HAS_READLINE:
        return

    crew_types = list_crew_types(router.config)

    def completer(text: str, state: int) -> str | None:
        line = readline.get_line_buffer().lstrip()
        options: list[str] = []

        if not line or line == text:
            # First word — suggest commands and crew types
            options = [c for c in _COMMANDS if c.startswith(text.lower())]
            options.extend(t for t in crew_types if t.startswith(text.lower()))
        else:
            first_word = line.split()[0].lower()
            if first_word in ("hire", "start", "create"):
                options = [t for t in crew_types if t.startswith(text.lower())]
            elif first_word in ("fire", "dismiss", "talk", "switch", "log"):
                options = [
                    cid for cid in router.crews if text.lower() in cid.lower()
                ]

        return options[state] if state < len(options) else None

    readline.set_completer(completer)
    readline.set_completer_delims(" \t\n")
    # macOS uses libedit which needs different bind syntax
    if "libedit" in (getattr(readline, "__doc__", "") or ""):
        readline.parse_and_bind("bind ^I rl_complete")
    else:
        readline.parse_and_bind("tab: complete")


# ---------------------------------------------------------------------------
# REPL
# ---------------------------------------------------------------------------
async def run_repl(config: Config) -> None:
    """Run the interactive REPL."""
    # Restore default signal handling so Ctrl+C works in the REPL
    signal.signal(signal.SIGINT, signal.default_int_handler)

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

    # Setup readline
    _setup_readline(config)
    _setup_completer(router)

    ui = CLIOutput()

    while True:
        try:
            # Build prompt with crew status
            active = router.active_crew
            if active:
                status = active.status.value
                prompt = f"{CYAN}[{active.id}|{status}]{RESET} > "
            else:
                prompt = f"{CYAN}shipwright{RESET} > "

            line = input(prompt).strip()

            if not line:
                continue

            if line.lower() in ("quit", "exit", "q"):
                save_state(router.to_dict(), config, session_id="cli")
                print(f"\n  {DIM}State saved. Goodbye!{RESET}")
                break

            # Alias: 'crews' -> 'status'
            if line.lower() == "crews":
                line = "status"

            # Process the message
            try:
                ui.start_thinking()

                response = await router.handle_message(
                    line,
                    on_text=ui.on_text,
                    on_delegation_start=ui.on_delegation_start,
                    on_delegation_end=ui.on_delegation_end,
                    on_progress=ui.on_progress,
                )

                ui.finish_response()

                if response:
                    if ui.streamed:
                        # Lead text was already streamed; just add spacing
                        print()
                    else:
                        # Command response — render with markdown
                        print()
                        print(render_markdown(response))

                # Show timing for non-trivial operations
                elapsed = ui.elapsed
                if elapsed >= 1.0:
                    print(f"  {DIM}Done in {elapsed:.1f}s{RESET}")
                print()

            except KeyboardInterrupt:
                ui.finish_response()
                # Reset crew status if it was mid-work
                if active and active.status == CrewStatus.WORKING:
                    active.status = CrewStatus.IDLE
                print(f"\n  {DIM}Cancelled.{RESET}\n")
                continue
            except asyncio.CancelledError:
                ui.finish_response()
                print(f"\n  {DIM}Cancelled.{RESET}\n")
                continue

            # Auto-save after each interaction
            save_state(router.to_dict(), config, session_id="cli")

            # Refresh completer with potentially new crews
            _setup_completer(router)

        except KeyboardInterrupt:
            # Ctrl+C during input() — just show a new prompt
            print()
            continue

        except EOFError:
            print()
            save_state(router.to_dict(), config, session_id="cli")
            break

        except Exception as exc:
            ui.finish_response()
            logger.error("Error: %s", exc, exc_info=True)
            print(f"\n  {RED}Error: {exc}{RESET}\n")


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

    ui = CLIOutput()
    ui.start_thinking()

    response = await router.handle_message(
        message,
        on_text=ui.on_text,
        on_delegation_start=ui.on_delegation_start,
        on_delegation_end=ui.on_delegation_end,
        on_progress=ui.on_progress,
    )

    ui.finish_response()

    if response:
        if ui.streamed:
            print()
        else:
            print()
            print(render_markdown(response))

    elapsed = ui.elapsed
    if elapsed >= 1.0:
        print(f"  {DIM}Done in {elapsed:.1f}s{RESET}")
    print()

    save_state(router.to_dict(), config, session_id="cli")
    return 0
