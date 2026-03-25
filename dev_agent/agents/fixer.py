"""Fixer agent — reads QA failures and fixes the implementation.

Critically, this agent does NOT touch test files. If tests fail, the
implementation is wrong, not the tests.
"""

from dev_agent.agents.base import AgentResult, run_agent
from dev_agent.config import Config

SYSTEM_PROMPT = """\
You are a senior debugger. You will receive a QA report describing test \
failures and bugs. Your job is to fix the implementation code so that all \
tests pass and all bugs are resolved.

## Rules
1. Read the QA report carefully — understand every failure.
2. Read the failing test code to understand what's expected.
3. Read the implementation code to understand what's happening.
4. Fix the IMPLEMENTATION. Do NOT modify test files.
5. After fixing, explain what you changed and why.
6. If a failure is caused by an environment issue (app not running, DB not \
   seeded, missing service), note it but don't try to fix infrastructure.
7. Make the minimal change needed to fix each issue. Don't refactor unrelated code.
8. If you need to install dependencies or run migrations, do so.
"""


async def run_fixer(
    qa_report: str,
    spec: str,
    config: Config,
    worktree_dir: str,
) -> AgentResult:
    return await run_agent(
        name="fixer",
        prompt=(
            f"Fix the bugs described in this QA report. Work in {worktree_dir}.\n\n"
            f"## QA Report\n{qa_report}\n\n"
            f"## Original Spec (for context)\n{spec}\n\n"
            "Fix the implementation. Do NOT modify test files. "
            "Explain each fix."
        ),
        system_prompt=SYSTEM_PROMPT,
        allowed_tools=[
            "Read", "Edit", "Glob", "Grep",
            "Bash",
        ],
        config=config,
        cwd=worktree_dir,
        max_turns=60,
    )
