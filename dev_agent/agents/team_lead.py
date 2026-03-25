"""Team Lead agent — a hierarchical coordinator that manages sub-agents.

Uses direct Anthropic API calls. Decomposes complex tasks into subtasks
and dispatches them to specialized agents with adaptive decision-making.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field

from dev_agent.agents.base import AgentResult, TokenUsage, query_llm
from dev_agent.config import Config

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """\
You are a technical team lead managing a sub-team of AI agents. You receive a \
high-level objective and must break it into an ordered plan of agent steps, \
then execute them one at a time, reviewing each result before proceeding.

Your available agents:
- architect: reads codebase, discovers tech stack, writes a technical spec (READ-ONLY)
- implementer: writes code from a spec
- test_writer: writes tests from a requirement (isolated from implementation)
- qa: runs tests + manual exploration
- fixer: fixes code based on QA failures (never touches tests)
- reviewer: final quality gate

Respond with JSON. No markdown fences, no extra text.

Phase 1 (planning) -- when you first receive an objective:
{"phase": "plan", "plan": [{"agent": "string", "task": "string"}], \
"summary": "string - brief description of your plan"}

Phase 2 (after each step result) -- decide what to do next:
{"phase": "next", "action": "continue" | "fix" | "skip" | "done", \
"feedback": "string - your assessment of the last result", \
"adjust_next_task": "string | null - override the next step's task if needed"}

If action is "fix", you add an extra fixer step before continuing.
If action is "skip", you skip the next planned step.
If action is "done", you are finished early.

Phase 3 (final summary):
{"phase": "complete", "summary": "string - overall result summary", \
"quality": "high" | "medium" | "low", "issues": ["string"]}
"""


@dataclass
class SubTask:
    agent: str
    task: str
    status: str = "pending"
    result: str = ""


@dataclass
class TeamLeadState:
    objective: str = ""
    plan: list[SubTask] = field(default_factory=list)
    current_step: int = 0
    context: dict = field(default_factory=dict)


def _extract_json(text: str) -> str:
    """Extract JSON from potentially messy LLM output."""
    if "```" in text:
        for part in text.split("```"):
            part = part.strip().removeprefix("json").strip()
            if part.startswith("{"):
                text = part
                break
    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end > start:
        return text[start : end + 1]
    return text.strip()


async def _query_team_lead(prompt: str, config: Config) -> tuple[dict, TokenUsage]:
    """Query the team lead LLM for a decision."""
    text, usage = await query_llm(
        prompt=prompt,
        system_prompt=SYSTEM_PROMPT,
        config=config,
        max_tokens=4096,
    )

    cleaned = _extract_json(text)
    try:
        return json.loads(cleaned), usage
    except json.JSONDecodeError:
        logger.warning("Team lead JSON parse failed: %s", cleaned[:200])
        return {
            "phase": "complete",
            "summary": text[:500],
            "quality": "low",
            "issues": ["JSON parse error"],
        }, usage


async def _run_sub_agent(
    agent: str, task: str, ctx: dict, config: Config
) -> tuple[str, TokenUsage]:
    """Run a sub-agent and return (output, usage)."""
    from dev_agent.agents.architect import run_architect
    from dev_agent.agents.fixer import run_fixer
    from dev_agent.agents.implementer import run_implementer
    from dev_agent.agents.qa import run_qa
    from dev_agent.agents.reviewer import run_reviewer
    from dev_agent.agents.test_writer import run_test_writer

    wd = ctx.get("worktree") or str(config.repo_root)
    ws = str(config.workspace_dir)

    runners = {
        "architect": lambda: run_architect(
            requirement=task, config=config, workspace_dir=ws
        ),
        "implementer": lambda: run_implementer(
            spec=ctx.get("spec", task), config=config, worktree_dir=wd
        ),
        "test_writer": lambda: run_test_writer(
            requirement=task,
            spec=ctx.get("spec", ""),
            config=config,
            worktree_dir=wd,
        ),
        "qa": lambda: run_qa(
            spec=ctx.get("spec", task),
            config=config,
            worktree_dir=wd,
            workspace_dir=ws,
        ),
        "fixer": lambda: run_fixer(
            qa_report=ctx.get("qa_report", task),
            spec=ctx.get("spec", ""),
            config=config,
            worktree_dir=wd,
        ),
        "reviewer": lambda: run_reviewer(
            requirement=ctx.get("requirement", task),
            spec=ctx.get("spec", ""),
            qa_report=ctx.get("qa_report", ""),
            config=config,
            worktree_dir=wd,
        ),
    }

    if agent not in runners:
        raise ValueError(f"Unknown sub-agent: {agent}")

    result = await runners[agent]()
    return result.output, result.usage


async def run_team_lead(
    objective: str,
    config: Config,
    worktree_dir: str | None = None,
    workspace_dir: str | None = None,
    on_progress: callable | None = None,
) -> AgentResult:
    """Run a team lead that adaptively manages a sub-agent pipeline."""
    state = TeamLeadState(objective=objective)
    state.context["requirement"] = objective
    if worktree_dir:
        state.context["worktree"] = worktree_dir
    ws = workspace_dir or str(config.workspace_dir)
    total_usage = TokenUsage()

    def progress(msg: str) -> None:
        if on_progress:
            on_progress(msg)
        logger.info("[team_lead] %s", msg)

    # Phase 1: Plan
    progress(f"Planning: {objective[:80]}")
    plan_response, usage = await _query_team_lead(
        f"Objective: {objective}\n\nCreate a plan.", config
    )
    total_usage.input_tokens += usage.input_tokens
    total_usage.output_tokens += usage.output_tokens
    total_usage.api_calls += usage.api_calls

    if plan_response.get("phase") != "plan" or not plan_response.get("plan"):
        return AgentResult(
            success=False,
            output=plan_response.get("summary", "Team lead failed to create a plan."),
            usage=total_usage,
        )

    for step_def in plan_response["plan"]:
        state.plan.append(SubTask(agent=step_def["agent"], task=step_def["task"]))

    plan_summary = plan_response.get("summary", "")
    progress(f"Plan ({len(state.plan)} steps): {plan_summary}")

    # Phase 2: Execute steps adaptively
    outputs: list[str] = []
    while state.current_step < len(state.plan):
        subtask = state.plan[state.current_step]
        subtask.status = "running"
        step_num = state.current_step + 1
        total = len(state.plan)

        progress(f"Step {step_num}/{total}: {subtask.agent} -- {subtask.task[:60]}")

        try:
            result, sub_usage = await _run_sub_agent(
                subtask.agent, subtask.task, state.context, config
            )
            total_usage.input_tokens += sub_usage.input_tokens
            total_usage.output_tokens += sub_usage.output_tokens
            total_usage.api_calls += sub_usage.api_calls

            subtask.status = "done"
            subtask.result = result
            outputs.append(f"[{subtask.agent}] {result}")

            if subtask.agent == "architect":
                from pathlib import Path
                sp = Path(ws) / "spec.md"
                state.context["spec"] = sp.read_text() if sp.exists() else result
            elif subtask.agent == "qa":
                state.context["qa_report"] = result

        except Exception as exc:
            subtask.status = "failed"
            subtask.result = str(exc)[:300]
            logger.exception("[team_lead] Step %d (%s) failed", step_num, subtask.agent)
            result = f"FAILED: {exc}"
            outputs.append(f"[{subtask.agent}] {result}")

        if state.current_step < len(state.plan) - 1:
            remaining = [
                f"  {i+1}. {s.agent}: {s.task[:50]}"
                for i, s in enumerate(
                    state.plan[state.current_step + 1 :],
                    start=state.current_step + 1,
                )
            ]
            decision_prompt = (
                f"Objective: {objective}\n\n"
                f"Just completed step {step_num}/{total}: {subtask.agent}\n"
                f"Status: {subtask.status}\n"
                f"Result (last 1000 chars): {result[-1000:]}\n\n"
                f"Remaining steps:\n" + "\n".join(remaining) + "\n\n"
                f"What should we do next?"
            )
            decision, d_usage = await _query_team_lead(decision_prompt, config)
            total_usage.input_tokens += d_usage.input_tokens
            total_usage.output_tokens += d_usage.output_tokens
            total_usage.api_calls += d_usage.api_calls

            action = decision.get("action", "continue")
            feedback = decision.get("feedback", "")

            if feedback:
                progress(f"Assessment: {feedback[:100]}")

            if action == "done":
                progress("Team lead decided to stop early.")
                for s in state.plan[state.current_step + 1 :]:
                    s.status = "skipped"
                break
            elif action == "skip":
                progress(f"Skipping step {step_num + 1}")
                state.plan[state.current_step + 1].status = "skipped"
                state.current_step += 2
                continue
            elif action == "fix":
                progress("Inserting fix step before continuing.")
                fix_task = SubTask(
                    agent="fixer",
                    task=decision.get("adjust_next_task", result[-2000:]),
                )
                state.plan.insert(state.current_step + 1, fix_task)

            override = decision.get("adjust_next_task")
            if (
                override
                and action == "continue"
                and state.current_step + 1 < len(state.plan)
            ):
                state.plan[state.current_step + 1].task = override

        state.current_step += 1

    # Phase 3: Final summary
    step_results = "\n".join(
        f"- {s.agent} [{s.status}]: {s.result[:200]}" for s in state.plan
    )
    final_prompt = (
        f"Objective: {objective}\n\n"
        f"All steps completed:\n{step_results}\n\n"
        f"Provide your final summary."
    )
    final, f_usage = await _query_team_lead(final_prompt, config)
    total_usage.input_tokens += f_usage.input_tokens
    total_usage.output_tokens += f_usage.output_tokens
    total_usage.api_calls += f_usage.api_calls

    summary = final.get("summary", "Team lead pipeline complete.")
    quality = final.get("quality", "unknown")
    issues = final.get("issues", [])

    result_text = f"Quality: {quality}\n\n{summary}"
    if issues:
        result_text += "\n\nOpen issues:\n" + "\n".join(f"- {i}" for i in issues)

    progress(f"Complete. Quality: {quality}")

    return AgentResult(
        success=quality in ("high", "medium"),
        output=result_text,
        usage=total_usage,
    )
