"""Company — manages employees, teams, and work assignment.

The Company is the central organizational unit in Shipwright V2.
It manages:
- Employees (hired individually, persist until fired)
- Teams (optional organizational structure with team leads)
- Work assignment (direct to employee or team)
- Git worktree isolation
- Cost tracking
"""

from __future__ import annotations

import asyncio
import re
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from shipwright.config import Config, MemberDef
from shipwright.company.employee import (
    Employee,
    EmployeeStatus,
    Task,
    MemberResult,
    DelegationRequest,
    HireRequest,
    LeadResponse,
    ReviseRequest,
    Roadmap,
    RoadmapState,
    RoadmapTask,
    RoadmapTaskStatus,
    parse_delegations,
    parse_hire_blocks,
    parse_revise_blocks,
    parse_roadmap_block,
    parse_execute_roadmap,
    next_name,
)
from shipwright.utils.logging import get_logger
from shipwright.workspace.git import (
    cleanup_worktree,
    get_branch_context,
    commit,
    create_pr,
    create_worktree,
    push_branch,
    slug,
)

logger = get_logger("company.company")

# Hierarchy — role-based permissions for delegation, hiring, and revision
ROLES_CAN_HIRE: frozenset[str] = frozenset({"cto"})
ROLES_CAN_DELEGATE: frozenset[str] = frozenset({"cto", "team-lead"})
ROLES_CAN_REVISE: frozenset[str] = frozenset({"cto", "team-lead"})

_CASUAL_GREETING_PATTERNS: set[str] = {
    "hi", "hello", "hey", "sup", "yo", "oi", "hola", "hiya", "heya",
    "morning", "good morning", "afternoon", "good afternoon",
    "evening", "good evening", "howdy", "whats up", "what's up",
    "wassup", "wazzup", "wasup", "wassap", "wasap", "whaddup", "waddup",
    "thanks", "thank you", "cool", "ok", "okay", "got it",
}
_CASUAL_GREETING_RE = re.compile(
    r"^(?:w+a+s+(?:a|u)?p+|w+a+z+u+p+|w+h?a+d+d*u+p+)$"
)
_REPO_STATUS_RE = re.compile(
    r"\b(status|changed|branch|repo|git|diff|commit)\b|what(?:'s|s)? changed|"
    r"what(?:'s|s)? the status|how(?:'s|s)? the repo|what branch are we on"
)


def format_duration_ms(ms: int) -> str:
    """Format milliseconds into a human-readable duration string."""
    if ms <= 0:
        return "0s"
    seconds = ms / 1000
    if seconds < 60:
        return f"{seconds:.0f}s"
    minutes = int(seconds // 60)
    secs = int(seconds % 60)
    if minutes < 60:
        return f"{minutes}m {secs}s" if secs else f"{minutes}m"
    hours = int(minutes // 60)
    mins = minutes % 60
    if mins:
        return f"{hours}h {mins}m"
    return f"{hours}h"


def _looks_casual_message(message: str) -> bool:
    """Best-effort guardrail for slangy greetings that should not hit the CTO."""
    lower = re.sub(r"\s+", " ", message.lower().strip())
    lower = re.sub(r"[!?.,:;]+$", "", lower)
    squashed = re.sub(r"[^a-z]", "", lower)

    if lower in _CASUAL_GREETING_PATTERNS:
        return True
    if _CASUAL_GREETING_RE.fullmatch(squashed):
        return True

    words = lower.split()
    if len(words) <= 2 and words:
        first = re.sub(r"[^a-z]", "", words[0])
        if first in {
            "hi", "hello", "hey", "sup", "yo", "oi", "hola",
            "morning", "afternoon", "evening", "howdy",
        }:
            return True
        if _CASUAL_GREETING_RE.fullmatch(first):
            return True

    return False


def _looks_repo_status_message(message: str) -> bool:
    """Detect repo/status questions for stronger non-LLM fallback handling."""
    lower = re.sub(r"\s+", " ", message.lower().strip())
    lower = re.sub(r"[!?.,:;]+$", "", lower)
    return bool(_REPO_STATUS_RE.search(lower))


def _repo_snapshot_from_context(repo_root: Path) -> str:
    """Build a concise repo snapshot for fallback responses."""
    try:
        context = get_branch_context(repo_root)
    except Exception:
        return "Repo state unavailable."

    if not context.strip():
        return "Repo state unavailable."

    branch = None
    remote = None
    working_tree = None
    changed_files: list[str] = []
    recent_commits: list[str] = []
    mode: str | None = None

    for raw_line in context.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith("Branch:"):
            branch = line.partition(":")[2].strip()
            mode = None
            continue
        if line.startswith("Remote:"):
            remote = line.partition(":")[2].strip()
            mode = None
            continue
        if line.startswith("Working tree:"):
            working_tree = line.partition(":")[2].strip()
            mode = "tree" if "clean" not in line.lower() else None
            continue
        if line.startswith("Recent commits:"):
            mode = "commits"
            continue
        if mode == "tree":
            changed_files.append(line)
            continue
        if mode == "commits":
            recent_commits.append(line)

    parts: list[str] = []
    if branch:
        parts.append(f"branch {branch}")
    if remote:
        parts.append(remote)
    if working_tree:
        parts.append(f"working tree {working_tree}")
    if changed_files:
        parts.append("Changed files: " + "; ".join(changed_files[:3]))
    if recent_commits:
        parts.append("Recent commits: " + "; ".join(recent_commits[:2]))

    if not parts:
        parts.append(context.splitlines()[0])

    return "Repo snapshot: " + " · ".join(parts) + "."


def _casual_fallback_response(message: str) -> str:
    """Return a human fallback when the CTO SDK fails on casual chat."""
    lower = re.sub(r"\s+", " ", message.lower().strip())
    lower = re.sub(r"[!?.,:;]+$", "", lower)

    if any(word in lower for word in ("thanks", "thank you", "thx", "ty")):
        return "Any time."
    if any(word in lower for word in ("cool", "okay", "ok", "got it", "understood")):
        return "Understood. What's next?"
    return "I'm here. What do you need built?"


def _progress_hint(actor_name: str, task: str) -> str:
    """Return a human progress line before a long-running agent call starts."""
    lower = re.sub(r"\s+", " ", task.lower().strip())

    if any(token in lower for token in (
        "explore", "inspect", "understand", "read the repo", "read this repo",
        "explore this repo", "explore this codebase", "inspect this codebase",
        "codebase", "repository", "repo",
    )):
        return f"{actor_name} exploring the codebase..."
    if any(token in lower for token in ("review", "audit", "analyze", "analyse")):
        return f"{actor_name} reviewing the work..."
    if any(token in lower for token in ("plan", "roadmap", "break this down")):
        return f"{actor_name} planning the approach..."
    if any(token in lower for token in ("fix", "debug", "investigate")):
        return f"{actor_name} investigating the issue..."
    if any(token in lower for token in ("build", "implement", "add", "create")):
        return f"{actor_name} scoping the implementation..."
    return f"{actor_name} working..."


@dataclass
class Team:
    """A team of employees with an optional lead."""

    name: str
    lead: str | None = None  # employee name
    members: list[str] = field(default_factory=list)  # employee names

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "lead": self.lead,
            "members": self.members,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Team":
        return cls(
            name=data["name"],
            lead=data.get("lead"),
            members=data.get("members", []),
        )


@dataclass
class Company:
    """Manages employees, teams, and work assignment.

    Usage:
        company = Company(config=config)
        company.hire("backend-dev")  # Hires an employee
        await company.assign("Alex", "Build the API")  # Assigns work
        response = await company.talk("Alex", "How's it going?")  # Chat
    """

    config: Config
    employees: dict[str, Employee] = field(default_factory=dict)  # keyed by name
    teams: dict[str, Team] = field(default_factory=dict)
    project_context: str = ""
    worktree_path: Path | None = None
    branch: str | None = None
    active_roadmap: Roadmap | None = None
    _active_employee: str | None = field(default=None, repr=False)
    _active_employee_explicit: bool = field(default=False, repr=False)
    _stale_worktree: str | None = field(default=None, repr=False)

    max_delegation_rounds: int = 5

    @property
    def active_employee(self) -> Employee | None:
        if self._active_employee:
            return self.employees.get(self._active_employee)
        return None

    @property
    def is_stale(self) -> bool:
        return self._stale_worktree is not None

    @property
    def active_employee_is_explicit(self) -> bool:
        return self._active_employee_explicit

    def _run_checkpoint(
        self, on_checkpoint: Callable[[], None] | None,
    ) -> None:
        """Persist current state opportunistically without breaking execution."""
        if not on_checkpoint:
            return
        try:
            on_checkpoint()
        except Exception as exc:
            logger.warning("Checkpoint callback failed: %s", exc)

    def hire(
        self,
        role_id: str,
        role_def: MemberDef,
        name: str | None = None,
    ) -> Employee:
        """Hire a new employee with the given role."""
        used_names = set(self.employees.keys())
        emp_name = name or next_name(used_names)

        # Avoid name collisions
        if emp_name in self.employees:
            raise ValueError(f"An employee named '{emp_name}' already exists.")

        emp_id = f"{emp_name.lower()}-{role_id}"
        cwd = str(self.worktree_path or self.config.repo_root)

        employee = Employee(
            id=emp_id,
            name=emp_name,
            role=role_id,
            role_def=role_def,
            cwd=cwd,
            model=self.config.model,
            permission_mode=self.config.permission_mode,
            context_reset_threshold=self.config.context_reset_threshold,
        )

        self.employees[emp_name] = employee

        # Auto-set as active if first employee
        if self._active_employee is None:
            self._active_employee = emp_name
            self._active_employee_explicit = False

        logger.info("Hired %s as %s (%s)", emp_name, role_def.role, role_id)
        return employee

    def fire(self, name: str) -> Employee:
        """Fire an employee by name."""
        if name not in self.employees:
            raise ValueError(f"No employee named '{name}'.")

        employee = self.employees.pop(name)

        # Remove from team if assigned
        if employee.team and employee.team in self.teams:
            team = self.teams[employee.team]
            if employee.name in team.members:
                team.members.remove(employee.name)
            if team.lead == employee.name:
                team.lead = None

        # Update active employee
        if self._active_employee == name:
            self._active_employee = next(iter(self.employees), None)

        logger.info("Fired %s (%s)", name, employee.role_def.role)
        return employee

    def fire_team(self, team_name: str) -> list[Employee]:
        """Fire all members of a team and remove the team."""
        if team_name not in self.teams:
            raise ValueError(f"No team named '{team_name}'.")

        team = self.teams[team_name]
        fired = []
        for member_name in list(team.members):
            if member_name in self.employees:
                emp = self.employees.pop(member_name)
                fired.append(emp)

        if team.lead and team.lead in self.employees and team.lead not in [e.name for e in fired]:
            emp = self.employees.pop(team.lead)
            fired.append(emp)

        del self.teams[team_name]

        # Update active employee
        if self._active_employee and self._active_employee not in self.employees:
            self._active_employee = next(iter(self.employees), None)
            self._active_employee_explicit = False

        return fired

    def create_team(self, name: str) -> Team:
        """Create a new team."""
        if name in self.teams:
            raise ValueError(f"Team '{name}' already exists.")
        team = Team(name=name)
        self.teams[name] = team
        logger.info("Created team '%s'", name)
        return team

    def promote_to_lead(self, employee_name: str, team_name: str) -> None:
        """Promote an employee to team lead."""
        if employee_name not in self.employees:
            raise ValueError(f"No employee named '{employee_name}'.")
        if team_name not in self.teams:
            raise ValueError(f"No team named '{team_name}'.")

        employee = self.employees[employee_name]
        team = self.teams[team_name]

        # Add to team if not already a member
        if employee_name not in team.members:
            team.members.append(employee_name)

        # Remove previous team assignment
        if employee.team and employee.team != team_name and employee.team in self.teams:
            old_team = self.teams[employee.team]
            if employee_name in old_team.members:
                old_team.members.remove(employee_name)
            if old_team.lead == employee_name:
                old_team.lead = None

        team.lead = employee_name
        employee.team = team_name
        employee.is_lead = True
        logger.info("Promoted %s to lead of team '%s'", employee_name, team_name)

    def assign_to_team(self, employee_name: str, team_name: str) -> None:
        """Assign an employee to a team."""
        if employee_name not in self.employees:
            raise ValueError(f"No employee named '{employee_name}'.")
        if team_name not in self.teams:
            raise ValueError(f"No team named '{team_name}'.")

        employee = self.employees[employee_name]
        team = self.teams[team_name]

        # Remove from previous team
        if employee.team and employee.team != team_name and employee.team in self.teams:
            old_team = self.teams[employee.team]
            if employee_name in old_team.members:
                old_team.members.remove(employee_name)
            if old_team.lead == employee_name:
                old_team.lead = None
                employee.is_lead = False

        if employee_name not in team.members:
            team.members.append(employee_name)
        employee.team = team_name
        logger.info("Assigned %s to team '%s'", employee_name, team_name)

    def set_active(self, name: str, *, explicit: bool = True) -> None:
        """Set the active employee for conversation."""
        if name not in self.employees:
            raise ValueError(f"No employee named '{name}'.")
        self._active_employee = name
        self._active_employee_explicit = explicit

    # ------------------------------------------------------------------
    # Hierarchy enforcement
    # ------------------------------------------------------------------

    def _can_hire(self, employee: Employee) -> bool:
        """Check if an employee's role permits hiring."""
        return employee.role in ROLES_CAN_HIRE

    def _can_delegate(self, employee: Employee) -> bool:
        """Check if an employee's role permits delegation."""
        return employee.role in ROLES_CAN_DELEGATE or employee.is_lead

    def _can_revise(self, employee: Employee) -> bool:
        """Check if an employee's role permits revision requests."""
        return employee.role in ROLES_CAN_REVISE or employee.is_lead

    def _get_delegation_scope(self, employee: Employee) -> set[str]:
        """Get the set of employee names this employee can delegate to."""
        if employee.role == "cto":
            return {n for n in self.employees if n != employee.name}
        if employee.is_lead and employee.team and employee.team in self.teams:
            team = self.teams[employee.team]
            return {m for m in team.members if m != employee.name}
        return set()

    def _filter_delegations(
        self, employee: Employee, delegations: list[DelegationRequest],
    ) -> list[DelegationRequest]:
        """Filter delegations based on role permissions and scope."""
        if not self._can_delegate(employee):
            if delegations:
                logger.warning(
                    "[%s] Unauthorized delegation (role=%s) — stripped %d block(s)",
                    employee.name, employee.role, len(delegations),
                )
            return []
        scope = self._get_delegation_scope(employee)
        filtered = []
        for d in delegations:
            if d.member_name in scope:
                filtered.append(d)
            else:
                logger.warning(
                    "[%s] Cannot delegate to '%s' — not in scope",
                    employee.name, d.member_name,
                )
        return filtered

    def _filter_hires(
        self, employee: Employee, hires: list[HireRequest],
    ) -> list[HireRequest]:
        """Filter hire requests based on role permissions."""
        if not self._can_hire(employee):
            if hires:
                logger.warning(
                    "[%s] Unauthorized hire (role=%s) — stripped %d block(s)",
                    employee.name, employee.role, len(hires),
                )
            return []
        return hires

    def _filter_revisions(
        self, employee: Employee, revisions: list[ReviseRequest],
    ) -> list[ReviseRequest]:
        """Filter revision requests based on role permissions and scope."""
        if not self._can_revise(employee):
            if revisions:
                logger.warning(
                    "[%s] Unauthorized revise (role=%s) — stripped %d block(s)",
                    employee.name, employee.role, len(revisions),
                )
            return []
        scope = self._get_delegation_scope(employee)
        filtered = []
        for r in revisions:
            if r.employee_name in scope:
                filtered.append(r)
            else:
                logger.warning(
                    "[%s] Cannot revise '%s' — not in scope",
                    employee.name, r.employee_name,
                )
        return filtered

    # ------------------------------------------------------------------
    # CTO auto-pilot
    # ------------------------------------------------------------------

    def get_cto(self) -> Employee | None:
        """Get the CTO employee, if one exists."""
        for emp in self.employees.values():
            if emp.role == "cto":
                return emp
        return None

    def ensure_cto(self) -> Employee:
        """Ensure a CTO employee exists. Creates one if needed. Idempotent."""
        existing = self.get_cto()
        if existing:
            return existing

        from shipwright.company.roles import get_role_def

        role_def = get_role_def("cto")
        cto = self.hire("cto", role_def, name="CTO")
        # CTO should be active by default when it's the only employee
        # (hire() already handles this via the "first hire" logic)
        return cto

    def _build_cto_prompt(self) -> str:
        """Build the dynamic CTO system prompt with current company state."""
        from shipwright.company.roles import get_role_def

        base_prompt = get_role_def("cto").prompt

        # Employee section
        emp_lines = []
        for emp in self.employees.values():
            if emp.role == "cto":
                continue
            status = emp.status.value
            if emp.current_task:
                status = f"working: {emp.current_task.description[:50]}"
            team_tag = f" [{emp.team}]" if emp.team else ""
            task_count = len(emp.task_history)
            last_task = ""
            if emp.task_history:
                lt = emp.task_history[-1]
                last_task = f" — last: {lt.description[:40]} ({lt.status})"
            emp_lines.append(
                f"- **{emp.name}** ({emp.display_role}){team_tag} — "
                f"{status}, {task_count} tasks done{last_task}"
            )

        employees_section = (
            "\n".join(emp_lines)
            if emp_lines
            else "No employees hired yet. Hire with [HIRE:role] or [HIRE:role:name]."
        )

        # Recent tasks
        recent_tasks = []
        for emp in self.employees.values():
            if emp.role == "cto":
                continue
            for task in emp.task_history[-3:]:
                icon = "DONE" if task.status == "done" else "FAILED"
                recent_tasks.append(
                    f"- [{icon}] {emp.name}: {task.description[:60]}"
                )
        tasks_section = (
            "\n".join(recent_tasks[-10:])
            if recent_tasks
            else "No tasks completed yet."
        )

        # Roadmap context
        roadmap_section = ""
        if self.active_roadmap:
            rm = self.active_roadmap
            if rm.state in (RoadmapState.PAUSED, RoadmapState.INTERRUPTED):
                desc = rm.paused_task_description or "unknown"
                roadmap_section = (
                    f"\n### Paused Work\n"
                    f"There is a {rm.state.value} roadmap: \"{rm.original_request}\"\n"
                    f"Progress: {rm.done_count}/{rm.total_count} tasks done. "
                    f"Next task: {desc}\n"
                    f"**Do NOT resume this work unless the CEO explicitly says "
                    f"continue/resume/go. Mentioning it briefly is fine.**"
                )
            elif rm.state == RoadmapState.STOPPED:
                roadmap_section = (
                    f"\n### Stopped Roadmap\n"
                    f"A previous roadmap was cancelled: \"{rm.original_request}\" "
                    f"({rm.done_count}/{rm.total_count} done). This is just context."
                )

        # Git context — refreshed on each prompt build so the CTO sees
        # the latest branch/diff/commit state.
        from shipwright.workspace.git import get_branch_context

        try:
            git_context = get_branch_context(self.config.repo_root)
        except Exception:
            git_context = ""

        repo_section = ""
        if git_context:
            repo_section = f"\n### Repository State\n{git_context}"

        return f"""{base_prompt}

## Current Company State

### Your Team
{employees_section}

### Recent Work
{tasks_section}
{roadmap_section}

### Project
{self.project_context or "No project context loaded yet."}
{repo_section}
"""

    async def cto_chat(
        self,
        message: str,
        on_text: Callable[[str], None] | None = None,
        on_delegation_start: Callable[[str, str, int, int], None] | None = None,
        on_delegation_end: Callable[[str, float, bool], None] | None = None,
        on_progress: Callable[[str], None] | None = None,
        on_checkpoint: Callable[[], None] | None = None,
    ) -> str:
        """Full CTO flow: respond → hire → delegate → review → present.

        The CTO processes the user's message, optionally hires employees,
        delegates work, reviews results, and presents to the user.
        If the CTO outputs a [ROADMAP] block, it's stored for user approval.
        """
        cto = self.get_cto()
        if not cto:
            return "No CTO available."

        system_prompt = self._build_cto_prompt()

        # Budget check
        budget = self.config.budget_limit_usd
        if budget > 0 and self.total_cost >= budget:
            return (
                f"**Budget exceeded.** Spent ${self.total_cost:.4f} "
                f"of ${budget:.2f} limit."
            )

        # Step 1: CTO responds to the user's message (streamed)
        try:
            if on_progress:
                on_progress(_progress_hint("CTO", message))
            result = await cto.run(
                task=message,
                system_prompt=system_prompt,
                on_text=on_text,
            )
        except Exception as exc:
            logger.error("CTO execution failed before delegation: %s", exc)
            if _looks_casual_message(message):
                return _casual_fallback_response(message)
            if _looks_repo_status_message(message):
                return _repo_snapshot_from_context(self.config.repo_root)
            return (
                "CTO hit an execution error before delegation. "
                "Try again, or restate the request as a concrete task."
            )
        response_text = result.output
        if not response_text.strip():
            if _looks_casual_message(message):
                return _casual_fallback_response(message)
            if _looks_repo_status_message(message):
                return _repo_snapshot_from_context(self.config.repo_root)
            return (
                "CTO returned no response. "
                "Try again with a concrete task or a clearer status question."
            )

        # Track CTO conversation
        cto._conversation.append({"role": "user", "text": message})
        cto._conversation.append({"role": "cto", "text": response_text})

        # Step 2a: Check for roadmap block
        response_text, roadmap = parse_roadmap_block(response_text)
        if roadmap:
            roadmap.original_request = message
            self.active_roadmap = roadmap
            self._run_checkpoint(on_checkpoint)
            # Return CTO's commentary + the roadmap for user approval
            parts = []
            if response_text:
                parts.append(response_text)
            parts.append(roadmap.status_display())
            parts.append(
                "\nType **go** or **approve** to start autonomous execution, "
                "or modify the plan first."
            )
            return "\n\n".join(parts)

        # Step 2b: Check for [EXECUTE_ROADMAP] signal
        response_text, should_execute = parse_execute_roadmap(response_text)
        if should_execute and self.active_roadmap and self.active_roadmap.approved:
            # CTO is signalling to continue roadmap execution
            exec_result = await self.execute_roadmap(
                on_text=on_text,
                on_delegation_start=on_delegation_start,
                on_delegation_end=on_delegation_end,
                on_progress=on_progress,
                on_checkpoint=on_checkpoint,
            )
            parts = []
            if response_text:
                parts.append(response_text)
            parts.append(exec_result)
            return "\n\n".join(filter(None, parts))

        # Step 3: Parse and filter blocks (hierarchy enforcement)
        response_text, hires = parse_hire_blocks(response_text)
        hires = self._filter_hires(cto, hires)
        hire_messages = self._process_hires(hires)
        if hire_messages:
            self._run_checkpoint(on_checkpoint)

        response_text, delegations = parse_delegations(response_text)
        delegations = self._filter_delegations(cto, delegations)

        if not delegations:
            # No delegations — CTO is just talking/planning
            parts = []
            if response_text:
                parts.append(response_text)
            if hire_messages:
                parts.append("\n".join(hire_messages))
            return "\n\n".join(parts) if parts else response_text

        # Step 4: Delegation loop (shared with team-leads)
        loop_result = await self._delegation_loop(
            coordinator=cto,
            delegations=delegations,
            coordinator_text=response_text,
            on_text=on_text,
            on_delegation_start=on_delegation_start,
            on_delegation_end=on_delegation_end,
            on_progress=on_progress,
            on_checkpoint=on_checkpoint,
        )

        # Combine all parts
        parts = []
        if response_text:
            parts.append(response_text)
        if hire_messages:
            parts.append("\n".join(hire_messages))
        if loop_result:
            parts.append(loop_result)
        return "\n\n".join(filter(None, parts))

    async def execute_roadmap(
        self,
        on_text: Callable[[str], None] | None = None,
        on_delegation_start: Callable[[str, str, int, int], None] | None = None,
        on_delegation_end: Callable[[str, float, bool], None] | None = None,
        on_progress: Callable[[str], None] | None = None,
        on_roadmap_task_complete: Callable[[int, int, str], None] | None = None,
        on_checkpoint: Callable[[], None] | None = None,
    ) -> str:
        """Execute the active roadmap task by task.

        For each task:
        1. Provide accumulated context + roadmap progress to the CTO
        2. CTO delegates the task via the normal hire/delegate/review flow
        3. After completion, context-reset the CTO and capture the handoff
        4. Record progress, report to user, move to next task

        Returns a summary of all completed work.
        Pauses on failure or if interrupted (sets roadmap.paused = True).
        """
        roadmap = self.active_roadmap
        if not roadmap:
            return "No active roadmap."
        if not roadmap.approved:
            return "Roadmap not yet approved. Type **go** to start."

        cto = self.get_cto()
        if not cto:
            return "No CTO available."

        roadmap.paused = False
        roadmap.state = RoadmapState.RUNNING
        collected_reports: list[str] = []
        self._run_checkpoint(on_checkpoint)

        while True:
            idx = roadmap.current_task_index
            if idx is None:
                break  # All tasks done

            task = roadmap.tasks[idx - 1]  # convert 1-based to 0-based

            # Build context for this task
            context_parts = [
                f"# Roadmap Execution — Task {idx}/{roadmap.total_count}",
                f"## Original Request\n{roadmap.original_request}",
                f"## Current Task\n{task.description}",
            ]
            accumulated = roadmap.accumulated_context
            if accumulated:
                context_parts.append(
                    f"## Completed Tasks Context\n{accumulated}"
                )
            context_parts.append(
                "## Instructions\n"
                "Execute this specific task. Hire engineers if needed, "
                "delegate the work, review the results. Focus only on this task."
            )
            task_prompt = "\n\n".join(context_parts)

            # Budget check
            budget = self.config.budget_limit_usd
            if budget > 0 and self.total_cost >= budget:
                task.status = RoadmapTaskStatus.FAILED
                task.output_summary = "Budget exceeded"
                task.last_error = "Budget exceeded"
                task.last_updated_at = time.time()
                roadmap.paused = True
                roadmap.state = RoadmapState.PAUSED
                self._run_checkpoint(on_checkpoint)
                collected_reports.append(
                    f"Task {idx}/{roadmap.total_count} **paused**: Budget exceeded."
                )
                break

            now = time.time()
            task.attempts += 1
            task.status = RoadmapTaskStatus.RUNNING
            task.last_started_at = now
            task.last_updated_at = now
            task.last_error = ""
            self._run_checkpoint(on_checkpoint)

            if on_progress:
                on_progress(
                    f"Roadmap task {idx}/{roadmap.total_count}: {task.description}"
                    f" (attempt {task.attempts}/{self.config.max_roadmap_task_retries + 1})"
                )

            # Execute via the normal CTO flow (without recursing into roadmap)
            try:
                system_prompt = self._build_cto_prompt()
                result = await cto.run(
                    task=task_prompt,
                    system_prompt=system_prompt,
                    on_text=on_text,
                )
                response_text = result.output

                cto._conversation.append({"role": "system", "text": task_prompt})
                cto._conversation.append({"role": "cto", "text": response_text})

                # Process hires and delegations from CTO response
                response_text, hires = parse_hire_blocks(response_text)
                hires = self._filter_hires(cto, hires)
                self._process_hires(hires)

                response_text, delegations = parse_delegations(response_text)
                delegations = self._filter_delegations(cto, delegations)

                if delegations:
                    loop_result = await self._delegation_loop(
                        coordinator=cto,
                        delegations=delegations,
                        coordinator_text=response_text,
                        on_text=None,  # suppress streaming during auto-exec
                        on_delegation_start=on_delegation_start,
                        on_delegation_end=on_delegation_end,
                        on_progress=on_progress,
                        on_checkpoint=on_checkpoint,
                    )
                    response_text = (
                        f"{response_text}\n\n{loop_result}" if loop_result else response_text
                    )

                # Task completed
                task.status = RoadmapTaskStatus.DONE
                # Build summary from first 200 chars of response
                summary_line = response_text.strip().split("\n")[0][:200] if response_text else "Done"
                task.output_summary = summary_line
                task.last_error = ""
                task.last_updated_at = time.time()

                # Context reset the CTO to stay fresh
                artifact_path = cto.save_handoff_artifact(
                    task_description=task.description,
                )
                if artifact_path and artifact_path.exists():
                    task.handoff_artifact = artifact_path.read_text()[:3000]
                cto._session_id = None
                cto._conversation.clear()
                cto._cumulative_turns = 0
                self._run_checkpoint(on_checkpoint)

                report = (
                    f"Task {idx}/{roadmap.total_count} done: "
                    f"{task.description}"
                )
                if task.attempts > 1:
                    report += f" after {task.attempts} attempts"
                collected_reports.append(report)

                if on_roadmap_task_complete:
                    on_roadmap_task_complete(idx, roadmap.total_count, task.description)

                if on_progress:
                    remaining = roadmap.total_count - roadmap.done_count
                    if remaining > 0:
                        on_progress(
                            f"{report}. {remaining} task(s) remaining."
                        )

            except asyncio.CancelledError:
                # Ctrl+C / cancellation — interrupted
                task.status = RoadmapTaskStatus.PENDING
                task.last_updated_at = time.time()
                roadmap.paused = True
                roadmap.state = RoadmapState.INTERRUPTED
                self._run_checkpoint(on_checkpoint)
                collected_reports.append(
                    f"Task {idx}/{roadmap.total_count} **interrupted**. "
                    "Type `continue` to resume."
                )
                break

            except Exception as exc:
                logger.error("Roadmap task %d failed: %s", idx, exc)
                task.last_error = str(exc)[:500]
                task.last_updated_at = time.time()

                if task.attempts <= self.config.max_roadmap_task_retries:
                    next_attempt = task.attempts + 1
                    total_attempts = self.config.max_roadmap_task_retries + 1
                    task.status = RoadmapTaskStatus.PENDING
                    task.output_summary = (
                        f"Retrying after error: {exc}"
                    )[:2000]
                    self._run_checkpoint(on_checkpoint)
                    if on_progress:
                        on_progress(
                            f"Roadmap task {idx}/{roadmap.total_count} hit an error; "
                            f"retrying attempt {next_attempt}/{total_attempts}."
                        )
                    continue

                task.status = RoadmapTaskStatus.FAILED
                task.output_summary = f"Error: {exc}"
                roadmap.paused = True
                roadmap.state = RoadmapState.PAUSED
                self._run_checkpoint(on_checkpoint)
                collected_reports.append(
                    f"Task {idx}/{roadmap.total_count} **failed** after "
                    f"{task.attempts} attempts: {exc}\n"
                    "Roadmap paused. Fix the issue and type `continue` to retry, "
                    "or modify the roadmap."
                )
                break

        # Final summary
        if roadmap.is_complete:
            roadmap.state = RoadmapState.COMPLETE
            self._run_checkpoint(on_checkpoint)
            collected_reports.append(
                f"\n**Roadmap complete!** All {roadmap.total_count} tasks done."
            )
            # Clear the roadmap
            self.active_roadmap = None
            self._run_checkpoint(on_checkpoint)

        return "\n".join(collected_reports)

    def _process_hires(self, hires: list[HireRequest]) -> list[str]:
        """Process [HIRE] requests from CTO. Returns status messages."""
        from shipwright.company.roles import get_role_def

        messages = []
        for h in hires:
            try:
                role_def = get_role_def(h.role, self.config)
                emp = self.hire(h.role, role_def, name=h.name)
                messages.append(f"Hired **{emp.name}** as {role_def.role}")
                logger.info("CTO hired %s as %s", emp.name, h.role)
            except (ValueError, Exception) as e:
                messages.append(f"Failed to hire {h.role}: {e}")
                logger.warning("CTO hire failed for %s: %s", h.role, e)
        return messages

    async def _execute_delegations(
        self,
        coordinator: Employee,
        delegations: list[DelegationRequest],
        context_chain: list[str] | None = None,
        on_delegation_start: Callable[[str, str, int, int], None] | None = None,
        on_delegation_end: Callable[[str, float, bool], None] | None = None,
        on_progress: Callable[[str], None] | None = None,
        on_checkpoint: Callable[[], None] | None = None,
    ) -> str:
        """Execute delegations. Routes team-leads through their team delegation loop."""
        results_parts = []
        for d in delegations:
            if d.member_name not in self.employees:
                results_parts.append(
                    f"### [FAILED] {d.member_name}\n"
                    f"No employee named '{d.member_name}'."
                )
                continue

            emp = self.employees[d.member_name]

            if on_delegation_start:
                on_delegation_start(d.member_name, d.task, 1, 1)

            start_time = time.time()

            # Route team-leads through their team delegation loop
            if emp.is_lead and emp.team and emp.team in self.teams:
                output = await self._assign_to_team(
                    emp.team, d.task, context_chain=context_chain,
                    on_progress=on_progress,
                    on_checkpoint=on_checkpoint,
                )
                # Team delegation loop handles errors internally
                is_error = False
            else:
                output = await self._assign_to_employee(
                    d.member_name, d.task, context_chain=context_chain,
                    on_progress=on_progress,
                    on_checkpoint=on_checkpoint,
                )
                is_error = (
                    emp.task_history and emp.task_history[-1].status == "failed"
                )

            duration = time.time() - start_time

            if on_delegation_end:
                on_delegation_end(d.member_name, duration, is_error)

            status = "FAILED" if is_error else "COMPLETED"
            truncated = output[:5000]
            if len(output) > 5000:
                truncated += "\n... (truncated)"
            results_parts.append(f"### [{status}] {d.member_name}\n{truncated}")

        return "\n\n".join(results_parts)

    async def _get_coordinator_review(
        self, coordinator: Employee, results_summary: str,
    ) -> str:
        """Ask a coordinator (CTO or team-lead) to review delegation results."""
        review_prompt = (
            f"Here are the results from the team:\n\n{results_summary}\n\n"
            "Review the work quality. Options:\n"
            "1. If quality is good, present a summary.\n"
            "2. If something needs fixing, use [REVISE:EmployeeName] blocks "
            "with specific feedback.\n"
            "3. If more work is needed, use [DELEGATE:name] blocks.\n"
            "Be a quality gate — only present work you'd ship."
        )

        if coordinator.role == "cto":
            system_prompt = self._build_cto_prompt()
            result = await coordinator.run(
                task=review_prompt, system_prompt=system_prompt, on_text=None,
            )
            text = result.output
            coordinator._conversation.append(
                {"role": "system", "text": "Team results review"}
            )
            coordinator._conversation.append({"role": "cto", "text": text})
            return text

        # Team lead — use respond_as_lead
        team_members = {}
        if coordinator.team and coordinator.team in self.teams:
            for name in self.teams[coordinator.team].members:
                if name in self.employees:
                    team_members[name] = self.employees[name]
        response = await coordinator.respond_as_lead(
            user_message=review_prompt,
            team_name=coordinator.team or "",
            team_members=team_members,
            project_context=self.project_context,
            on_text=None,
        )
        return response.text

    async def _delegation_loop(
        self,
        coordinator: Employee,
        delegations: list[DelegationRequest],
        coordinator_text: str = "",
        context_chain: list[str] | None = None,
        on_text: Callable[[str], None] | None = None,
        on_delegation_start: Callable[[str, str, int, int], None] | None = None,
        on_delegation_end: Callable[[str, float, bool], None] | None = None,
        on_progress: Callable[[str], None] | None = None,
        on_checkpoint: Callable[[], None] | None = None,
        max_rounds: int | None = None,
    ) -> str:
        """Shared delegation loop for CTO and team-leads.

        Executes delegations, reviews results, handles revisions and
        additional delegations, returns the coordinator's final synthesis.
        Both CTO and team-leads use this — only the scope differs.
        max_rounds defaults to config.max_revision_rounds if not provided.
        """
        if max_rounds is None:
            max_rounds = self.config.max_revision_rounds
        # Build context chain for this level
        chain = list(context_chain or [])
        if coordinator_text:
            chain.append(f"From {coordinator.name}: {coordinator_text[:500]}")

        pending_delegations = list(delegations)
        pending_revisions: list[ReviseRequest] = []
        collected_output: list[str] = []
        review_text = ""

        for _round in range(max_rounds):
            # Execute pending delegations
            results_parts: list[str] = []
            if pending_delegations:
                del_results = await self._execute_delegations(
                    coordinator, pending_delegations, context_chain=chain,
                    on_delegation_start=on_delegation_start,
                    on_delegation_end=on_delegation_end,
                    on_progress=on_progress,
                    on_checkpoint=on_checkpoint,
                )
                results_parts.append(del_results)

            # Execute pending revisions
            for rev in pending_revisions:
                if rev.employee_name not in self.employees:
                    results_parts.append(
                        f"### [FAILED] {rev.employee_name}\n"
                        f"No employee named '{rev.employee_name}'."
                    )
                    continue
                if on_progress:
                    on_progress(f"{rev.employee_name} revising work...")

                emp = self.employees[rev.employee_name]
                feedback_task = (
                    f"Revise your previous work based on feedback:\n\n{rev.feedback}"
                )
                # Route team-lead revisions through their team loop
                if emp.is_lead and emp.team and emp.team in self.teams:
                    output = await self._assign_to_team(
                        emp.team, feedback_task, context_chain=chain,
                        on_checkpoint=on_checkpoint,
                    )
                else:
                    output = await self._assign_to_employee(
                        rev.employee_name, feedback_task, context_chain=chain,
                        on_progress=on_progress,
                        on_checkpoint=on_checkpoint,
                    )
                truncated = output[:5000]
                if len(output) > 5000:
                    truncated += "\n... (truncated)"
                results_parts.append(
                    f"### [REVISED] {rev.employee_name}\n{truncated}"
                )

            results_summary = "\n\n".join(results_parts)

            # Get coordinator review
            if on_progress:
                on_progress(f"{coordinator.name} reviewing results...")

            review_text = await self._get_coordinator_review(
                coordinator, results_summary,
            )

            # Parse all block types from review
            review_text, revisions = parse_revise_blocks(review_text)
            review_text, hires = parse_hire_blocks(review_text)
            review_text, more_delegations = parse_delegations(review_text)

            # Apply hierarchy filters
            pending_revisions = self._filter_revisions(coordinator, revisions)
            hires = self._filter_hires(coordinator, hires)
            pending_delegations = self._filter_delegations(
                coordinator, more_delegations,
            )

            # Process hires
            if hires:
                hire_msgs = self._process_hires(hires)
                collected_output.extend(hire_msgs)
                self._run_checkpoint(on_checkpoint)

            if not pending_revisions and not pending_delegations:
                # Coordinator approved the results
                if on_text and review_text:
                    on_text("\n\n" + review_text)
                collected_output.append(review_text)
                return "\n\n".join(filter(None, collected_output))

        # Max rounds reached
        collected_output.append(review_text)
        collected_output.append(
            "(Reached maximum rounds. Presenting current results.)"
        )
        if on_text:
            on_text(
                "\n\n" + review_text
                + "\n\n(Reached maximum rounds. Presenting current results.)"
            )
        return "\n\n".join(filter(None, collected_output))

    async def assign_work(
        self,
        target: str,
        task_description: str,
        on_text: Callable[[str], None] | None = None,
        on_delegation_start: Callable[[str, str, int, int], None] | None = None,
        on_delegation_end: Callable[[str, float, bool], None] | None = None,
        on_progress: Callable[[str], None] | None = None,
        on_checkpoint: Callable[[], None] | None = None,
    ) -> str:
        """Assign work to an employee or team.

        If target is an employee name, assigns directly.
        If target is a team name, assigns through the team lead.
        Returns a warning string if budget is exceeded.
        """
        budget = self.config.budget_limit_usd
        if budget > 0 and self.total_cost >= budget:
            return (
                f"**Budget exceeded.** Spent ${self.total_cost:.4f} "
                f"of ${budget:.2f} limit. "
                f"Increase BUDGET_LIMIT_USD or dismiss employees to free budget."
            )

        if target in self.employees:
            return await self._assign_to_employee(
                target, task_description, on_text=on_text,
                on_progress=on_progress,
                on_checkpoint=on_checkpoint,
            )
        elif target in self.teams:
            return await self._assign_to_team(
                target, task_description,
                on_text=on_text,
                on_delegation_start=on_delegation_start,
                on_delegation_end=on_delegation_end,
                on_progress=on_progress,
                on_checkpoint=on_checkpoint,
            )
        else:
            raise ValueError(f"No employee or team named '{target}'.")

    async def _assign_to_employee(
        self,
        employee_name: str,
        task_description: str,
        on_text: Callable[[str], None] | None = None,
        context_chain: list[str] | None = None,
        on_progress: Callable[[str], None] | None = None,
        on_checkpoint: Callable[[], None] | None = None,
    ) -> str:
        """Assign work directly to an employee."""
        employee = self.employees[employee_name]

        task = Task(
            id=str(uuid.uuid4())[:8],
            description=task_description,
            assigned_to=employee_name,
        )
        task.status = "running"
        employee.current_task = task
        employee.status = EmployeeStatus.WORKING
        self._run_checkpoint(on_checkpoint)
        if on_progress:
            on_progress(_progress_hint(employee.name, task_description))

        # Build context with upstream delegation chain
        context_parts = []
        if context_chain:
            context_parts.append("\n".join(context_chain))
        if self.project_context:
            context_parts.append(self.project_context)
        context = "\n\n".join(context_parts) if context_parts else self.project_context

        start_time = time.time()
        try:
            result = await employee.run(
                task=task_description,
                context=context,
                on_text=on_text,
            )

            task.status = "done" if not result.is_error else "failed"
            task.output = result.output
            task.cost_usd = result.total_cost_usd
            task.duration_ms = result.duration_ms
            task.completed_at = time.time()

            # Auto-commit after code changes
            if self.worktree_path and any(
                t in employee.role_def.tools for t in ("Edit", "Write")
            ):
                try:
                    commit(self.worktree_path, f"{employee.name}: {task_description[:50]}", no_verify=True)
                except Exception as e:
                    logger.warning("Auto-commit failed: %s", e)

        except Exception as exc:
            task.status = "failed"
            task.output = str(exc)
            result = MemberResult(output=str(exc), is_error=True)
            logger.error("[%s] Error: %s", employee_name, exc)
        finally:
            employee.task_history.append(task)
            employee.current_task = None
            employee.status = EmployeeStatus.IDLE
            self._run_checkpoint(on_checkpoint)

        return result.output

    async def _assign_to_team(
        self,
        team_name: str,
        task_description: str,
        on_text: Callable[[str], None] | None = None,
        on_delegation_start: Callable[[str, str, int, int], None] | None = None,
        on_delegation_end: Callable[[str, float, bool], None] | None = None,
        on_progress: Callable[[str], None] | None = None,
        context_chain: list[str] | None = None,
        on_checkpoint: Callable[[], None] | None = None,
    ) -> str:
        """Assign work to a team — the lead coordinates via delegation loop."""
        team = self.teams[team_name]
        if not team.lead:
            raise ValueError(f"Team '{team_name}' has no lead. Promote someone first.")

        lead = self.employees[team.lead]

        # Build team members dict (excluding lead)
        team_members = {}
        for member_name in team.members:
            if member_name in self.employees:
                team_members[member_name] = self.employees[member_name]

        # Build task with upstream context
        full_task = task_description
        if context_chain:
            chain_str = "\n".join(context_chain)
            full_task = f"{chain_str}\n\nTask: {task_description}"

        if on_progress:
            on_progress(_progress_hint(lead.name, task_description))

        # Initial lead response
        response = await lead.respond_as_lead(
            user_message=full_task,
            team_name=team_name,
            team_members=team_members,
            project_context=self.project_context,
            on_text=on_text,
        )
        self._run_checkpoint(on_checkpoint)

        clean_text, delegations = parse_delegations(response.text)
        # Hierarchy enforcement: team-lead scoped to team
        delegations = self._filter_delegations(lead, delegations)

        collected_responses: list[str] = []
        if clean_text:
            collected_responses.append(clean_text)

        if not delegations:
            return "\n\n".join(collected_responses) if collected_responses else response.text

        # Delegation loop (shared with CTO)
        loop_result = await self._delegation_loop(
            coordinator=lead,
            delegations=delegations,
            coordinator_text=clean_text,
            context_chain=context_chain,
            on_text=on_text,
            on_delegation_start=on_delegation_start,
            on_delegation_end=on_delegation_end,
            on_progress=on_progress,
            on_checkpoint=on_checkpoint,
        )

        if loop_result:
            collected_responses.append(loop_result)

        return "\n\n".join(collected_responses)

    async def talk(
        self,
        employee_name: str,
        message: str,
        on_text: Callable[[str], None] | None = None,
        on_delegation_start: Callable[[str, str, int, int], None] | None = None,
        on_delegation_end: Callable[[str, float, bool], None] | None = None,
        on_progress: Callable[[str], None] | None = None,
        on_checkpoint: Callable[[], None] | None = None,
    ) -> str:
        """Talk to an employee. If they're a team lead, uses delegation mode."""
        if employee_name not in self.employees:
            raise ValueError(f"No employee named '{employee_name}'.")

        employee = self.employees[employee_name]

        if employee.is_lead and employee.team and employee.team in self.teams:
            # Team lead — use delegation mode
            return await self._assign_to_team(
                employee.team, message,
                on_text=on_text,
                on_delegation_start=on_delegation_start,
                on_delegation_end=on_delegation_end,
                on_progress=on_progress,
                on_checkpoint=on_checkpoint,
            )
        else:
            # Individual contributor — direct conversation
            if on_progress:
                on_progress(_progress_hint(employee.name, message))
            result = await employee.run(
                task=message,
                context=self.project_context,
                on_text=on_text,
            )
            # Track conversation
            employee._conversation.append({"role": "user", "text": message})
            employee._conversation.append({"role": "employee", "text": result.output})
            self._run_checkpoint(on_checkpoint)
            return result.output

    def setup_worktree(self) -> Path:
        """Create a git worktree for isolated work."""
        if self.worktree_path:
            return self.worktree_path

        self.branch = "shipwright/company"
        self.worktree_path = create_worktree(self.config.repo_root, self.branch)

        # Update all employee working directories
        for employee in self.employees.values():
            employee.cwd = str(self.worktree_path)

        logger.info("Company worktree: %s", self.worktree_path)
        return self.worktree_path

    def cleanup(self) -> None:
        """Clean up worktree and resources."""
        if self.worktree_path and self.branch:
            cleanup_worktree(self.config.repo_root, self.worktree_path, self.branch)
            self.worktree_path = None
            logger.info("Company worktree cleaned up")

    async def ship(self, target: str | None = None) -> str | None:
        """Open a PR. If target is a team name, PR for team's work."""
        if not self.worktree_path or not self.branch:
            return None

        msg = "shipwright: company work"
        if target:
            msg = f"shipwright: {target} team work"

        commit(self.worktree_path, msg, no_verify=True)

        title = target or "Shipwright work"
        body = f"## Generated by Shipwright\n\nEmployees: {', '.join(self.employees.keys())}"

        try:
            push_branch(self.worktree_path, self.branch)
            pr_url = create_pr(self.worktree_path, self.branch, title, body)
            logger.info("PR opened: %s", pr_url)
            return pr_url
        except Exception as exc:
            logger.error("Failed to create PR: %s", exc)
            return None

    @property
    def status_summary(self) -> str:
        """Human-readable company status with tree hierarchy."""
        lines = []

        # CTO line
        cto = self.get_cto()
        if cto:
            lines.append(f"  CTO \u2014 online")

        # Teams
        for team in self.teams.values():
            members = [self.employees.get(n) for n in team.members]
            members = [e for e in members if e is not None]
            lines.append(f"\n  **{team.name}** ({len(members)} members)")
            for i, emp in enumerate(members):
                is_last = i == len(members) - 1
                connector = "\u2514\u2500\u2500" if is_last else "\u251c\u2500\u2500"
                lead_tag = " (Lead)" if emp.is_lead else ""
                status = self._emp_status_line(emp)
                lines.append(
                    f"  {connector} {emp.name} ({emp.display_role}){lead_tag}"
                    f" \u2014 {status}"
                )

        # Independent employees (excluding CTO)
        independent = [
            emp for emp in self.employees.values()
            if not emp.team and emp.role != "cto"
        ]
        if independent:
            if self.teams:
                lines.append("")
            for i, emp in enumerate(independent):
                is_last = i == len(independent) - 1
                connector = "\u2514\u2500\u2500" if is_last else "\u251c\u2500\u2500"
                status = self._emp_status_line(emp)
                lines.append(
                    f"  {connector} {emp.name} ({emp.display_role})"
                    f" \u2014 {status}"
                )

        # Roadmap status
        if self.active_roadmap:
            rm = self.active_roadmap
            state_tag = ""
            if rm.state == RoadmapState.PAUSED:
                state_tag = " *Paused*"
            elif rm.state == RoadmapState.INTERRUPTED:
                state_tag = " *Interrupted*"
            elif rm.state == RoadmapState.STOPPED:
                state_tag = " *Stopped*"
            elif rm.state == RoadmapState.RUNNING:
                state_tag = " (running)"
            lines.append(
                f"\n  Roadmap: {rm.done_count}/{rm.total_count} done{state_tag}"
            )
            desc = rm.paused_task_description
            if desc and rm.state in (RoadmapState.PAUSED, RoadmapState.INTERRUPTED):
                lines.append(f"  Next: {desc[:60]}")

        # Cost summary
        if self.total_cost > 0:
            lines.append(f"\n  Cost: ${self.total_cost:.4f}")
            if self.config.budget_limit_usd > 0:
                remaining = self.config.budget_limit_usd - self.total_cost
                lines.append(f"  Budget remaining: ${remaining:.4f}")

        return "\n".join(lines)

    def _emp_status_line(self, emp: Employee) -> str:
        """Build a compact status string for an employee."""
        if emp.status == EmployeeStatus.WORKING and emp.current_task:
            return f"working: {emp.current_task.description[:40]}"
        task_count = len(emp.task_history)
        if task_count:
            return f"idle  [{task_count} tasks]"
        return "idle"

    @property
    def cost_report(self) -> str:
        """Detailed cost report with per-employee breakdown."""
        lines = ["**Cost Report**\n"]
        total_cost = 0.0
        total_duration = 0
        has_costs = False

        for emp in self.employees.values():
            tasks_done = [t for t in emp.task_history if t.status == "done"]
            emp_duration = sum(t.duration_ms for t in emp.task_history)
            task_count = len(tasks_done)
            total_cost += emp.cost_total_usd
            total_duration += emp_duration

            if emp.cost_total_usd > 0 or task_count > 0:
                has_costs = True
                duration_str = format_duration_ms(emp_duration)
                task_label = "task" if task_count == 1 else "tasks"
                lines.append(
                    f"  {emp.name} ({emp.role_def.role}): "
                    f"${emp.cost_total_usd:.4f} — "
                    f"{task_count} {task_label}, {duration_str}"
                )
                for task in emp.task_history[-5:]:
                    if task.cost_usd > 0:
                        lines.append(f"    - {task.description[:50]}: ${task.cost_usd:.4f}")

        if has_costs:
            lines.append(
                f"\n  **Total: ${total_cost:.4f} | {format_duration_ms(total_duration)}**"
            )
            if self.config.budget_limit_usd > 0:
                pct = (total_cost / self.config.budget_limit_usd) * 100
                lines.append(
                    f"  Budget: ${self.config.budget_limit_usd:.2f} ({pct:.0f}% used)"
                )
        else:
            lines.append("  No costs recorded yet.")

        return "\n".join(lines)

    @property
    def total_cost(self) -> float:
        return sum(e.cost_total_usd for e in self.employees.values())

    def to_dict(self) -> dict:
        """Serialize company state for persistence."""
        wt_str = None
        if self.worktree_path:
            wt_str = str(self.worktree_path)
        elif self._stale_worktree:
            wt_str = self._stale_worktree

        result = {
            "employees": {name: emp.to_dict() for name, emp in self.employees.items()},
            "teams": {name: team.to_dict() for name, team in self.teams.items()},
            "active_employee": self._active_employee,
            "active_employee_explicit": self._active_employee_explicit,
            "worktree_path": wt_str,
            "branch": self.branch,
        }
        if self.active_roadmap:
            result["active_roadmap"] = self.active_roadmap.to_dict()
        return result

    @classmethod
    def from_dict(cls, data: dict, config: Config) -> "Company":
        """Restore company from persisted data."""
        from shipwright.company.roles import get_role_def

        company = cls(config=config)
        company._active_employee = data.get("active_employee")
        company._active_employee_explicit = data.get("active_employee_explicit", False)
        company.branch = data.get("branch")

        wt = data.get("worktree_path")
        if wt:
            if Path(wt).exists():
                company.worktree_path = Path(wt)
            else:
                company._stale_worktree = wt
                logger.warning("Company worktree %s no longer exists (marked stale)", wt)

        cwd = str(company.worktree_path or config.repo_root)

        # Restore employees
        for name, emp_data in data.get("employees", {}).items():
            try:
                role_id = emp_data["role"]
                role_def = get_role_def(role_id, config)
                employee = Employee.from_dict(
                    emp_data, role_def, cwd, config.model, config.permission_mode,
                )
                company.employees[name] = employee
            except (ValueError, KeyError) as e:
                logger.warning("Failed to restore employee %s: %s", name, e)

        # Restore teams
        for name, team_data in data.get("teams", {}).items():
            company.teams[name] = Team.from_dict(team_data)

        # Restore active roadmap
        roadmap_data = data.get("active_roadmap")
        if roadmap_data:
            company.active_roadmap = Roadmap.from_dict(roadmap_data)

        return company
