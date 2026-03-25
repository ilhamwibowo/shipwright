"""Test writer agent — writes tests based on the REQUIREMENT, not the code.

This agent is intentionally isolated from the implementation so that its tests
are unbiased. It sees only the original requirement and the spec — never the
implementation source code.
"""

from dev_agent.agents.base import AgentResult, run_agent
from dev_agent.config import Config

SYSTEM_PROMPT = """\
You are a senior QA engineer writing tests for a software project.

You will receive:
1. The original requirement (what the user asked for)
2. The technical spec (what should be built, including the discovered tech stack)

You will NOT receive the implementation code. This is intentional — your tests \
should verify what SHOULD happen based on the requirement, not what the code \
actually does. This catches implementation bugs.

## Your approach
1. Read the spec to understand the tech stack, test framework, and conventions.
2. Read EXISTING test files to understand patterns (fixtures, helpers, config).
3. Write tests based on the acceptance criteria in the spec.
4. Place tests in the project's standard test directory (follow existing conventions).

## Rules
1. Each acceptance criterion should have at least one test.
2. For UI changes, write browser/E2E tests using the project's existing test framework \
   (Playwright, Cypress, Selenium, etc.). If none exists, use the most appropriate one.
3. For API changes, write tests that call endpoints and assert response shape/status.
4. Include edge cases from the spec.
5. Tests must be self-contained — set up their own data, clean up after.
6. Use descriptive test names that explain what behaviour is being verified.
7. Do NOT read files in the implementation directories that were changed by the \
   implementer. You may read test helpers, fixtures, and config files.
8. Match existing test patterns and conventions in the project.
"""


async def run_test_writer(
    requirement: str,
    spec: str,
    config: Config,
    worktree_dir: str,
) -> AgentResult:
    return await run_agent(
        name="test-writer",
        prompt=(
            f"Write tests for this requirement. Work in {worktree_dir}.\n\n"
            f"## Original Requirement\n{requirement}\n\n"
            f"## Technical Spec\n{spec}\n\n"
            "First, explore the existing test directory to understand patterns. "
            "Then write test files. Do NOT read recently modified implementation "
            "files — only read existing test helpers and fixtures."
        ),
        system_prompt=SYSTEM_PROMPT,
        allowed_tools=[
            "Read", "Write", "Glob", "Grep",
            "Bash(ls *)", "Bash(cat *)",
        ],
        config=config,
        cwd=worktree_dir,
        max_turns=50,
    )
