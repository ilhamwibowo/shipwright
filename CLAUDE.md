# dev-agent

Multi-agent development pipeline. Takes a requirement, autonomously
plans, implements, tests (including browser E2E via Playwright), self-corrects,
reviews, and opens a PR. Works on any codebase.

## Usage
```bash
cd /path/to/your/project
pip install /path/to/dev-agent
dev-agent "Add cancellation reasons to order flow"
```

## Architecture
Six agents coordinated by a Python orchestrator:
1. **Architect** — explores codebase, discovers tech stack, writes spec (read-only)
2. **Implementer** — writes code from spec
3. **Test Writer** — writes tests from requirement (isolated from implementation)
4. **QA** — runs tests + manual exploration
5. **Fixer** — fixes code from QA failures (never touches tests)
6. **Reviewer** — final quality gate

## Key design decisions
- Agents discover the tech stack by reading the repo — no hardcoded assumptions
- Test Writer never sees implementation code — tests are unbiased
- Fixer cannot modify tests — if tests fail, the code is wrong
- Each agent has restricted tool access enforced at the API level
- Work happens in a git worktree for isolation
- Failed pipelines push WIP branches so humans can pick up
- Uses direct Anthropic API calls with tool_use (no external agent SDK)

## Dependencies
- `anthropic` — Claude API client
- `httpx` — HTTP client for Telegram bot
- `discord.py` — Discord bot (optional)
- `python-dotenv` — .env file loading
