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

import re
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
import shipwright.sdk_patch  # noqa: ensure patch is applied

from shipwright.config import Config, CrewDef
from shipwright.utils.logging import get_logger

logger = get_logger("crew.lead")


def _build_lead_system_prompt(crew_def: CrewDef, project_context: str = "") -> str:
    """Build the system prompt for the crew lead."""
    member_descriptions = []
    for mname, mdef in crew_def.members.items():
        tools = ", ".join(mdef.tools)
        # Use only first line of prompt for lead context — full prompt goes to member
        summary = mdef.prompt.strip().split('\n')[0]
        member_descriptions.append(
            f"- **{mname}** ({mdef.role}): {summary} [Tools: {tools}]"
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
3. Tell the user what you're about to do (e.g. "I'm having the architect look at the codebase...")
4. Use the delegation format below to assign work to members
5. Review member results and either delegate more work or respond to the user

## Delegation Format
When you need a team member to do work, include one or more delegation blocks in your response:

[DELEGATE:member_name]
Detailed task description for the member.
Include all context they need to do the work.
[/DELEGATE]

For example:
[DELEGATE:architect]
Explore the codebase and identify all payment-related code.
Look for existing Stripe integrations or payment models.
Write a brief spec of what exists and what needs to change.
[/DELEGATE]

You can delegate to multiple members at once — they will work in parallel:
[DELEGATE:frontend]
Build the checkout form component.
[/DELEGATE]
[DELEGATE:backend]
Implement the /api/checkout endpoint.
[/DELEGATE]

You can also delegate in stages: first delegate to the architect for analysis, review the results, \
then delegate to implementers with the spec. The system will feed member results back to you \
so you can plan the next step.

## Communication Style
- Be conversational and collaborative
- ALWAYS tell the user what you're delegating and why before the [DELEGATE] block
- Report progress at milestones
- After receiving member results, summarize what was done
- Ask for feedback before moving to the next phase

## Important Rules
- You MUST use [DELEGATE:member_name] blocks to assign work. You cannot do work yourself — you have no write tools.
- NEVER just talk about delegating. Actually include the [DELEGATE:member_name]...[/DELEGATE] block in your response.
- If the user asks for code changes, you MUST delegate. Responding without a [DELEGATE] block when work is needed is a failure.
- Each member has specific tools; respect their capabilities
- Work happens in isolated git worktrees for safety
- If something fails, diagnose and retry or escalate to the user
- Member names must exactly match: {', '.join(crew_def.members.keys()) if crew_def.members else 'none'}

{f"## Project Context{chr(10)}{project_context}" if project_context else ""}"""


_DELEGATE_PATTERN = re.compile(
    r"\[DELEGATE:(\w+)\]\s*\n(.*?)\[/DELEGATE\]",
    re.DOTALL,
)


@dataclass
class DelegationRequest:
    """A parsed delegation request from the lead's response."""

    member_name: str
    task: str


def parse_delegations(text: str) -> tuple[str, list[DelegationRequest]]:
    """Parse [DELEGATE:member] blocks from the lead's response.

    Returns:
        Tuple of (clean_text with blocks removed, list of DelegationRequests).
    """
    delegations: list[DelegationRequest] = []
    for match in _DELEGATE_PATTERN.finditer(text):
        member_name = match.group(1).strip()
        task = match.group(2).strip()
        if member_name and task:
            delegations.append(DelegationRequest(member_name=member_name, task=task))

    clean_text = _DELEGATE_PATTERN.sub("", text).strip()
    return clean_text, delegations


@dataclass
class LeadResponse:
    """Parsed response from the crew lead."""

    text: str
    delegations: list[DelegationRequest] = field(default_factory=list)
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
            async for message in query(prompt=prompt, options=options):
                if message is None:
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
                    # Only use ResultMessage.result as fallback — TextBlocks
                    # already captured the same text during streaming.
                    if not collected_text and getattr(message, "result", None):
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
