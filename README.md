# Shipwright

**Build an AI engineering company powered by Claude. Hire persistent employees, organize them into teams, and assign work conversationally.**

Shipwright lets you hire AI engineers by role — backend developers, architects, QA engineers — and manage them like a real company. Employees persist across tasks, accumulate context, and remember what they've worked on. Organize them into teams with leads who coordinate and delegate. It's collaborative AI development with memory and structure.

```
shipwright > hire architect
  Hired Nori as Architect (idle)

shipwright > hire backend-dev
  Hired Quinn as Backend Developer (idle)

shipwright > assign Nori "Explore the codebase and map the architecture"
  Nori is working...
  Nori done (34.2s)

shipwright > talk Nori
  [Nori/Architect] > What did you find?

  Nori: The codebase has two main services: a Django REST backend and a
        FastAPI agent runner. Backend handles restaurants, orders, and
        telephony. Agent runner manages AI voice/SMS agents...

shipwright > assign Quinn "Add Stripe payment processing — use Nori's notes"
  Quinn is working...
```

---

## Quick Start

### Prerequisites

- Python 3.12+
- [Claude Code CLI](https://docs.anthropic.com/en/docs/claude-code) installed and authenticated

### Installation

```bash
pip install shipwright

# With optional extras
pip install shipwright[yaml]       # YAML config support
pip install shipwright[discord]    # Discord bot
pip install shipwright[all]        # Everything
```

### Your First Hire

```bash
# Launch interactive REPL
shipwright

# Or quick-hire from the command line
shipwright hire backend-dev
shipwright assign Alex "Add Stripe payments"
shipwright status
```

Inside the REPL:

```
shipwright > roles
  Available roles:
    architect, backend-dev, frontend-dev, fullstack-dev, db-engineer,
    qa-engineer, devops-engineer, security-auditor, tech-writer, designer,
    team-lead

shipwright > hire backend-dev
  Hired Alex as Backend Developer (idle)

shipwright > hire backend-dev as "Kai"
  Hired Kai as Backend Developer (idle)

shipwright > assign Alex "Build the checkout API"
  Alex is working...
  Alex done (45.1s)

shipwright > talk Alex
  [Alex/Backend Developer] > How did it go?

  Alex: Done. I added three endpoints — create-intent, confirm, and webhook.
        The webhook handler verifies Stripe signatures and updates order
        status. Want me to add tests?
```

---

## Features

### Persistent Employees

Unlike fire-and-forget agents, Shipwright employees persist. Each employee maintains a Claude Code SDK session — they remember past tasks, conversations, and codebase context. The more an employee works, the more effective they become.

```
shipwright > assign Nori "Explore the codebase"
  ...done

shipwright > assign Nori "Review what Quinn built"
  Nori already knows the codebase from the first task — no re-exploration needed.
```

### Teams & Delegation

Organize employees into teams with leads who coordinate work:

```
shipwright > team create backend
  Created team 'backend'

shipwright > promote Quinn to lead of backend
  Quinn is now Team Lead of 'backend'

shipwright > assign Kai to backend
  Kai added to team 'backend'

shipwright > assign backend "Add Stripe payment processing"
  Quinn (Team Lead): Got it. I'll have Kai build the API endpoints.
  Let me review the architecture first.
  Kai working on: Payment API endpoints
  ...
```

When work is assigned to a team, the lead breaks it into sub-tasks, delegates to members in parallel, reviews results, and synthesizes a response. Up to 5 delegation rounds per interaction.

### Built-in Roles

| Role | Description | Best For |
|------|-------------|----------|
| **architect** | Explores codebase, writes specs, designs systems | Architecture, planning |
| **backend-dev** | Implements APIs, services, business logic | Server-side features |
| **frontend-dev** | Builds UI components, pages | Client-side features |
| **fullstack-dev** | Both frontend and backend | End-to-end features |
| **db-engineer** | Schemas, migrations, query optimization | Data layer |
| **qa-engineer** | Writes and runs tests | Testing and quality |
| **devops-engineer** | Infra, CI/CD, deployment | Infrastructure |
| **security-auditor** | Security review, pen testing | Security hardening |
| **tech-writer** | Documentation, API docs | Documentation |
| **designer** | UI/UX design, component design | Design |
| **team-lead** | Coordinates a group of employees | Team management |

Each role has a rich, senior-level system prompt with engineering philosophy, patterns, anti-patterns, and standards.

### Claude Code SDK Integration

Every employee is a Claude Code SDK session. Shipwright uses your local Claude Code installation and existing subscription — no extra API costs. Employees get role-appropriate tools:

- **Architects** get read-only tools (Read, Glob, Grep) for exploration
- **Developers** get write tools (Read, Edit, Write, Bash) for implementation
- **Team leads** get read-only tools for coordination

### Git Worktree Isolation

All work happens in an isolated git worktree on a dedicated branch (`shipwright/company`). The main branch stays clean, and completed work ships as a PR.

### Multiple Interfaces

```bash
shipwright                # Interactive REPL
shipwright --telegram     # Telegram bot (per-chat state)
shipwright --discord      # Discord bot (per-channel state)
```

### Sessions

Save and restore your company state across restarts:

```
shipwright > save
shipwright > sessions
shipwright > session save my-project
shipwright > session load my-project
```

Or use named sessions from the command line:

```bash
shipwright --session my-project
```

---

## CLI Command Reference

### Shell Commands

```bash
shipwright                              # Interactive REPL
shipwright --session <name>             # Use a named session
shipwright hire <role>                  # Quick hire
shipwright hire <role> as "Name"        # Hire with custom name
shipwright assign <name> "<task>"       # Assign work
shipwright talk <name>                  # Talk to employee
shipwright fire <name>                  # Fire employee
shipwright status                       # Company overview
shipwright team                         # Team overview
shipwright sessions                     # List saved sessions
shipwright --telegram                   # Telegram bot mode
shipwright --discord                    # Discord bot mode
```

### REPL Commands

| Command | Aliases | Description |
|---------|---------|-------------|
| `roles` | | List available roles to hire |
| `hire <role>` | | Hire an employee with auto-generated name |
| `hire <role> as "Name"` | | Hire with a custom name |
| `fire <name>` | | Fire an employee |
| `fire <team>` | | Fire an entire team and all members |
| `team` | `teams`, `company`, `org` | Show company overview |
| `team create <name>` | | Create a new team |
| `promote <name> to lead of <team>` | | Promote an employee to team lead |
| `assign <name> to <team>` | | Add an employee to a team |
| `assign <name> "<task>"` | | Assign work to an employee |
| `assign <team> "<task>"` | | Assign work to a team (lead coordinates) |
| `talk <name>` | | Switch conversation to an employee |
| `status` | `overview`, `board` | Company overview with status |
| `costs` | `cost`, `spending`, `budget` | Cost report per employee |
| `history <name>` | | Task history for an employee |
| `ship` | `pr` | Open a PR for all work |
| `ship <team>` | | Open a PR for a team's work |
| `save` | | Save current state |
| `session save <name>` | | Save as a named session |
| `session load <name>` | | Load a named session |
| `session clear` | `session reset` | Clear current session |
| `sessions` | `session list` | List saved sessions |
| `shop` | `browse`, `marketplace`, `available` | Browse all available roles |
| `installed` | `plugins`, `custom` | List installed custom plugins |
| `inspect <name>` | | Show details for a role or specialist |
| `help` | `?`, `commands` | Show available commands |

Any text that isn't a command is sent as a message to the active employee.

---

## Architecture

```
User
 │
 ▼
Interface (CLI / Telegram / Discord)
 │
 ▼
Router (command parsing + dispatch)
 │
 ▼
Company (employees, teams, work assignment)
 ├── Employee (wraps Claude Code SDK session)
 │     ├── Individual work (run tasks directly)
 │     └── Team lead work (delegate to members)
 └── Team (optional org structure)
       ├── Lead (coordinates)
       └── Members (execute)
```

### Module Layout

```
shipwright/
├── main.py                  # CLI entry point, arg parsing
├── config.py                # Config loading (env + YAML + plugins)
├── sdk_patch.py             # Monkey-patch SDK for unknown message types
├── company/
│   ├── company.py           # Company — manages employees and teams
│   ├── employee.py          # Employee — wraps Claude Code SDK session
│   └── roles.py             # Built-in role definitions
├── conversation/
│   ├── session.py           # Message history and conversation state
│   └── router.py            # Command parsing + message routing
├── workspace/
│   ├── git.py               # Git worktree management, PR creation
│   └── project.py           # Project discovery (languages, frameworks)
├── interfaces/
│   ├── cli.py               # Interactive REPL with streaming output
│   ├── telegram.py          # Telegram bot (per-chat state)
│   └── discord.py           # Discord bot (per-channel state)
├── persistence/
│   └── store.py             # JSON state save/restore
└── utils/
    └── logging.py           # Structured logging
```

### Key Data Models

```python
Employee:
    name: str                # Display name (auto-generated or chosen)
    role: str                # Role ID (architect, backend-dev, etc.)
    status: EmployeeStatus   # idle, working, blocked
    team: str | None         # Team name if assigned
    is_lead: bool            # Whether this employee is a team lead
    task_history: list[Task] # Completed tasks
    session_id: str | None   # SDK session for memory continuity
    cost_total_usd: float    # Accumulated cost

Team:
    name: str                # Team name
    lead: str | None         # Employee name of the lead
    members: list[str]       # Employee names

Company:
    employees: dict          # All employees by name
    teams: dict              # All teams by name
    project_context: str     # Discovered project info
```

### How Delegation Works

When work is assigned to a team:

1. The team lead receives the task
2. Lead analyzes and responds with `[DELEGATE:member_name]` blocks
3. Delegated tasks execute in parallel
4. Results feed back to the lead
5. Lead either delegates more work or responds to the user
6. Loop continues up to 5 rounds

```
[DELEGATE:kai]
Build the /api/checkout endpoint with PaymentIntent creation.
[/DELEGATE]

[DELEGATE:reese]
Implement the Stripe webhook handler for payment confirmation.
[/DELEGATE]
```

---

## Custom Roles via Plugins

Extend Shipwright with custom roles and specialists.

### Plugin Directories

```
./shipwright/crews/       # Project-local (highest priority)
~/.shipwright/crews/      # User-global
```

Each plugin is a directory with a `crew.yaml`:

```
stripe-specialist/
├── crew.yaml
└── references/          # Optional: docs loaded into context
    ├── api-guide.md
    └── patterns.md
```

### Defining a Custom Role

```yaml
kind: role
name: stripe-specialist
role: Stripe Specialist
description: "Expert in Stripe payments integration"
prompt: |
  You are an expert in Stripe payments integration. You know the Stripe API
  inside out — PaymentIntents, Checkout Sessions, Webhooks, Subscriptions.
  Always use the latest Stripe API patterns.
tools: [Read, Edit, Write, Bash, Glob, Grep]
max_turns: 60
references: true   # Loads ./references/*.md into context
```

### Defining a Team Template

```yaml
kind: crew
name: ml-crew
description: "Machine learning engineering team"
lead: "ML tech lead who coordinates data science and ML engineering work"
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

### Resolution Order

1. **Project-local** `./shipwright/crews/`
2. **User-global** `~/.shipwright/crews/`
3. **YAML config** `./shipwright.yaml`
4. **Built-in** roles

---

## Configuration

### Environment Variables

Create a `.env` file in your project root:

```bash
# Model (default: claude-sonnet-4-6)
SHIPWRIGHT_MODEL=claude-sonnet-4-6

# Permission mode for Claude Code SDK (default: bypassPermissions)
SHIPWRIGHT_PERMISSION_MODE=bypassPermissions

# Budget limit in USD (default: 0 = no limit)
BUDGET_LIMIT_USD=50

# Project root override (default: current directory)
REPO_ROOT=/path/to/project

# Telegram bot
TELEGRAM_BOT_TOKEN=your-token
TELEGRAM_CHAT_ID=your-chat-id
TELEGRAM_ALLOWED_USERS=user1,user2

# Discord bot
DISCORD_BOT_TOKEN=your-token
DISCORD_CHANNEL_ID=your-channel-id
```

### shipwright.yaml

Project-level configuration for custom crews, placed at the project root:

```yaml
crews:
  my-crew:
    lead: "Senior tech lead for custom work"
    members:
      developer:
        role: "Developer"
        prompt: "You are a developer specializing in..."
        tools: [Read, Edit, Write, Bash]
        max_turns: 80
        model: claude-sonnet-4-6
```

### State & Sessions

State is saved to `~/.shipwright/sessions/` and restored automatically. Each interface (CLI, Telegram chat, Discord channel) maintains independent state.

---

## Contributing

```bash
git clone https://github.com/your-org/shipwright.git
cd shipwright
pip install -e ".[all]"

# Run tests
python3 -m pytest tests/ -v

# Lint
ruff check shipwright/ tests/
```

## License

MIT
