"""Team Lead coordinator -- a conversational AI team lead.

Manages multiple concurrent tasks. Maintains conversation context.
Delegates to specialized agents. Reports progress.
Uses direct Anthropic API calls.
"""

from __future__ import annotations

import asyncio
import json
import logging
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from dev_agent.agents.architect import run_architect
from dev_agent.agents.base import TokenUsage, query_llm
from dev_agent.agents.fixer import run_fixer
from dev_agent.agents.implementer import run_implementer
from dev_agent.agents.qa import run_qa
from dev_agent.agents.reviewer import run_reviewer
from dev_agent.agents.team_lead import run_team_lead
from dev_agent.agents.test_writer import run_test_writer
from dev_agent.config import Config

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Task tracking
# ---------------------------------------------------------------------------

@dataclass
class Task:
    id: int
    description: str
    status: str = "queued"  # queued, running, done, failed
    plan: dict = field(default_factory=dict)
    result: str = ""
    pr_url: str | None = None
    error: str | None = None
    usage: TokenUsage = field(default_factory=TokenUsage)
    started_at: float | None = None
    finished_at: float | None = None

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "description": self.description,
            "status": self.status,
            "plan": self.plan,
            "result": self.result[:2000] if self.result else "",
            "pr_url": self.pr_url,
            "error": self.error,
            "usage": {
                "input_tokens": self.usage.input_tokens,
                "output_tokens": self.usage.output_tokens,
                "api_calls": self.usage.api_calls,
            },
            "started_at": self.started_at,
            "finished_at": self.finished_at,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Task":
        usage_data = data.get("usage", {})
        usage = TokenUsage(
            input_tokens=usage_data.get("input_tokens", 0),
            output_tokens=usage_data.get("output_tokens", 0),
            api_calls=usage_data.get("api_calls", 0),
        )
        return cls(
            id=data["id"],
            description=data["description"],
            status=data.get("status", "queued"),
            plan=data.get("plan", {}),
            result=data.get("result", ""),
            pr_url=data.get("pr_url"),
            error=data.get("error"),
            usage=usage,
            started_at=data.get("started_at"),
            finished_at=data.get("finished_at"),
        )


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
            icon = {"queued": "[ ]", "running": "[~]", "done": "[x]", "failed": "[!]"}.get(t.status, "[?]")
            line = f"{icon} #{t.id} [{t.status}] {t.description[:60]}"
            if t.pr_url:
                line += f"\n    PR: {t.pr_url}"
            if t.error:
                line += f"\n    Error: {t.error[:80]}"
            if t.usage.api_calls > 0:
                cost = t.usage.estimated_cost("claude-sonnet-4-6")
                line += f"\n    Tokens: {t.usage.input_tokens + t.usage.output_tokens:,} (~${cost:.2f})"
            lines.append(line)
        return "\n".join(lines)

    def to_dict(self) -> dict:
        return {
            "tasks": {str(k): v.to_dict() for k, v in self.tasks.items()},
            "conversation": self.conversation[-50:],
            "next_id": self.next_id,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "TeamState":
        state = cls()
        state.next_id = data.get("next_id", 1)
        state.conversation = data.get("conversation", [])
        for k, v in data.get("tasks", {}).items():
            task = Task.from_dict(v)
            state.tasks[task.id] = task
        return state


# ---------------------------------------------------------------------------
# Coordinator LLM call
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = """\
You are a senior engineering team lead. The user gives you directions and you \
manage a team of AI agents.

Your team: architect (explores codebase + writes spec), implementer (writes code), \
test_writer (writes tests, isolated from implementation), qa (runs tests + \
manual exploration), fixer (fixes bugs, never touches tests), reviewer (final review), \
team_lead (a sub-team manager for complex multi-step pipelines).

Always respond with a single valid JSON object. No markdown fences, no extra text.

Schema:
{"reply": "string - your conversational reply", "new_tasks": \
[{"description": "string", "steps": [{"agent": "string", "task": "string", \
"parallel_with": null|int}], "needs_worktree": bool, "needs_pr": bool}]}

Rules:
- "reply" is always required. Be conversational, helpful, and informative.
- For greetings, status checks, questions, or casual chat: set new_tasks to \
an empty list [] and put your full conversational response in "reply".
- One message can produce multiple tasks.
- For large/complex features: delegate to team_lead with a single step.
- For "implement X" (clear scope): architect -> implementer + test_writer (parallel) \
-> qa -> reviewer -> PR
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
- "thanks" -> {"reply": "Happy to help!", "new_tasks": []}"""

MAX_RETRIES = 2


async def _call_coordinator(message: str, state: TeamState, config: Config) -> dict:
    """Call the coordinator LLM and get structured JSON response."""
    recent = state.conversation[-20:]
    conv = "\n".join(
        f"{'User' if m['role'] == 'user' else 'Lead'}: {m['text']}"
        for m in recent
    )

    prompt = (
        f"Task board:\n{state.summary}\n\n"
        f"Conversation:\n{conv or '(first message)'}\n\n"
        f"User says: {message}"
    )

    for attempt in range(MAX_RETRIES + 1):
        current_prompt = prompt
        if attempt > 1:
            current_prompt = (
                f"User says: {message}\n\n"
                "Respond with a JSON object: "
                '{"reply": "your answer", "new_tasks": []}'
            )
            logger.info("Using shortened prompt for retry %d", attempt + 1)

        try:
            text, _usage = await asyncio.to_thread(
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
                logger.warning("Empty response, retrying (%d/%d)", attempt + 1, MAX_RETRIES)
                continue
            return {"reply": "I didn't get a response. Could you rephrase that?", "new_tasks": []}

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
                logger.warning("JSON parse failed (attempt %d): %s", attempt + 1, cleaned[:200])
                continue
            logger.error("Failed to parse after retries: %s", text[:300])
            return {"reply": text[:800], "new_tasks": []}

    return {"reply": "Something went wrong. Please try again.", "new_tasks": []}


def _sync_query_coordinator(prompt: str, config: Config) -> tuple[str, TokenUsage]:
    """Run the coordinator LLM query synchronously in a thread."""
    import asyncio as _asyncio

    async def _do_query() -> tuple[str, TokenUsage]:
        return await query_llm(
            prompt=prompt, system_prompt=SYSTEM_PROMPT,
            config=config, max_tokens=4096,
        )

    return _asyncio.run(_do_query())


def _extract_json(text: str) -> str:
    """Try to extract a JSON object from potentially messy LLM output."""
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


# ---------------------------------------------------------------------------
# Git helpers
# ---------------------------------------------------------------------------

def _git(args: list[str], cwd: str) -> str:
    try:
        r = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, timeout=60)
    except subprocess.TimeoutExpired:
        raise RuntimeError(f"git {' '.join(args)}: timed out after 60s")
    except FileNotFoundError:
        raise RuntimeError("git is not installed or not in PATH")
    if r.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)}: {r.stderr.strip()}")
    return r.stdout.strip()


def _get_default_branch(repo: str) -> str:
    """Detect the default branch (main or master)."""
    try:
        return _git(["symbolic-ref", "refs/remotes/origin/HEAD", "--short"], repo).split("/")[-1]
    except RuntimeError:
        for branch in ("main", "master"):
            try:
                _git(["rev-parse", "--verify", branch], repo)
                return branch
            except RuntimeError:
                continue
        return "main"


def _create_worktree(repo: str, branch: str) -> str:
    path = str(Path(repo).parent / f".dev-agent-wt-{branch.replace('/', '-')}")
    default_branch = _get_default_branch(repo)
    for cmd in [["worktree", "remove", "--force", path], ["branch", "-D", branch]]:
        try:
            _git(cmd, repo)
        except RuntimeError:
            pass
    _git(["worktree", "add", "-b", branch, path, default_branch], repo)
    return path


def _cleanup_worktree(repo: str, path: str, branch: str) -> None:
    for cmd in [["worktree", "remove", "--force", path], ["branch", "-D", branch]]:
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
    try:
        r = subprocess.run(
            ["gh", "pr", "create", "--title", title, "--body", body, "--head", branch],
            cwd=path, capture_output=True, text=True, timeout=30,
        )
    except FileNotFoundError:
        raise RuntimeError("GitHub CLI (gh) is not installed or not in PATH")
    except subprocess.TimeoutExpired:
        raise RuntimeError("gh pr create timed out after 30s")
    if r.returncode != 0:
        raise RuntimeError(f"gh pr create: {r.stderr.strip()}")
    return r.stdout.strip()


def _slug(text: str) -> str:
    words = text.lower().split()[:6]
    slug = "-".join("".join(c for c in w if c.isalnum()) for w in words)[:40]
    return slug or "task"


# ---------------------------------------------------------------------------
# Task execution
# ---------------------------------------------------------------------------

async def _run_step_in_thread(step: dict, ctx: dict, config: Config) -> str:
    return await asyncio.to_thread(_sync_run_step, step, ctx, config)


def _sync_run_step(step: dict, ctx: dict, config: Config) -> str:
    import asyncio as _asyncio
    return _asyncio.run(_async_run_step(step, ctx, config))


async def _async_run_step(step: dict, ctx: dict, config: Config) -> str:
    agent = step["agent"]
    task = step["task"]
    wd = ctx.get("worktree") or str(config.repo_root)
    ws = str(config.workspace_dir)

    runners = {
        "architect": lambda: run_architect(requirement=task, config=config, workspace_dir=ws),
        "implementer": lambda: run_implementer(spec=ctx.get("spec", task), config=config, worktree_dir=wd),
        "test_writer": lambda: run_test_writer(requirement=task, spec=ctx.get("spec", ""), config=config, worktree_dir=wd),
        "qa": lambda: run_qa(spec=ctx.get("spec", task), config=config, worktree_dir=wd, workspace_dir=ws),
        "fixer": lambda: run_fixer(qa_report=ctx.get("qa_report", task), spec=ctx.get("spec", ""), config=config, worktree_dir=wd),
        "reviewer": lambda: run_reviewer(requirement=ctx.get("requirement", task), spec=ctx.get("spec", ""), qa_report=ctx.get("qa_report", ""), config=config, worktree_dir=wd),
        "team_lead": lambda: run_team_lead(objective=task, config=config, worktree_dir=wd, workspace_dir=ws),
    }

    if agent not in runners:
        raise ValueError(f"Unknown agent: {agent}")

    result = await runners[agent]()

    prev_usage = ctx.get("_total_usage")
    if prev_usage and hasattr(result, "usage"):
        prev_usage.input_tokens += result.usage.input_tokens
        prev_usage.output_tokens += result.usage.output_tokens
        prev_usage.api_calls += result.usage.api_calls

    return result.output


async def _execute_task(
    task: Task, config: Config, notify: Callable[[str], None],
    state: "TeamState | None" = None,
) -> None:
    task.status = "running"
    task.started_at = time.time()
    repo = str(config.repo_root)
    Path(config.workspace_dir).mkdir(parents=True, exist_ok=True)

    steps = task.plan.get("steps", [])
    needs_wt = task.plan.get("needs_worktree", False)
    needs_pr = task.plan.get("needs_pr", False)

    wt_path = None
    branch = None
    ctx: dict = {"requirement": task.description, "_total_usage": task.usage}

    try:
        if needs_wt:
            branch = f"dev-agent/{_slug(task.description)}"
            wt_path = _create_worktree(repo, branch)
            ctx["worktree"] = wt_path
            notify(f"**#{task.id}** Worktree ready on branch `{branch}`")

        i = 0
        while i < len(steps):
            step = steps[i]

            if task.started_at and config.agent_timeout_seconds > 0:
                elapsed = time.time() - task.started_at
                if elapsed > config.agent_timeout_seconds * len(steps):
                    raise TimeoutError(f"Task exceeded total timeout ({config.agent_timeout_seconds * len(steps)}s)")

            group = [step]
            group_idx = {i}
            for j in range(i + 1, len(steps)):
                if steps[j].get("parallel_with") in group_idx:
                    group.append(steps[j])
                    group_idx.add(j)

            agents = ", ".join(s["agent"] for s in group)
            notify(f"**#{task.id}** Running: *{agents}*")

            if len(group) > 1:
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
                try:
                    if config.agent_timeout_seconds > 0:
                        out = await asyncio.wait_for(
                            _run_step_in_thread(step, ctx, config),
                            timeout=config.agent_timeout_seconds,
                        )
                    else:
                        out = await _run_step_in_thread(step, ctx, config)
                except asyncio.TimeoutError:
                    out = f"Agent {step['agent']} timed out after {config.agent_timeout_seconds}s"
                    logger.error(out)

                ctx[f"{step['agent']}_output"] = out

                if step["agent"] == "architect":
                    sp = config.workspace_dir / "spec.md"
                    ctx["spec"] = sp.read_text() if sp.exists() else out
                elif step["agent"] == "qa":
                    ctx["qa_report"] = out
                    if "fail" in out.lower()[-300:]:
                        for att in range(1, config.max_fix_attempts + 1):
                            notify(f"**#{task.id}** QA found issues, fixing (attempt {att}/{config.max_fix_attempts})...")
                            await _run_step_in_thread({"agent": "fixer", "task": out[-2000:]}, ctx, config)
                            if wt_path:
                                _commit(wt_path, f"fix: attempt {att}")
                            notify(f"**#{task.id}** Re-running QA...")
                            out = await _run_step_in_thread(step, ctx, config)
                            ctx["qa_report"] = out
                            if "fail" not in out.lower()[-300:]:
                                break
                elif step["agent"] in ("implementer", "test_writer") and wt_path:
                    _commit(wt_path, f"{step['agent']}: {task.description[:50]}")

            i += len(group)

        if needs_pr and wt_path and branch:
            _commit(wt_path, f"dev-agent: {task.description[:60]}")
            title = task.description[:70] if len(task.description) <= 70 else task.description[:67] + "..."
            body = f"## Request\n{task.description}\n\n---\n*Generated by dev-agent*"
            task.pr_url = _push_pr(wt_path, branch, title, body)
            notify(f"**#{task.id}** PR opened: {task.pr_url}")

        task.status = "done"
        task.finished_at = time.time()
        task.result = ctx.get("architect_output", ctx.get("qa_report", "Done"))
        _send_result_to_user(task, notify)

        if state:
            _persist_state_safe(state, config)

    except Exception as exc:
        task.status = "failed"
        task.finished_at = time.time()
        task.error = str(exc)[:200]
        notify(f"**#{task.id} FAILED**\n\n**Task:** {task.description}\n**Error:** `{task.error}`")
        logger.exception("Task #%d failed", task.id)

        if wt_path and branch:
            try:
                _commit(wt_path, f"wip: {task.description[:50]}")
                _git(["push", "-u", "origin", branch], wt_path)
                notify(f"**#{task.id}** WIP pushed to `{branch}` so you can pick it up.")
            except RuntimeError:
                pass

        if state:
            _persist_state_safe(state, config)
    finally:
        if wt_path and branch:
            _cleanup_worktree(repo, wt_path, branch)


def _send_result_to_user(task: Task, notify: Callable[[str], None]) -> None:
    result = task.result
    header = f"**#{task.id} Complete**: {task.description}\n"
    if task.pr_url:
        header += f"\nPR: {task.pr_url}\n"
    cost = task.usage.estimated_cost("claude-sonnet-4-6")
    if task.usage.api_calls > 0:
        header += f"\nTokens: {task.usage.input_tokens + task.usage.output_tokens:,} (~${cost:.2f})\n"
    if len(result) > 3500:
        result = result[:3500] + "\n\n... (output truncated)"
    notify(f"{header}\n{result}")


def _persist_state_safe(state: TeamState, config: Config) -> None:
    try:
        from dev_agent.persistence import save_state
        save_state(state, config)
    except Exception:
        logger.warning("Failed to persist state", exc_info=True)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

_states: dict[int | str, TeamState] = {}


def get_state(chat_id: int | str) -> TeamState:
    if chat_id not in _states:
        _states[chat_id] = TeamState()
    return _states[chat_id]


def set_state(chat_id: int | str, state: TeamState) -> None:
    _states[chat_id] = state


async def handle_message(
    chat_id: int | str, message: str, config: Config,
    on_reply: Callable[[str], None] | None = None,
    on_update: Callable[[str], None] | None = None,
) -> None:
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
            on_reply(f"**Task #{task.id} queued:** {task.description}")
        asyncio.ensure_future(_execute_task(task, config, notify, state))

    _persist_state_safe(state, config)
