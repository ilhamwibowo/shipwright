# dev-agent

Multi-agent development pipeline that autonomously plans, implements, tests, self-corrects, reviews, and opens pull requests. Works on **any** codebase — discovers the tech stack by reading the repo.

## How it works

Six specialized AI agents coordinated by a team lead:

1. **Architect** — explores the codebase, discovers the tech stack, writes a detailed spec
2. **Implementer** — writes code from the spec
3. **Test Writer** — writes tests from the requirement (isolated from implementation for unbiased testing)
4. **QA** — runs the test suite + manual exploration
5. **Fixer** — fixes code from QA failures (never touches tests)
6. **Reviewer** — final quality gate before PR

A **Team Lead** orchestrator manages the pipeline, making adaptive decisions (add fix cycles, skip steps, stop early).

```
User: "Add password reset to the auth flow"
  → Architect explores repo, writes spec
  → Implementer + Test Writer run in parallel
  → QA runs tests, finds issues
  → Fixer patches the code (up to 3 attempts)
  → Reviewer approves
  → PR opened automatically
```

## Installation

```bash
pip install .

# With Discord support
pip install ".[discord]"

# With dev dependencies (testing, linting)
pip install ".[dev]"

# Everything
pip install ".[all]"
```

## Configuration

Copy `.env.example` to `.env` and fill in your values:

```bash
cp .env.example .env
```

Required:
- `ANTHROPIC_API_KEY` — your Anthropic API key

Optional:
- `REPO_ROOT` — path to the repo to work on (default: current directory)
- `AGENT_MODEL` — Claude model to use (default: `claude-sonnet-4-6`)
- `MAX_FIX_ATTEMPTS` — max QA-fix cycles (default: 3)
- `MAX_BUDGET_PER_AGENT_USD` — token budget per agent (default: $5.00)
- `AGENT_TIMEOUT_SECONDS` — per-agent timeout in seconds (default: 600, 0 = no timeout)

## Usage

### CLI Mode

```bash
# One-off request
cd /path/to/your/project
dev-agent "Add cancellation reasons to the order flow"

# Quick questions
dev-agent "Explain the authentication architecture"

# Testing
dev-agent "Run the E2E tests and report any failures"
```

### Telegram Bot

```bash
# Set these in .env:
# TELEGRAM_BOT_TOKEN=...
# TELEGRAM_ALLOWED_USERS=your_username

dev-agent --telegram
```

Talk to it like a CTO — multiple tasks run concurrently.

### Discord Bot

```bash
# Set these in .env:
# DISCORD_BOT_TOKEN=...
# DISCORD_CHANNEL_ID=...  (optional, restricts to one channel)

pip install ".[discord]"
dev-agent --discord
```

Commands: `!status`, `!help`

### Docker

```bash
docker build -t dev-agent .
docker run -e ANTHROPIC_API_KEY=sk-... -v /path/to/repo:/repo -e REPO_ROOT=/repo dev-agent "your request"
```

## Architecture

```
dev_agent/
├── main.py              # Entry point (CLI, Telegram, Discord)
├── config.py            # Configuration from .env
├── coordinator.py       # Task orchestration, git helpers, pipeline execution
├── persistence.py       # Save/restore task state to JSON
├── telegram_bot.py      # Telegram long-polling bot
├── discord_bot.py       # Discord bot
├── notifier.py          # Notification helpers
└── agents/
    ├── base.py          # Agentic loop (Anthropic API + local tool execution)
    ├── tools.py         # Tool definitions (Read, Write, Edit, Glob, Grep, Bash)
    ├── architect.py     # Spec writer (READ-ONLY)
    ├── implementer.py   # Code writer
    ├── test_writer.py   # Test writer (isolated from implementation)
    ├── qa.py            # Test runner + manual exploration
    ├── fixer.py         # Bug fixer (never touches tests)
    ├── reviewer.py      # Code reviewer (READ-ONLY)
    └── team_lead.py     # Sub-coordinator for complex tasks
```

### Key design decisions

- **Tech-stack agnostic** — agents discover the project structure and conventions by reading the repo
- **Unbiased testing** — Test Writer never sees implementation code
- **Unidirectional fixes** — Fixer cannot modify tests; if tests fail, the code is wrong
- **Tool restrictions** — each agent has a restricted set of tools enforced at the API level
- **Worktree isolation** — all code changes happen in isolated git worktrees
- **Budget tracking** — per-agent token usage tracking with configurable limits
- **Task persistence** — state saved to JSON, survives restarts
- **Failed pipelines push WIP branches** — humans can pick up where the agent left off

## Testing

```bash
pip install ".[dev]"

# Unit tests
pytest tests/ -v

# Integration tests (requires ANTHROPIC_API_KEY)
ANTHROPIC_API_KEY=sk-... pytest tests/integration/ -v
```

## License

MIT
