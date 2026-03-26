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
from shipwright.company.employee import EmployeeStatus
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
        0. Direct employee access: @name message
        1. Async commands that need ``await`` (assign work, ship)
        2. Synchronous commands (hire, fire, status, ...)
        3. Conversational fallback — route to CTO or active employee
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

        # ---- 0. Direct employee access: @name message ----------------------
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

        # ---- 1. Async: assign work -----------------------------------------
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

        # ---- 2a. Async: roadmap approval / resume ----------------------------
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

        if lower in ("continue", "resume"):
            response = await self._roadmap_resume(
                on_text=on_text,
                on_delegation_start=on_delegation_start,
                on_delegation_end=on_delegation_end,
                on_progress=on_progress,
            )
            if response:
                self.session.add_system_message(response)
                return response
            # No roadmap to resume — fall through to conversational

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

        # roadmap — show current roadmap status
        if lower in ("roadmap", "roadmap status", "plan"):
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
            # Check for confirmation suffix
            confirmed = raw_target.endswith(" confirm")
            target = raw_target.removesuffix(" confirm").strip() if confirmed else raw_target
            return True, self._fire(target, confirmed=confirmed)

        # team overview
        if lower in ("team", "teams", "company", "org"):
            return True, self._team_overview()

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

        # status
        if lower in ("status", "overview", "board"):
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

    def _team_overview(self) -> str:
        """Show company overview."""
        n = len(self.company.employees)
        nt = len(self.company.teams)

        if n == 0:
            return (
                "No employees yet. Hire some!\n"
                "Type `roles` to see available roles, or `hire <role>` to get started."
            )

        team_label = f", {nt} team(s)" if nt else ""
        lines = [f"**Your Company** ({n} employees{team_label})\n"]
        lines.append(self.company.status_summary)

        if not self.company.teams:
            lines.append(
                "\n  No teams configured. Employees work independently.\n"
                "  Use `team create <name>` to organize them."
            )

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
        return f"Now talking to **{resolved}** ({emp.display_role})."

    def _back(self) -> str:
        """Return conversation to the CTO."""
        cto = self.company.get_cto()
        if cto:
            self.company.set_active(cto.name)
            return "Back to **CTO**."
        # No CTO yet — create one
        cto = self.company.ensure_cto()
        self.company.set_active(cto.name)
        return "Back to **CTO**."

    def _status(self) -> str:
        return self._team_overview()

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
            "**Shipwright Commands**\n\n"
            "  Just type naturally — the CTO handles your requests.\n"
            "  The CTO hires engineers, delegates work, reviews quality,\n"
            "  and presents results. You only get asked when a decision is needed.\n\n"
            "  `@<name> <message>` — Talk directly to an employee\n"
            "  `back` — Return to CTO conversation\n"
            "  `talk <name>` — Switch active employee\n"
            "  `status` — Company overview\n"
            "  `roadmap` — Show current roadmap status\n"
            "  `go` / `approve` — Approve and start roadmap execution\n"
            "  `continue` / `resume` — Resume a paused roadmap\n"
            "  `costs` — Budget/token usage per employee\n"
            "  `history <name>` — Task history for an employee\n\n"
            "  **Power user commands (bypass CTO):**\n"
            "  `roles` — List available roles to hire\n"
            "  `hire <role>` — Hire an employee directly\n"
            '  `hire <role> as "Name"` — Hire with a custom name\n'
            "  `fire <name>` — Fire an employee\n"
            "  `fire <team>` — Fire an entire team\n"
            "  `team create <name>` — Create a team\n"
            "  `promote <name> to lead of <team>` — Make someone team lead\n"
            "  `assign <name> to <team>` — Add employee to a team\n"
            '  `assign <name> "<task>"` — Give work directly to an employee\n'
            "  `ship` — Open PR for all work\n"
            "  `save` — Save current state\n"
            "  `sessions` — List saved sessions\n"
            "  `session save <name>` / `session load <name>` — Manage sessions\n"
            "  `session clear` — Reset everything\n"
            "  `shop` — Browse all available roles & specialists\n"
            "  `installed` — List custom/installed plugins\n"
            "  `inspect <name>` — Show role/specialist details\n"
            "  `help` — Show this help\n"
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
    # Roadmap commands
    # ------------------------------------------------------------------

    def _roadmap_status(self) -> str:
        """Show the current roadmap status."""
        roadmap = self.company.active_roadmap
        if not roadmap:
            return "No active roadmap."
        return roadmap.status_display()

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
        if roadmap.approved and not roadmap.paused:
            return "Roadmap is already running."
        roadmap.approved = True
        roadmap.paused = False
        result = await self.company.execute_roadmap(
            on_text=on_text,
            on_delegation_start=on_delegation_start,
            on_delegation_end=on_delegation_end,
            on_progress=on_progress,
        )
        return result

    async def _roadmap_resume(
        self,
        on_text: Callable[[str], None] | None = None,
        on_delegation_start: Callable[[str, str, int, int], None] | None = None,
        on_delegation_end: Callable[[str, float, bool], None] | None = None,
        on_progress: Callable[[str], None] | None = None,
    ) -> str | None:
        """Resume a paused roadmap. Returns None if no roadmap to resume."""
        roadmap = self.company.active_roadmap
        if not roadmap:
            return None
        if not roadmap.paused:
            return None  # Not paused, fall through
        # Reset any failed task to pending so it can be retried
        for t in roadmap.tasks:
            if t.status.value == "failed":
                from shipwright.company.employee import RoadmapTaskStatus
                t.status = RoadmapTaskStatus.PENDING
                break
        roadmap.paused = False
        result = await self.company.execute_roadmap(
            on_text=on_text,
            on_delegation_start=on_delegation_start,
            on_delegation_end=on_delegation_end,
            on_progress=on_progress,
        )
        return result

    def _suggest_hire(self, text: str) -> str:
        roles = list_roles(self.config)
        return (
            "No employees yet. Hire some!\n\n"
            f"Available roles: {', '.join(roles[:8])}\n\n"
            "Examples:\n"
            "  `hire architect`\n"
            "  `hire backend-dev`\n"
            '  `hire frontend-dev as "Kai"`\n\n'
            "Type `roles` for the full list or `help` for all commands."
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
