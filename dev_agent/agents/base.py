"""Shared helpers for running Agent SDK queries.

The Agent SDK (claude_agent_sdk) uses anyio internally. When called from
a thread that already has an asyncio event loop, anyio gets confused.

The solution: agent steps are always called from within a fresh
asyncio.run() inside a thread (orchestrated by the coordinator).
This module just provides the async interface; the coordinator handles
thread isolation.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ResultMessage,
    TextBlock,
    ToolUseBlock,
    query,
)
from dev_agent.config import Config

logger = logging.getLogger(__name__)


@dataclass
class AgentResult:
    success: bool
    output: str


async def run_agent(
    *,
    name: str,
    prompt: str,
    system_prompt: str,
    allowed_tools: list[str],
    config: Config,
    cwd: str | None = None,
    max_turns: int = 50,
    mcp_servers: dict | None = None,
    on_text: callable | None = None,
) -> AgentResult:
    """Run a single agent and collect its output.

    This is an async function that should be called from within
    a fresh asyncio.run() context (i.e., in its own thread).
    """

    logger.info("[%s] Starting", name)

    collected_text: list[str] = []

    options = ClaudeAgentOptions(
        allowed_tools=allowed_tools,
        permission_mode="bypassPermissions",
        max_turns=max_turns,
        model=config.agent_model,
        effort="high",
        system_prompt=system_prompt,
        cwd=cwd or str(config.repo_root),
    )

    if mcp_servers:
        options.mcp_servers = mcp_servers

    try:
        async for message in query(prompt=prompt, options=options):
            if isinstance(message, AssistantMessage):
                for block in message.content:
                    if isinstance(block, TextBlock):
                        collected_text.append(block.text)
                        if on_text:
                            on_text(block.text)
                    elif isinstance(block, ToolUseBlock):
                        logger.debug("[%s] Tool: %s", name, block.name)

            elif isinstance(message, ResultMessage):
                output = "\n".join(collected_text)
                if message.subtype == "success":
                    logger.info("[%s] Done (%d chars output)", name, len(output))
                    return AgentResult(success=True, output=output)
                else:
                    logger.warning("[%s] Stopped: %s", name, message.subtype)
                    return AgentResult(success=False, output=output)

    except Exception:
        logger.exception("[%s] Agent query raised an exception", name)
        output = "\n".join(collected_text)
        return AgentResult(
            success=False,
            output=output or f"Agent {name} failed with an exception",
        )

    output = "\n".join(collected_text)
    logger.warning("[%s] Ended without ResultMessage (%d chars)", name, len(output))
    return AgentResult(success=False, output=output)
