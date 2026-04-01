"""Router — handles user messages for the Shipwright V2 company model.

Routes commands and conversational messages through the Company.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Callable

from shipwright.config import Config
from shipwright.conversation.session import Session
from shipwright.company.company import Company
from shipwright.company.employee import EmployeeStatus, RoadmapState
from shipwright.company.roles import (
    BUILTIN_ROLES,
    ROLE_DISPLAY_NAMES,
    get_role_def,
    get_specialist_def,
    inspect_role,
    list_installed,
    list_roles,
    list_specialists,
)
from shipwright.persistence.store import (
    clear_state,
    list_sessions,
    load_state,
    save_state,
)
from shipwright.utils.logging import get_logger
from shipwright.workspace.project import ProjectInfo, discover_project

logger = get_logger("conversation.router")


# ---------------------------------------------------------------------------
# Intent classification — gate before CTO execution
# ---------------------------------------------------------------------------

class Intent:
    """Classified intent of a user message."""
    GREETING = "greeting"
    STATUS_QUERY = "status_query"
    RESUME = "resume"
    PAUSE = "pause"
    PAUSE_NOW = "pause_now"
    STOP = "stop"
    COMMAND = "command"
    TASK = "task"


# Greeting patterns — casual greetings that should NEVER trigger work
_GREETING_PATTERNS: set[str] = {
    "hi", "hello", "hey", "sup", "yo", "oi", "hola",
    "morning", "good morning", "gm",
    "afternoon", "good afternoon",
    "evening", "good evening",
    "howdy", "whats up", "what's up", "wassup", "wazzup",
    "hiya", "heya", "heyy", "heyyy",
    "greetings", "salutations",
    "hi there", "hello there", "hey there",
    "good day", "g'day",
}

# Small-talk patterns that are NOT work requests
_SMALLTALK_PATTERNS: set[str] = {
    "how are you", "how's it going", "how are things",
    "what's good", "how do you do",
    "long time no see", "nice to see you",
    "thanks", "thank you", "ty", "thx",
    "cool", "nice", "great", "awesome", "ok", "okay", "k",
    "got it", "understood", "noted",
}

_RESUME_PATTERNS: set[str] = {
    "continue", "resume", "go on", "keep going",
    "pick up where we left off", "carry on",
    "proceed", "let's continue", "let's resume",
    "continue roadmap", "resume roadmap",
}

_PAUSE_PATTERNS: set[str] = {
    "pause", "hold", "hold on", "wait",
    "pause roadmap", "hold roadmap",
}

_PAUSE_NOW_PATTERNS: set[str] = {
    "pause now", "stop now", "halt", "halt now",
    "abort", "pause immediately", "stop immediately",
}

_STOP_PATTERNS: set[str] = {
    "stop", "cancel", "cancel roadmap", "stop roadmap",
    "drop it", "nevermind", "never mind", "nvm",
    "scrap it", "kill it", "scratch that",
}


def classify_intent(text: str) -> str:
    """Classify user intent from message text.

    Returns one of the Intent constants. This runs BEFORE any command parsing
    or CTO routing, so it catches greetings and execution controls early.
    """
    lower = text.lower().strip().rstrip("!?.,:;")

    # Exact match first (highest confidence)
    if lower in _PAUSE_NOW_PATTERNS:
        return Intent.PAUSE_NOW
    if lower in _PAUSE_PATTERNS:
        return Intent.PAUSE
    if lower in _STOP_PATTERNS:
        return Intent.STOP
    if lower in _RESUME_PATTERNS:
        return Intent.RESUME
    if lower in _GREETING_PATTERNS:
        return Intent.GREETING
    if lower in _SMALLTALK_PATTERNS:
        return Intent.GREETING  # treat small talk same as greeting

    # Fuzzy greeting detection: short messages that look casual
    # Only 1-2 words where the first word is a greeting — "hi team", "hey cto"
    # 3+ words starting with a greeting word are likely real requests ("hello world endpoint")
    words = lower.split()
    if len(words) <= 2:
        if words[0] in {"hi", "hello", "hey", "sup", "yo", "oi", "morning",
                         "afternoon", "evening", "howdy", "hiya", "heya",
                         "greetings", "hola", "gm"}:
            return Intent.GREETING

    return Intent.TASK


@dataclass
class Router:
    """Routes user messages to the right employee/team.

    Manages the lifecycle of the company: hiring, firing, team management,
    work assignment, and conversation routing.
    """

    config: Config
    session: Session
    company: Company = field(init=False)
    session_name: str = "default"
    _project_info: ProjectInfo | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        self.company = Company(config=self.config)

    @property
    def project_info(self) -> ProjectInfo:
        if self._project_info is None:
            self._project_info = discover_project(self.config.repo_root)
        return self._project_info

    # ------------------------------------------------------------------
    # Main entry point
    # ------------------------------------------------------------------

    async def handle_message(
        self,
        text: str,
        on_text: Callable[[str], None] | None = None,
        on_delegation_start: Callable[[str, str, int, int], None] | None = None,
        on_delegation_end: Callable[[str, float, bool], None] | None = None,
        on_progress: Callable[[str], None] | None = None,
    ) -> str:
        """Process a user message and return the response.

        The dispatch order is:
        0. Intent classification — catch greetings, pause/stop/resume early
        1. Direct employee access: @name message
        2. Async commands that need ``await`` (assign work, ship)
        3. Synchronous commands (hire, fire, status, ...)
        4. Conversational fallback — route to CTO or active employee
        """
        text = text.strip()
        if not text:
            return ""

        # Cap extremely long input to prevent resource issues
        MAX_INPUT_LEN = 10_000
        if len(text) > MAX_INPUT_LEN:
            text = text[:MAX_INPUT_LEN]

        self.session.add_user_message(text)

        # Ensure project context is populated
        if not self.company.project_context:
            self.company.project_context = self.project_info.to_prompt_context()

        lower = text.lower().strip()

        # ---- 0. Intent classification — gate before CTO --------------------
        intent = classify_intent(text)

        if intent == Intent.GREETING:
            response = self._handle_greeting(text)
            self.session.add_system_message(response)
            return response

        if intent == Intent.PAUSE:
            response = self._handle_pause()
            self.session.add_system_message(response)
            return response

        if intent == Intent.PAUSE_NOW:
            response = self._handle_pause_now()
            self.session.add_system_message(response)
            return response

        if intent == Intent.STOP:
            response = self._handle_stop()
            self.session.add_system_message(response)
            return response

        if intent == Intent.RESUME:
            response = await self._handle_resume(
                on_text=on_text,
                on_delegation_start=on_delegation_start,
                on_delegation_end=on_delegation_end,
                on_progress=on_progress,
            )
            self.session.add_system_message(response)
            return response

        # ---- 1. Direct employee access: @name message ----------------------
        at_match = re.match(r'^@(\w+)\s+(.+)$', text, re.DOTALL)
        if at_match:
            name = at_match.group(1)
            message = at_match.group(2).strip()
            resolved = self._resolve_name(name)
            if not resolved or resolved not in self.company.employees:
                response = f"No employee named '{name}'."
                self.session.add_system_message(response)
                return response
            try:
                response = await self.company.talk(
                    resolved, message,
                    on_text=on_text,
                    on_delegation_start=on_delegation_start,
                    on_delegation_end=on_delegation_end,
                    on_progress=on_progress,
                )
                self.session.add_lead_message(response, crew_id=resolved)
                return response
            except Exception as e:
                logger.error("Error talking to %s: %s", resolved, e)
                response = f"Error communicating with {resolved}: {e}"
                self.session.add_system_message(response)
                return response

        # ---- 2. Async: assign work -----------------------------------------
        # "assign <target> to <team>" — team membership (sync, handled below)
        # "assign <target> "<task>"" or "assign <target> <task>" — work (async)

        assign_match = re.match(
            r'^assign\s+(\w+)\s+"([^"]+)"$', text, re.IGNORECASE,
        )
        if not assign_match:
            # Try unquoted form, but disambiguate from "assign X to Y"
            assign_match2 = re.match(
                r'^assign\s+(\w+)\s+(.+)$', text, re.IGNORECASE,
            )
            if assign_match2:
                target = assign_match2.group(1)
                rest = assign_match2.group(2).strip()
                # "assign X to Y" → team membership (handled in sync branch)
                to_match = re.match(r'^to\s+(.+)$', rest, re.IGNORECASE)
                if to_match:
                    team_name = to_match.group(1).strip()
                    response = self._assign_to_team_cmd(target, team_name)
                    self.session.add_system_message(response)
                    return response
                # Otherwise it's a work assignment
                assign_match = assign_match2

        if assign_match:
            target = assign_match.group(1)
            task_desc = assign_match.group(2).strip().strip('"')
            if not task_desc:
                response = "Task description cannot be empty. Usage: `assign <name> \"<task>\"`"
                self.session.add_system_message(response)
                return response
            resolved = self._resolve_name(target)
            if not resolved:
                response = f"No employee or team named '{target}'."
                self.session.add_system_message(response)
                return response
            # Check if employee is already working
            if resolved in self.company.employees:
                emp = self.company.employees[resolved]
                if emp.status == EmployeeStatus.WORKING:
                    response = (
                        f"**{resolved}** is currently working"
                        f"{': ' + emp.current_task.description[:40] if emp.current_task else ''}. "
                        f"Wait for them to finish or talk to another employee."
                    )
                    self.session.add_system_message(response)
                    return response
            try:
                response = await self.company.assign_work(
                    resolved,
                    task_desc,
                    on_text=on_text,
                    on_delegation_start=on_delegation_start,
                    on_delegation_end=on_delegation_end,
                    on_progress=on_progress,
                )
                self.session.add_lead_message(response, crew_id=resolved)
                return response
            except ValueError as e:
                response = str(e)
                self.session.add_system_message(response)
                return response
            except Exception as e:
                logger.error("Unexpected error during work assignment: %s", e)
                # Reset employee status on unexpected error
                if resolved in self.company.employees:
                    emp = self.company.employees[resolved]
                    emp.status = EmployeeStatus.IDLE
                    emp.current_task = None
                response = f"Error assigning work to {resolved}: {e}"
                self.session.add_system_message(response)
                return response

        # ---- 2a. Async: roadmap approval ------------------------------------
        if lower in ("go", "approve", "ship it", "lgtm"):
            response = await self._roadmap_approve(
                on_text=on_text,
                on_delegation_start=on_delegation_start,
                on_delegation_end=on_delegation_end,
                on_progress=on_progress,
            )
            if response:
                self.session.add_system_message(response)
                return response
            # No roadmap to approve — fall through to conversational

        # ---- 2b. Async: ship / pr ------------------------------------------
        if lower.startswith("ship") or lower in ("pr", "open pr", "create pr"):
            parts = text.split(maxsplit=1)
            target = (
                parts[1].strip()
                if len(parts) > 1 and parts[0].lower() == "ship"
                else None
            )
            response = await self._ship(target)
            self.session.add_system_message(response)
            return response

        # ---- 3. Synchronous commands ----------------------------------------
        command, response = self._try_sync_command(text, lower)
        if command:
            self.session.add_system_message(response)
            return response

        # ---- 4. Conversational fallback — route to CTO or active employee ---
        employee = self.company.active_employee
        if not employee:
            # No active employee — auto-create CTO
            self.company.ensure_cto()
            employee = self.company.active_employee

        if not employee:
            # Shouldn't happen with ensure_cto, but safety fallback
            response = self._suggest_hire(text)
            self.session.add_system_message(response)
            return response

        try:
            if employee.role == "cto":
                # Route through CTO auto-pilot flow
                response = await self.company.cto_chat(
                    message=text,
                    on_text=on_text,
                    on_delegation_start=on_delegation_start,
                    on_delegation_end=on_delegation_end,
                    on_progress=on_progress,
                )
            else:
                # Direct conversation with active employee
                response = await self.company.talk(
                    employee.name,
                    text,
                    on_text=on_text,
                    on_delegation_start=on_delegation_start,
                    on_delegation_end=on_delegation_end,
                    on_progress=on_progress,
                )
            self.session.add_lead_message(response, crew_id=employee.name)
            return response
        except Exception as e:
            logger.error("Error talking to %s: %s", employee.name, e)
            employee.status = EmployeeStatus.IDLE
            employee.current_task = None
            response = f"Error communicating with {employee.name}: {e}"
            self.session.add_system_message(response)
            return response

    # ------------------------------------------------------------------
    # Sync command dispatcher
    # ------------------------------------------------------------------

    def _try_sync_command(self, text: str, lower: str) -> tuple[bool, str]:
        """Try synchronous commands. Returns (is_command, response)."""

        # roadmap / board — show current roadmap status
        if lower in ("roadmap", "roadmap status", "plan", "board"):
            return True, self._roadmap_status()

        # back — return to CTO
        if lower == "back":
            return True, self._back()

        # roles
        if lower in ("roles", "available roles"):
            return True, self._roles()

        # hire <role> [as "Name"]
        hire_match = re.match(
            r'^(?:hire)\s+([\w-]+)(?:\s+as\s+"([^"]+)"|\s+as\s+(\S+))?$',
            text,
            re.IGNORECASE,
        )
        if hire_match:
            role_id = hire_match.group(1).lower()
            custom_name = hire_match.group(2) or hire_match.group(3)
            return True, self._hire(role_id, custom_name)

        # fire <name> [confirm]
        fire_match = re.match(r'^(?:fire|dismiss)\s+(.+)$', lower)
        if fire_match:
            raw_target = fire_match.group(1).strip()
            confirmed = raw_target.endswith(" confirm")
            target = raw_target.removesuffix(" confirm").strip() if confirmed else raw_target
            return True, self._fire(target, confirmed=confirmed)

        # org / team / company — org chart view
        if lower in ("org", "team", "teams", "company"):
            return True, self._org_view()

        # who — quick view of who is doing what
        if lower in ("who", "who is working", "workers"):
            return True, self._who()

        # team create <name>
        team_create_match = re.match(r'^team\s+create\s+(.+)$', lower)
        if team_create_match:
            return True, self._team_create(team_create_match.group(1).strip())

        # promote <name> to lead of <team>
        promote_match = re.match(
            r'^promote\s+(\w+)\s+to\s+lead\s+of\s+(.+)$', text, re.IGNORECASE,
        )
        if promote_match:
            return True, self._promote(
                promote_match.group(1), promote_match.group(2).strip(),
            )

        # talk <name>
        talk_match = re.match(
            r'^(?:talk|talk\s+to|switch\s+to)\s+(\w+)$', text, re.IGNORECASE,
        )
        if talk_match:
            return True, self._talk(talk_match.group(1))

        # status — company overview
        if lower in ("status", "overview"):
            return True, self._status()

        # costs
        if lower in ("costs", "cost", "spending", "budget"):
            return True, self._costs()

        # history <name>
        history_match = re.match(r'^(?:history|log)\s+(\w+)$', lower)
        if history_match:
            return True, self._history(history_match.group(1))

        # help
        if lower in ("help", "?", "commands"):
            return True, self._help()

        # sessions
        if lower in ("sessions", "session list"):
            return True, self._list_sessions()

        session_save_match = re.match(r'^(?:session\s+save|save)\s+(.+)$', lower)
        if session_save_match:
            return True, self._session_save(session_save_match.group(1).strip())

        if lower == "save":
            return True, self._session_save(self.session_name)

        session_load_match = re.match(r'^session\s+load\s+(.+)$', lower)
        if session_load_match:
            return True, self._session_load(text[len("session load "):].strip())

        if lower in ("session clear", "session reset"):
            return True, self._session_clear(confirmed=False)

        if lower in ("session clear confirm", "session reset confirm"):
            return True, self._session_clear(confirmed=True)

        # shop / installed / inspect
        if lower in ("shop", "browse", "marketplace", "available"):
            return True, self._shop()

        if lower in ("installed", "plugins", "custom"):
            return True, self._installed()

        inspect_match = re.match(r'^inspect\s+(.+)$', lower)
        if inspect_match:
            return True, self._inspect(inspect_match.group(1).strip())

        return False, ""

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _resolve_name(self, name: str) -> str | None:
        """Resolve a case-insensitive name to an employee or team name."""
        # Exact match first
        if name in self.company.employees:
            return name
        if name in self.company.teams:
            return name
        # Case-insensitive
        for emp_name in self.company.employees:
            if emp_name.lower() == name.lower():
                return emp_name
        for team_name in self.company.teams:
            if team_name.lower() == name.lower():
                return team_name
        return None

    # ------------------------------------------------------------------
    # Command implementations
    # ------------------------------------------------------------------

    def _roles(self) -> str:
        """List all available roles."""
        roles = list_roles(self.config)
        builtin = [r for r in roles if r in BUILTIN_ROLES]
        custom = [r for r in roles if r not in BUILTIN_ROLES]

        lines = ["**Available Roles**\n"]
        for r in sorted(builtin):
            display = ROLE_DISPLAY_NAMES.get(r, r)
            lines.append(f"  `{r}` — {display}")

        if custom:
            lines.append("\n**Custom Roles (installed plugins):**")
            for r in sorted(custom):
                lines.append(f"  `{r}`")

        lines.append("\nHire with: `hire <role>` or `hire <role> as \"Name\"`")
        return "\n".join(lines)

    def _hire(self, role_id: str, custom_name: str | None = None) -> str:
        """Hire a new employee."""
        try:
            role_def = get_role_def(role_id, self.config)
        except ValueError as e:
            available = list_roles(self.config)
            return (
                f"{e}\n\n"
                f"Available roles: {', '.join(available[:10])}\n"
                f"Type `roles` for the full list."
            )

        if not self.company.project_context:
            self.company.project_context = self.project_info.to_prompt_context()

        # Validate custom name doesn't conflict with team names
        if custom_name and custom_name.lower() in {
            t.lower() for t in self.company.teams
        }:
            return (
                f"Name '{custom_name}' conflicts with an existing team. "
                f"Choose a different name."
            )

        try:
            employee = self.company.hire(role_id, role_def, name=custom_name)
        except ValueError as e:
            return str(e)

        display = ROLE_DISPLAY_NAMES.get(role_id, role_def.role)
        return f"Hired **{employee.name}** as {display} (idle)"

    def _fire(self, target: str, confirmed: bool = False) -> str:
        """Fire an employee or team. Requires confirmation."""
        resolved = self._resolve_name(target)
        if not resolved:
            return f"No employee or team named '{target}'."

        if not confirmed:
            if resolved in self.company.teams:
                team = self.company.teams[resolved]
                members = ", ".join(team.members) if team.members else "no members"
                return (
                    f"Fire team **{resolved}** ({members})? "
                    f"This will dismiss all team members.\n"
                    f"Type: `fire {resolved} confirm`"
                )
            else:
                emp = self.company.employees[resolved]
                return (
                    f"Fire **{resolved}** ({emp.role_def.role})? "
                    f"This will dismiss them and lose their session context.\n"
                    f"Type: `fire {resolved} confirm`"
                )

        if resolved in self.company.teams:
            try:
                fired = self.company.fire_team(resolved)
                names = ", ".join(e.name for e in fired)
                return f"Fired team **{resolved}** ({names})."
            except ValueError as e:
                return str(e)
        else:
            try:
                emp = self.company.fire(resolved)
                return f"Fired **{emp.name}** ({emp.role_def.role})."
            except ValueError as e:
                return str(e)

    def _org_view(self) -> str:
        """Show org chart — structured view of teams and employees."""
        n = len(self.company.employees)

        if n == 0:
            return (
                "No employees yet.\n"
                "Type `hire <role>` to get started, or just tell the CTO what to build."
            )

        lines = [f"**Org Chart** ({n} total)\n"]
        lines.append(self.company.status_summary)
        return "\n".join(lines)

    def _who(self) -> str:
        """Quick view: who is doing what right now."""
        employees = [
            e for e in self.company.employees.values() if e.role != "cto"
        ]
        if not employees:
            return "No employees hired yet."

        lines: list[str] = []
        working = [e for e in employees if e.status == EmployeeStatus.WORKING]
        idle = [e for e in employees if e.status == EmployeeStatus.IDLE]

        if working:
            lines.append("**Working**")
            for e in working:
                task = e.current_task.description[:50] if e.current_task else "..."
                lines.append(f"  {e.name} ({e.display_role}) \u2014 {task}")
        if idle:
            lines.append("**Idle**" if working else "**All idle**")
            for e in idle:
                last = ""
                if e.task_history:
                    lt = e.task_history[-1]
                    last = f" \u2014 last: {lt.description[:35]}"
                lines.append(f"  {e.name} ({e.display_role}){last}")

        return "\n".join(lines)

    def _team_create(self, name: str) -> str:
        if not name:
            return "Team name cannot be empty. Usage: `team create <name>`"
        # Check for name conflicts with employees
        if self._resolve_name(name) and name.lower() in {
            n.lower() for n in self.company.employees
        }:
            return (
                f"Name '{name}' conflicts with an existing employee. "
                f"Choose a different team name."
            )
        try:
            self.company.create_team(name)
            return f"Created team **{name}**."
        except ValueError as e:
            return str(e)

    def _promote(self, emp_name: str, team_name: str) -> str:
        resolved = self._resolve_name(emp_name)
        if not resolved or resolved not in self.company.employees:
            return f"No employee named '{emp_name}'."
        try:
            self.company.promote_to_lead(resolved, team_name)
            return f"**{resolved}** is now Team Lead of **{team_name}**."
        except ValueError as e:
            return str(e)

    def _assign_to_team_cmd(self, emp_name: str, team_name: str) -> str:
        resolved_emp = self._resolve_name(emp_name)
        if not resolved_emp or resolved_emp not in self.company.employees:
            return f"No employee named '{emp_name}'."
        resolved_team = self._resolve_name(team_name)
        if not resolved_team or resolved_team not in self.company.teams:
            return f"No team named '{team_name}'."
        try:
            self.company.assign_to_team(resolved_emp, resolved_team)
            return f"**{resolved_emp}** added to team **{resolved_team}**."
        except ValueError as e:
            return str(e)

    def _talk(self, name: str) -> str:
        resolved = self._resolve_name(name)
        if not resolved or resolved not in self.company.employees:
            return f"No employee named '{name}'."
        self.company.set_active(resolved)
        emp = self.company.employees[resolved]
        status = ""
        if emp.status == EmployeeStatus.WORKING and emp.current_task:
            status = f" \u2014 working on: {emp.current_task.description[:40]}"
        tasks = len(emp.task_history)
        tasks_tag = f", {tasks} tasks done" if tasks else ""
        return f"Switched to **{resolved}** ({emp.display_role}{tasks_tag}){status}.\nType `back` to return to CTO."

    def _back(self) -> str:
        """Return conversation to the CTO."""
        prev = self.company.active_employee
        cto = self.company.get_cto()
        if not cto:
            cto = self.company.ensure_cto()
        self.company.set_active(cto.name)
        if prev and prev.role != "cto":
            return f"Back to **CTO**. (Was talking to {prev.name})"
        return "Back to **CTO**."

    def _status(self) -> str:
        """Show concise company status."""
        n = len(self.company.employees)
        if n == 0:
            return (
                "No employees yet.\n"
                "Tell the CTO what to build, or type `hire <role>` directly."
            )

        working = [e for e in self.company.employees.values()
                   if e.status == EmployeeStatus.WORKING and e.role != "cto"]
        idle = [e for e in self.company.employees.values()
                if e.status == EmployeeStatus.IDLE and e.role != "cto"]
        n_teams = len(self.company.teams)

        parts = [f"**Status** \u2014 {n} employee{'s' if n != 1 else ''}"]
        if n_teams:
            parts[0] += f", {n_teams} team{'s' if n_teams != 1 else ''}"

        if working:
            parts.append("")
            for e in working:
                task = e.current_task.description[:45] if e.current_task else "..."
                parts.append(f"  {e.name} \u2014 working: {task}")
        if idle:
            parts.append(f"  {len(idle)} idle: {', '.join(e.name for e in idle[:5])}")

        # Roadmap
        rm = self.company.active_roadmap
        if rm:
            state = rm.state.value if rm.state else "pending"
            parts.append(f"\n  Roadmap: {rm.done_count}/{rm.total_count} done ({state})")

        # Cost
        if self.company.total_cost > 0:
            parts.append(f"  Cost: ${self.company.total_cost:.4f}")

        return "\n".join(parts)

    def _costs(self) -> str:
        return self.company.cost_report

    def _history(self, name: str) -> str:
        resolved = self._resolve_name(name)
        if not resolved or resolved not in self.company.employees:
            return f"No employee named '{name}'."
        emp = self.company.employees[resolved]
        if not emp.task_history:
            return f"No task history for {resolved}."

        from datetime import datetime
        from shipwright.company.company import format_duration_ms

        lines = [f"**Task History for {resolved}** ({len(emp.task_history)} tasks)\n"]
        for task in emp.task_history[-10:]:
            icon = {"done": "[x]", "failed": "[!]", "running": "[~]"}.get(
                task.status, "[ ]",
            )
            cost = f" ${task.cost_usd:.4f}" if task.cost_usd > 0 else ""
            duration = f" {format_duration_ms(task.duration_ms)}" if task.duration_ms > 0 else ""
            timestamp = ""
            if task.created_at:
                try:
                    dt = datetime.fromtimestamp(task.created_at)
                    timestamp = f" ({dt.strftime('%H:%M %b %d')})"
                except (OSError, ValueError):
                    pass
            # Output preview (first line, truncated)
            preview = ""
            if task.output and task.status == "done":
                first_line = task.output.strip().split("\n")[0][:80]
                if first_line:
                    preview = f"\n       {first_line}"
            lines.append(
                f"  {icon} {task.description[:60]}{cost}{duration}{timestamp}{preview}"
            )
        return "\n".join(lines)

    async def _ship(self, target: str | None = None) -> str:
        if not self.company.employees:
            return "No employees. Nothing to ship."
        if target:
            resolved = self._resolve_name(target)
            if not resolved or resolved not in self.company.teams:
                return f"No team named '{target}'. Use `ship` to ship all work."
            target = resolved
        try:
            pr_url = await self.company.ship(target)
            if pr_url:
                return f"PR opened: {pr_url}"
            return "No code changes to ship, or PR creation failed."
        except Exception as e:
            logger.error("Error creating PR: %s", e)
            return f"Failed to create PR: {e}"

    def _help(self) -> str:
        return (
            "**Shipwright** \u2014 AI engineering company\n\n"
            "  Just talk naturally. The CTO handles hiring, delegation,\n"
            "  review, and revision. You get asked when a decision is needed.\n\n"
            "  **Conversation**\n"
            "  `@<name> <msg>` \u2014 Direct message to employee\n"
            "  `talk <name>` \u2014 Switch to an employee\n"
            "  `back` \u2014 Return to CTO\n\n"
            "  **Visibility**\n"
            "  `status` \u2014 Quick company overview\n"
            "  `org` \u2014 Org chart with teams\n"
            "  `who` \u2014 Who is doing what right now\n"
            "  `roadmap` \u2014 Current roadmap progress\n"
            "  `costs` \u2014 Spending per employee\n"
            "  `history <name>` \u2014 Task history\n\n"
            "  **Execution**\n"
            "  `go` / `approve` \u2014 Start roadmap execution\n"
            "  `continue` \u2014 Resume paused work\n"
            "  `pause` / `pause now` \u2014 Pause roadmap\n"
            "  `stop` \u2014 Cancel roadmap\n\n"
            "  **Management**\n"
            "  `hire <role>` \u2014 Hire directly (bypass CTO)\n"
            "  `fire <name>` \u2014 Dismiss employee or team\n"
            "  `team create <name>` \u2014 Create a team\n"
            "  `promote <name> to lead of <team>`\n"
            '  `assign <name> "<task>"` \u2014 Assign work directly\n'
            "  `ship` \u2014 Open PR\n\n"
            "  **Session**\n"
            "  `save` / `sessions` / `session load <name>`\n"
            "  `roles` / `shop` / `installed` / `inspect <name>`\n"
        )

    def _shop(self) -> str:
        lines = ["**Available Roles & Specialists**\n"]
        lines.append("**Built-in Roles:**")
        for role_id in sorted(BUILTIN_ROLES.keys()):
            display = ROLE_DISPLAY_NAMES.get(role_id, role_id)
            lines.append(f"  `{role_id}` — {display}")

        specialists = list_specialists(self.config)
        if specialists:
            lines.append("\n**Specialists:**")
            for name in specialists:
                sdef = self.config.custom_specialists[name]
                desc = sdef.description or sdef.member_def.role
                lines.append(f"  `{name}` [{sdef.source}] — {desc}")

        lines.append("\nUse `inspect <name>` for details, `hire <name>` to hire.")
        return "\n".join(lines)

    def _installed(self) -> str:
        items = list_installed(self.config)
        if not items:
            return (
                "No custom roles or specialists installed.\n\n"
                "Add them to `./shipwright/crews/` or `~/.shipwright/crews/`."
            )
        lines = ["**Installed Roles & Specialists**\n"]
        for item in items:
            kind_tag = item.get("kind", "role")
            desc = item["description"] or "(no description)"
            lines.append(
                f"  `{item['name']}` ({kind_tag}) [{item['source']}] — {desc}"
            )
        return "\n".join(lines)

    def _inspect(self, name: str) -> str:
        return inspect_role(name, self.config)

    # ------------------------------------------------------------------
    # Intent handlers — greeting, pause, stop, resume
    # ------------------------------------------------------------------

    def _handle_greeting(self, text: str) -> str:
        """Handle casual greetings without triggering any work.

        Context-aware: reflects actual company state naturally.
        Never resumes paused work — just acknowledges it.
        """
        import random

        has_employees = any(
            e.role != "cto" for e in self.company.employees.values()
        )
        has_cto = self.company.get_cto() is not None

        # Check for paused/interrupted roadmap
        roadmap = self.company.active_roadmap
        paused_desc = None
        if roadmap and roadmap.state in (
            RoadmapState.PAUSED, RoadmapState.INTERRUPTED,
        ):
            paused_desc = roadmap.paused_task_description

        if paused_desc:
            short = paused_desc[:50]
            return (
                f"We have a paused roadmap on **{short}** "
                f"({roadmap.done_count}/{roadmap.total_count} done). "
                f"Type `continue` to pick up, or tell me what's next."
            )

        if not has_cto and not has_employees:
            openers = [
                "Tell me what we're building.",
                "What's the project?",
                "Ready when you are. What do we need?",
            ]
            return random.choice(openers)

        if has_employees:
            working = [
                e for e in self.company.employees.values()
                if e.status == EmployeeStatus.WORKING
            ]
            idle = [
                e for e in self.company.employees.values()
                if e.status == EmployeeStatus.IDLE and e.role != "cto"
            ]
            if working:
                names = ", ".join(e.name for e in working[:3])
                return f"{names} {'is' if len(working) == 1 else 'are'} on it. What do you need?"
            if idle:
                count = len(idle)
                return f"Team's here — {count} engineer{'s' if count != 1 else ''} idle. What's next?"
            return "What do you need?"

        return "What are we working on?"

    def _handle_pause(self) -> str:
        """Gracefully pause the active roadmap at a safe point."""
        roadmap = self.company.active_roadmap
        if not roadmap:
            return "Nothing to pause — no active roadmap."
        if roadmap.state in (RoadmapState.PAUSED, RoadmapState.INTERRUPTED):
            return "Already paused."
        if roadmap.state == RoadmapState.STOPPED:
            return "Roadmap was already stopped."

        roadmap.paused = True
        roadmap.state = RoadmapState.PAUSED
        # Mark any running task back to pending
        for t in roadmap.tasks:
            if t.status.value == "running":
                from shipwright.company.employee import RoadmapTaskStatus
                t.status = RoadmapTaskStatus.PENDING
        desc = roadmap.paused_task_description or "current work"
        return (
            f"**Paused.** Roadmap stopped at a safe point.\n"
            f"Next up: {desc}\n"
            f"Type `continue` or `resume` to pick up where you left off."
        )

    def _handle_pause_now(self) -> str:
        """Immediately interrupt the active roadmap."""
        roadmap = self.company.active_roadmap
        if not roadmap:
            return "Nothing to pause — no active roadmap."
        if roadmap.state in (RoadmapState.PAUSED, RoadmapState.INTERRUPTED):
            return "Already paused."
        if roadmap.state == RoadmapState.STOPPED:
            return "Roadmap was already stopped."

        roadmap.paused = True
        roadmap.state = RoadmapState.INTERRUPTED
        # Mark any running task back to pending
        for t in roadmap.tasks:
            if t.status.value == "running":
                from shipwright.company.employee import RoadmapTaskStatus
                t.status = RoadmapTaskStatus.PENDING
        desc = roadmap.paused_task_description or "current work"
        return (
            f"**Interrupted.** Roadmap halted immediately.\n"
            f"Was working on: {desc}\n"
            f"Type `continue` to retry, or `stop` to cancel."
        )

    def _handle_stop(self) -> str:
        """Cancel the active roadmap, keeping history."""
        roadmap = self.company.active_roadmap
        if not roadmap:
            return "Nothing to stop — no active roadmap."
        if roadmap.state == RoadmapState.STOPPED:
            return "Already stopped."

        roadmap.paused = True
        roadmap.state = RoadmapState.STOPPED
        # Mark any running task back to pending (won't be resumed)
        for t in roadmap.tasks:
            if t.status.value == "running":
                from shipwright.company.employee import RoadmapTaskStatus
                t.status = RoadmapTaskStatus.FAILED
                t.output_summary = "Cancelled by user"
        done = roadmap.done_count
        total = roadmap.total_count
        return (
            f"**Stopped.** Roadmap cancelled ({done}/{total} tasks were done).\n"
            f"History preserved. Start a new task whenever you're ready."
        )

    async def _handle_resume(
        self,
        on_text: Callable[[str], None] | None = None,
        on_delegation_start: Callable[[str, str, int, int], None] | None = None,
        on_delegation_end: Callable[[str, float, bool], None] | None = None,
        on_progress: Callable[[str], None] | None = None,
    ) -> str:
        """Resume a paused/interrupted roadmap. Only called for explicit resume intent."""
        roadmap = self.company.active_roadmap
        if not roadmap:
            return "Nothing to resume — no active roadmap."
        if roadmap.state == RoadmapState.STOPPED:
            return "Roadmap was stopped. Start a new task instead."
        if roadmap.state not in (RoadmapState.PAUSED, RoadmapState.INTERRUPTED):
            # Check if it's an unapproved roadmap — treat continue as approval
            if not roadmap.approved:
                return await self._roadmap_approve(
                    on_text=on_text,
                    on_delegation_start=on_delegation_start,
                    on_delegation_end=on_delegation_end,
                    on_progress=on_progress,
                ) or "No roadmap to resume."
            return "Roadmap is already running."

        # Reset any failed tasks to pending for retry
        for t in roadmap.tasks:
            if t.status.value == "failed":
                from shipwright.company.employee import RoadmapTaskStatus
                t.status = RoadmapTaskStatus.PENDING
                break
        roadmap.paused = False
        roadmap.state = RoadmapState.RUNNING
        result = await self.company.execute_roadmap(
            on_text=on_text,
            on_delegation_start=on_delegation_start,
            on_delegation_end=on_delegation_end,
            on_progress=on_progress,
        )
        return result

    # ------------------------------------------------------------------
    # Roadmap commands
    # ------------------------------------------------------------------

    def _roadmap_status(self) -> str:
        """Show the current roadmap status with context."""
        roadmap = self.company.active_roadmap
        if not roadmap:
            return "No active roadmap. Ask the CTO to build something."
        lines = []
        if roadmap.original_request:
            lines.append(f"**Roadmap** \u2014 {roadmap.original_request[:60]}\n")
        lines.append(roadmap.status_display())
        if roadmap.state in (RoadmapState.PAUSED, RoadmapState.INTERRUPTED):
            lines.append(f"\nType `continue` to resume, `stop` to cancel.")
        elif not roadmap.approved:
            lines.append(f"\nType `go` to start execution.")
        return "\n".join(lines)

    async def _roadmap_approve(
        self,
        on_text: Callable[[str], None] | None = None,
        on_delegation_start: Callable[[str, str, int, int], None] | None = None,
        on_delegation_end: Callable[[str, float, bool], None] | None = None,
        on_progress: Callable[[str], None] | None = None,
    ) -> str | None:
        """Approve and start executing the active roadmap. Returns None if no roadmap."""
        roadmap = self.company.active_roadmap
        if not roadmap:
            return None
        if roadmap.state == RoadmapState.STOPPED:
            return "Roadmap was stopped. Start a new task instead."
        if roadmap.approved and not roadmap.paused:
            return "Roadmap is already running."
        roadmap.approved = True
        roadmap.paused = False
        roadmap.state = RoadmapState.RUNNING
        result = await self.company.execute_roadmap(
            on_text=on_text,
            on_delegation_start=on_delegation_start,
            on_delegation_end=on_delegation_end,
            on_progress=on_progress,
        )
        return result

    def _suggest_hire(self, text: str) -> str:
        return (
            "No team yet. Just describe what you need \u2014 the CTO will "
            "hire the right people and get it done.\n\n"
            "Or hire directly: `hire backend-dev`, `hire architect`"
        )

    # ---- Session management ----

    def _list_sessions(self) -> str:
        sessions = list_sessions(self.config)
        if not sessions:
            return "No saved sessions."
        lines = ["**Saved Sessions**\n"]
        for name in sorted(sessions):
            marker = " (active)" if name == self.session_name else ""
            lines.append(f"  `{name}`{marker}")
        return "\n".join(lines)

    def _session_save(self, name: str) -> str:
        if not name:
            return "Session name cannot be empty. Usage: `session save <name>`"
        try:
            save_state(self.to_dict(), self.config, session_id=name)
            return f"Session saved as **{name}**."
        except Exception as e:
            logger.error("Failed to save session '%s': %s", name, e)
            return f"Failed to save session: {e}"

    def _session_load(self, name: str) -> str:
        if not name:
            return "Session name cannot be empty. Usage: `session load <name>`"
        data = load_state(self.config, session_id=name)
        if not data:
            sessions = list_sessions(self.config)
            if sessions:
                return (
                    f"No session named '{name}' found.\n"
                    f"Available sessions: {', '.join(sorted(sessions))}"
                )
            return f"No session named '{name}' found. No saved sessions exist."

        try:
            self.company = Company.from_dict(data.get("company", {}), self.config)
            self.session = Session.from_dict(data.get("session", {"id": name}))
            self.session_name = name
        except Exception as e:
            logger.error("Failed to restore session '%s': %s", name, e)
            return (
                f"Session '{name}' is corrupted and could not be loaded: {e}\n"
                f"You may want to clear it with `session clear confirm`."
            )

        n = len(self.company.employees)
        msg = f"Loaded session **{name}** with {n} employee(s)."
        if self.company.is_stale:
            msg += "\n\nWarning: company worktree no longer exists (marked stale)."
        return msg

    def _session_clear(self, confirmed: bool = False) -> str:
        if not confirmed:
            n = len(self.company.employees)
            if n == 0:
                # Nothing to lose — just clear
                self.company = Company(config=self.config)
                self.session = Session(id=self.session.id)
                return "Session cleared."
            return (
                f"Clear session? This will dismiss all {n} employee(s) "
                f"and lose their session context.\n"
                f"Type: `session clear confirm`"
            )
        self.company = Company(config=self.config)
        self.session = Session(id=self.session.id)
        return "Session cleared. All employees dismissed."

    # ------------------------------------------------------------------
    # Serialization
    # ------------------------------------------------------------------

    def to_dict(self) -> dict:
        data: dict = {
            "session": self.session.to_dict(),
            "company": self.company.to_dict(),
            "session_name": self.session_name,
        }
        if self.config.budget_limit_usd > 0:
            data["budget_limit_usd"] = self.config.budget_limit_usd
        return data

    @classmethod
    def from_dict(cls, data: dict, config: Config) -> "Router":
        """Restore router from persisted data."""
        session = Session.from_dict(data.get("session", {"id": "default"}))
        session_name = data.get("session_name", "default")
        router = cls(config=config, session=session, session_name=session_name)

        # Restore company
        company_data = data.get("company", {})
        if company_data:
            router.company = Company.from_dict(company_data, config)

        # Backward compat: handle old crew-based state
        elif "crews" in data:
            logger.warning("Old crew-based state detected; ignoring.")

        return router
