# shipwright

Virtual engineering crews powered by Claude. Hire AI dev teams
that collaborate conversationally — not fire-and-forget.

## Usage
```bash
cd /path/to/your/project
pip install /path/to/shipwright

# Interactive REPL
shipwright

# Quick hire
shipwright hire backend "Add Stripe payments"
shipwright hire frontend "Redesign the dashboard"
shipwright hire enterprise "Build complete SaaS billing"

# Status
shipwright status

# Bot modes
shipwright --telegram
shipwright --discord
```

## Architecture
Crew-based conversational model:

```
shipwright/
├── main.py                  # CLI entry point
├── config.py                # Config loading (env + shipwright.yaml + plugins)
├── sdk_patch.py             # Monkey-patch SDK for unknown message types
├── crew/
│   ├── registry.py          # Built-in crew definitions + custom crew resolution
│   ├── crew.py              # Crew + EnterpriseCrew orchestration
│   ├── member.py            # CrewMember — wraps a Claude Code SDK session
│   └── lead.py              # CrewLead — the conversational coordinator
├── conversation/
│   ├── session.py           # Conversation session — message history, context
│   └── router.py            # Routes user messages to the right crew
├── workspace/
│   ├── git.py               # Git worktree management
│   └── project.py           # Project discovery (detect tech stack, structure)
├── interfaces/
│   ├── cli.py               # Interactive CLI (REPL)
│   ├── telegram.py          # Telegram bot (per-chat state)
│   └── discord.py           # Discord bot (per-channel state)
├── persistence/
│   └── store.py             # Save/restore crews, conversations, state
└── utils/
    └── logging.py           # Structured logging
```

## Key design decisions
- **Crew model**: Users hire domain crews (backend, frontend, qa, etc.) and talk to a crew lead
- **Conversational**: Crew leads ask clarifying questions, propose approaches, report progress
- **Claude Code SDK**: All agent execution through `claude_code_sdk` — uses local subscription, no API costs
- **Persistent conversations**: Crews remember context across restarts
- **Git worktree isolation**: Each crew works on its own branch
- **Project discovery**: Auto-detects tech stack by scanning the repo
- **Plugin system**: Custom crews and specialists via `crew.yaml` packages
- **Enterprise mode**: 3-level hierarchy (Project Lead → Crew Leads → Members) for large cross-domain projects
- **SDK patch**: Monkey-patches `parse_message` to handle unknown types like `rate_limit_event` gracefully

## Built-in crews
- **fullstack** — Architect, Frontend Dev, Backend Dev, DB Engineer
- **frontend** — UI Designer, Frontend Dev, CSS Specialist
- **backend** — API Architect, Backend Dev, DB Engineer
- **qa** — Test Engineer, Manual Tester, Performance Tester
- **devops** — Infra Engineer, CI/CD Specialist, Monitoring
- **security** — Security Auditor, Pen Tester
- **docs** — Technical Writer, API Docs Specialist
- **enterprise** — Project Lead → auto-spawned sub-crews (3-level hierarchy)

## Plugin system
- Custom crews/specialists in `./shipwright/crews/` or `~/.shipwright/crews/`
- Each plugin is a directory with `crew.yaml` (kind: crew or kind: specialist)
- Specialists support `references/` directory for bundled docs
- Resolution order: project-local → user-global → YAML config → built-in
- Recruit specialists into active crews at runtime

## CLI commands (REPL)
- `hire <type> <objective>` — Hire a new crew
- `fire <crew-id>` — Dismiss a crew
- `status` / `crews` — Show active crews
- `talk to <crew-id>` — Switch active crew
- `ship` / `pr` — Create a PR from active crew's work
- `shop` — List all available crew types
- `installed` — List custom crews and specialists
- `inspect <name>` — Show crew/specialist details
- `recruit <specialist> into <crew-id>` — Add specialist to crew
- `log <crew-id>` — View conversation history
- `help` — Show commands

## Dependencies
- `claude-code-sdk` — Claude Code SDK for agent execution
- `httpx` — HTTP client for Telegram bot
- `python-dotenv` — .env file loading
- `discord.py` — Discord bot (optional)
- `pyyaml` — YAML config parsing (optional)
