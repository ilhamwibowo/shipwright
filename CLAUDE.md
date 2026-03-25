# shipwright

Virtual engineering company powered by Claude. Hire persistent AI employees,
organize them into teams, and assign work conversationally.

## Usage
```bash
cd /path/to/your/project
pip install /path/to/shipwright

# Interactive REPL
shipwright

# Quick hire
shipwright hire backend-dev "Add Stripe payments"
shipwright hire frontend-dev "Redesign the dashboard"

# Status
shipwright status

# Bot modes
shipwright --telegram
shipwright --discord
```

## Architecture
Company model — persistent employees organized into optional teams:

```
shipwright/
├── main.py                  # CLI entry point
├── config.py                # Config loading (env + shipwright.yaml + plugins)
├── sdk_patch.py             # Monkey-patch SDK for unknown message types
├── company/
│   ├── company.py           # Company — manages employees, teams, work assignment
│   ├── employee.py          # Employee — wraps a Claude Code SDK session
│   └── roles.py             # Built-in role definitions + plugin resolution
├── conversation/
│   ├── session.py           # Conversation session — message history, context
│   └── router.py            # Routes user messages to the right employee/team
├── workspace/
│   ├── git.py               # Git worktree management
│   └── project.py           # Project discovery (detect tech stack, structure)
├── interfaces/
│   ├── cli.py               # Interactive CLI (REPL)
│   ├── telegram.py          # Telegram bot (per-chat state)
│   └── discord.py           # Discord bot (per-channel state)
├── persistence/
│   └── store.py             # Save/restore company state, sessions
└── utils/
    └── logging.py           # Structured logging
```

## Key design decisions
- **Company model**: Users hire individual employees with specific roles, optionally organize into teams
- **Persistent employees**: Employees remember context across tasks via SDK session_id resume
- **Team delegation**: Team leads coordinate members via delegation blocks (`[DELEGATE:member]...[/DELEGATE]`)
- **Claude Code SDK**: All agent execution through `claude_code_sdk` — uses local subscription, no API costs
- **Git worktree isolation**: All company work on an isolated branch
- **Project discovery**: Auto-detects tech stack by scanning the repo
- **Plugin system**: Custom roles and specialists via `crew.yaml` packages
- **Cost tracking**: Per-employee cost/time tracking with optional budget limits (`BUDGET_LIMIT_USD`)
- **SDK patch**: Monkey-patches `parse_message` to handle unknown types like `rate_limit_event` gracefully

## Built-in roles
Individual roles that can be hired standalone or organized into teams:
- **architect** — System Architect (read-only, specs)
- **backend-dev** — Backend Developer
- **frontend-dev** — Frontend Developer
- **db-engineer** — Database Engineer
- **designer** — UI/UX Designer
- **team-lead** — Team Lead (coordinates via delegation)
- **test-engineer** — Test Engineer
- **tech-writer** — Technical Writer
- Plus crew-specific roles (fullstack, frontend, backend, qa, devops, security, docs)

## Plugin system
- Custom roles/specialists in `./shipwright/crews/` or `~/.shipwright/crews/`
- Each plugin is a directory with `crew.yaml` (kind: crew or kind: specialist)
- Specialists support `references/` directory for bundled docs
- Resolution order: project-local → user-global → YAML config → built-in

## CLI commands (REPL)
- `hire <role>` / `hire <role> as "Name"` — Hire an employee
- `fire <name>` — Dismiss an employee or team
- `team create <name>` — Create a team
- `promote <name> to lead of <team>` — Make someone team lead
- `assign <name> to <team>` — Add employee to a team
- `assign <name|team> "<task>"` — Give work to an employee or team
- `talk <name>` — Switch active employee
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
- `BUDGET_LIMIT_USD` — Optional spending cap (default: no limit)
- `SHIPWRIGHT_MODEL` — Model to use (default: `claude-sonnet-4-6`)
- `SHIPWRIGHT_PERMISSION_MODE` — SDK permission mode (default: `bypassPermissions`)
- `TELEGRAM_BOT_TOKEN` / `DISCORD_BOT_TOKEN` — Bot tokens for chat interfaces

## Dependencies
- `claude-code-sdk` — Claude Code SDK for agent execution
- `httpx` — HTTP client for Telegram bot
- `python-dotenv` — .env file loading
- `discord.py` — Discord bot (optional)
- `pyyaml` — YAML config parsing (optional)
