"""Interactive CLI REPL — the primary interface for Shipwright.

Provides an operator-console terminal interface where users talk to the CTO,
manage their virtual engineering company, and monitor work execution.
"""

from __future__ import annotations

import asyncio
import os
import re
import signal
import sys
import time

from shipwright.config import Config
from shipwright.conversation.router import Router
from shipwright.conversation.session import Session
from shipwright.company.employee import EmployeeStatus, RoadmapState
from shipwright.company.roles import list_roles
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
ITALIC = "\033[3m"
UNDERLINE = "\033[4m"
RESET = "\033[0m"

# Standard colors
CYAN = "\033[36m"
GREEN = "\033[32m"
YELLOW = "\033[33m"
RED = "\033[31m"
BLUE = "\033[34m"
MAGENTA = "\033[35m"
WHITE = "\033[37m"

# Bright / high-intensity colors
BR_CYAN = "\033[96m"
BR_GREEN = "\033[92m"
BR_YELLOW = "\033[93m"
BR_RED = "\033[91m"
BR_BLUE = "\033[94m"
BR_MAGENTA = "\033[95m"
BR_WHITE = "\033[97m"

# Background colors (muted)
BG_DARK = "\033[48;5;236m"

# ---------------------------------------------------------------------------
# Role color mapping — each role gets a distinct terminal color
# ---------------------------------------------------------------------------
ROLE_COLORS: dict[str, str] = {
    "cto": f"{BOLD}{BR_CYAN}",
    "architect": f"{BR_BLUE}",
    "backend-dev": f"{BLUE}",
    "frontend-dev": f"{MAGENTA}",
    "fullstack-dev": f"{BR_MAGENTA}",
    "db-engineer": f"{YELLOW}",
    "qa-engineer": f"{GREEN}",
    "devops-engineer": f"{BR_GREEN}",
    "security-auditor": f"{BR_RED}",
    "tech-writer": f"{WHITE}",
    "designer": f"{BR_MAGENTA}",
    "team-lead": f"{BOLD}{YELLOW}",
    "evaluator": f"{BR_YELLOW}",
    "researcher": f"{BR_CYAN}",
}


def role_color(role_id: str) -> str:
    """Get the ANSI color code for a role."""
    return ROLE_COLORS.get(role_id, CYAN)


# ---------------------------------------------------------------------------
# Event stream icons — concise, terminal-safe Unicode
# ---------------------------------------------------------------------------
ICON_DELEGATE = "\u25b6"   # ▶
ICON_REVIEW = "\u25c9"     # ◉
ICON_REVISE = "\u21bb"     # ↻
ICON_DONE = "\u2713"       # ✓
ICON_FAIL = "\u2717"       # ✗
ICON_WARN = "\u25b2"       # ▲
ICON_TASK = "\u25cf"       # ●
ICON_PENDING = "\u25cb"    # ○
ICON_HIRE = "+"
ICON_ARROW = "\u2192"      # →
ICON_DOT = "\u00b7"        # ·


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _format_elapsed(seconds: float) -> str:
    """Format elapsed seconds into a compact string."""
    if seconds < 60:
        return f"{seconds:.1f}s"
    minutes = int(seconds // 60)
    secs = int(seconds % 60)
    if minutes < 60:
        return f"{minutes}m {secs}s" if secs else f"{minutes}m"
    hours = int(minutes // 60)
    mins = minutes % 60
    return f"{hours}h {mins}m"


def _term_width() -> int:
    """Get terminal width, default 80."""
    try:
        return os.get_terminal_size().columns
    except (ValueError, OSError):
        return 80


# ---------------------------------------------------------------------------
# Status strip — compact one-line company state above the prompt
# ---------------------------------------------------------------------------
def _render_status_strip(router: Router) -> str:
    """Render a compact status strip showing company state at a glance."""
    company = router.company
    if not company:
        return ""

    parts: list[str] = []

    # CTO status
    cto = company.get_cto()
    if cto:
        parts.append(f"{BOLD}{BR_CYAN}CTO{RESET} {GREEN}{ICON_DOT}{RESET}")
    else:
        parts.append(f"{DIM}CTO offline{RESET}")

    # Employee count (excluding CTO)
    n_emp = len([e for e in company.employees.values() if e.role != "cto"])
    if n_emp:
        working = [e for e in company.employees.values()
                   if e.status == EmployeeStatus.WORKING and e.role != "cto"]
        if working:
            parts.append(f"{YELLOW}{len(working)}{RESET}/{n_emp} working")
        else:
            parts.append(f"{n_emp} idle")

    # Roadmap progress
    rm = company.active_roadmap
    if rm:
        if rm.state == RoadmapState.RUNNING:
            parts.append(f"roadmap {BR_CYAN}{rm.done_count}/{rm.total_count}{RESET}")
        elif rm.state in (RoadmapState.PAUSED, RoadmapState.INTERRUPTED):
            label = "paused" if rm.state == RoadmapState.PAUSED else "interrupted"
            parts.append(f"roadmap {YELLOW}{rm.done_count}/{rm.total_count} {label}{RESET}")
        elif rm.state == RoadmapState.STOPPED:
            parts.append(f"roadmap {RED}stopped{RESET}")

    # Session tag
    session_name = getattr(router, "session_name", "default")
    if session_name != "default":
        parts.append(f"{DIM}{session_name}{RESET}")

    if not parts:
        return ""

    sep = f" {DIM}\u2502{RESET} "
    return f"  {DIM}\u2500{RESET} {sep.join(parts)} {DIM}\u2500{RESET}"


# Commands recognised by the REPL (for tab completion)
_COMMANDS = [
    "approve",
    "assign",
    "back",
    "board",
    "continue",
    "costs",
    "events",
    "exit",
    "fire",
    "go",
    "help",
    "hire",
    "history",
    "inspect",
    "installed",
    "org",
    "pause",
    "pause now",
    "promote",
    "quit",
    "resume",
    "roadmap",
    "roles",
    "save",
    "session clear",
    "session load",
    "session save",
    "sessions",
    "ship",
    "shop",
    "status",
    "stop",
    "talk",
    "team",
    "team create",
    "who",
]


# ---------------------------------------------------------------------------
# Spinner
# ---------------------------------------------------------------------------
class Spinner:
    """Async braille-pattern spinner shown while waiting for SDK responses."""

    _FRAMES = "\u280b\u2819\u2839\u2838\u283c\u2834\u2826\u2827\u2807\u280f"

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
# CLIOutput — coordinates spinner, streaming text, and event feed
# ---------------------------------------------------------------------------
class CLIOutput:
    """Manages all terminal output during a single request/response cycle.

    Separates the conversation stream (user <-> CTO) from the event feed
    (internal activity: hiring, delegating, reviewing, revising).
    """

    def __init__(self, company=None) -> None:
        self.spinner = Spinner()
        self._got_text = False
        self._start_time: float = 0.0
        self._company = company
        self._event_count = 0

    def _get_role_for_name(self, name: str) -> str:
        """Look up role_id for an employee name."""
        if self._company and name in self._company.employees:
            return self._company.employees[name].role
        return ""

    def _event_separator(self) -> None:
        """Print a visual separator before the first event in a cycle."""
        if self._event_count == 0 and self._got_text:
            sys.stdout.write(f"{RESET}\n")

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
        """Called when an employee starts working — compact event line."""
        if self.spinner.active:
            self.spinner.stop()
        sys.stdout.write(RESET)
        self._event_separator()

        role_id = self._get_role_for_name(member_name)
        color = role_color(role_id)
        display_name = member_name.replace("_", " ").title()
        short_task = task.split("\n")[0][:55]

        round_tag = ""
        if round_num > 1:
            round_tag = f" {DIM}r{round_num}{RESET}"

        sys.stdout.write(
            f"\n  {DIM}\u2502{RESET} {YELLOW}{ICON_DELEGATE}{RESET} "
            f"{color}{display_name}{RESET}"
            f" {DIM}{ICON_ARROW} {short_task}{RESET}{round_tag}\n"
        )
        sys.stdout.flush()
        self._event_count += 1
        self.spinner.start(f"{display_name} working...")

    def on_delegation_end(
        self, member_name: str, duration_s: float, is_error: bool
    ) -> None:
        """Called when an employee finishes — compact result with timing."""
        if self.spinner.active:
            self.spinner.stop()

        role_id = self._get_role_for_name(member_name)
        color = role_color(role_id)
        display_name = member_name.replace("_", " ").title()
        time_str = _format_elapsed(duration_s)

        if is_error:
            sys.stdout.write(
                f"  {DIM}\u2502{RESET} {RED}{ICON_FAIL}{RESET} "
                f"{color}{display_name}{RESET} "
                f"{RED}failed{RESET}  {DIM}{time_str}{RESET}\n"
            )
        else:
            sys.stdout.write(
                f"  {DIM}\u2502{RESET} {GREEN}{ICON_DONE}{RESET} "
                f"{color}{display_name}{RESET} "
                f"{GREEN}done{RESET}  {DIM}{time_str}{RESET}\n"
            )
        sys.stdout.flush()
        self._event_count += 1

    def on_progress(self, message: str) -> None:
        """Show a progress status line in the event feed."""
        if self.spinner.active:
            self.spinner.stop()
        self._event_separator()

        lower = message.lower()
        if "reviewing" in lower or "review" in lower:
            icon = f"{BR_BLUE}{ICON_REVIEW}{RESET}"
        elif "revis" in lower:
            icon = f"{YELLOW}{ICON_REVISE}{RESET}"
        elif "roadmap task" in lower:
            # Parse roadmap progress from message like "Roadmap task 2/5: ..."
            icon = f"{BR_CYAN}{ICON_TASK}{RESET}"
        elif "remaining" in lower:
            icon = f"{DIM}{ICON_PENDING}{RESET}"
        elif "hiring" in lower or "hired" in lower:
            icon = f"{GREEN}{ICON_HIRE}{RESET}"
        else:
            icon = f"{DIM}{ICON_DOT}{RESET}"

        sys.stdout.write(f"  {DIM}\u2502{RESET} {icon} {DIM}{message}{RESET}\n")
        sys.stdout.flush()
        self._event_count += 1
        self.spinner.start(message)

    # -- lifecycle ----------------------------------------------------------

    def start_thinking(self) -> None:
        """Call before sending a message to the router."""
        self._got_text = False
        self._start_time = time.time()
        self._event_count = 0
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
# Status colorization — post-process text with ANSI highlights
# ---------------------------------------------------------------------------
def _colorize_status(line: str) -> str:
    """Apply color to status-related patterns in a line."""
    # Task status icons
    line = line.replace("[x]", f"{GREEN}{ICON_DONE}{RESET}")
    line = line.replace("[!]", f"{RED}{ICON_FAIL}{RESET}")
    line = line.replace("[~]", f"{YELLOW}{ICON_TASK}{RESET}")
    line = line.replace("[ ]", f"{DIM}{ICON_PENDING}{RESET}")

    # Status words (word-boundary safe)
    line = re.sub(r'(?<!\w)(idle)(?!\w)', rf'{DIM}\1{RESET}', line)
    line = re.sub(r'(?<!\w)(working:)', rf'{YELLOW}\1{RESET}', line)
    line = re.sub(r'(?<!\w)(DONE)(?!\w)', rf'{GREEN}\1{RESET}', line)
    line = re.sub(r'(?<!\w)(FAILED)(?!\w)', rf'{RED}\1{RESET}', line)
    line = re.sub(r'(?<!\w)(COMPLETED)(?!\w)', rf'{GREEN}\1{RESET}', line)
    line = re.sub(r'(?<!\w)(REVISED)(?!\w)', rf'{YELLOW}\1{RESET}', line)

    # Dollar amounts
    line = re.sub(r'(\$[\d.]+)', rf'{GREEN}\1{RESET}', line)

    # Team Lead tag
    line = line.replace("(Team Lead)", f"{BOLD}{YELLOW}(Lead){RESET}")

    # Warning keyword
    line = re.sub(r'(?<!\w)(Warning:)', rf'{YELLOW}{ICON_WARN} \1{RESET}', line)

    # Paused/Interrupted/Stopped tags
    line = line.replace("*Paused*", f"{YELLOW}{BOLD}PAUSED{RESET}")
    line = line.replace("*Interrupted*", f"{BR_RED}{BOLD}INTERRUPTED{RESET}")
    line = line.replace("*Stopped*", f"{RED}{BOLD}STOPPED{RESET}")
    line = re.sub(r'(?<!\w)(PAUSED)(?!\w)', rf'{YELLOW}{BOLD}\1{RESET}', line)
    line = re.sub(r'(?<!\w)(INTERRUPTED)(?!\w)', rf'{BR_RED}{BOLD}\1{RESET}', line)
    line = re.sub(r'(?<!\w)(STOPPED)(?!\w)', rf'{RED}{BOLD}\1{RESET}', line)

    # Paused-here marker
    line = line.replace("← paused here", f"{YELLOW}← paused here{RESET}")
    line = line.replace("← interrupted here", f"{BR_RED}← interrupted here{RESET}")

    return line


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
            output.append(f"  {DIM}{'\u2500' * 50}{RESET}")
            continue

        if in_code_block:
            output.append(f"  {DIM}  {line}{RESET}")
            continue

        # Horizontal rule
        if stripped and len(stripped) >= 3 and all(c in "-*_" for c in stripped):
            output.append(f"  {DIM}{'\u2500' * 50}{RESET}")
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

        # Status colorization
        formatted = _colorize_status(formatted)

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
    """Set up tab completion for commands and employee names."""
    if not HAS_READLINE:
        return

    roles = list_roles(router.config)
    employee_names = list(router.company.employees.keys()) if router.company else []

    def completer(text: str, state: int) -> str | None:
        line = readline.get_line_buffer().lstrip()
        options: list[str] = []

        if not line or line == text:
            # First word — suggest commands and roles
            options = [c for c in _COMMANDS if c.startswith(text.lower())]
            options.extend(r for r in roles if r.startswith(text.lower()))
        else:
            first_word = line.split()[0].lower()
            if first_word in ("hire",):
                options = [r for r in roles if r.startswith(text.lower())]
            elif first_word in ("fire", "talk", "assign", "promote", "history"):
                options = [
                    n for n in employee_names if text.lower() in n.lower()
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
# Startup display
# ---------------------------------------------------------------------------
def _print_startup(router: Router, session_name: str) -> None:
    """Print a clean, intentional startup screen."""
    print()
    print(f"  {BOLD}{BR_CYAN}shipwright{RESET}")
    print()

    company = router.company
    cto = company.get_cto() if company else None
    n_emp = len([e for e in company.employees.values() if e.role != "cto"]) if company else 0

    if cto or n_emp:
        # Restored session — show compact state
        parts: list[str] = []
        if cto:
            parts.append(f"{BOLD}{BR_CYAN}CTO{RESET} {GREEN}online{RESET}")
        if n_emp:
            working = [e for e in company.employees.values()
                       if e.status == EmployeeStatus.WORKING and e.role != "cto"]
            if working:
                names = ", ".join(e.name for e in working[:3])
                parts.append(f"{YELLOW}{len(working)}{RESET} working ({names})")
            else:
                parts.append(f"{n_emp} employee{'s' if n_emp != 1 else ''}")

        sep = f" {DIM}{ICON_DOT}{RESET} "
        print(f"  {sep.join(parts)}")

        # Show roadmap state if active
        rm = company.active_roadmap
        if rm:
            if rm.state in (RoadmapState.PAUSED, RoadmapState.INTERRUPTED):
                label = "paused" if rm.state == RoadmapState.PAUSED else "interrupted"
                desc = rm.paused_task_description or ""
                short = f": {desc[:45]}" if desc else ""
                print(f"  {YELLOW}{ICON_WARN}{RESET} Roadmap {rm.done_count}/{rm.total_count} {YELLOW}{label}{RESET}{short}")
                print(f"  Type {BOLD}continue{RESET} to resume or {BOLD}roadmap{RESET} for details.")
            elif rm.state == RoadmapState.RUNNING:
                print(f"  {BR_CYAN}{ICON_TASK}{RESET} Roadmap {rm.done_count}/{rm.total_count} in progress")

        if company.is_stale:
            print(f"  {YELLOW}{ICON_WARN} Worktree no longer exists (stale){RESET}")

        if session_name != "default":
            print(f"  {DIM}session: {session_name}{RESET}")
    else:
        # Fresh session
        print(f"  {DIM}Describe what you need. The CTO handles the rest.{RESET}")
        print(f"  {DIM}Type {RESET}{BOLD}help{RESET}{DIM} for commands.{RESET}")

    print()


# ---------------------------------------------------------------------------
# Prompt builder
# ---------------------------------------------------------------------------
def _build_prompt(router: Router) -> str:
    """Build the input prompt showing who the user is talking to."""
    active = router.company.active_employee if router.company else None
    if active:
        color = role_color(active.role)
        if active.role == "cto":
            return f"{BOLD}{BR_CYAN}CTO{RESET} {DIM}\u203a{RESET} "
        else:
            return (
                f"{color}{active.name}{RESET}"
                f" {DIM}({active.display_role}) \u203a{RESET} "
            )
    return f"{BOLD}{BR_CYAN}shipwright{RESET} {DIM}\u203a{RESET} "


# ---------------------------------------------------------------------------
# REPL
# ---------------------------------------------------------------------------
async def run_repl(config: Config, session_name: str = "default") -> None:
    """Run the interactive REPL."""
    # Restore default signal handling so Ctrl+C works in the REPL
    signal.signal(signal.SIGINT, signal.default_int_handler)

    session = Session(id=session_name)

    # Restore state if available
    saved = load_state(config, session_id=session_name)
    if saved:
        router = Router.from_dict(saved, config)
        router.session_name = session_name
    else:
        router = Router(config=config, session=session, session_name=session_name)

    _print_startup(router, session_name)

    # Setup readline
    _setup_readline(config)
    _setup_completer(router)

    ui = CLIOutput(company=router.company)

    while True:
        try:
            prompt = _build_prompt(router)
            line = input(prompt).strip()

            if not line:
                continue

            if line.lower() in ("quit", "exit", "q"):
                save_state(router.to_dict(), config, session_id=router.session_name)
                print(f"\n  {DIM}State saved. Goodbye.{RESET}\n")
                break

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

                if response and response.strip():
                    if ui.streamed:
                        # Lead text was already streamed
                        # If events happened after, add a closing separator
                        if ui._event_count > 0:
                            pass  # events already visually separated
                        print()
                    else:
                        # Command response — render with markdown
                        print()
                        print(render_markdown(response))
                elif not ui.streamed:
                    print(f"\n  {DIM}(no response){RESET}")

                # Show timing for non-trivial operations
                elapsed = ui.elapsed
                if elapsed >= 2.0:
                    time_str = _format_elapsed(elapsed)
                    print(f"  {DIM}{ICON_DONE} {time_str}{RESET}")
                print()

            except KeyboardInterrupt:
                ui.finish_response()
                # Reset employee status if it was mid-work
                active = router.company.active_employee if router.company else None
                if active and active.status == EmployeeStatus.WORKING:
                    active.status = EmployeeStatus.IDLE
                # Interrupt roadmap if one is running
                if router.company.active_roadmap and not router.company.active_roadmap.paused:
                    from shipwright.company.employee import RoadmapTaskStatus
                    roadmap = router.company.active_roadmap
                    roadmap.paused = True
                    roadmap.state = RoadmapState.INTERRUPTED
                    for t in roadmap.tasks:
                        if t.status.value == "running":
                            t.status = RoadmapTaskStatus.PENDING
                    desc = roadmap.paused_task_description or "current work"
                    print(
                        f"\n  {YELLOW}{ICON_WARN} Interrupted.{RESET}"
                        f" {DIM}{desc[:50]}{RESET}\n"
                        f"  Type {BOLD}continue{RESET} to resume, "
                        f"{BOLD}stop{RESET} to cancel.\n"
                    )
                else:
                    print(f"\n  {DIM}Cancelled.{RESET}\n")
                continue
            except asyncio.CancelledError:
                ui.finish_response()
                print(f"\n  {DIM}Cancelled.{RESET}\n")
                continue

            # Auto-save after each interaction
            save_state(router.to_dict(), config, session_id=router.session_name)

            # Refresh completer with potentially new employees
            _setup_completer(router)

        except KeyboardInterrupt:
            print()
            continue

        except EOFError:
            print()
            save_state(router.to_dict(), config, session_id=router.session_name)
            break

        except Exception as exc:
            ui.finish_response()
            logger.error("Error: %s", exc, exc_info=True)
            print(f"\n  {RED}{ICON_FAIL} Error: {exc}{RESET}\n")


async def run_oneshot(
    config: Config, message: str, session_name: str = "default",
) -> int:
    """Run a single message through the router (non-interactive mode)."""
    session = Session(id=session_name)

    saved = load_state(config, session_id=session_name)
    if saved:
        router = Router.from_dict(saved, config)
        router.session_name = session_name
    else:
        router = Router(config=config, session=session, session_name=session_name)

    print(f"\n  {DIM}Message:{RESET} {message}")
    print(f"  {DIM}Model:{RESET}   {config.model}")
    print(f"  {DIM}Repo:{RESET}    {config.repo_root}\n")

    ui = CLIOutput(company=router.company)
    ui.start_thinking()

    response = await router.handle_message(
        message,
        on_text=ui.on_text,
        on_delegation_start=ui.on_delegation_start,
        on_delegation_end=ui.on_delegation_end,
        on_progress=ui.on_progress,
    )

    ui.finish_response()

    if response and response.strip():
        if ui.streamed:
            print()
        else:
            print()
            print(render_markdown(response))
    elif not ui.streamed:
        print(f"\n  {DIM}(No response){RESET}")

    elapsed = ui.elapsed
    if elapsed >= 1.0:
        time_str = _format_elapsed(elapsed)
        print(f"  {DIM}{ICON_DONE} {time_str}{RESET}")
    print()

    save_state(router.to_dict(), config, session_id=router.session_name)
    return 0
