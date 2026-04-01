"""Employee — a persistent AI team member wrapping a Claude Agent SDK session.

Each employee has:
- A name (auto-generated or user-chosen)
- A role with specialized system prompt and tools
- Persistent memory via SDK session_id resume
- Can work as individual contributor OR as team lead coordinator
"""

from __future__ import annotations

import os
import re
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Callable

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    RateLimitEvent,
    ResultMessage,
    TextBlock,
    ThinkingBlock,
    ToolUseBlock,
    query,
)

from shipwright.config import MemberDef
from shipwright.utils.logging import get_logger

logger = get_logger("company.employee")


# ---------------------------------------------------------------------------
# Roadmap — multi-task autonomous execution plan
# ---------------------------------------------------------------------------

class RoadmapTaskStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"


@dataclass
class RoadmapTask:
    """A single task within a roadmap."""
    index: int  # 1-based position
    description: str
    status: RoadmapTaskStatus = RoadmapTaskStatus.PENDING
    output_summary: str = ""
    handoff_artifact: str = ""  # path or content from handoff

    def to_dict(self) -> dict:
        return {
            "index": self.index,
            "description": self.description,
            "status": self.status.value,
            "output_summary": self.output_summary[:2000],
            "handoff_artifact": self.handoff_artifact[:3000],
        }

    @classmethod
    def from_dict(cls, data: dict) -> "RoadmapTask":
        return cls(
            index=data["index"],
            description=data["description"],
            status=RoadmapTaskStatus(data.get("status", "pending")),
            output_summary=data.get("output_summary", ""),
            handoff_artifact=data.get("handoff_artifact", ""),
        )


class RoadmapState(str, Enum):
    """Execution state of a roadmap."""
    PENDING = "pending"        # Created but not yet approved
    RUNNING = "running"        # Actively executing
    PAUSED = "paused"          # Gracefully paused by user (safe point)
    INTERRUPTED = "interrupted" # Force-stopped mid-task (pause now / Ctrl+C)
    STOPPED = "stopped"        # Cancelled by user, keep history
    COMPLETE = "complete"      # All tasks done


@dataclass
class Roadmap:
    """A multi-task execution plan created by the CTO for large projects."""
    tasks: list[RoadmapTask] = field(default_factory=list)
    original_request: str = ""
    approved: bool = False
    paused: bool = False  # backward compat — True when paused/interrupted
    state: RoadmapState = RoadmapState.PENDING

    @property
    def current_task_index(self) -> int | None:
        """Return the 1-based index of the next pending/running task, or None if all done."""
        for t in self.tasks:
            if t.status in (RoadmapTaskStatus.PENDING, RoadmapTaskStatus.RUNNING):
                return t.index
        return None

    @property
    def done_count(self) -> int:
        return sum(1 for t in self.tasks if t.status == RoadmapTaskStatus.DONE)

    @property
    def total_count(self) -> int:
        return len(self.tasks)

    @property
    def is_complete(self) -> bool:
        return all(t.status == RoadmapTaskStatus.DONE for t in self.tasks)

    @property
    def accumulated_context(self) -> str:
        """Build context from all completed tasks' handoff artifacts."""
        parts = []
        for t in self.tasks:
            if t.status == RoadmapTaskStatus.DONE and t.handoff_artifact:
                parts.append(
                    f"## Task {t.index}: {t.description}\n{t.handoff_artifact}"
                )
        return "\n\n".join(parts)

    @property
    def paused_task_description(self) -> str | None:
        """Return the description of the task that was paused/interrupted, if any."""
        for t in self.tasks:
            if t.status in (RoadmapTaskStatus.PENDING, RoadmapTaskStatus.RUNNING):
                return t.description
        return self.original_request or None

    def status_display(self) -> str:
        """Human-readable roadmap status."""
        state_label = ""
        if self.state == RoadmapState.PAUSED:
            state_label = " — PAUSED"
        elif self.state == RoadmapState.INTERRUPTED:
            state_label = " — INTERRUPTED"
        elif self.state == RoadmapState.STOPPED:
            state_label = " — STOPPED"

        lines = [
            f"**Roadmap** ({self.done_count}/{self.total_count} tasks done{state_label})\n",
            f"  {'─' * 48}",
        ]
        for t in self.tasks:
            icon = {
                "pending": "[ ]",
                "running": "[~]",
                "done": "[x]",
                "failed": "[!]",
            }[t.status.value]
            # Mark which task was paused/interrupted
            suffix = ""
            if t.status == RoadmapTaskStatus.PENDING and self.state == RoadmapState.PAUSED:
                # First pending task after pause
                if t.index == (self.current_task_index or 0):
                    suffix = "  ← paused here"
            elif t.status == RoadmapTaskStatus.PENDING and self.state == RoadmapState.INTERRUPTED:
                if t.index == (self.current_task_index or 0):
                    suffix = "  ← interrupted here"
            lines.append(f"  {icon} {t.index}. {t.description}{suffix}")
            if t.output_summary:
                lines.append(f"       {t.output_summary[:80]}")
        lines.append(f"  {'─' * 48}")
        if self.state == RoadmapState.PAUSED:
            lines.append("\n  *Paused* — type `continue` or `resume` to pick up where you left off.")
        elif self.state == RoadmapState.INTERRUPTED:
            lines.append("\n  *Interrupted* — type `continue` or `resume` to retry.")
        elif self.state == RoadmapState.STOPPED:
            lines.append("\n  *Stopped* — roadmap cancelled. Start a new task or create a new roadmap.")
        return "\n".join(lines)

    def to_dict(self) -> dict:
        return {
            "tasks": [t.to_dict() for t in self.tasks],
            "original_request": self.original_request,
            "approved": self.approved,
            "paused": self.paused,
            "state": self.state.value,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Roadmap":
        state_str = data.get("state")
        if state_str:
            state = RoadmapState(state_str)
        else:
            # Backward compat: derive state from old paused bool
            paused = data.get("paused", False)
            approved = data.get("approved", False)
            if paused:
                state = RoadmapState.PAUSED
            elif approved:
                state = RoadmapState.RUNNING
            else:
                state = RoadmapState.PENDING
        return cls(
            tasks=[RoadmapTask.from_dict(t) for t in data.get("tasks", [])],
            original_request=data.get("original_request", ""),
            approved=data.get("approved", False),
            paused=data.get("paused", False),
            state=state,
        )


# Name pool for auto-generating employee names
NAME_POOL = [
    "Alex", "Blake", "Casey", "Drew", "Ellis", "Finley", "Gray", "Harper",
    "Indigo", "Jordan", "Kai", "Lane", "Morgan", "Nori", "Oakley", "Phoenix",
    "Quinn", "Reese", "Sage", "Tatum", "Unity", "Val", "Winter", "Xen",
    "Yael", "Zen",
]


class EmployeeStatus(str, Enum):
    IDLE = "idle"
    WORKING = "working"
    BLOCKED = "blocked"


@dataclass
class Task:
    """Record of work assigned to an employee."""
    id: str
    description: str
    assigned_to: str  # employee name
    status: str = "pending"  # pending, running, done, failed
    output: str = ""
    cost_usd: float = 0.0
    duration_ms: int = 0
    created_at: float = field(default_factory=time.time)
    completed_at: float | None = None

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "description": self.description,
            "assigned_to": self.assigned_to,
            "status": self.status,
            "output": self.output[:2000],
            "cost_usd": self.cost_usd,
            "duration_ms": self.duration_ms,
            "created_at": self.created_at,
            "completed_at": self.completed_at,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Task":
        return cls(
            id=data["id"],
            description=data["description"],
            assigned_to=data["assigned_to"],
            status=data.get("status", "done"),
            output=data.get("output", ""),
            cost_usd=data.get("cost_usd", 0.0),
            duration_ms=data.get("duration_ms", 0),
            created_at=data.get("created_at", time.time()),
            completed_at=data.get("completed_at"),
        )


@dataclass
class MemberResult:
    """Result from an employee's work."""
    output: str
    session_id: str = ""
    num_turns: int = 0
    duration_ms: int = 0
    total_cost_usd: float = 0.0
    is_error: bool = False


# Delegation parsing (reused from lead.py)
_DELEGATE_PATTERN = re.compile(
    r"\[DELEGATE:(\w+)\]\s*\n(.*?)\[/DELEGATE\]",
    re.DOTALL,
)


@dataclass
class DelegationRequest:
    """A parsed delegation request from a team lead's response."""
    member_name: str
    task: str


def parse_delegations(text: str) -> tuple[str, list[DelegationRequest]]:
    """Parse [DELEGATE:member] blocks from a lead's response.
    Returns (clean_text with blocks removed, list of DelegationRequests).
    """
    delegations: list[DelegationRequest] = []
    for match in _DELEGATE_PATTERN.finditer(text):
        member_name = match.group(1).strip()
        task = match.group(2).strip()
        if member_name and task:
            delegations.append(DelegationRequest(member_name=member_name, task=task))
    clean_text = _DELEGATE_PATTERN.sub("", text).strip()
    return clean_text, delegations


# ---------------------------------------------------------------------------
# CTO block parsing — [HIRE:role], [HIRE:role:name], [REVISE:name]
# ---------------------------------------------------------------------------

_HIRE_PATTERN = re.compile(
    r"\[HIRE:([\w-]+)(?::([\w]+))?\]",
)

_REVISE_PATTERN = re.compile(
    r"\[REVISE:(\w+)\]\s*\n(.*?)\[/REVISE\]",
    re.DOTALL,
)


@dataclass
class HireRequest:
    """A parsed hire request from the CTO's response."""
    role: str
    name: str | None = None


@dataclass
class ReviseRequest:
    """A parsed revision request from the CTO's response."""
    employee_name: str
    feedback: str


def parse_hire_blocks(text: str) -> tuple[str, list[HireRequest]]:
    """Parse [HIRE:role] and [HIRE:role:name] blocks from CTO response.
    Returns (clean_text with blocks removed, list of HireRequests).
    """
    hires: list[HireRequest] = []
    for match in _HIRE_PATTERN.finditer(text):
        role = match.group(1).strip()
        name = match.group(2)
        if name:
            name = name.strip()
        if role:
            hires.append(HireRequest(role=role, name=name))
    clean_text = _HIRE_PATTERN.sub("", text).strip()
    return clean_text, hires


def parse_revise_blocks(text: str) -> tuple[str, list[ReviseRequest]]:
    """Parse [REVISE:name] blocks from CTO response.
    Returns (clean_text with blocks removed, list of ReviseRequests).
    """
    revisions: list[ReviseRequest] = []
    for match in _REVISE_PATTERN.finditer(text):
        name = match.group(1).strip()
        feedback = match.group(2).strip()
        if name and feedback:
            revisions.append(ReviseRequest(employee_name=name, feedback=feedback))
    clean_text = _REVISE_PATTERN.sub("", text).strip()
    return clean_text, revisions


# ---------------------------------------------------------------------------
# Roadmap block parsing — [ROADMAP] and [EXECUTE_ROADMAP]
# ---------------------------------------------------------------------------

_ROADMAP_PATTERN = re.compile(
    r"\[ROADMAP\]\s*\n(.*?)\[/ROADMAP\]",
    re.DOTALL,
)

_EXECUTE_ROADMAP_PATTERN = re.compile(
    r"\[EXECUTE_ROADMAP\]",
)


def parse_roadmap_block(text: str) -> tuple[str, Roadmap | None]:
    """Parse a [ROADMAP]...[/ROADMAP] block from CTO response.
    Returns (clean_text with block removed, Roadmap or None).
    """
    match = _ROADMAP_PATTERN.search(text)
    if not match:
        return text, None

    raw = match.group(1).strip()
    tasks: list[RoadmapTask] = []
    for line in raw.split("\n"):
        line = line.strip()
        if not line:
            continue
        # Match lines like "1. Do something" or "- Do something"
        m = re.match(r'^(?:(\d+)[.)]\s*|-\s*)(.*)', line)
        if m:
            idx = int(m.group(1)) if m.group(1) else len(tasks) + 1
            desc = m.group(2).strip()
            if desc:
                tasks.append(RoadmapTask(index=idx, description=desc))

    if not tasks:
        return text, None

    # Re-index to ensure sequential 1-based
    for i, t in enumerate(tasks):
        t.index = i + 1

    clean = _ROADMAP_PATTERN.sub("", text).strip()
    return clean, Roadmap(tasks=tasks)


def parse_execute_roadmap(text: str) -> tuple[str, bool]:
    """Parse [EXECUTE_ROADMAP] signal from CTO response.
    Returns (clean_text, should_execute).
    """
    has_execute = bool(_EXECUTE_ROADMAP_PATTERN.search(text))
    clean = _EXECUTE_ROADMAP_PATTERN.sub("", text).strip()
    return clean, has_execute


@dataclass
class LeadResponse:
    """Parsed response from a team lead."""
    text: str
    delegations: list[DelegationRequest] = field(default_factory=list)
    session_id: str = ""


def _build_team_lead_prompt(
    employee_name: str,
    team_name: str,
    members: dict[str, "Employee"],
    project_context: str = "",
) -> str:
    """Build the system prompt for an employee acting as team lead."""
    member_descriptions = []
    for name, emp in members.items():
        if name == employee_name:
            continue  # Skip the lead itself
        tools = ", ".join(emp.role_def.tools)
        summary = emp.role_def.prompt.strip().split('\n')[0]
        member_descriptions.append(
            f"- **{name}** ({emp.role_def.role}): {summary} [Tools: {tools}]"
        )

    members_section = "\n".join(member_descriptions) if member_descriptions else "No members assigned yet."
    member_names = [n for n in members if n != employee_name]

    return f"""You are {employee_name}, the Team Lead of the **{team_name}** team. You coordinate your team members to accomplish tasks.

## Your Team
{members_section}

## How You Work
1. When you receive a task, analyze what needs to be done
2. Break it into steps and delegate to the right team members
3. Tell the user what you're about to do
4. Use the delegation format below to assign work to members
5. Review member results and either delegate more work or respond to the user

## Delegation Format
When you need a team member to do work, include delegation blocks:

[DELEGATE:member_name]
Detailed task description for the member.
[/DELEGATE]

You can delegate to multiple members at once — they will work in parallel.
You can also delegate in stages: first analyze, review results, then implement.

## Communication Style
- Be conversational and collaborative
- ALWAYS tell the user what you're delegating and why
- Report progress at milestones
- After receiving results, summarize what was done
- Ask for feedback before moving to the next phase

## Important Rules
- You MUST use [DELEGATE:member_name] blocks to assign work. You cannot do work yourself.
- NEVER just talk about delegating. Actually include the [DELEGATE] block.
- Member names must exactly match: {', '.join(member_names) if member_names else 'none'}

{f"## Project Context\n{project_context}" if project_context else ""}"""


def next_name(used_names: set[str]) -> str:
    """Get the next available name from the pool."""
    for name in NAME_POOL:
        if name not in used_names:
            return name
    # If all names used, append numbers
    for i in range(2, 100):
        for name in NAME_POOL:
            candidate = f"{name}{i}"
            if candidate not in used_names:
                return candidate
    return f"Employee-{len(used_names) + 1}"


@dataclass
class Employee:
    """A persistent AI team member backed by a Claude Agent SDK session.

    Can operate in two modes:
    1. Individual contributor: executes tasks directly via SDK
    2. Team lead: coordinates team members via delegation blocks
    """

    id: str
    name: str
    role: str  # role_id like "architect", "backend-dev"
    role_def: MemberDef
    status: EmployeeStatus = EmployeeStatus.IDLE
    team: str | None = None
    is_lead: bool = False
    task_history: list[Task] = field(default_factory=list)
    current_task: Task | None = None
    _session_id: str | None = field(default=None, repr=False)
    cost_total_usd: float = 0.0
    _conversation: list[dict] = field(default_factory=list, repr=False)
    cwd: str = ""
    model: str = "claude-sonnet-4-6"
    permission_mode: str = "bypassPermissions"
    _cumulative_turns: int = field(default=0, repr=False)
    context_reset_threshold: int = 30

    @property
    def display_role(self) -> str:
        """Display name for the role."""
        if self.is_lead and self.team:
            return f"Team Lead / {self.role_def.role}"
        return self.role_def.role

    @property
    def session_id(self) -> str | None:
        return self._session_id

    @property
    def cumulative_turns(self) -> int:
        return self._cumulative_turns

    def _needs_context_reset(self) -> bool:
        """Check if context has grown past the reset threshold."""
        return (
            self.context_reset_threshold > 0
            and self._cumulative_turns >= int(self.context_reset_threshold * 0.8)
        )

    def _build_handoff_artifact(self, task_description: str = "") -> str:
        """Build a structured handoff artifact summarising session state."""
        recent = self._conversation[-20:]
        conv_summary = []
        for msg in recent:
            role = msg.get("role", "?")
            text = msg.get("text", "")[:300]
            conv_summary.append(f"- [{role}] {text}")

        recent_tasks = []
        for t in self.task_history[-5:]:
            status_icon = "DONE" if t.status == "done" else "FAILED"
            recent_tasks.append(f"- [{status_icon}] {t.description[:80]}")

        return (
            f"# Handoff Artifact — {self.name} ({self.role})\n\n"
            f"## Summary\n"
            f"Employee **{self.name}** ({self.role_def.role}) has been working for "
            f"{self._cumulative_turns} turns. Session is being reset to maintain quality.\n\n"
            f"## State\n"
            f"- Total cost: ${self.cost_total_usd:.4f}\n"
            f"- Tasks completed: {len(self.task_history)}\n"
            f"- Current task: {task_description or 'None'}\n"
            f"- Team: {self.team or 'Unassigned'}\n\n"
            f"## Recent Tasks\n"
            + ("\n".join(recent_tasks) if recent_tasks else "No tasks yet.")
            + "\n\n"
            f"## Recent Conversation\n"
            + ("\n".join(conv_summary) if conv_summary else "No conversation yet.")
            + "\n\n"
            f"## Next Steps\n"
            f"Continue working on the current assignment. "
            f"Refer to the task history and conversation above for context.\n\n"
            f"## Key Decisions\n"
            f"Preserve all prior implementation choices. "
            f"Review the codebase state to pick up where you left off.\n"
        )

    def save_handoff_artifact(
        self, task_description: str = "", data_dir: str | Path | None = None,
    ) -> Path | None:
        """Write a handoff artifact to .shipwright/handoffs/ and return the path."""
        if data_dir is None:
            data_dir = Path(self.cwd) / ".shipwright"
        else:
            data_dir = Path(data_dir)

        handoffs_dir = data_dir / "handoffs"
        handoffs_dir.mkdir(parents=True, exist_ok=True)

        task_id = "general"
        if self.current_task:
            task_id = self.current_task.id
        elif self.task_history:
            task_id = self.task_history[-1].id

        filename = f"{self.name.lower()}_{task_id}.md"
        artifact_path = handoffs_dir / filename
        artifact_content = self._build_handoff_artifact(task_description)
        artifact_path.write_text(artifact_content)

        logger.info(
            "[%s] Handoff artifact saved to %s (%d turns)",
            self.name, artifact_path, self._cumulative_turns,
        )
        return artifact_path

    def context_reset(self, task_description: str = "", data_dir: str | Path | None = None) -> Path | None:
        """Perform a full context reset: save handoff, clear session, return artifact path."""
        artifact_path = self.save_handoff_artifact(task_description, data_dir)
        old_turns = self._cumulative_turns
        self._session_id = None
        self._conversation.clear()
        self._cumulative_turns = 0
        logger.info(
            "[%s] Context reset after %d turns (artifact: %s)",
            self.name, old_turns, artifact_path,
        )
        return artifact_path

    def _load_handoff_context(self, artifact_path: Path) -> str:
        """Read a handoff artifact file and return its content as context."""
        if artifact_path and artifact_path.exists():
            return artifact_path.read_text()
        return ""

    async def run(
        self,
        task: str,
        context: str = "",
        on_text: Callable[[str], None] | None = None,
        system_prompt: str | None = None,
    ) -> MemberResult:
        """Execute a task using the Claude Agent SDK.

        Uses session_id for memory continuity across tasks.
        If system_prompt is provided, it overrides the role_def.prompt.
        Automatically performs a context reset when approaching the threshold.
        """
        # Check for context reset before starting
        if self._needs_context_reset() and self._session_id:
            artifact_path = self.context_reset(task_description=task)
            if artifact_path:
                handoff_context = self._load_handoff_context(artifact_path)
                context = f"{handoff_context}\n\n{context}" if context else handoff_context

        prompt = task
        if context:
            prompt = f"{context}\n\n---\n\nTask:\n{task}"

        effective_model = self.role_def.model or self.model
        effective_prompt = system_prompt or self.role_def.prompt

        options = ClaudeAgentOptions(
            system_prompt=effective_prompt,
            allowed_tools=self.role_def.tools,
            permission_mode=self.permission_mode,
            max_turns=self.role_def.max_turns,
            model=effective_model,
            cwd=self.cwd,
            setting_sources=["project", "user"],
        )

        # Resume previous session for memory continuity
        if self._session_id:
            options.resume = self._session_id

        logger.info(
            "[%s/%s] Starting task (model=%s, cwd=%s)",
            self.name, self.role_def.role, effective_model, self.cwd,
        )

        collected_text: list[str] = []
        result = MemberResult(output="")

        try:
            async for message in query(prompt=prompt, options=options):
                if isinstance(message, RateLimitEvent):
                    logger.debug("[%s] Rate limited, retrying in %ss", self.name, getattr(message, "retry_after", "?"))
                    continue
                if isinstance(message, AssistantMessage):
                    for block in message.content:
                        if isinstance(block, TextBlock):
                            collected_text.append(block.text)
                            if on_text:
                                on_text(block.text)
                        elif isinstance(block, ThinkingBlock):
                            logger.debug("[%s] Thinking: %s", self.name, block.thinking[:100])
                        elif isinstance(block, ToolUseBlock):
                            logger.debug("[%s] Tool use: %s", self.name, block.name)
                elif isinstance(message, ResultMessage):
                    result.session_id = getattr(message, "session_id", "")
                    result.num_turns = getattr(message, "num_turns", 0)
                    result.duration_ms = getattr(message, "duration_ms", 0)
                    result.is_error = getattr(message, "is_error", False)
                    result.total_cost_usd = getattr(message, "total_cost_usd", 0.0) or 0.0
                    self._session_id = result.session_id or self._session_id
                    if not collected_text and getattr(message, "result", None):
                        collected_text.append(message.result)
        except Exception as exc:
            logger.error("[%s] Failed: %s", self.name, exc)
            result.is_error = True
            collected_text.append(f"Error: {exc}")

        result.output = "\n".join(collected_text)
        self.cost_total_usd += result.total_cost_usd
        self._cumulative_turns += result.num_turns

        logger.info(
            "[%s/%s] Done in %d turns (%.1fs, $%.4f, cumulative=%d)",
            self.name, self.role_def.role, result.num_turns,
            result.duration_ms / 1000, result.total_cost_usd,
            self._cumulative_turns,
        )
        return result

    async def respond_as_lead(
        self,
        user_message: str,
        team_name: str,
        team_members: dict[str, "Employee"],
        project_context: str = "",
        status_context: str = "",
        on_text: Callable[[str], None] | None = None,
    ) -> LeadResponse:
        """Respond as a team lead — generates delegation blocks."""
        system_prompt = _build_team_lead_prompt(
            self.name, team_name, team_members, project_context,
        )

        # Build prompt with conversation context
        parts = []
        if status_context:
            parts.append(f"Current status:\n{status_context}")
        recent = self._conversation[-20:]
        if recent:
            conv_lines = []
            for msg in recent:
                role = "User" if msg["role"] == "user" else "You"
                conv_lines.append(f"{role}: {msg['text'][:500]}")
            parts.append("Recent conversation:\n" + "\n".join(conv_lines))
        parts.append(f"User says: {user_message}")
        prompt = "\n\n".join(parts)

        effective_model = self.role_def.model or self.model

        options = ClaudeAgentOptions(
            system_prompt=system_prompt,
            allowed_tools=["Read", "Glob", "Grep"],  # Lead is read-only
            permission_mode=self.permission_mode,
            max_turns=20,
            model=effective_model,
            cwd=self.cwd,
            setting_sources=["project", "user"],
        )

        if self._session_id:
            options.resume = self._session_id

        logger.info("[%s/lead] Processing: %s", self.name, user_message[:80])

        collected_text: list[str] = []
        response = LeadResponse(text="")

        try:
            async for message in query(prompt=prompt, options=options):
                if isinstance(message, RateLimitEvent):
                    logger.debug("[%s/lead] Rate limited, retrying in %ss", self.name, getattr(message, "retry_after", "?"))
                    continue
                if isinstance(message, AssistantMessage):
                    for block in message.content:
                        if isinstance(block, TextBlock):
                            collected_text.append(block.text)
                            if on_text:
                                on_text(block.text)
                elif isinstance(message, ResultMessage):
                    response.session_id = getattr(message, "session_id", "")
                    self._session_id = response.session_id or self._session_id
                    if not collected_text and getattr(message, "result", None):
                        collected_text.append(message.result)
        except Exception as exc:
            logger.error("[%s/lead] Error: %s", self.name, exc)
            collected_text.append(f"I encountered an error: {exc}")

        full_text = "\n".join(collected_text)
        response.text = full_text

        # Track conversation
        self._conversation.append({"role": "user", "text": user_message})
        self._conversation.append({"role": "lead", "text": full_text})

        return response

    def reset_session(self) -> None:
        """Clear the session ID so next run starts fresh."""
        self._session_id = None
        self._conversation.clear()

    @property
    def conversation_history(self) -> list[dict]:
        return list(self._conversation)

    def to_dict(self) -> dict:
        """Serialize employee state for persistence."""
        return {
            "id": self.id,
            "name": self.name,
            "role": self.role,
            "status": self.status.value,
            "team": self.team,
            "is_lead": self.is_lead,
            "session_id": self._session_id,
            "cost_total_usd": self.cost_total_usd,
            "conversation": self._conversation[-50:],
            "task_history": [t.to_dict() for t in self.task_history[-20:]],
            "current_task": self.current_task.to_dict() if self.current_task else None,
            "cumulative_turns": self._cumulative_turns,
            "context_reset_threshold": self.context_reset_threshold,
        }

    @classmethod
    def from_dict(cls, data: dict, role_def: MemberDef, cwd: str, model: str, permission_mode: str) -> "Employee":
        """Restore an employee from persisted data."""
        emp = cls(
            id=data["id"],
            name=data["name"],
            role=data["role"],
            role_def=role_def,
            status=EmployeeStatus(data.get("status", "idle")),
            team=data.get("team"),
            is_lead=data.get("is_lead", False),
            cost_total_usd=data.get("cost_total_usd", 0.0),
            cwd=cwd,
            model=model,
            permission_mode=permission_mode,
        )
        emp._session_id = data.get("session_id")
        emp._conversation = data.get("conversation", [])
        emp._cumulative_turns = data.get("cumulative_turns", 0)
        emp.context_reset_threshold = data.get("context_reset_threshold", 30)
        emp.task_history = [Task.from_dict(t) for t in data.get("task_history", [])]
        ct = data.get("current_task")
        if ct:
            emp.current_task = Task.from_dict(ct)
        return emp
