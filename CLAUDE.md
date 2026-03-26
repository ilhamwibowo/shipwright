# shipwright

Virtual engineering company powered by Claude. You talk to the CTO (Opus) —
it auto-hires, delegates, reviews, and revises until the work is done.

## Usage
```bash
cd /path/to/your/project
pip install /path/to/shipwright

# Interactive REPL — CTO auto-created on first message
shipwright

# Shell commands
shipwright hire backend-dev
shipwright assign Alex "Add Stripe payments"
shipwright status

# Bot modes
shipwright --telegram
shipwright --discord
```

## Architecture

CTO-first model — user talks to CTO, CTO runs the company:

```
User → Interface → Router → Company
                              │
                              ├── CTO auto-pilot (default message path)
                              │    ├── [HIRE:role] → create employees
                              │    ├── [DELEGATE:name] → parallel execution
                              │    ├── Review results
                              │    └── [REVISE:name] → revision loop (≤3 rounds)
                              │
                              └── @name / talk → direct employee access
```

```
shipwright/
├── main.py                  # CLI entry point
├── config.py                # Config loading (env + shipwright.yaml + plugins)
├── company/
│   ├── company.py           # Company — CTO auto-pilot, delegation loop, hierarchy enforcement
│   ├── employee.py          # Employee — SDK session, context reset, handoff artifacts
│   └── roles.py             # 13 built-in roles, team templates, evaluator, rich prompts
├── conversation/
│   ├── session.py           # Conversation session — message history, context
│   └── router.py            # Command parsing, @name dispatch, CTO-first routing
├── workspace/
│   ├── git.py               # Git worktree management
│   └── project.py           # Project discovery (detect tech stack, structure)
├── interfaces/
│   ├── cli.py               # Interactive CLI (REPL) with streaming + delegation UI
│   ├── telegram.py          # Telegram bot (per-chat state)
│   └── discord.py           # Discord bot (per-channel state)
├── persistence/
│   └── store.py             # Save/restore company state, sessions
└── utils/
    └── logging.py           # Structured logging
```

## Key design decisions
- **CTO-first**: All messages route to CTO (Opus) by default; CTO auto-hires and delegates
- **Hierarchy enforcement**: `ROLES_CAN_HIRE = {cto}`, `ROLES_CAN_DELEGATE = {cto, team-lead}`, `ROLES_CAN_REVISE = {cto, team-lead}`
- **Delegation protocol**: `[DELEGATE:name]...[/DELEGATE]`, `[HIRE:role]`, `[REVISE:name]...[/REVISE]` blocks parsed and filtered
- **Delegation loop**: CTO/lead delegates → employees execute → CTO reviews → revise or approve (up to `MAX_REVISION_ROUNDS`, default 3)
- **Persistent employees**: Session resume via SDK `session_id` — memory continuity across tasks
- **Context resets**: At 80% of `CONTEXT_RESET_THRESHOLD` (default 30), generates handoff artifact to `.shipwright/handoffs/`, resets session, loads artifact as context on next run
- **Claude Agent SDK**: All agent execution through `claude_agent_sdk` — uses local subscription, no API costs
- **Git worktree isolation**: All work on `shipwright/company` branch, auto-commit per task
- **Project discovery**: Auto-detects tech stack by scanning the repo
- **Plugin system**: Custom roles and specialists via `crew.yaml` packages
- **Cost tracking**: Per-employee cost/time tracking with optional budget limits (`BUDGET_LIMIT_USD`)
- **SDK types**: Uses `RateLimitEvent` from `claude_agent_sdk` to handle rate limits gracefully
- **Evaluator role**: Read-only code critic that grades on correctness, quality, completeness, integration (1-5 each); issues APPROVE/REVISE/REJECT verdicts

## Built-in roles (13)
- **cto** — Chief Technology Officer (Opus model, read-only, orchestrates everything)
- **architect** — System Architect (read-only + write specs)
- **backend-dev** — Backend Developer
- **frontend-dev** — Frontend Developer
- **fullstack-dev** — Fullstack Developer
- **db-engineer** — Database Engineer
- **qa-engineer** — QA Engineer
- **devops-engineer** — DevOps Engineer
- **security-auditor** — Security Auditor
- **tech-writer** — Technical Writer
- **designer** — UI/UX Designer
- **team-lead** — Team Lead (coordinates via delegation)
- **evaluator** — Code quality critic (read-only, grades + verdicts)

## Team templates
Pre-built team structures: fullstack, frontend, backend, qa, devops, security, docs

## Plugin system
- Custom roles/specialists in `./shipwright/crews/` or `~/.shipwright/crews/`
- Each plugin is a directory with `crew.yaml` (kind: role or kind: crew)
- Specialists support `references/` directory for bundled docs
- Resolution order: project-local → user-global → YAML config → built-in

## CLI commands (REPL)
- `hire <role>` / `hire <role> as "Name"` — Hire an employee
- `fire <name>` — Dismiss an employee or team
- `team create <name>` — Create a team
- `promote <name> to lead of <team>` — Make someone team lead
- `assign <name> to <team>` — Add employee to a team
- `assign <name|team> "<task>"` — Give work to an employee or team
- `@<name> <message>` — Direct message to employee (bypasses CTO)
- `talk <name>` — Switch active employee
- `back` — Return to CTO
- `status` — Company overview with cost data
- `costs` — Detailed cost/budget report per employee
- `history <name>` — Task history for an employee
- `ship` / `ship <team>` — Create a PR
- `save` / `session save <name>` — Save session state
- `session load <name>` — Load a named session
- `sessions` — List saved sessions
- `session clear` — Reset everything
- `roles` — List available roles
- `shop` — Browse all roles & specialists
- `installed` — List custom/installed plugins
- `inspect <name>` — Show role/specialist details
- `help` — Show commands

## Environment variables
- `SHIPWRIGHT_MODEL` — Model for IC employees (default: `claude-sonnet-4-6`; CTO always uses `claude-opus-4-6`)
- `SHIPWRIGHT_PERMISSION_MODE` — SDK permission mode (default: `bypassPermissions`)
- `BUDGET_LIMIT_USD` — Optional spending cap (default: no limit)
- `CONTEXT_RESET_THRESHOLD` — Turns before context reset (default: 30, triggers at 80%)
- `MAX_REVISION_ROUNDS` — Delegation loop revision rounds (default: 3)
- `REPO_ROOT` — Project root override
- `TELEGRAM_BOT_TOKEN` / `TELEGRAM_CHAT_ID` / `TELEGRAM_ALLOWED_USERS` — Telegram config
- `DISCORD_BOT_TOKEN` / `DISCORD_CHANNEL_ID` — Discord config

## Dependencies
- `claude-agent-sdk` — Claude Agent SDK for agent execution
- `httpx` — HTTP client for Telegram bot
- `python-dotenv` — .env file loading
- `discord.py` — Discord bot (optional)
- `pyyaml` — YAML config parsing (optional)
