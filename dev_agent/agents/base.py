"""Core agentic loop using the Anthropic messages API with tool_use.

Replaces the claude-agent-sdk dependency with direct API calls. Each agent
runs as a loop: send prompt -> get response -> if tool_use, execute tool
and send result -> repeat until done or max_turns.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Callable

import anthropic

from dev_agent.agents.tools import (
    execute_tool,
    get_tool_schemas,
    parse_allowed_tools,
)
from dev_agent.config import Config

logger = logging.getLogger(__name__)

# Pricing per 1M tokens (USD)
MODEL_PRICING: dict[str, tuple[float, float]] = {
    "claude-opus-4-6": (15.0, 75.0),
    "claude-sonnet-4-6": (3.0, 15.0),
    "claude-haiku-4-5-20251001": (0.80, 4.0),
}


@dataclass
class TokenUsage:
    """Tracks token usage and estimated cost for an agent run."""

    input_tokens: int = 0
    output_tokens: int = 0
    api_calls: int = 0

    def add(self, usage: object) -> None:
        self.input_tokens += getattr(usage, "input_tokens", 0)
        self.output_tokens += getattr(usage, "output_tokens", 0)
        self.api_calls += 1

    def estimated_cost(self, model: str = "claude-sonnet-4-6") -> float:
        base_model = model.split(":")[0] if ":" in model else model
        pricing = MODEL_PRICING.get(base_model)
        if not pricing:
            for key, val in MODEL_PRICING.items():
                if key.startswith(base_model.split("-")[0]):
                    pricing = val
                    break
        if not pricing:
            pricing = (3.0, 15.0)
        return (
            self.input_tokens * pricing[0] / 1_000_000
            + self.output_tokens * pricing[1] / 1_000_000
        )


@dataclass
class AgentResult:
    """Result from running an agent."""

    success: bool
    output: str
    usage: TokenUsage = field(default_factory=TokenUsage)
    duration_seconds: float = 0.0


async def query_llm(
    *,
    prompt: str,
    system_prompt: str,
    config: Config,
    max_tokens: int = 4096,
) -> tuple[str, TokenUsage]:
    """Simple one-shot LLM query (no tools). Used by coordinator and team_lead."""
    client = anthropic.Anthropic(api_key=config.anthropic_api_key)
    usage = TokenUsage()

    try:
        response = client.messages.create(
            model=config.agent_model,
            max_tokens=max_tokens,
            system=system_prompt,
            messages=[{"role": "user", "content": prompt}],
        )
    except anthropic.APIError as exc:
        logger.error("LLM query failed: %s", exc)
        return "", usage

    if hasattr(response, "usage"):
        usage.add(response.usage)

    text_parts = []
    for block in response.content:
        if block.type == "text":
            text_parts.append(block.text)

    return "".join(text_parts).strip(), usage


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
    on_text: Callable[[str], None] | None = None,
) -> AgentResult:
    """Run a single agent with an agentic tool-use loop.

    Calls the Anthropic messages API in a loop, executing tools locally
    when the model requests them, until the model produces a final response
    without tool calls or max_turns is reached.
    """
    start_time = time.monotonic()
    working_dir = cwd or str(config.repo_root)
    usage = TokenUsage()

    logger.info("[%s] Starting (max_turns=%d, cwd=%s)", name, max_turns, working_dir)

    tool_names, bash_patterns = parse_allowed_tools(allowed_tools)
    tool_schemas = get_tool_schemas(allowed_tools)

    client = anthropic.Anthropic(api_key=config.anthropic_api_key)
    messages: list[dict] = [{"role": "user", "content": prompt}]
    collected_text: list[str] = []

    for turn in range(max_turns):
        try:
            response = client.messages.create(
                model=config.agent_model,
                max_tokens=16384,
                system=system_prompt,
                tools=tool_schemas if tool_schemas else anthropic.NOT_GIVEN,
                messages=messages,
            )
        except anthropic.APIError as exc:
            logger.error("[%s] API error on turn %d: %s", name, turn + 1, exc)
            return AgentResult(
                success=False,
                output="\n".join(collected_text) or f"Agent {name} API error: {exc}",
                usage=usage,
                duration_seconds=time.monotonic() - start_time,
            )

        if hasattr(response, "usage"):
            usage.add(response.usage)

        tool_calls = []
        for block in response.content:
            if block.type == "text":
                collected_text.append(block.text)
                if on_text:
                    on_text(block.text)
                logger.debug("[%s] Turn %d text: %s", name, turn + 1, block.text[:200])
            elif block.type == "tool_use":
                tool_calls.append(block)
                logger.debug("[%s] Turn %d tool: %s", name, turn + 1, block.name)

        if not tool_calls:
            output = "\n".join(collected_text)
            logger.info(
                "[%s] Done in %d turns (%d chars, $%.4f)",
                name, turn + 1, len(output), usage.estimated_cost(config.agent_model),
            )
            return AgentResult(
                success=True,
                output=output,
                usage=usage,
                duration_seconds=time.monotonic() - start_time,
            )

        messages.append({"role": "assistant", "content": response.content})

        tool_results = []
        for tc in tool_calls:
            tool_name = tc.name
            tool_input = tc.input

            if tool_name not in tool_names:
                result_text = f"Error: Tool '{tool_name}' is not available."
            else:
                result_text = execute_tool(
                    name=tool_name,
                    params=tool_input,
                    cwd=working_dir,
                    bash_patterns=bash_patterns if tool_name == "Bash" else None,
                )

            tool_results.append({
                "type": "tool_result",
                "tool_use_id": tc.id,
                "content": result_text,
            })

        messages.append({"role": "user", "content": tool_results})

        cost = usage.estimated_cost(config.agent_model)
        if cost > config.max_budget_per_agent_usd:
            logger.warning(
                "[%s] Budget exceeded ($%.2f > $%.2f), stopping",
                name, cost, config.max_budget_per_agent_usd,
            )
            collected_text.append(
                f"\n\n[Agent stopped: budget limit ${config.max_budget_per_agent_usd:.2f} exceeded]"
            )
            break

    output = "\n".join(collected_text)
    logger.warning(
        "[%s] Hit max turns (%d). Output: %d chars, $%.4f",
        name, max_turns, len(output), usage.estimated_cost(config.agent_model),
    )
    return AgentResult(
        success=len(output) > 0,
        output=output or f"Agent {name} reached max turns without output",
        usage=usage,
        duration_seconds=time.monotonic() - start_time,
    )
