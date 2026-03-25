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

# Status
shipwright status
```

## Architecture
Crew-based conversational model:

```
shipwright/
├── main.py                  # CLI entry point
├── config.py                # Config loading (env + shipwright.yaml)
├── crew/
│   ├── registry.py          # Built-in crew definitions
│   ├── crew.py              # Crew class — manages members + conversation
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
│   ├── telegram.py          # Telegram bot
│   └── discord.py           # Discord bot
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
- **Custom crews**: Define your own in `shipwright.yaml`

## Built-in crews
- **fullstack** — Architect, Frontend Dev, Backend Dev, DB Engineer
- **frontend** — UI Designer, React/Vue Dev, CSS Specialist
- **backend** — API Architect, Service Dev, DB Engineer
- **qa** — Test Engineer, Manual Tester, Performance Tester
- **devops** — Infra Engineer, CI/CD Specialist, Monitoring
- **security** — Security Auditor, Pen Tester
- **docs** — Technical Writer, API Docs Specialist

## Dependencies
- `claude-code-sdk` — Claude Code SDK for agent execution
- `httpx` — HTTP client for Telegram bot
- `python-dotenv` — .env file loading
- `discord.py` — Discord bot (optional)
- `pyyaml` — YAML config parsing (optional)
