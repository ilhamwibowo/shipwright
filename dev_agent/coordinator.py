"""Team Lead coordinator -- a conversational AI team lead you talk to like a CTO.

Manages multiple concurrent tasks. Maintains conversation context.
Delegates to specialized agents. Reports progress.
"""

from __future__ import annotations

import asyncio
import json
import logging
import subprocess
import textwrap
from dataclasses import dataclass, field
from pathlib import Path

from dev_agent.agents.architect import run_architect
from dev_agent.agents.fixer import run_fixer
from dev_agent.agents.implementer import run_implementer
from dev_agent.agents.qa import run_qa
from dev_agent.agents.reviewer import run_reviewer
from dev_agent.agents.team_lead import run_team_lead
from dev_agent.agents.test_writer import run_test_writer
from dev_agent.config import Config

logger = logging.getLogger(__name__)


# -- Task tracking -----------------------------------------------------------

@dataclass
class Task:
    id: int
    description: str
    status: str = "queued"  # queued, running, done, failed
    plan: dict = field(default_factory=dict)
    result: str = ""
    pr_url: str | None = None
    error: str | None = None


@dataclass
class TeamState:
    tasks: dict[int, Task] = field(default_factory=dict)
    conversation: list[dict] = field(default_factory=list)
    next_id: int = 1

    def add_task(self, description: str, plan: dict) -> Task:
        task = Task(id=self.next_id, description=description, plan=plan)
        self.tasks[self.next_id] = task
        self.next_id += 1
        return task

    @property
    def summary(self) -> str:
        if not self.tasks:
            return "No tasks yet."
        lines = []
        for t in self.tasks.values():
            icon = {
                "queued": "[ ]",
                "running": "[~]",
                "done": "[x]",
                "failed": "[!]",
            }.get(t.status, "[?]")
            lines.append(f"{icon} #{t.id} [{t.status}] {t.description[:60]}")
            if t.pr_url:
                lines.append(f"    PR: {t.pr_url}")
            if t.error:
                lines.append(f"    Error: {t.error[:80]}")
        return "\n".join(lines)


# -- Coordinator LLM call ---------------------------------------------------

SYSTEM_PROMPT = """\
You are a senior engineering team lead. The CTO gives you directions and you \
manage a team of AI agents.

Your team: architect (explores/plans), implementer (writes code), test_writer \
(writes E2E tests, isolated from implementation), qa (runs tests + Playwright \
browser testing), fixer (fixes bugs, never touches tests), reviewer (final review), \
team_lead (a sub-team manager that can autonomously run multi-step pipelines -- \
delegate complex features to a team_lead instead of planning every step yourself).

Always respond with a single valid JSON object. No markdown fences, no extra text.

Schema:
{"reply": "string - your conversational reply to the CTO", "new_tasks": \
[{"description": "string", "steps": [{"agent": "string", "task": "string", \
"parallel_with": null|int}], "needs_worktree": bool, "needs_pr": bool}]}

Rules:
- "reply" is always required. Be conversational, helpful, and informative.
- For greetings, status checks, questions, or casual chat: set new_tasks to \
an empty list [] and put your full conversational response in "reply".
- One message can produce multiple tasks.
- For large/complex features: delegate to team_lead with a single step. The team_lead \
will autonomously plan and execute its own pipeline of sub-agents. Prefer this for \
tasks that span multiple services or have unclear scope.
- For "implement X" (clear scope): architect -> implementer + test_writer (parallel) -> qa -> reviewer -> PR
- For "test X" or "check X": just qa
- For "explain X" or "how does X work": just architect
- For "fix X": fixer -> qa
- For "write tests for X": just test_writer
- implementer always needs architect first (spec).
- test_writer parallel_with = implementer's step index.
- If qa might fail, add fixer + second qa after it.
- needs_worktree and needs_pr only if code changes are expected.

Examples of reply-only (no tasks):
- "hello" -> {"reply": "Hey! I'm your engineering team lead. What are we building today?", "new_tasks": []}
- "what's the status?" -> {"reply": "Here's what's going on: ...", "new_tasks": []}
- "thanks" -> {"reply": "Happy to help! Let me know if you need anything else.", "new_tasks": []}"""

MAX_RETRIES = 2


async def _call_coordinator(message: str, state: TeamState, config: Config) -> dict:
    """Call the team lead LLM and get structured JSON response.

    Runs the Agent SDK query in a thread to isolate its internal anyio
    event loop from our main asyncio loop.
    """
    recent = state.conversation[-20:]
    conv = "\n".join(
        f"{'CTO' if m['role'] == 'user' else 'Lead'}: {m['text']}"
        for m in recent
    )

    prompt = (
        f"Task board:\n{state.summary}\n\n"
        f"Conversation:\n{conv or '(first message)'}\n\n"
        f"CTO says: {message}"
    )

    used_short_prompt = False

    for attempt in range(MAX_RETRIES + 1):
        current_prompt = prompt

        # On retry after an empty response, use a shorter prompt so the
        # model has more token budget for the actual JSON answer.
        if attempt > 0 and used_short_prompt is False:
            pass  # first retry uses original prompt
        elif attempt > 1:
            current_prompt = (
                f"CTO says: {message}\n\n"
                "Respond with a JSON object: "
                '{"reply": "your answer", "new_tasks": []}'
            )
            used_short_prompt = True
            logger.info("Using shortened prompt for retry attempt %d", attempt + 1)

        try:
            text = await asyncio.to_thread(
                _sync_query_coordinator, current_prompt, config
            )
        except Exception:
            logger.exception("Coordinator query failed (attempt %d)", attempt + 1)
            if attempt < MAX_RETRIES:
                continue
            return {
                "reply": "Sorry, I had trouble processing that. Could you try again?",
                "new_tasks": [],
            }

        if not text:
            if attempt < MAX_RETRIES:
                logger.warning(
                    "Empty response, retrying (%d/%d)", attempt + 1, MAX_RETRIES
                )
                continue
            return {
                "reply": "I didn't get a response. Could you rephrase that?",
                "new_tasks": [],
            }

        # Extract JSON from response
        cleaned = _extract_json(text)

        try:
            result = json.loads(cleaned)
            if "reply" not in result:
                result["reply"] = ""
            if "new_tasks" not in result:
                result["new_tasks"] = []
            return result
        except json.JSONDecodeError:
            if attempt < MAX_RETRIES:
                logger.warning(
                    "JSON parse failed (attempt %d), retrying: %s",
                    attempt + 1,
                    cleaned[:200],
                )
                continue
            logger.error("Failed to parse after retries: %s", text[:300])
            # Return the raw text as the reply -- it's better than nothing
            return {"reply": text[:800], "new_tasks": []}

    return {"reply": "Something went wrong. Please try again.", "new_tasks": []}


def _sync_query_coordinator(prompt: str, config: Config) -> str:
    """Run the Agent SDK query synchronously in the calling thread.

    The Agent SDK uses anyio internally, so we run it in its own sync
    context via asyncio.run() in a fresh event loop (handled by
    asyncio.to_thread which runs in a ThreadPoolExecutor).

    Returns text content if available, otherwise falls back to thinking
    content (the model sometimes spends its entire budget on thinking
    and produces no TextBlock).
    """
    import asyncio as _asyncio

    from claude_agent_sdk import (
        AssistantMessage,
        ClaudeAgentOptions,
        TextBlock,
        ThinkingBlock,
        ToolUseBlock,
        query,
    )

    async def _do_query() -> str:
        collected_text: list[str] = []
        collected_thinking: list[str] = []
        block_types_seen: list[str] = []

        async for msg in query(
            prompt=prompt,
            options=ClaudeAgentOptions(
                allowed_tools=[],
                permission_mode="bypassPermissions",
                max_turns=3,
                model=config.agent_model,
                system_prompt=SYSTEM_PROMPT,
            ),
        ):
            if isinstance(msg, AssistantMessage):
                for block in msg.content:
                    block_type = type(block).__name__
                    block_types_seen.append(block_type)
                    if isinstance(block, TextBlock):
                        collected_text.append(block.text)
                    elif isinstance(block, ThinkingBlock):
                        collected_thinking.append(block.thinking)
                    elif isinstance(block, ToolUseBlock):
                        logger.debug(
                            "Coordinator produced ToolUseBlock (tool=%s), ignoring",
                            getattr(block, "name", "?"),
                        )

        logger.info(
            "Coordinator blocks: %s | text_parts=%d thinking_parts=%d",
            block_types_seen,
            len(collected_text),
            len(collected_thinking),
        )

        text = "".join(collected_text).strip()
        if text:
            return text

        # Fallback: use thinking content if no text was produced
        thinking = "".join(collected_thinking).strip()
        if thinking:
            logger.warning(
                "No TextBlock produced; falling back to ThinkingBlock content (%d chars)",
                len(thinking),
            )
            return thinking

        logger.warning("Coordinator returned no text and no thinking content")
        return ""

    return _asyncio.run(_do_query())


def _extract_json(text: str) -> str:
    """Try to extract a JSON object from potentially messy LLM output."""
    # Strip markdown code fences
    if "```" in text:
        for part in text.split("```"):
            part = part.strip().removeprefix("json").strip()
            if part.startswith("{"):
                text = part
                break

    # Find the first { and match to the last }
    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end > start:
        return text[start : end + 1]

    return text.strip()


# -- Git helpers -------------------------------------------------------------

def _git(args: list[str], cwd: str) -> str:
    r = subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True, timeout=60
    )
    if r.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)}: {r.stderr}")
    return r.stdout.strip()


def _create_worktree(repo: str, branch: str) -> str:
    path = str(Path(repo).parent / f".dev-agent-wt-{branch.replace('/', '-')}")
    for cmd in [
        ["worktree", "remove", "--force", path],
        ["branch", "-D", branch],
    ]:
        try:
            _git(cmd, repo)
        except RuntimeError:
            pass
    _git(["worktree", "add", "-b", branch, path, "main"], repo)
    return path


def _cleanup_worktree(repo: str, path: str, branch: str) -> None:
    for cmd in [
        ["worktree", "remove", "--force", path],
        ["branch", "-D", branch],
    ]:
        try:
            _git(cmd, repo)
        except RuntimeError:
            pass


def _commit(path: str, msg: str) -> None:
    _git(["add", "-A"], path)
    if _git(["status", "--porcelain"], path):
        _git(["commit", "-m", msg], path)


def _push_pr(path: str, branch: str, title: str, body: str) -> str:
    _git(["push", "-u", "origin", branch], path)
    r = subprocess.run(
        [
            "gh", "pr", "create",
            "--title", title,
            "--body", body,
            "--base", "main",
            "--head", branch,
        ],
        cwd=path,
        capture_output=True,
        text=True,
        timeout=30,
    )
    if r.returncode != 0:
        raise RuntimeError(f"gh pr create: {r.stderr}")
    return r.stdout.strip()


def _slug(text: str) -> str:
    slug = "-".join(
        c for c in "".join(text.lower().split()[:6]) if c.isalnum() or c == "-"
    )[:40]
    return slug or "task"


# -- Task execution (runs agent SDK calls in threads) -----------------------

async def _run_step_in_thread(step: dict, ctx: dict, config: Config) -> str:
    """Run a single agent step in a thread so its internal anyio loop
    doesn't conflict with the main asyncio loop.
    """
    return await asyncio.to_thread(_sync_run_step, step, ctx, config)


def _sync_run_step(step: dict, ctx: dict, config: Config) -> str:
    """Synchronously run an agent step using asyncio.run() to give it
    a fresh, isolated event loop."""
    import asyncio as _asyncio
    return _asyncio.run(_async_run_step(step, ctx, config))


async def _async_run_step(step: dict, ctx: dict, config: Config) -> str:
    """The actual async agent dispatch."""
    agent = step["agent"]
    task = step["task"]
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
        "team_lead": lambda: run_team_lead(
            objective=task,
            config=config,
            worktree_dir=wd,
            workspace_dir=ws,
        ),
    }

    if agent not in runners:
        raise ValueError(f"Unknown agent: {agent}")

    result = await runners[agent]()
    return result.output


async def _execute_task(
    task: Task, config: Config, notify: callable
) -> None:
    """Execute a full task pipeline. Runs on the main event loop but
    delegates each agent step to a thread."""

    task.status = "running"
    repo = str(config.repo_root)
    Path(config.workspace_dir).mkdir(parents=True, exist_ok=True)

    steps = task.plan.get("steps", [])
    needs_wt = task.plan.get("needs_worktree", False)
    needs_pr = task.plan.get("needs_pr", False)

    wt_path = None
    branch = None
    ctx: dict = {"requirement": task.description}

    try:
        if needs_wt:
            branch = f"dev-agent/{_slug(task.description)}"
            wt_path = _create_worktree(repo, branch)
            ctx["worktree"] = wt_path
            notify(f"<b>#{task.id}</b> Worktree ready on branch <code>{branch}</code>")

        i = 0
        while i < len(steps):
            step = steps[i]

            # Collect parallel group
            group = [step]
            group_idx = {i}
            for j in range(i + 1, len(steps)):
                if steps[j].get("parallel_with") in group_idx:
                    group.append(steps[j])
                    group_idx.add(j)

            agents = ", ".join(s["agent"] for s in group)
            notify(f"<b>#{task.id}</b> Running: <i>{agents}</i>")

            if len(group) > 1:
                # Run parallel steps concurrently, each in its own thread
                coros = [_run_step_in_thread(s, ctx, config) for s in group]
                results = await asyncio.gather(*coros, return_exceptions=True)

                for s, out in zip(group, results):
                    if isinstance(out, Exception):
                        logger.error("Step %s failed: %s", s["agent"], out)
                        out = f"Agent {s['agent']} failed: {out}"

                    ctx[f"{s['agent']}_output"] = out
                    if s["agent"] == "architect":
                        sp = config.workspace_dir / "spec.md"
                        ctx["spec"] = sp.read_text() if sp.exists() else out
                    elif s["agent"] == "qa":
                        ctx["qa_report"] = out
            else:
                out = await _run_step_in_thread(step, ctx, config)
                ctx[f"{step['agent']}_output"] = out

                if step["agent"] == "architect":
                    sp = config.workspace_dir / "spec.md"
                    ctx["spec"] = sp.read_text() if sp.exists() else out
                elif step["agent"] == "qa":
                    ctx["qa_report"] = out
                    if "fail" in out.lower()[-300:]:
                        for att in range(1, config.max_fix_attempts + 1):
                            notify(
                                f"<b>#{task.id}</b> QA found issues, fixing "
                                f"(attempt {att}/{config.max_fix_attempts})..."
                            )
                            await _run_step_in_thread(
                                {"agent": "fixer", "task": out[-2000:]}, ctx, config
                            )
                            if wt_path:
                                _commit(wt_path, f"fix: attempt {att}")
                            notify(f"<b>#{task.id}</b> Re-running QA...")
                            out = await _run_step_in_thread(step, ctx, config)
                            ctx["qa_report"] = out
                            if "fail" not in out.lower()[-300:]:
                                break
                elif step["agent"] in ("implementer", "test_writer") and wt_path:
                    _commit(wt_path, f"{step['agent']}: {task.description[:50]}")

            i += len(group)

        # PR creation
        if needs_pr and wt_path and branch:
            _commit(wt_path, f"dev-agent: {task.description[:60]}")
            title = (
                task.description[:70]
                if len(task.description) <= 70
                else task.description[:67] + "..."
            )
            body = (
                f"## Request\n{task.description}\n\n---\n*Generated by dev-agent*"
            )
            task.pr_url = _push_pr(wt_path, branch, title, body)
            notify(f"<b>#{task.id}</b> PR opened: {task.pr_url}")

        task.status = "done"
        task.result = ctx.get("architect_output", ctx.get("qa_report", "Done"))

        # Send the actual result back to the user
        _send_result_to_user(task, notify)

    except Exception as exc:
        task.status = "failed"
        task.error = str(exc)[:200]
        notify(
            f"<b>#{task.id} FAILED</b>\n\n"
            f"<b>Task:</b> {task.description}\n"
            f"<b>Error:</b> <code>{task.error}</code>"
        )
        logger.exception("Task #%d failed", task.id)

        if wt_path and branch:
            try:
                _commit(wt_path, f"wip: {task.description[:50]}")
                _git(["push", "-u", "origin", branch], wt_path)
                notify(
                    f"<b>#{task.id}</b> WIP pushed to <code>{branch}</code> "
                    "so you can pick it up."
                )
            except RuntimeError:
                pass
    finally:
        if wt_path and branch:
            _cleanup_worktree(repo, wt_path, branch)


def _send_result_to_user(task: Task, notify: callable) -> None:
    """Send a well-formatted completion message with the actual content."""
    result = task.result
    header = f"<b>#{task.id} Complete</b>: {task.description}\n"

    if task.pr_url:
        header += f"\nPR: {task.pr_url}\n"

    # Trim very long results but keep the useful content
    if len(result) > 3500:
        result = result[:3500] + "\n\n<i>... (output truncated)</i>"

    notify(f"{header}\n{result}")


# -- Public API --------------------------------------------------------------

_states: dict[int | str, TeamState] = {}


def get_state(chat_id: int | str) -> TeamState:
    if chat_id not in _states:
        _states[chat_id] = TeamState()
    return _states[chat_id]


async def handle_message(
    chat_id: int | str,
    message: str,
    config: Config,
    on_reply: callable | None = None,
    on_update: callable | None = None,
) -> None:
    """Handle a CTO message. May reply, spawn tasks, or both."""

    state = get_state(chat_id)
    state.conversation.append({"role": "user", "text": message})

    try:
        response = await _call_coordinator(message, state, config)
    except Exception as exc:
        logger.exception("Coordinator call failed")
        reply = f"Planning error: {exc}"
        state.conversation.append({"role": "assistant", "text": reply})
        if on_reply:
            on_reply(reply)
        return

    reply = response.get("reply", "")
    new_tasks = response.get("new_tasks", [])

    state.conversation.append({"role": "assistant", "text": reply})

    # Always send the coordinator's reply
    if on_reply and reply:
        on_reply(reply)

    def notify(msg: str) -> None:
        if on_update:
            on_update(msg)

    for task_plan in new_tasks:
        task = state.add_task(
            description=task_plan.get("description", "unnamed"),
            plan=task_plan,
        )
        if on_reply:
            on_reply(
                f"<b>Task #{task.id} queued:</b> {task.description}"
            )

        # Schedule on the current event loop -- _execute_task delegates
        # heavy work to threads internally
        asyncio.ensure_future(_execute_task(task, config, notify))
