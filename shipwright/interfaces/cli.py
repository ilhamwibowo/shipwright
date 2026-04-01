"""Interactive CLI REPL — the primary interface for Shipwright.

Provides an operator-console terminal interface where users talk to the CTO,
manage their virtual engineering company, and monitor work execution.
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
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
from shipwright.workspace.git import GitError, get_current_branch, get_status

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

# Status-specific colors for the control room
STATUS_WORKING = f"{BOLD}{BR_YELLOW}"
STATUS_IDLE = DIM
STATUS_BLOCKED = f"{BOLD}{BR_RED}"
STATUS_ONLINE = BR_CYAN
HEADER_COLOR = f"{BOLD}{BR_WHITE}"

# Roster icons
ICON_CTO = "\u25c6"        # ◆
ICON_IDLE = "\u25cb"        # ○  (reuses ICON_PENDING glyph)
ICON_BLOCKED = "\u25a0"    # ■
ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")


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


def _visible_len(text: str) -> int:
    """Return the display width of a string after stripping ANSI codes."""
    return len(ANSI_RE.sub("", text))


def _pad_visible(text: str, width: int) -> str:
    """Pad a line to the requested visible width."""
    text = _truncate_visible(text, width)
    pad = max(width - _visible_len(text), 0)
    return text + (" " * pad)


def _truncate_visible(text: str, width: int) -> str:
    """Truncate text to a visible width without stripping ANSI codes."""
    if width <= 0 or _visible_len(text) <= width:
        return text

    out: list[str] = []
    visible = 0
    i = 0
    limit = max(width - 1, 0)

    while i < len(text) and visible < limit:
        if text[i] == "\x1b":
            match = ANSI_RE.match(text, i)
            if match:
                out.append(match.group(0))
                i = match.end()
                continue
        out.append(text[i])
        visible += 1
        i += 1

    out.append("…")
    if "\x1b[" in text:
        out.append(RESET)
    return "".join(out)


def _panel_width() -> int:
    """Inner content width for control-room panels."""
    return max(34, min(_term_width() - 8, 86))


def _render_panel(title: str, rows: list[str], accent: str = HEADER_COLOR) -> str:
    """Render a consistent width-aware panel."""
    content_width = _panel_width()
    title_text = f" {title} "
    top_fill = max(content_width + 2 - len(title_text), 0)

    lines = [
        f"  {DIM}╭{RESET}{accent}{title_text}{RESET}{DIM}{'─' * top_fill}╮{RESET}",
    ]

    for row in rows:
        lines.append(
            f"  {DIM}│{RESET} {_pad_visible(row, content_width)} {DIM}│{RESET}"
        )

    lines.append(f"  {DIM}╰{'─' * (content_width + 2)}╯{RESET}")
    return "\n".join(lines)


def _repo_snapshot(config: Config) -> tuple[str, str, str]:
    """Return repo name, branch, and working tree summary for the header."""
    repo_name = Path(config.repo_root).name or str(config.repo_root)

    try:
        branch = get_current_branch(config.repo_root)
    except GitError:
        branch = "n/a"

    try:
        changed = len([line for line in get_status(config.repo_root).splitlines() if line.strip()])
        tree = "clean" if changed == 0 else f"{changed} changed"
    except GitError:
        tree = "git unavailable"

    return repo_name, branch, tree


def _roadmap_tag(company) -> str:
    """Build a concise roadmap tag for headers and footers."""
    rm = company.active_roadmap if company else None
    if not rm:
        return "no roadmap"
    if rm.state == RoadmapState.RUNNING:
        state = "running"
    elif rm.state == RoadmapState.PAUSED:
        state = "paused"
    elif rm.state == RoadmapState.INTERRUPTED:
        state = "interrupted"
    elif rm.state == RoadmapState.STOPPED:
        state = "stopped"
    else:
        state = rm.state.value
    return f"roadmap {rm.done_count}/{rm.total_count} {state}"


def _render_control_header(router: Router, session_name: str) -> str:
    """Render the top-level control-room summary card."""
    company = router.company
    repo_name, branch, tree = _repo_snapshot(router.config)
    active = company.active_employee.name if company and company.active_employee else "CTO standby"
    emp_count = len([e for e in company.employees.values() if e.role != "cto"]) if company else 0

    rows = [
        f"{BOLD}{BR_CYAN}shipwright{RESET} {DIM}control room{RESET}",
        (
            f"{DIM}repo{RESET} {repo_name}  {DIM}{ICON_DOT}{RESET}  "
            f"{DIM}branch{RESET} {branch}  {DIM}{ICON_DOT}{RESET}  "
            f"{DIM}tree{RESET} {tree}"
        ),
        (
            f"{DIM}session{RESET} {session_name}  {DIM}{ICON_DOT}{RESET}  "
            f"{DIM}active{RESET} {active}  {DIM}{ICON_DOT}{RESET}  "
            f"{DIM}crew{RESET} {emp_count}"
        ),
        (
            f"{DIM}mode{RESET} autopilot  {DIM}{ICON_DOT}{RESET}  "
            f"{DIM}state{RESET} {_roadmap_tag(company)}"
        ),
    ]

    if company and company.is_stale:
        rows.append(f"{YELLOW}{ICON_WARN} stale worktree detected{RESET}")

    return _render_panel("COMMAND BRIDGE", rows, accent=f"{BOLD}{BR_CYAN}")


def _render_operator_hints(router: Router) -> str:
    """Render state-aware next-step hints."""
    company = router.company
    employees = [e for e in company.employees.values() if e.role != "cto"]
    rm = company.active_roadmap

    if rm and rm.state in (RoadmapState.PAUSED, RoadmapState.INTERRUPTED):
        rows = [
            f"{DIM}resume{RESET} continue  {DIM}{ICON_DOT}{RESET}  "
            f"{DIM}inspect{RESET} tasks / events  {DIM}{ICON_DOT}{RESET}  "
            f"{DIM}abort{RESET} stop",
        ]
    elif not employees:
        rows = [
            "Tell the CTO what to build, or hire directly with `hire backend-dev`.",
            f"{DIM}good probes{RESET} build auth flow  {DIM}{ICON_DOT}{RESET}  "
            f"fix failing tests  {DIM}{ICON_DOT}{RESET}  review repo state",
        ]
    elif rm and rm.state == RoadmapState.RUNNING:
        rows = [
            f"{DIM}watch{RESET} who / tasks / events  {DIM}{ICON_DOT}{RESET}  "
            f"{DIM}interrupt{RESET} pause  {DIM}{ICON_DOT}{RESET}  "
            f"{DIM}steer{RESET} give the CTO a new directive",
        ]
    else:
        rows = [
            f"{DIM}crew{RESET} who  {DIM}{ICON_DOT}{RESET}  "
            f"{DIM}board{RESET} tasks  {DIM}{ICON_DOT}{RESET}  "
            f"{DIM}feed{RESET} events  {DIM}{ICON_DOT}{RESET}  "
            f"{DIM}help{RESET} help",
        ]

    return _render_panel("OPS", rows, accent=BR_YELLOW)


def _render_session_panel(router: Router) -> str:
    """Render a session-focused control card."""
    company = router.company
    session_name = getattr(router, "session_name", "default")
    active = company.active_employee.name if company and company.active_employee else "CTO standby"
    message_count = len(router.session.messages) if router and router.session else 0
    event_count = len(getattr(router, "_events", []))
    cwd = Path(router.config.repo_root).name or str(router.config.repo_root)

    rows = [
        f"{DIM}session{RESET} {session_name}",
        f"{DIM}workspace{RESET} {cwd}",
        f"{DIM}messages{RESET} {message_count}",
        f"{DIM}events{RESET} {event_count}",
        f"{DIM}active{RESET} {active}",
    ]

    return _render_panel("SESSION", rows, accent=BR_GREEN)


def _render_roadmap_panel(router: Router) -> str:
    """Render a roadmap card showing task execution status."""
    company = router.company
    rm = company.active_roadmap if company else None

    if not rm:
        rows = [
            "No active roadmap.",
            f"{DIM}Ask the CTO for a plan, or use `go` after a plan appears.{RESET}",
        ]
        return _render_panel("ROADMAP", rows, accent=BR_CYAN)

    if rm.state == RoadmapState.RUNNING:
        state_line = f"{BR_GREEN}running{RESET}"
    elif rm.state == RoadmapState.PAUSED:
        state_line = f"{YELLOW}paused{RESET}"
    elif rm.state == RoadmapState.INTERRUPTED:
        state_line = f"{BR_RED}interrupted{RESET}"
    elif rm.state == RoadmapState.STOPPED:
        state_line = f"{RED}stopped{RESET}"
    else:
        state_line = rm.state.value

    rows = [
        f"{DIM}request{RESET} {_truncate_visible(rm.original_request or 'No request recorded.', _panel_width() - 10)}",
        f"{DIM}progress{RESET} {rm.done_count}/{rm.total_count} {state_line}",
    ]

    if rm.state in (RoadmapState.PAUSED, RoadmapState.INTERRUPTED):
        rows.append(f"{DIM}action{RESET} continue / stop")
    elif rm.state == RoadmapState.RUNNING:
        rows.append(f"{DIM}action{RESET} watch tasks move forward")
    elif rm.state == RoadmapState.STOPPED:
        rows.append(f"{DIM}action{RESET} start a new request")
    elif not rm.approved:
        rows.append(f"{DIM}action{RESET} go / approve to start")

    task_rows: list[str] = []
    for task in rm.tasks[:6]:
        icon = {
            "pending": ICON_PENDING,
            "running": ICON_TASK,
            "done": ICON_DONE,
            "failed": ICON_FAIL,
        }[task.status.value]
        state_color = {
            "pending": DIM,
            "running": BR_YELLOW,
            "done": GREEN,
            "failed": RED,
        }[task.status.value]
        prefix = "↳ " if rm.current_task_index == task.index and task.status.value == "running" else ""
        suffix = ""
        if rm.state == RoadmapState.PAUSED and task.index == (rm.current_task_index or 0):
            suffix = "  paused here"
        elif rm.state == RoadmapState.INTERRUPTED and task.index == (rm.current_task_index or 0):
            suffix = "  interrupted here"
        task_rows.append(
            f"{state_color}{icon}{RESET} {task.index}. {prefix}{_truncate_visible(task.description, _panel_width() - 12)}{suffix}"
        )

    if task_rows:
        rows.append("")
        rows.extend(task_rows)
        if len(rm.tasks) > len(task_rows):
            rows.append(f"{DIM}... and {len(rm.tasks) - len(task_rows)} more task(s){RESET}")

    return _render_panel("ROADMAP", rows, accent=BR_CYAN)


# ---------------------------------------------------------------------------
# Crew roster — live panel showing everyone and their current state
# ---------------------------------------------------------------------------
def _render_roster(company) -> str:
    """Render the crew roster panel with ANSI colors.

    Used at startup and in direct-render contexts (not through the markdown
    pipeline).
    """
    if not company or not company.employees:
        return ""

    lines: list[str] = []

    # CTO
    cto = company.get_cto()
    if cto:
        if cto.status == EmployeeStatus.WORKING:
            lines.append(
                f"  {STATUS_ONLINE}{ICON_CTO}{RESET}  {BOLD}CTO{RESET}"
                f"  {STATUS_WORKING}working{RESET}"
            )
        else:
            lines.append(
                f"  {STATUS_ONLINE}{ICON_CTO}{RESET}  {BOLD}CTO{RESET}"
                f"  {STATUS_ONLINE}online{RESET}"
            )

    # Other employees
    for emp in company.employees.values():
        if emp.role == "cto":
            continue

        rc = role_color(emp.role)

        if emp.status == EmployeeStatus.WORKING:
            icon = f"{BR_YELLOW}{ICON_TASK}{RESET}"
            task_desc = (
                emp.current_task.description.split("\n")[0][:30]
                if emp.current_task else "..."
            )
            elapsed = ""
            if emp.current_task and emp.current_task.created_at:
                secs = time.time() - emp.current_task.created_at
                elapsed = f"  {DIM}{_format_elapsed(secs)}{RESET}"
            status_part = f"{YELLOW}{task_desc}{RESET}{elapsed}"
        elif emp.status == EmployeeStatus.BLOCKED:
            icon = f"{BR_RED}{ICON_BLOCKED}{RESET}"
            status_part = f"{BR_RED}blocked{RESET}"
        else:
            icon = f"{DIM}{ICON_IDLE}{RESET}"
            nt = len(emp.task_history)
            if nt:
                status_part = f"{DIM}idle {ICON_DOT} {nt}t{RESET}"
            else:
                status_part = f"{DIM}idle{RESET}"

        lines.append(
            f"{icon}  {rc}{emp.name}{RESET} {DIM}{ICON_DOT}{RESET} "
            f"{DIM}{emp.display_role}{RESET}  {status_part}"
        )

    return _render_panel("CREW", lines)


# ---------------------------------------------------------------------------
# Event log — recent activity feed for direct display
# ---------------------------------------------------------------------------
def _render_event_log(events: list[dict], limit: int = 5) -> str:
    """Render recent events with ANSI colors for direct display."""
    if not events:
        return ""

    from datetime import datetime

    lines: list[str] = []

    _ICONS = {
        "hire": f"{BR_GREEN}+{RESET}",
        "fire": f"{RED}\u2212{RESET}",
        "delegate": f"{BR_YELLOW}{ICON_DELEGATE}{RESET}",
        "done": f"{GREEN}{ICON_DONE}{RESET}",
        "fail": f"{RED}{ICON_FAIL}{RESET}",
        "pause": f"{YELLOW}\u2016{RESET}",
        "resume": f"{BR_GREEN}{ICON_DELEGATE}{RESET}",
        "stop": f"{RED}{ICON_BLOCKED}{RESET}",
    }

    for ev in events[-limit:]:
        ts = datetime.fromtimestamp(ev["ts"]).strftime("%H:%M")
        icon = _ICONS.get(ev["kind"], f"{DIM}{ICON_DOT}{RESET}")
        name = ev.get("name", "")
        detail = ev.get("detail", "")

        if ev["kind"] == "hire":
            text = f"Hired {name} as {detail}"
        elif ev["kind"] == "fire":
            text = f"Dismissed {name}"
        elif ev["kind"] == "delegate":
            text = f"{name} {ICON_ARROW} {detail}"
        elif ev["kind"] == "done":
            text = f"{name} done  {DIM}{detail}{RESET}"
        elif ev["kind"] == "fail":
            text = f"{name} {RED}failed{RESET}  {DIM}{detail}{RESET}"
        elif ev["kind"] == "pause":
            text = "Roadmap paused"
        elif ev["kind"] == "resume":
            text = "Roadmap resumed"
        elif ev["kind"] == "stop":
            text = "Roadmap stopped"
        else:
            text = f"{name} {detail}".strip()

        lines.append(f"{DIM}{ts}{RESET}  {icon} {text}")

    return _render_panel("EVENTS", lines)


# ---------------------------------------------------------------------------
# Status strip — compact one-line company state above the prompt
# ---------------------------------------------------------------------------
def _render_status_strip(router: Router) -> str:
    """Render a compact status strip above the prompt with color accents."""
    company = router.company
    if not company:
        return ""

    segments: list[str] = []

    # CTO status
    cto = company.get_cto()
    if cto:
        segments.append(f"{DIM}CTO{RESET}")
    else:
        segments.append(f"{DIM}CTO offline{RESET}")

    # Employee count — highlight working in yellow
    n_emp = len([e for e in company.employees.values() if e.role != "cto"])
    if n_emp:
        working = [e for e in company.employees.values()
                   if e.status == EmployeeStatus.WORKING and e.role != "cto"]
        if working:
            segments.append(
                f"{BR_YELLOW}{len(working)}{RESET}"
                f"{DIM}/{n_emp} working{RESET}"
            )
        else:
            segments.append(f"{DIM}{n_emp} idle{RESET}")

    # Roadmap progress
    rm = company.active_roadmap
    if rm:
        if rm.state == RoadmapState.RUNNING:
            segments.append(
                f"{DIM}roadmap {rm.done_count}/{rm.total_count}{RESET}"
            )
        elif rm.state in (RoadmapState.PAUSED, RoadmapState.INTERRUPTED):
            label = "paused" if rm.state == RoadmapState.PAUSED else "interrupted"
            segments.append(f"{YELLOW}roadmap {label}{RESET}")
        elif rm.state == RoadmapState.STOPPED:
            segments.append(f"{DIM}roadmap stopped{RESET}")

    events = len(getattr(router, "_events", []))
    if events:
        segments.append(f"{DIM}events {events}{RESET}")

    # Session tag
    session_name = getattr(router, "session_name", "default")
    if session_name != "default":
        segments.append(f"{DIM}{session_name}{RESET}")

    if not segments:
        return ""

    sep = f" {DIM}{ICON_DOT}{RESET} "
    return f"  {sep.join(segments)}"


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
    "repo",
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
    "tasks",
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
        self._speaker_label: str | None = None
        self._speaker_role: str | None = None

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
        """Stream lead text — stops spinner on first chunk, prints high-contrast."""
        if self.spinner.active:
            self.spinner.stop()
        if not self._got_text:
            self._got_text = True
            if self._speaker_label:
                color = role_color(self._speaker_role or "cto")
                sys.stdout.write(
                    f"\n  {color}[{self._speaker_label}]{RESET} {BR_WHITE}"
                )
            else:
                sys.stdout.write(f"\n  {BR_WHITE}")
        sys.stdout.write(text)
        sys.stdout.flush()

    def on_delegation_start(
        self, member_name: str, task: str, round_num: int, max_rounds: int
    ) -> None:
        """Called when an employee starts working — colored event line."""
        if self.spinner.active:
            self.spinner.stop()
        sys.stdout.write(RESET)
        self._event_separator()

        display_name = member_name.replace("_", " ").title()
        short_task = task.split("\n")[0][:55]

        # Look up role color for the employee
        rc = ""
        if self._company and member_name in self._company.employees:
            rc = role_color(self._company.employees[member_name].role)

        round_tag = ""
        if round_num > 1:
            round_tag = f" {DIM}r{round_num}{RESET}"

        sys.stdout.write(
            f"\n    {rc}{display_name}{RESET} {DIM}{ICON_ARROW}{RESET} "
            f"{DIM}{short_task}{RESET}{round_tag}\n"
        )
        sys.stdout.flush()
        self._event_count += 1
        self.spinner.start(f"{display_name} working...")

    def on_delegation_end(
        self, member_name: str, duration_s: float, is_error: bool
    ) -> None:
        """Called when an employee finishes — colored result with timing."""
        if self.spinner.active:
            self.spinner.stop()

        display_name = member_name.replace("_", " ").title()
        time_str = _format_elapsed(duration_s)

        # Look up role color for the employee
        rc = ""
        if self._company and member_name in self._company.employees:
            rc = role_color(self._company.employees[member_name].role)

        if is_error:
            sys.stdout.write(
                f"    {rc}{display_name}{RESET} {BOLD}{RED}failed{RESET}"
                f"  {DIM}{time_str}{RESET}\n"
            )
        else:
            sys.stdout.write(
                f"    {rc}{display_name}{RESET} {GREEN}done{RESET}"
                f"  {DIM}{time_str}{RESET}\n"
            )
        sys.stdout.flush()
        self._event_count += 1

    def on_progress(self, message: str) -> None:
        """Show a progress status line — quiet, secondary to conversation."""
        if self.spinner.active:
            self.spinner.stop()
        self._event_separator()

        sys.stdout.write(f"    {DIM}{message}{RESET}\n")
        sys.stdout.flush()
        self._event_count += 1
        self.spinner.start(message)

    # -- lifecycle ----------------------------------------------------------

    def start_thinking(
        self,
        speaker_label: str | None = None,
        speaker_role: str | None = None,
    ) -> None:
        """Call before sending a message to the router."""
        self._got_text = False
        self._start_time = time.time()
        self._event_count = 0
        self._speaker_label = speaker_label
        self._speaker_role = speaker_role
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

    # Roster and event feed icons (from who/tasks/events commands)
    if "\u25cf " in line:
        line = line.replace("\u25cf ", f"{BR_YELLOW}\u25cf{RESET} ")
    if "\u25cb " in line:
        line = line.replace("\u25cb ", f"{DIM}\u25cb{RESET} ")
    if "\u25c6 " in line:
        line = line.replace("\u25c6 ", f"{BR_CYAN}\u25c6{RESET} ")
    if "\u25a0 " in line:
        line = line.replace("\u25a0 ", f"{RED}\u25a0{RESET} ")
    if "\u25b6 " in line:
        line = line.replace("\u25b6 ", f"{BR_YELLOW}\u25b6{RESET} ")
    if "\u2713 " in line:
        line = line.replace("\u2713 ", f"{GREEN}\u2713{RESET} ")
    if "\u2717 " in line:
        line = line.replace("\u2717 ", f"{RED}\u2717{RESET} ")
    if "\u2016 " in line:
        line = line.replace("\u2016 ", f"{YELLOW}\u2016{RESET} ")
    if "\u2212 " in line:
        line = line.replace("\u2212 ", f"{RED}\u2212{RESET} ")

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

        # Blockquote
        if stripped.startswith("> "):
            content = stripped[2:]
            content = re.sub(r"\*\*(.+?)\*\*", rf"{BOLD}\1{RESET}", content)
            content = re.sub(r"`([^`]+)`", rf"{DIM}\1{RESET}", content)
            content = _colorize_status(content)
            output.append(f"  {BR_CYAN}▌{RESET} {DIM}{content}{RESET}")
            continue

        # Lists
        bullet_match = re.match(r"^([-*+])\s+(.+)$", stripped)
        if bullet_match:
            content = bullet_match.group(2)
            content = re.sub(r"\*\*(.+?)\*\*", rf"{BOLD}\1{RESET}", content)
            content = re.sub(r"`([^`]+)`", rf"{DIM}\1{RESET}", content)
            content = _colorize_status(content)
            output.append(f"  {DIM}•{RESET} {content}")
            continue

        ordered_match = re.match(r"^(\d+)\.\s+(.+)$", stripped)
        if ordered_match:
            number = ordered_match.group(1)
            content = ordered_match.group(2)
            content = re.sub(r"\*\*(.+?)\*\*", rf"{BOLD}\1{RESET}", content)
            content = re.sub(r"`([^`]+)`", rf"{DIM}\1{RESET}", content)
            content = _colorize_status(content)
            output.append(f"  {BOLD}{number}.{RESET} {content}")
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
    """Print the control-room startup screen with crew roster."""
    print()
    print(_render_control_header(router, session_name))
    print()
    print(_render_operator_hints(router))
    print()
    print(_render_session_panel(router))

    company = router.company

    # Show roster if there are employees
    if company and company.employees:
        print()
        print(_render_roster(company))

    print()
    print(_render_roadmap_panel(router))

    # Recent events (from restored session)
    events = getattr(router, "_events", [])
    if events:
        print()
        print(_render_event_log(events, limit=5))

    if company and company.is_stale:
        print()
        print(f"  {YELLOW}{ICON_WARN} Stale worktree{RESET}")

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
            return f"{BOLD}{BR_CYAN}[CTO]{RESET} {DIM}\u203a{RESET} "
        else:
            return (
                f"{color}[{active.name}]{RESET}"
                f" {DIM}{active.display_role} \u203a{RESET} "
            )
    return f"{BOLD}{BR_CYAN}[CTO]{RESET} {DIM}\u203a{RESET} "


def _response_identity(router: Router, text: str) -> tuple[str, str]:
    """Infer the likely responding party for streamed output framing."""
    at_match = re.match(r'^@(\w+)\s+(.+)$', text.strip(), re.DOTALL)
    if at_match:
        resolved = router._resolve_name(at_match.group(1))
        if resolved and resolved in router.company.employees:
            emp = router.company.employees[resolved]
            return emp.name, emp.role

    active = router.company.active_employee if router.company else None
    if active:
        return active.name, active.role
    return "CTO", "cto"


def _render_cycle_footer(ui: CLIOutput, router: Router) -> str:
    """Render a compact footer after each response cycle."""
    segments: list[str] = []

    if ui._event_count:
        segments.append(f"{ui._event_count} ops")
    if ui.elapsed >= 0.5:
        segments.append(_format_elapsed(ui.elapsed))

    rm = router.company.active_roadmap if router.company else None
    if rm:
        segments.append(f"{rm.done_count}/{rm.total_count} {rm.state.value}")

    active = router.company.active_employee.name if router.company and router.company.active_employee else "CTO"
    if segments or active != "CTO":
        segments.append(f"{active}")

    if not segments:
        return ""

    return f"  {DIM}{f' {ICON_DOT} '.join(segments)}{RESET}"


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

    # Event-logging wrappers for the control-room feed
    def _on_deleg_start(name, task, rn, mr):
        router._log_event("delegate", name, task.split("\n")[0][:50])
        ui.on_delegation_start(name, task, rn, mr)

    def _on_deleg_end(name, dur, err):
        router._log_event("fail" if err else "done", name, _format_elapsed(dur))
        ui.on_delegation_end(name, dur, err)

    def _checkpoint():
        save_state(router.to_dict(), config, session_id=router.session_name)

    while True:
        try:
            strip = _render_status_strip(router)
            if strip:
                sys.stdout.write(strip + "\n")
                sys.stdout.flush()
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
                speaker_label, speaker_role = _response_identity(router, line)
                ui.start_thinking(speaker_label=speaker_label, speaker_role=speaker_role)

                response = await router.handle_message(
                    line,
                    on_text=ui.on_text,
                    on_delegation_start=_on_deleg_start,
                    on_delegation_end=_on_deleg_end,
                    on_progress=ui.on_progress,
                    on_checkpoint=_checkpoint,
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

                footer = _render_cycle_footer(ui, router)
                if footer:
                    print(footer)
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

    def _on_deleg_start(name, task, rn, mr):
        router._log_event("delegate", name, task.split("\n")[0][:50])
        ui.on_delegation_start(name, task, rn, mr)

    def _on_deleg_end(name, dur, err):
        router._log_event("fail" if err else "done", name, _format_elapsed(dur))
        ui.on_delegation_end(name, dur, err)

    def _checkpoint():
        save_state(router.to_dict(), config, session_id=router.session_name)

    speaker_label, speaker_role = _response_identity(router, message)
    ui.start_thinking(speaker_label=speaker_label, speaker_role=speaker_role)

    response = await router.handle_message(
        message,
        on_text=ui.on_text,
        on_delegation_start=_on_deleg_start,
        on_delegation_end=_on_deleg_end,
        on_progress=ui.on_progress,
        on_checkpoint=_checkpoint,
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

    footer = _render_cycle_footer(ui, router)
    if footer:
        print(footer)
    print()

    save_state(router.to_dict(), config, session_id=router.session_name)
    return 0
