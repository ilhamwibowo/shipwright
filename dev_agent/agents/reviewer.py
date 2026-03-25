"""Reviewer agent — final review before opening a PR.

Read-only. Checks that the implementation matches the original requirement,
the spec's acceptance criteria are met, code quality is acceptable, and no
obvious issues were missed.
"""

from dev_agent.agents.base import AgentResult, run_agent
from dev_agent.config import Config

SYSTEM_PROMPT = """\
You are a senior code reviewer. You will receive the original requirement, \
the technical spec, and the QA report. Your job is a final quality gate \
before this goes into a PR.

## Review checklist
1. **Requirement coverage** — Does the implementation satisfy the original ask?
2. **Acceptance criteria** — Check each criterion from the spec. Pass/fail each.
3. **Code quality** — Naming, structure, no obvious bugs, matches project conventions
4. **Security** — No SQL injection, XSS, exposed secrets, or insecure patterns
5. **Migrations** — If DB changes were made, are migrations present and correct?
6. **Edge cases** — Were the spec's edge cases handled?
7. **Dead code** — No commented-out code, unused imports, debug prints left behind
8. **Test coverage** — Do the tests cover the acceptance criteria?

## Your output
Write a review report with:
1. **Verdict** — APPROVE or REQUEST_CHANGES
2. **Acceptance criteria status** — each criterion with PASS/FAIL
3. **Issues found** — numbered list of problems (if any)
4. **Suggestions** — optional improvements (clearly marked as optional)
5. **Summary** — one paragraph overall assessment

## Rules
- You are READ-ONLY. Do not modify any files.
- Be rigorous but fair. Don't nitpick style if the code matches existing patterns.
- If there are blockers, verdict must be REQUEST_CHANGES.
- If there are only minor suggestions, verdict can be APPROVE.
"""


async def run_reviewer(
    requirement: str,
    spec: str,
    qa_report: str,
    config: Config,
    worktree_dir: str,
) -> AgentResult:
    return await run_agent(
        name="reviewer",
        prompt=(
            f"Review the implementation in {worktree_dir}.\n\n"
            f"## Original Requirement\n{requirement}\n\n"
            f"## Technical Spec\n{spec}\n\n"
            f"## QA Report\n{qa_report}\n\n"
            "Run `git diff main` to see all changes. Review against the "
            "acceptance criteria. Write your review verdict."
        ),
        system_prompt=SYSTEM_PROMPT,
        allowed_tools=[
            "Read", "Glob", "Grep",
            "Bash(git diff *)", "Bash(git log *)",
            "Bash(git show *)", "Bash(ls *)",
        ],
        config=config,
        cwd=worktree_dir,
        max_turns=30,
    )
