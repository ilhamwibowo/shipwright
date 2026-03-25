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

from shipwright.config import Config, CrewDef
from shipwright.crew.lead import CrewLead
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
    _on_update: Callable[[str], None] | None = field(default=None, repr=False)

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

    async def chat(
        self,
        user_message: str,
        on_text: Callable[[str], None] | None = None,
    ) -> str:
        """Send a message to the crew lead and get a response.

        This is the primary interaction point. The lead will:
        1. Analyze the message
        2. Respond conversationally
        3. Potentially delegate work to members

        Returns the lead's response text.
        """
        status_ctx = self._build_status_context()

        response = await self.lead.respond(
            user_message=user_message,
            status_context=status_ctx,
            on_text=on_text,
        )

        return response.text

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
                        commit(self.worktree_path, f"{member.role}: {task[:50]}")
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

        commit(self.worktree_path, f"shipwright: {self.objective[:60]}")

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
        parts = [f"**{self.id}** [{self.status.value}]"]
        parts.append(f"  Objective: {self.objective}")
        if self.branch:
            parts.append(f"  Branch: `{self.branch}`")
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
        return {
            "id": self.id,
            "crew_type": self.crew_type,
            "objective": self.objective,
            "status": self.status.value,
            "branch": self.branch,
            "worktree_path": str(self.worktree_path) if self.worktree_path else None,
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
        crew.worktree_path = Path(wt) if wt and Path(wt).exists() else None
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
