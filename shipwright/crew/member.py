"""CrewMember — a specialized agent wrapping a Claude Code SDK session.

Each member has a role-specific system prompt and restricted tool access.
All execution goes through claude_code_sdk.query().
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import AsyncIterator, Callable

from claude_code_sdk import (
    AssistantMessage,
    ClaudeCodeOptions,
    ResultMessage,
    TextBlock,
    ToolUseBlock,
    query,
)
from claude_code_sdk._errors import MessageParseError

from shipwright.config import MemberDef
from shipwright.utils.logging import get_logger

logger = get_logger("crew.member")


@dataclass
class MemberResult:
    """Result from a crew member's work."""

    output: str
    session_id: str = ""
    num_turns: int = 0
    duration_ms: int = 0
    total_cost_usd: float = 0.0
    is_error: bool = False


@dataclass
class CrewMember:
    """A single crew member backed by a Claude Code SDK session.

    Each member has:
    - A specialized role and system prompt
    - Restricted tool access
    - A working directory (usually a git worktree)
    """

    name: str
    definition: MemberDef
    cwd: str
    model: str = "claude-sonnet-4-6"
    permission_mode: str = "bypassPermissions"
    _session_id: str | None = field(default=None, repr=False)

    @property
    def role(self) -> str:
        return self.definition.role

    @property
    def system_prompt(self) -> str:
        return self.definition.prompt

    @property
    def allowed_tools(self) -> list[str]:
        return self.definition.tools

    @property
    def max_turns(self) -> int:
        return self.definition.max_turns

    async def run(
        self,
        task: str,
        context: str = "",
        on_text: Callable[[str], None] | None = None,
        on_tool_use: Callable[[str, dict], None] | None = None,
    ) -> MemberResult:
        """Execute a task using the Claude Code SDK.

        Args:
            task: The task description / prompt.
            context: Additional context (spec, project info, etc).
            on_text: Callback for streaming text output.
            on_tool_use: Callback for tool use events.

        Returns:
            MemberResult with the output and metadata.
        """
        prompt = task
        if context:
            prompt = f"{context}\n\n---\n\nTask:\n{task}"

        effective_model = self.definition.model or self.model

        options = ClaudeCodeOptions(
            system_prompt=self.system_prompt,
            allowed_tools=self.allowed_tools,
            permission_mode=self.permission_mode,
            max_turns=self.max_turns,
            model=effective_model,
            cwd=self.cwd,
        )

        # Resume previous session if we have one
        if self._session_id:
            options.resume = self._session_id

        logger.info(
            "[%s/%s] Starting task (model=%s, max_turns=%d, cwd=%s)",
            self.name, self.role, effective_model, self.max_turns, self.cwd,
        )

        collected_text: list[str] = []
        result = MemberResult(output="")

        try:
            stream = query(prompt=prompt, options=options).__aiter__()
            while True:
                try:
                    message = await stream.__anext__()
                except StopAsyncIteration:
                    break
                except MessageParseError as exc:
                    logger.debug("[%s] Skipping parse error: %s", self.name, exc)
                    continue
                except Exception as iter_exc:
                    logger.debug("[%s] Skipping iteration error: %s", self.name, iter_exc)
                    continue

                if isinstance(message, AssistantMessage):
                    for block in message.content:
                        if isinstance(block, TextBlock):
                            collected_text.append(block.text)
                            if on_text:
                                on_text(block.text)
                        elif isinstance(block, ToolUseBlock):
                            logger.debug(
                                "[%s] Tool use: %s", self.name, block.name
                            )
                            if on_tool_use:
                                on_tool_use(block.name, getattr(block, "input", {}))

                elif isinstance(message, ResultMessage):
                    result.session_id = getattr(message, "session_id", "")
                    result.num_turns = getattr(message, "num_turns", 0)
                    result.duration_ms = getattr(message, "duration_ms", 0)
                    result.is_error = getattr(message, "is_error", False)
                    result.total_cost_usd = getattr(message, "total_cost_usd", 0.0) or 0.0
                    self._session_id = result.session_id or self._session_id
                    if getattr(message, "result", None):
                        collected_text.append(message.result)

        except Exception as exc:
            logger.error("[%s] Failed: %s", self.name, exc)
            result.is_error = True
            collected_text.append(f"Error: {exc}")

        result.output = "\n".join(collected_text)
        logger.info(
            "[%s/%s] Done in %d turns (%.1fs, $%.4f)",
            self.name, self.role, result.num_turns,
            result.duration_ms / 1000, result.total_cost_usd,
        )
        return result

    def reset_session(self) -> None:
        """Clear the session ID so next run starts fresh."""
        self._session_id = None
