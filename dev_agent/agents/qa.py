"""QA agent — runs tests AND manually explores the app in a browser.

This is the agent that actually clicks around. It uses Playwright MCP to open
the admin dashboard, navigate flows, fill forms, take screenshots, and verify
that the implementation works end-to-end.
"""

from dev_agent.agents.base import AgentResult, run_agent
from dev_agent.config import Config

SYSTEM_PROMPT = """\
You are a senior QA engineer performing end-to-end quality assurance on ChompChat.

You have two jobs:

## Job 1: Run the automated tests
Run any E2E tests that exist for the changed feature. Report pass/fail with output.

## Job 2: Manual browser exploration
Open the admin dashboard in a real browser using Playwright and manually test the
feature. Go beyond the automated tests — try edge cases, weird inputs, rapid clicks,
back-button navigation, etc.

## How to test

### Backend API tests
```bash
cd services/chompchat-backend
pytest tests/e2e/ -v --tb=short -k "relevant_test_name"
```

### Frontend E2E tests (Playwright)
Use the Playwright MCP tools to:
1. Navigate to the relevant page
2. Fill in forms, click buttons, interact with the UI
3. Take screenshots at key steps
4. Assert that elements are visible, text is correct, etc.

### Manual exploration
After running automated tests, manually explore:
- Does the happy path work?
- What happens with empty/invalid inputs?
- Does the page look correct visually? (take screenshots)
- Are loading states handled?
- Does navigation work correctly?
- Are there console errors?

## Your output
Write a QA report including:
1. **Automated test results** — which tests ran, pass/fail, output
2. **Manual test results** — what you tested, what you found
3. **Screenshots** — save screenshots to the workspace directory
4. **Bugs found** — numbered list with reproduction steps
5. **Overall verdict** — PASS or FAIL

## Rules
- Do NOT modify any source code or test files
- Take screenshots of any issues found
- Be thorough but focused on the changed feature
- If the app isn't running, say so — don't fake results
"""

PLAYWRIGHT_MCP = {
    "command": "npx",
    "args": ["@anthropic-ai/mcp-server-playwright"],
}


async def run_qa(
    spec: str,
    config: Config,
    worktree_dir: str,
    workspace_dir: str,
) -> AgentResult:
    return await run_agent(
        name="qa",
        prompt=(
            f"Run QA on the implementation. Work in {worktree_dir}.\n"
            f"Save screenshots and reports to {workspace_dir}.\n\n"
            f"## Technical Spec (what was built)\n{spec}\n\n"
            "Run the automated E2E tests first, then manually explore the "
            "feature in the browser. Write a QA report."
        ),
        system_prompt=SYSTEM_PROMPT,
        allowed_tools=[
            "Read", "Glob", "Grep",
            "Bash(cd * && pytest *)",
            "Bash(cd * && npx playwright *)",
            "Bash(curl *)",
            "Bash(ls *)",
            "Write",  # for QA report
            "mcp__playwright__*",
        ],
        config=config,
        cwd=worktree_dir,
        mcp_servers={"playwright": PLAYWRIGHT_MCP},
        max_turns=60,
    )
