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
    LeadResponse,
    parse_delegations,
    next_name,
)
from shipwright.utils.logging import get_logger
from shipwright.workspace.git import (
    cleanup_worktree,
    commit,
    create_pr,
    create_worktree,
    push_branch,
    slug,
)

logger = get_logger("company.company")


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
    _active_employee: str | None = field(default=None, repr=False)
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
        )

        self.employees[emp_name] = employee

        # Auto-set as active if first employee
        if self._active_employee is None:
            self._active_employee = emp_name

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

    def set_active(self, name: str) -> None:
        """Set the active employee for conversation."""
        if name not in self.employees:
            raise ValueError(f"No employee named '{name}'.")
        self._active_employee = name

    async def assign_work(
        self,
        target: str,
        task_description: str,
        on_text: Callable[[str], None] | None = None,
        on_delegation_start: Callable[[str, str, int, int], None] | None = None,
        on_delegation_end: Callable[[str, float, bool], None] | None = None,
        on_progress: Callable[[str], None] | None = None,
    ) -> str:
        """Assign work to an employee or team.

        If target is an employee name, assigns directly.
        If target is a team name, assigns through the team lead.
        """
        if target in self.employees:
            return await self._assign_to_employee(
                target, task_description, on_text=on_text,
            )
        elif target in self.teams:
            return await self._assign_to_team(
                target, task_description,
                on_text=on_text,
                on_delegation_start=on_delegation_start,
                on_delegation_end=on_delegation_end,
                on_progress=on_progress,
            )
        else:
            raise ValueError(f"No employee or team named '{target}'.")

    async def _assign_to_employee(
        self,
        employee_name: str,
        task_description: str,
        on_text: Callable[[str], None] | None = None,
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

        start_time = time.time()
        try:
            result = await employee.run(
                task=task_description,
                context=self.project_context,
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

        return result.output

    async def _assign_to_team(
        self,
        team_name: str,
        task_description: str,
        on_text: Callable[[str], None] | None = None,
        on_delegation_start: Callable[[str, str, int, int], None] | None = None,
        on_delegation_end: Callable[[str, float, bool], None] | None = None,
        on_progress: Callable[[str], None] | None = None,
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

        # Initial lead response
        response = await lead.respond_as_lead(
            user_message=task_description,
            team_name=team_name,
            team_members=team_members,
            project_context=self.project_context,
            on_text=on_text,
        )

        clean_text, delegations = parse_delegations(response.text)
        collected_responses: list[str] = []
        if clean_text:
            collected_responses.append(clean_text)

        round_num = 0
        while delegations and round_num < self.max_delegation_rounds:
            round_num += 1
            logger.info(
                "[%s] Delegation round %d: %d task(s)",
                team_name, round_num, len(delegations),
            )

            # Execute delegations
            results_parts = []
            for d in delegations:
                member_name = d.member_name
                if member_name not in self.employees:
                    results_parts.append(f"### [FAILED] {member_name}\nNo employee named '{member_name}'.")
                    continue

                if on_delegation_start:
                    on_delegation_start(member_name, d.task, round_num, self.max_delegation_rounds)

                start_time = time.time()
                result = await self._assign_to_employee(
                    member_name, d.task, on_text=None,
                )
                duration = time.time() - start_time

                emp = self.employees[member_name]
                is_error = emp.task_history and emp.task_history[-1].status == "failed"

                if on_delegation_end:
                    on_delegation_end(member_name, duration, is_error)

                status = "FAILED" if is_error else "COMPLETED"
                output = result[:5000]
                if len(result) > 5000:
                    output += "\n... (truncated)"
                results_parts.append(f"### [{status}] {member_name}\n{output}")

            results_summary = "\n\n".join(results_parts)

            # Feed results back to the lead
            if on_progress:
                on_progress("Reviewing results...")

            followup = (
                f"Here are the results from the team:\n\n{results_summary}\n\n"
                "Review the results. If more work is needed, delegate again. "
                "Otherwise, summarize the outcome for the user."
            )

            response = await lead.respond_as_lead(
                user_message=followup,
                team_name=team_name,
                team_members=team_members,
                project_context=self.project_context,
                on_text=on_text,
            )

            clean_text, delegations = parse_delegations(response.text)
            if clean_text:
                collected_responses.append(clean_text)

        if delegations:
            collected_responses.append(
                "(Reached maximum delegation rounds. Some work may still be pending.)"
            )

        return "\n\n".join(collected_responses)

    async def talk(
        self,
        employee_name: str,
        message: str,
        on_text: Callable[[str], None] | None = None,
        on_delegation_start: Callable[[str, str, int, int], None] | None = None,
        on_delegation_end: Callable[[str, float, bool], None] | None = None,
        on_progress: Callable[[str], None] | None = None,
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
            )
        else:
            # Individual contributor — direct conversation
            result = await employee.run(
                task=message,
                context=self.project_context,
                on_text=on_text,
            )
            # Track conversation
            employee._conversation.append({"role": "user", "text": message})
            employee._conversation.append({"role": "employee", "text": result.output})
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
        """Human-readable company status."""
        lines = []

        # Teams
        for team in self.teams.values():
            lines.append(f"\n  Team: {team.name} ({len(team.members)} members)")
            for member_name in team.members:
                emp = self.employees.get(member_name)
                if emp:
                    lead_tag = " (Team Lead)" if emp.is_lead else ""
                    status = emp.status.value
                    if emp.current_task:
                        status = f"working: {emp.current_task.description[:40]}"
                    lines.append(f"    {emp.name} ({emp.display_role}){lead_tag} — {status}")

        # Independent employees
        independent = [
            emp for emp in self.employees.values()
            if not emp.team
        ]
        if independent:
            if self.teams:
                lines.append("\n  Independent:")
            for emp in independent:
                status = emp.status.value
                if emp.current_task:
                    status = f"working: {emp.current_task.description[:40]}"
                lines.append(f"    {emp.name} ({emp.display_role}) — {status}")

        return "\n".join(lines)

    @property
    def cost_report(self) -> str:
        """Detailed cost report."""
        lines = ["**Cost Report**\n"]
        total = 0.0
        for emp in self.employees.values():
            if emp.cost_total_usd > 0:
                lines.append(f"  {emp.name} ({emp.role_def.role}): ${emp.cost_total_usd:.4f}")
                total += emp.cost_total_usd
                for task in emp.task_history[-5:]:
                    if task.cost_usd > 0:
                        lines.append(f"    - {task.description[:50]}: ${task.cost_usd:.4f}")
        if total > 0:
            lines.append(f"\n  **Total: ${total:.4f}**")
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

        return {
            "employees": {name: emp.to_dict() for name, emp in self.employees.items()},
            "teams": {name: team.to_dict() for name, team in self.teams.items()},
            "active_employee": self._active_employee,
            "worktree_path": wt_str,
            "branch": self.branch,
        }

    @classmethod
    def from_dict(cls, data: dict, config: Config) -> "Company":
        """Restore company from persisted data."""
        from shipwright.company.roles import get_role_def

        company = cls(config=config)
        company._active_employee = data.get("active_employee")
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

        return company
