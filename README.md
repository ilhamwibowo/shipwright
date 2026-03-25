# shipwright

Virtual engineering crews powered by Claude. Hire AI dev teams that collaborate conversationally — not fire-and-forget.

## How it works

You hire crews of specialized AI developers, talk to them conversationally, and they build your software. It's collaborative, not autonomous.

```
You: hire a backend crew to add Stripe payments
Lead: What payment provider? Any existing payment code I should know about?
You: Stripe. No existing code, greenfield.
Lead: Got it. I'm having the Architect explore the codebase to understand the patterns.
      Meanwhile, should I design for subscriptions too or just one-time?
You: Design for subs but implement one-time first
Lead: Smart. Architect is done — here's the proposed approach:
      [shows spec summary]
      Want me to proceed or adjust anything?
You: Looks good, ship it
Lead: Kicking off implementation...
```

### Built-in Crews

| Crew | Members |
|------|---------|
| **fullstack** | Architect, Frontend Dev, Backend Dev, DB Engineer |
| **frontend** | UI Designer, React/Vue Dev, CSS Specialist |
| **backend** | API Architect, Service Dev, DB Engineer |
| **qa** | Test Engineer, Manual Tester, Performance Tester |
| **devops** | Infra Engineer, CI/CD Specialist, Monitoring |
| **security** | Security Auditor, Pen Tester |
| **docs** | Technical Writer, API Docs Specialist |

## Installation

```bash
pip install .

# With all optional deps
pip install ".[all]"
```

Requires [Claude Code](https://docs.anthropic.com/en/docs/claude-code) installed locally.

## Usage

### Interactive REPL (main mode)

```bash
shipwright
```

### Quick commands

```bash
shipwright hire backend "Add Stripe payments"
shipwright hire frontend "Redesign the dashboard"
shipwright status
shipwright fire backend-add-stripe-payments
```

### Telegram Bot

```bash
# Set in .env:
# TELEGRAM_BOT_TOKEN=...
# TELEGRAM_ALLOWED_USERS=your_username

shipwright --telegram
```

### Discord Bot

```bash
# Set in .env:
# DISCORD_BOT_TOKEN=...

pip install ".[discord]"
shipwright --discord
```

## Custom Crews

Define custom crews in `shipwright.yaml`:

```yaml
crews:
  ml-crew:
    lead: "ML engineering lead with focus on production ML systems."
    members:
      data_scientist:
        role: "Data Scientist"
        prompt: "You explore data, build models, run experiments."
        tools: [Read, Write, Bash]
        max_turns: 60
      ml_engineer:
        role: "ML Engineer"
        prompt: "You productionize models, build pipelines, optimize inference."
        tools: [Read, Edit, Write, Bash]
        max_turns: 80
```

## Configuration

Environment variables (or `.env` file):

| Variable | Default | Description |
|----------|---------|-------------|
| `SHIPWRIGHT_MODEL` | `claude-sonnet-4-6` | Claude model to use |
| `MAX_FIX_ATTEMPTS` | `3` | Max QA-fix cycles |
| `TELEGRAM_BOT_TOKEN` | — | Telegram bot token |
| `DISCORD_BOT_TOKEN` | — | Discord bot token |

## Architecture

```
shipwright/
├── main.py              # CLI entry point
├── config.py            # Config loading (env + shipwright.yaml)
├── crew/                # Crew management
│   ├── registry.py      # Built-in crew definitions
│   ├── crew.py          # Crew class
│   ├── member.py        # CrewMember (Claude Code SDK session)
│   └── lead.py          # CrewLead (conversational coordinator)
├── conversation/        # Message handling
│   ├── session.py       # Persistent message history
│   └── router.py        # Routes messages to crews
├── workspace/           # Project management
│   ├── git.py           # Git worktree isolation
│   └── project.py       # Tech stack auto-discovery
├── interfaces/          # User interfaces
│   ├── cli.py           # Interactive REPL
│   ├── telegram.py      # Telegram bot
│   └── discord.py       # Discord bot
└── persistence/         # State management
    └── store.py         # Save/restore to JSON
```

### Key design decisions

- **Crew leads are the interface** — you never talk directly to members
- **Members work in git worktrees** — isolation, each crew gets its own branch
- **Conversations are persistent** — survive restarts, crews remember context
- **Claude Code SDK** — all execution through local Claude Code, no API costs
- **Project discovery** — auto-detects tech stack by reading the repo

## Testing

```bash
pip install ".[dev]"
pytest tests/ -v
```

## License

MIT
