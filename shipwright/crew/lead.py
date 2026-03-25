"""CrewLead — the conversational coordinator for a crew.

The lead is the user-facing agent. It:
- Receives user messages and decides what to do
- Delegates work to crew members
- Reports progress and results
- Maintains conversation context

The lead itself is a Claude Code SDK session with a system prompt that
describes its crew members and how to coordinate them.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Callable

from claude_code_sdk import (
    AssistantMessage,
    ClaudeCodeOptions,
    ResultMessage,
    TextBlock,
    ToolUseBlock,
    query,
)
from claude_code_sdk._errors import MessageParseError

from shipwright.config import Config, CrewDef
from shipwright.utils.logging import get_logger

logger = get_logger("crew.lead")


def _build_lead_system_prompt(crew_def: CrewDef, project_context: str = "") -> str:
    """Build the system prompt for the crew lead."""
    member_descriptions = []
    for mname, mdef in crew_def.members.items():
        tools = ", ".join(mdef.tools)
        member_descriptions.append(
            f"- **{mname}** ({mdef.role}): {mdef.prompt} [Tools: {tools}]"
        )

    members_section = "\n".join(member_descriptions) if member_descriptions else "No members defined."

    return f"""{crew_def.lead_prompt}

You are the crew lead for the **{crew_def.name}** crew. You coordinate a team of specialized \
AI developers to accomplish tasks.

## Your Team
{members_section}

## How You Work
1. When you receive a task, analyze what needs to be done
2. Break it into steps and delegate to the right team members
3. When you need a member to work, describe exactly what they should do
4. Report progress and results to the user
5. Ask clarifying questions when the task is ambiguous

## Communication Style
- Be conversational and collaborative
- Explain your plan before executing
- Report progress at milestones
- Present results clearly with summaries
- Ask for feedback before moving to the next phase

## Important Rules
- You coordinate — you don't write code yourself
- Each member has specific tools; respect their capabilities
- Work happens in isolated git worktrees for safety
- If something fails, diagnose and retry or escalate to the user

{f"## Project Context{chr(10)}{project_context}" if project_context else ""}"""


@dataclass
class LeadResponse:
    """Parsed response from the crew lead."""

    text: str
    member_tasks: list[dict] = field(default_factory=list)
    session_id: str = ""


@dataclass
class CrewLead:
    """The conversational coordinator for a crew.

    The lead uses Claude Code SDK to have conversations with the user
    and delegate work to crew members.
    """

    crew_def: CrewDef
    config: Config
    project_context: str = ""
    _session_id: str | None = field(default=None, repr=False)
    _conversation: list[dict] = field(default_factory=list, repr=False)

    @property
    def system_prompt(self) -> str:
        return _build_lead_system_prompt(self.crew_def, self.project_context)

    async def respond(
        self,
        user_message: str,
        status_context: str = "",
        on_text: Callable[[str], None] | None = None,
    ) -> LeadResponse:
        """Process a user message and return the lead's response.

        The lead analyzes the message, decides whether to delegate work
        to members, and formulates a response.

        Args:
            user_message: The user's message.
            status_context: Current status info (active tasks, etc).
            on_text: Callback for streaming text chunks.

        Returns:
            LeadResponse with the reply text and any member task delegations.
        """
        # Build the prompt with conversation context
        prompt = self._build_prompt(user_message, status_context)

        effective_model = self.crew_def.model or self.config.model

        options = ClaudeCodeOptions(
            system_prompt=self.system_prompt,
            allowed_tools=["Read", "Glob", "Grep"],  # Lead is read-only
            permission_mode=self.config.permission_mode,
            max_turns=20,
            model=effective_model,
            cwd=str(self.config.repo_root),
        )

        if self._session_id:
            options.resume = self._session_id

        logger.info("[lead/%s] Processing: %s", self.crew_def.name, user_message[:80])

        collected_text: list[str] = []
        response = LeadResponse(text="")

        try:
            stream = query(prompt=prompt, options=options).__aiter__()
            while True:
                try:
                    message = await stream.__anext__()
                except StopAsyncIteration:
                    break
                except MessageParseError as exc:
                    logger.debug("[lead/%s] Skipping parse error: %s", self.crew_def.name, exc)
                    continue
                except Exception as iter_exc:
                    # Some SDK errors are non-fatal, log and continue
                    logger.debug("[lead/%s] Skipping iteration error: %s", self.crew_def.name, iter_exc)
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
                    if getattr(message, "result", None):
                        collected_text.append(message.result)

        except Exception as exc:
            logger.error("[lead/%s] Error: %s", self.crew_def.name, exc)
            collected_text.append(f"I encountered an error: {exc}")

        full_text = "\n".join(collected_text)
        response.text = full_text

        # Track conversation
        self._conversation.append({"role": "user", "text": user_message})
        self._conversation.append({"role": "lead", "text": full_text})

        return response

    def _build_prompt(self, user_message: str, status_context: str = "") -> str:
        """Build prompt including conversation history."""
        parts = []

        if status_context:
            parts.append(f"Current status:\n{status_context}")

        # Include recent conversation for context
        recent = self._conversation[-20:]
        if recent:
            conv_lines = []
            for msg in recent:
                role = "User" if msg["role"] == "user" else "You"
                conv_lines.append(f"{role}: {msg['text'][:500]}")
            parts.append(f"Recent conversation:\n" + "\n".join(conv_lines))

        parts.append(f"User says: {user_message}")

        return "\n\n".join(parts)

    @property
    def conversation_history(self) -> list[dict]:
        return list(self._conversation)

    def reset(self) -> None:
        """Reset conversation and session state."""
        self._session_id = None
        self._conversation.clear()

    def to_dict(self) -> dict:
        """Serialize lead state for persistence."""
        return {
            "session_id": self._session_id,
            "conversation": self._conversation[-50:],
        }

    def restore_from_dict(self, data: dict) -> None:
        """Restore lead state from persisted data."""
        self._session_id = data.get("session_id")
        self._conversation = data.get("conversation", [])
