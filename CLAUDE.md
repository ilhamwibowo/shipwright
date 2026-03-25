# dev-agent

Multi-agent development pipeline for ChompChat. Takes a requirement, autonomously
plans, implements, tests (including browser E2E via Playwright), self-corrects,
reviews, and opens a PR.

## Usage
```bash
cd /path/to/chompchat
pip install services/dev-agent
dev-agent "Add cancellation reasons to order flow"
```

## Architecture
Six agents coordinated by a Python orchestrator:
1. **Architect** — reads codebase, writes spec (read-only)
2. **Implementer** — writes code from spec
3. **Test Writer** — writes E2E tests from requirement (isolated from implementation)
4. **QA** — runs tests + Playwright browser exploration
5. **Fixer** — fixes code from QA failures (never touches tests)
6. **Reviewer** — final quality gate

## Key design decisions
- Test Writer never sees implementation code — tests are unbiased
- Fixer cannot modify tests — if tests fail, the code is wrong
- Each agent has restricted tool access enforced by the SDK
- Work happens in a git worktree for isolation
- Failed pipelines push WIP branches so humans can pick up
