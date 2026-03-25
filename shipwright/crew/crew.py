"""Crew — a team of specialized AI developers with a lead coordinator.

A Crew manages:
- A CrewLead that the user talks to
- Multiple CrewMembers that do the actual work
- A git worktree for isolated code changes
- Task state and progress tracking
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Callable

from shipwright.config import Config, CrewDef, MemberDef, SpecialistDef
from shipwright.crew.lead import CrewLead, DelegationRequest, parse_delegations
from shipwright.crew.member import CrewMember, MemberResult
from shipwright.utils.logging import get_logger
from shipwright.workspace.git import (
    cleanup_worktree,
    commit,
    create_pr,
    create_worktree,
    push_branch,
    slug,
)

logger = get_logger("crew.crew")


class CrewStatus(str, Enum):
    IDLE = "idle"
    WORKING = "working"
    PAUSED = "paused"
    DONE = "done"
    FAILED = "failed"


@dataclass
class TaskRecord:
    """Record of a delegated task to a crew member."""

    member_name: str
    task: str
    status: str = "pending"  # pending, running, done, failed
    output: str = ""
    started_at: float | None = None
    finished_at: float | None = None
    cost_usd: float = 0.0


@dataclass
class Crew:
    """A team of specialized AI developers with a lead coordinator.

    Usage:
        crew = Crew.create("backend", crew_def, config, objective="Add Stripe")
        response = await crew.chat("What payment provider should we use?")
    """

    id: str
    crew_type: str
    objective: str
    config: Config
    crew_def: CrewDef
    lead: CrewLead = field(init=False)
    members: dict[str, CrewMember] = field(default_factory=dict)
    status: CrewStatus = CrewStatus.IDLE
    worktree_path: Path | None = None
    branch: str | None = None
    task_records: list[TaskRecord] = field(default_factory=list)
    created_at: float = field(default_factory=time.time)
    pr_url: str | None = None
    _stale_worktree: str | None = field(default=None, repr=False)
    _on_update: Callable[[str], None] | None = field(default=None, repr=False)

    @property
    def is_stale(self) -> bool:
        """True if this crew had a worktree that no longer exists on disk."""
        return self._stale_worktree is not None

    def __post_init__(self) -> None:
        self.lead = CrewLead(
            crew_def=self.crew_def,
            config=self.config,
        )

    @classmethod
    def create(
        cls,
        crew_type: str,
        crew_def: CrewDef,
        config: Config,
        objective: str,
        project_context: str = "",
    ) -> "Crew":
        """Create a new crew for a given objective."""
        crew_id = f"{crew_type}-{slug(objective)}"

        crew = cls(
            id=crew_id,
            crew_type=crew_type,
            objective=objective,
            config=config,
            crew_def=crew_def,
        )

        # Set project context on the lead
        crew.lead.project_context = project_context

        logger.info("Created crew %s for: %s", crew_id, objective)
        return crew

    def _ensure_members(self) -> None:
        """Lazily create crew members when work starts."""
        if self.members:
            return

        cwd = str(self.worktree_path or self.config.repo_root)

        for mname, mdef in self.crew_def.members.items():
            self.members[mname] = CrewMember(
                name=mname,
                definition=mdef,
                cwd=cwd,
                model=self.crew_def.model or self.config.model,
                permission_mode=self.config.permission_mode,
            )

    def setup_worktree(self) -> Path:
        """Create a git worktree for this crew's isolated work."""
        if self.worktree_path:
            return self.worktree_path

        self.branch = f"shipwright/{self.id}"
        self.worktree_path = create_worktree(self.config.repo_root, self.branch)

        # Update member working directories
        for member in self.members.values():
            member.cwd = str(self.worktree_path)

        logger.info("Crew %s worktree: %s", self.id, self.worktree_path)
        return self.worktree_path

    def cleanup(self) -> None:
        """Clean up worktree and resources."""
        if self.worktree_path and self.branch:
            cleanup_worktree(self.config.repo_root, self.worktree_path, self.branch)
            self.worktree_path = None
            logger.info("Crew %s cleaned up", self.id)

    max_delegation_rounds: int = 5

    async def chat(
        self,
        user_message: str,
        on_text: Callable[[str], None] | None = None,
        on_delegation_start: Callable[[str, str, int, int], None] | None = None,
        on_delegation_end: Callable[[str, float, bool], None] | None = None,
        on_progress: Callable[[str], None] | None = None,
    ) -> str:
        """Send a message to the crew lead and get a response.

        Implements a delegation loop:
        1. Lead responds (may include [DELEGATE] blocks)
        2. Parse delegation requests and execute them
        3. Feed results back to the lead
        4. Repeat until the lead responds without delegations or max rounds hit

        Args:
            on_text: Callback for streaming lead text as it arrives.
            on_delegation_start: Called when a member starts work.
                Args: (member_name, task, round_num, max_rounds).
            on_delegation_end: Called when a member finishes.
                Args: (member_name, duration_seconds, is_error).
            on_progress: Callback for status updates (e.g. 'Reviewing results...').

        Returns the lead's final response text.
        """
        self._ensure_members()
        chat_start = time.time()
        status_ctx = self._build_status_context()
        collected_responses: list[str] = []

        # Initial lead response
        response = await self.lead.respond(
            user_message=user_message,
            status_context=status_ctx,
            on_text=on_text,
        )

        clean_text, delegations = parse_delegations(response.text)
        if clean_text:
            collected_responses.append(clean_text)

        round_num = 0
        while delegations and round_num < self.max_delegation_rounds:
            round_num += 1
            logger.info(
                "[%s] Delegation round %d: %d task(s)",
                self.id, round_num, len(delegations),
            )

            # Build context from prior delegation results for members
            delegation_context = self._build_delegation_context()

            # Execute delegations (parallel if multiple, sequential if single)
            if len(delegations) == 1:
                d = delegations[0]
                if on_delegation_start:
                    on_delegation_start(d.member_name, d.task, round_num, self.max_delegation_rounds)
                result = await self._execute_delegation(d, delegation_context)
                dt = self._last_task_duration(d.member_name)
                if on_delegation_end:
                    on_delegation_end(d.member_name, dt, result.is_error)
                results_summary = self._format_member_result(d.member_name, result)
            else:
                for d in delegations:
                    if on_delegation_start:
                        on_delegation_start(d.member_name, d.task, round_num, self.max_delegation_rounds)
                tasks = [
                    (d.member_name, d.task, delegation_context)
                    for d in delegations
                ]
                parallel_results = await self.delegate_parallel(tasks)
                parts = []
                for d in delegations:
                    r = parallel_results.get(d.member_name)
                    if r:
                        dt = self._last_task_duration(d.member_name)
                        if on_delegation_end:
                            on_delegation_end(d.member_name, dt, r.is_error)
                        parts.append(self._format_member_result(d.member_name, r))
                results_summary = "\n\n".join(parts)

            # Feed results back to the lead
            if on_progress:
                on_progress("Reviewing results...")

            followup = (
                f"Here are the results from the team:\n\n{results_summary}\n\n"
                "Review the results. If more work is needed, delegate again. "
                "Otherwise, summarize the outcome for the user."
            )

            response = await self.lead.respond(
                user_message=followup,
                status_context=self._build_status_context(),
                on_text=on_text,
            )

            clean_text, delegations = parse_delegations(response.text)
            if clean_text:
                collected_responses.append(clean_text)

        if delegations:
            logger.warning(
                "[%s] Hit max delegation rounds (%d), returning partial result",
                self.id, self.max_delegation_rounds,
            )
            collected_responses.append(
                "(Reached maximum delegation rounds. Some work may still be pending.)"
            )

        total_time = time.time() - chat_start
        logger.info("[%s] Chat completed in %.1fs", self.id, total_time)

        return "\n\n".join(collected_responses)

    async def _execute_delegation(
        self,
        delegation: DelegationRequest,
        context: str,
    ) -> MemberResult:
        """Execute a single delegation request."""
        return await self.delegate(
            member_name=delegation.member_name,
            task=delegation.task,
            context=context,
        )

    def _build_delegation_context(self) -> str:
        """Build context from prior delegation results for member tasks."""
        if not self.task_records:
            return ""

        parts = ["## Prior work by the crew:"]
        for r in self.task_records[-10:]:
            if r.status == "done" and r.output:
                parts.append(f"### {r.member_name}: {r.task[:80]}")
                # Truncate to keep context manageable
                output = r.output[:3000]
                if len(r.output) > 3000:
                    output += "\n... (truncated)"
                parts.append(output)
        return "\n\n".join(parts) if len(parts) > 1 else ""

    def _last_task_duration(self, member_name: str) -> float:
        """Get duration of the most recent completed task for a member."""
        for rec in reversed(self.task_records):
            if rec.member_name == member_name and rec.started_at and rec.finished_at:
                return rec.finished_at - rec.started_at
        return 0.0

    @staticmethod
    def _format_member_result(member_name: str, result: MemberResult) -> str:
        """Format a member result for the lead to review."""
        status = "FAILED" if result.is_error else "COMPLETED"
        output = result.output[:5000]
        if len(result.output) > 5000:
            output += "\n... (truncated)"
        return f"### [{status}] {member_name}\n{output}"

    async def delegate(
        self,
        member_name: str,
        task: str,
        context: str = "",
        on_text: Callable[[str], None] | None = None,
    ) -> MemberResult:
        """Delegate a task directly to a specific crew member.

        Used by the lead (or programmatically) to assign work.
        """
        self._ensure_members()

        if member_name not in self.members:
            available = ", ".join(self.members.keys())
            raise ValueError(
                f"No member '{member_name}' in crew. Available: {available}"
            )

        member = self.members[member_name]
        record = TaskRecord(member_name=member_name, task=task)
        self.task_records.append(record)

        record.status = "running"
        record.started_at = time.time()
        self.status = CrewStatus.WORKING

        if self._on_update:
            self._on_update(f"[{self.id}] {member.role} is working on: {task[:80]}")

        try:
            result = await member.run(task=task, context=context, on_text=on_text)
            record.status = "done" if not result.is_error else "failed"
            record.output = result.output
            record.cost_usd = result.total_cost_usd

            if result.is_error:
                logger.warning("[%s] Member %s failed: %s", self.id, member_name, result.output[:200])
            else:
                logger.info("[%s] Member %s completed task", self.id, member_name)

                # Auto-commit after code changes
                if self.worktree_path and any(
                    t in member.allowed_tools for t in ("Edit", "Write")
                ):
                    try:
                        commit(self.worktree_path, f"{member.role}: {task[:50]}", no_verify=True)
                    except Exception as e:
                        logger.warning("Auto-commit failed: %s", e)

        except Exception as exc:
            record.status = "failed"
            record.output = str(exc)
            result = MemberResult(output=str(exc), is_error=True)
            logger.error("[%s] Member %s error: %s", self.id, member_name, exc)

        finally:
            record.finished_at = time.time()
            # Check if all work is done
            if not any(r.status == "running" for r in self.task_records):
                if any(r.status == "failed" for r in self.task_records):
                    self.status = CrewStatus.FAILED
                else:
                    self.status = CrewStatus.IDLE

        return result

    async def delegate_parallel(
        self,
        tasks: list[tuple[str, str, str]],
    ) -> dict[str, MemberResult]:
        """Delegate multiple tasks in parallel.

        Args:
            tasks: List of (member_name, task, context) tuples.

        Returns:
            Dict mapping member_name to their result.
        """
        coros = [
            self.delegate(member_name=name, task=task, context=ctx)
            for name, task, ctx in tasks
        ]
        results = await asyncio.gather(*coros, return_exceptions=True)

        output = {}
        for (name, _, _), result in zip(tasks, results):
            if isinstance(result, Exception):
                output[name] = MemberResult(output=str(result), is_error=True)
            else:
                output[name] = result
        return output

    async def ship(self, title: str | None = None, body: str | None = None) -> str | None:
        """Open a PR for this crew's work."""
        if not self.worktree_path or not self.branch:
            logger.warning("No worktree to ship from")
            return None

        commit(self.worktree_path, f"shipwright: {self.objective[:60]}", no_verify=True)

        pr_title = title or (
            self.objective[:70] if len(self.objective) <= 70
            else self.objective[:67] + "..."
        )
        pr_body = body or (
            f"## Request\n{self.objective}\n\n"
            f"## Crew\n{self.crew_type}\n\n"
            f"---\n*Generated by shipwright*"
        )

        try:
            push_branch(self.worktree_path, self.branch)
            self.pr_url = create_pr(self.worktree_path, self.branch, pr_title, pr_body)
            self.status = CrewStatus.DONE
            logger.info("[%s] PR opened: %s", self.id, self.pr_url)
            return self.pr_url
        except Exception as exc:
            logger.error("[%s] Failed to create PR: %s", self.id, exc)
            return None

    def recruit_specialist(self, specialist: SpecialistDef) -> str:
        """Add a specialist to this running crew.

        Returns the member name used for the specialist.
        """
        member_name = specialist.name.replace("-", "_").replace(" ", "_")

        # Avoid name collisions
        if member_name in self.members or member_name in self.crew_def.members:
            member_name = f"specialist_{member_name}"

        cwd = str(self.worktree_path or self.config.repo_root)
        self.members[member_name] = CrewMember(
            name=member_name,
            definition=specialist.member_def,
            cwd=cwd,
            model=self.crew_def.model or self.config.model,
            permission_mode=self.config.permission_mode,
        )

        logger.info(
            "[%s] Recruited specialist %s (%s)",
            self.id, member_name, specialist.member_def.role,
        )
        return member_name

    def pause(self) -> None:
        self.status = CrewStatus.PAUSED

    def resume(self) -> None:
        self.status = CrewStatus.IDLE

    def _build_status_context(self) -> str:
        """Build status context for the lead."""
        lines = [f"Crew: {self.id} ({self.crew_type})", f"Status: {self.status.value}"]
        if self.objective:
            lines.append(f"Objective: {self.objective}")
        if self.branch:
            lines.append(f"Branch: {self.branch}")

        if self.task_records:
            lines.append("\nRecent tasks:")
            for r in self.task_records[-10:]:
                icon = {"pending": "[ ]", "running": "[~]", "done": "[x]", "failed": "[!]"}.get(
                    r.status, "[?]"
                )
                lines.append(f"  {icon} {r.member_name}: {r.task[:60]}")
                if r.output and r.status == "failed":
                    lines.append(f"      Error: {r.output[:100]}")

        return "\n".join(lines)

    @property
    def summary(self) -> str:
        """Human-readable summary of crew state."""
        status_label = self.status.value
        if self.is_stale:
            status_label = "stale"
        parts = [f"**{self.id}** [{status_label}]"]
        parts.append(f"  Objective: {self.objective}")
        if self.branch:
            parts.append(f"  Branch: `{self.branch}`")
        if self.is_stale:
            parts.append(f"  Worktree: STALE (was: {self._stale_worktree})")
        if self.pr_url:
            parts.append(f"  PR: {self.pr_url}")

        done = sum(1 for r in self.task_records if r.status == "done")
        total = len(self.task_records)
        if total:
            parts.append(f"  Tasks: {done}/{total} complete")

        total_cost = sum(r.cost_usd for r in self.task_records)
        if total_cost > 0:
            parts.append(f"  Cost: ${total_cost:.4f}")

        return "\n".join(parts)

    def to_dict(self) -> dict:
        """Serialize crew state for persistence."""
        # Preserve original worktree path even when stale (for re-detection)
        wt_str = None
        if self.worktree_path:
            wt_str = str(self.worktree_path)
        elif self._stale_worktree:
            wt_str = self._stale_worktree

        return {
            "id": self.id,
            "crew_type": self.crew_type,
            "objective": self.objective,
            "status": self.status.value,
            "branch": self.branch,
            "worktree_path": wt_str,
            "pr_url": self.pr_url,
            "created_at": self.created_at,
            "lead": self.lead.to_dict(),
            "task_records": [
                {
                    "member_name": r.member_name,
                    "task": r.task,
                    "status": r.status,
                    "output": r.output[:2000],
                    "started_at": r.started_at,
                    "finished_at": r.finished_at,
                    "cost_usd": r.cost_usd,
                }
                for r in self.task_records
            ],
        }

    @classmethod
    def from_dict(cls, data: dict, crew_def: CrewDef, config: Config) -> "Crew":
        """Restore a crew from persisted data."""
        crew = cls(
            id=data["id"],
            crew_type=data["crew_type"],
            objective=data["objective"],
            config=config,
            crew_def=crew_def,
        )
        crew.status = CrewStatus(data.get("status", "idle"))
        crew.branch = data.get("branch")
        wt = data.get("worktree_path")
        if wt:
            if Path(wt).exists():
                crew.worktree_path = Path(wt)
            else:
                crew._stale_worktree = wt
                logger.warning(
                    "Crew %s: worktree %s no longer exists (marked stale)",
                    crew.id, wt,
                )
        crew.pr_url = data.get("pr_url")
        crew.created_at = data.get("created_at", time.time())

        # Restore lead state
        if "lead" in data:
            crew.lead.restore_from_dict(data["lead"])

        # Restore task records
        for rec_data in data.get("task_records", []):
            crew.task_records.append(TaskRecord(
                member_name=rec_data["member_name"],
                task=rec_data["task"],
                status=rec_data.get("status", "done"),
                output=rec_data.get("output", ""),
                started_at=rec_data.get("started_at"),
                finished_at=rec_data.get("finished_at"),
                cost_usd=rec_data.get("cost_usd", 0.0),
            ))

        return crew


# ---------------------------------------------------------------------------
# Enterprise Mode — 3-level hierarchy
# ---------------------------------------------------------------------------

MAX_HIERARCHY_DEPTH = 3


@dataclass
class EnterpriseCrew(Crew):
    """Enterprise crew: Project Lead → Sub-Crew Leads → Members.

    Instead of delegating to individual CrewMembers, delegation spawns
    full sub-crews (each with their own lead + members).

    Hard-capped at 3 levels of hierarchy.
    """

    depth: int = 1  # 1 = top-level enterprise crew
    sub_crews: dict[str, Crew] = field(default_factory=dict)

    def _ensure_members(self) -> None:
        """Enterprise crew doesn't create members — it spawns sub-crews on demand."""
        # Members dict stays empty; sub-crews are created during delegation.
        pass

    async def _execute_delegation(
        self,
        delegation: DelegationRequest,
        context: str,
    ) -> MemberResult:
        """Override: spawn a sub-crew instead of running a single member."""
        return await self._delegate_to_subcrew(
            crew_type=delegation.member_name,
            task=delegation.task,
            context=context,
        )

    async def delegate(
        self,
        member_name: str,
        task: str,
        context: str = "",
        on_text: Callable[[str], None] | None = None,
    ) -> MemberResult:
        """Override: delegate to a sub-crew instead of an individual member."""
        return await self._delegate_to_subcrew(
            crew_type=member_name,
            task=task,
            context=context,
        )

    async def delegate_parallel(
        self,
        tasks: list[tuple[str, str, str]],
    ) -> dict[str, MemberResult]:
        """Override: delegate to multiple sub-crews in parallel."""
        coros = [
            self._delegate_to_subcrew(crew_type=name, task=task, context=ctx)
            for name, task, ctx in tasks
        ]
        results = await asyncio.gather(*coros, return_exceptions=True)

        output = {}
        for (name, _, _), result in zip(tasks, results):
            if isinstance(result, Exception):
                output[name] = MemberResult(output=str(result), is_error=True)
            else:
                output[name] = result
        return output

    async def _delegate_to_subcrew(
        self,
        crew_type: str,
        task: str,
        context: str = "",
    ) -> MemberResult:
        """Spawn a full sub-crew and run the task through it.

        The sub-crew gets its own lead + members and runs a complete
        delegation loop. Results flow back up to the project lead.
        """
        from shipwright.crew.registry import get_crew_def, BUILTIN_CREWS

        # Enforce depth cap
        if self.depth >= MAX_HIERARCHY_DEPTH:
            return MemberResult(
                output=f"Cannot spawn sub-crew: maximum hierarchy depth ({MAX_HIERARCHY_DEPTH}) reached.",
                is_error=True,
            )

        # Resolve the crew type — only allow known non-enterprise types
        if crew_type == "enterprise":
            return MemberResult(
                output="Cannot nest enterprise crews. Delegate to specific crew types (backend, frontend, etc.).",
                is_error=True,
            )

        try:
            sub_crew_def = get_crew_def(crew_type, self.config)
        except ValueError:
            available = list(BUILTIN_CREWS.keys())
            available = [k for k in available if k != "enterprise"]
            return MemberResult(
                output=f"Unknown crew type '{crew_type}'. Available: {', '.join(available)}",
                is_error=True,
            )

        # Don't accidentally spawn another enterprise crew
        if sub_crew_def.name == "enterprise":
            return MemberResult(
                output="Cannot nest enterprise crews.",
                is_error=True,
            )

        # Create sub-crew
        sub_id = f"{self.id}/{crew_type}"
        project_context = self.lead.project_context

        record = TaskRecord(member_name=crew_type, task=task)
        self.task_records.append(record)
        record.status = "running"
        record.started_at = time.time()
        self.status = CrewStatus.WORKING

        logger.info(
            "[%s] Spawning sub-crew '%s' (depth %d) for: %s",
            self.id, crew_type, self.depth + 1, task[:80],
        )

        try:
            sub_crew = Crew(
                id=sub_id,
                crew_type=crew_type,
                objective=task,
                config=self.config,
                crew_def=sub_crew_def,
            )
            sub_crew.lead.project_context = project_context

            # Set up worktree branched from enterprise crew's branch
            if self.worktree_path and self.branch:
                sub_branch = f"{self.branch}/{crew_type}"
                sub_crew.branch = sub_branch
                sub_crew.worktree_path = create_worktree(
                    self.worktree_path, sub_branch,
                )
                # Update member cwd after worktree setup
                for member in sub_crew.members.values():
                    member.cwd = str(sub_crew.worktree_path)

            self.sub_crews[crew_type] = sub_crew

            # Build the full prompt with context
            prompt = task
            if context:
                prompt = f"{context}\n\n---\n\nTask:\n{task}"

            # Run the sub-crew's delegation loop
            result_text = await sub_crew.chat(user_message=prompt)

            record.status = "done"
            record.output = result_text

            logger.info("[%s] Sub-crew '%s' completed", self.id, crew_type)
            return MemberResult(output=result_text)

        except Exception as exc:
            record.status = "failed"
            record.output = str(exc)
            logger.error("[%s] Sub-crew '%s' failed: %s", self.id, crew_type, exc)
            return MemberResult(output=str(exc), is_error=True)

        finally:
            record.finished_at = time.time()
            if not any(r.status == "running" for r in self.task_records):
                if any(r.status == "failed" for r in self.task_records):
                    self.status = CrewStatus.FAILED
                else:
                    self.status = CrewStatus.IDLE

    def cleanup(self) -> None:
        """Clean up all sub-crew worktrees, then our own."""
        for sub_crew in self.sub_crews.values():
            try:
                sub_crew.cleanup()
            except Exception as e:
                logger.warning("Sub-crew cleanup failed: %s", e)
        super().cleanup()

    @property
    def summary(self) -> str:
        """Human-readable summary including sub-crew hierarchy."""
        parts = [super().summary]

        if self.sub_crews:
            parts.append("  Sub-crews:")
            for name, sub in self.sub_crews.items():
                status = sub.status.value
                done = sum(1 for r in sub.task_records if r.status == "done")
                total = len(sub.task_records)
                task_info = f" ({done}/{total} tasks)" if total else ""
                parts.append(f"    → {name} [{status}]{task_info}")

        return "\n".join(parts)

    def to_dict(self) -> dict:
        """Serialize enterprise crew state."""
        data = super().to_dict()
        data["is_enterprise"] = True
        data["depth"] = self.depth
        data["sub_crews"] = {
            name: sub.to_dict() for name, sub in self.sub_crews.items()
        }
        return data

    @classmethod
    def from_dict(cls, data: dict, crew_def: CrewDef, config: Config) -> "EnterpriseCrew":
        """Restore an enterprise crew from persisted data."""
        crew = cls(
            id=data["id"],
            crew_type=data["crew_type"],
            objective=data["objective"],
            config=config,
            crew_def=crew_def,
        )
        crew.status = CrewStatus(data.get("status", "idle"))
        crew.branch = data.get("branch")
        wt = data.get("worktree_path")
        if wt:
            if Path(wt).exists():
                crew.worktree_path = Path(wt)
            else:
                crew._stale_worktree = wt
                logger.warning(
                    "EnterpriseCrew %s: worktree %s no longer exists (marked stale)",
                    crew.id, wt,
                )
        crew.pr_url = data.get("pr_url")
        crew.created_at = data.get("created_at", time.time())
        crew.depth = data.get("depth", 1)

        if "lead" in data:
            crew.lead.restore_from_dict(data["lead"])

        for rec_data in data.get("task_records", []):
            crew.task_records.append(TaskRecord(
                member_name=rec_data["member_name"],
                task=rec_data["task"],
                status=rec_data.get("status", "done"),
                output=rec_data.get("output", ""),
                started_at=rec_data.get("started_at"),
                finished_at=rec_data.get("finished_at"),
                cost_usd=rec_data.get("cost_usd", 0.0),
            ))

        # Restore sub-crews
        from shipwright.crew.registry import get_crew_def as _get_crew_def
        for name, sub_data in data.get("sub_crews", {}).items():
            try:
                sub_def = _get_crew_def(sub_data["crew_type"], config)
                sub_crew = Crew.from_dict(sub_data, sub_def, config)
                crew.sub_crews[name] = sub_crew
            except (ValueError, KeyError) as e:
                logger.warning("Failed to restore sub-crew %s: %s", name, e)

        return crew
