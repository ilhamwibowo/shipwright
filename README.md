# Shipwright

**A virtual engineering company powered by Claude. You talk to the CTO — it handles the rest.**

Shipwright gives you a fully staffed AI engineering company. When you type a message, the CTO (Claude Opus) reads it, hires the right engineers, delegates work, reviews their output, and requests revisions until the work meets quality standards. You never manage individual agents — you manage a company.

```
shipwright > Build a REST API for user authentication with JWT tokens

  CTO: I'll staff this up and get it done.

  [Hired] Reese — Backend Developer
  [Hired] Morgan — QA Engineer

  ⚡ Delegating...
    → Reese: Implement auth API with JWT (register, login, refresh, middleware)
    → Morgan: Write integration tests for all auth endpoints

  ✓ Reese done (52.3s)
  ✓ Morgan done (38.1s)

  CTO: Done. Reese built the auth API — register, login, token refresh, plus
       JWT middleware for protected routes. Morgan wrote 14 integration tests,
       all passing. The refresh token rotation follows OWASP guidelines.

       Want me to have someone add rate limiting?
```

You just talk. The CTO runs the company.

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

### Launch

```bash
shipwright                    # Interactive REPL — CTO is always present
shipwright --telegram         # Telegram bot (per-chat state)
shipwright --discord          # Discord bot (per-channel state)
```

The CTO is created automatically on your first message. No setup needed.

---

## How It Works

### The CTO-First Flow

Every message goes to the CTO (Claude Opus), who decides what to do:

```
You: "Add Stripe payment processing"
 │
 ▼
CTO (Opus) — reads message, sees company state
 ├── [HIRE:backend-dev:Kai]    → hires Kai as Backend Developer
 ├── [HIRE:qa-engineer:River]  → hires River as QA Engineer
 ├── [DELEGATE:Kai]            → "Implement Stripe checkout with PaymentIntents..."
 └── [DELEGATE:River]          → "Write tests for the payment flow..."
      │
      ▼
   Employees execute in parallel
      │
      ▼
   CTO reviews results
      ├── Looks good → presents summary to you
      └── Needs work → [REVISE:Kai] "The webhook handler needs signature verification"
                         │
                         ▼
                       Kai fixes → CTO reviews again → up to 3 rounds
```

The CTO is an opinionated engineering manager. It hires, delegates, reviews, and revises — you just describe what you want.

### Direct Access with @name

Bypass the CTO and talk to any employee directly:

```
shipwright > @Kai How did you structure the webhook handler?

  Kai: I set up a single /webhooks/stripe endpoint that verifies the
       signature first, then dispatches by event type — checkout.session.completed,
       payment_intent.succeeded, and invoice.payment_failed...
```

### The Review Loop

The CTO doesn't just delegate and walk away. After employees finish:

1. **Review** — CTO examines all results
2. **Revise** — Sends `[REVISE:name]` blocks with specific feedback
3. **Re-execute** — Employee applies fixes
4. **Loop** — Up to 3 revision rounds (configurable via `MAX_REVISION_ROUNDS`)

For quality-critical work, the CTO can hire an **evaluator** — a dedicated read-only critic that grades code on correctness, quality, completeness, and integration (1-5 each) and issues APPROVE/REVISE/REJECT verdicts.

### Hierarchy Enforcement

Not everyone can do everything:

| Permission | Who |
|---|---|
| Hire employees | CTO only |
| Delegate work | CTO, team leads |
| Request revisions | CTO, team leads |
| Execute tasks | All employees |

Team leads can only delegate to their own team members. The CTO can delegate to anyone.

---

## Roles

### 13 Built-in Roles

| Role | Description | Tools | Max Turns |
|------|-------------|-------|-----------|
| **cto** | Chief Technology Officer — orchestrates everything | Read, Glob, Grep | 15 |
| **architect** | System design, specs, codebase exploration | Read, Glob, Grep, Write, Bash | 40 |
| **backend-dev** | APIs, services, business logic | Read, Edit, Write, Glob, Grep, Bash | 80 |
| **frontend-dev** | UI components, pages, client-side | Read, Edit, Write, Glob, Grep, Bash | 80 |
| **fullstack-dev** | Frontend + backend | Read, Edit, Write, Glob, Grep, Bash | 80 |
| **db-engineer** | Schemas, migrations, query optimization | Read, Edit, Write, Bash | 40 |
| **qa-engineer** | Tests and quality assurance | Read, Write, Glob, Grep, Bash | 60 |
| **devops-engineer** | Infrastructure, CI/CD, deployment | Read, Edit, Write, Glob, Grep, Bash | 60 |
| **security-auditor** | Security review, pen testing | Read, Glob, Grep, Write | 50 |
| **tech-writer** | Documentation | Read, Write, Glob, Grep | 40 |
| **designer** | UI/UX design, component specs | Read, Glob, Grep, Write | 30 |
| **team-lead** | Coordinates a team via delegation | Read, Glob, Grep | 20 |
| **evaluator** | Read-only code quality critic | Read, Glob, Grep | 30 |

The CTO runs on **Claude Opus** (smartest model). All other roles default to **Claude Sonnet** (configurable via `SHIPWRIGHT_MODEL`).

Each role has a rich system prompt with engineering philosophy, patterns, anti-patterns, and domain-specific standards.

### Team Templates

Pre-built team structures for common setups:

- **fullstack** — Architect, Frontend, Backend, DB Engineer
- **frontend** — Designer, Developer, CSS Specialist
- **backend** — Architect, Developer, DB Engineer
- **qa** — Test Engineer, Manual Tester, Performance Tester
- **devops** — Infra Engineer, CI/CD Specialist, Monitoring
- **security** — Security Auditor, Penetration Tester
- **docs** — Tech Writer, API Docs Specialist

---

## Context Management

### Persistent Employees

Employees maintain Claude Agent SDK sessions with `session_id` resume. They remember past tasks, conversations, and codebase context across interactions:

```
shipwright > assign Nori "Explore the codebase"
  ...done

shipwright > assign Nori "Review what Kai built"
  Nori already knows the codebase — no re-exploration needed.
```

### Context Resets with Handoff Artifacts

When an employee hits ~80% of the context reset threshold (default: 24 of 30 turns), Shipwright automatically:

1. **Generates a handoff artifact** — structured summary of the employee's state, recent tasks, key decisions, and conversation highlights
2. **Saves it** to `.shipwright/handoffs/{name}_{task_id}.md`
3. **Resets the session** — clears the SDK session
4. **Loads the artifact** on next run as context prefix

The employee effectively "reads their own notes" and continues where they left off, with a fresh context window.

---

## CLI Command Reference

### Shell Commands

```bash
shipwright                              # Interactive REPL (CTO auto-created)
shipwright --session <name>             # Use a named session
shipwright hire <role>                  # Quick hire
shipwright hire <role> as "Name"        # Hire with custom name
shipwright assign <name> "<task>"       # Assign work
shipwright status                       # Company overview
shipwright --telegram                   # Telegram bot mode
shipwright --discord                    # Discord bot mode
```

### REPL Commands

| Command | Aliases | Description |
|---------|---------|-------------|
| `roles` | | List available roles |
| `hire <role>` | | Hire with auto-generated name |
| `hire <role> as "Name"` | | Hire with custom name |
| `fire <name>` | | Fire an employee |
| `fire <team>` | | Fire a team and all members |
| `team create <name>` | | Create a new team |
| `promote <name> to lead of <team>` | | Make someone team lead |
| `assign <name> to <team>` | | Add employee to a team |
| `assign <name> "<task>"` | | Assign work to an employee |
| `assign <team> "<task>"` | | Assign work to a team (lead coordinates) |
| `@<name> <message>` | | Direct message to an employee (bypasses CTO) |
| `talk <name>` | | Switch active employee |
| `back` | | Return to CTO |
| `status` | `team`, `company`, `org`, `overview`, `board` | Company overview |
| `costs` | `cost`, `spending`, `budget` | Cost report per employee |
| `history <name>` | | Task history with durations and costs |
| `ship` | `pr` | Open a PR for all work |
| `ship <team>` | | Open a PR for a team's work |
| `save` | | Save current state |
| `session save <name>` | | Save as named session |
| `session load <name>` | | Load a named session |
| `session clear` | `session reset` | Clear session |
| `sessions` | `session list` | List saved sessions |
| `shop` | `browse`, `marketplace`, `available` | Browse all roles and specialists |
| `installed` | `plugins`, `custom` | List installed plugins |
| `inspect <name>` | | Show role/specialist details |
| `help` | `?`, `commands` | Show available commands |

Any text that isn't a command goes to the active employee (CTO by default).

---

## Architecture

```
User
 │
 ▼
Interface (CLI / Telegram / Discord)
 │
 ▼
Router (command parsing + @name dispatch)
 │
 ├── Command → direct execution (hire, fire, team, etc.)
 │
 └── Message → Company
              │
              ├── CTO auto-pilot (default)
              │    ├── [HIRE] → create employees
              │    ├── [DELEGATE] → parallel execution
              │    ├── Review results
              │    └── [REVISE] → revision loop (up to 3 rounds)
              │
              └── Direct talk (@name or talk <name>)
                   └── Employee.run() → Claude Agent SDK session
```

### Module Layout

```
shipwright/
├── main.py                  # CLI entry point, arg parsing
├── config.py                # Config loading (env + YAML + plugins)
├── company/
│   ├── company.py           # Company — CTO auto-pilot, delegation loop, hierarchy
│   ├── employee.py          # Employee — SDK session, context reset, handoff artifacts
│   └── roles.py             # 13 built-in roles, team templates, rich prompts
├── conversation/
│   ├── session.py           # Message history and conversation state
│   └── router.py            # Command parsing, @name dispatch, CTO-first routing
├── workspace/
│   ├── git.py               # Git worktree management, PR creation
│   └── project.py           # Project discovery (languages, frameworks, test commands)
├── interfaces/
│   ├── cli.py               # Interactive REPL with streaming, spinner, delegation UI
│   ├── telegram.py          # Telegram bot (per-chat state)
│   └── discord.py           # Discord bot (per-channel state)
├── persistence/
│   └── store.py             # JSON state save/restore
└── utils/
    └── logging.py           # Structured logging
```

### Delegation Protocol

The CTO and team leads use structured blocks to coordinate:

```
[HIRE:backend-dev]                    # Hire with auto-generated name
[HIRE:qa-engineer:River]              # Hire with specific name

[DELEGATE:Kai]                        # Delegate work to an employee
Build the checkout API with
PaymentIntent creation and webhooks.
[/DELEGATE]

[REVISE:Kai]                          # Request revisions
The webhook handler needs Stripe
signature verification before
processing events.
[/REVISE]
```

Blocks are parsed, filtered through hierarchy enforcement, and executed automatically.

---

## Custom Roles via Plugins

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

```bash
# Model for IC employees (default: claude-sonnet-4-6)
# CTO always uses claude-opus-4-6 regardless of this setting
SHIPWRIGHT_MODEL=claude-sonnet-4-6

# Permission mode for Claude Agent SDK (default: bypassPermissions)
SHIPWRIGHT_PERMISSION_MODE=bypassPermissions

# Budget limit in USD (default: 0 = no limit)
BUDGET_LIMIT_USD=50

# Context reset threshold in turns (default: 30, triggers at 80%)
CONTEXT_RESET_THRESHOLD=30

# Max revision rounds in delegation loop (default: 3)
MAX_REVISION_ROUNDS=3

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

Project-level configuration:

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

### Git Worktree Isolation

All work happens in an isolated git worktree on a `shipwright/company` branch. The main branch stays clean. Each employee's completed task is auto-committed. Ship as a PR with `ship`.

### Sessions

State persists to `~/.shipwright/sessions/`. Each interface (CLI, Telegram chat, Discord channel) maintains independent state. Employee SDK sessions resume automatically — full memory continuity.

---

## Contributing

```bash
git clone https://github.com/your-org/shipwright.git
cd shipwright
pip install -e ".[all]"

# Run tests (389 tests)
python3 -m pytest tests/ -v

# Lint
ruff check shipwright/ tests/
```

## License

MIT
