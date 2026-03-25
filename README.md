# 🚢 Shipwright

**Virtual engineering crews powered by Claude. Hire AI dev teams that collaborate conversationally — not fire-and-forget.**

Shipwright lets you hire domain-specific AI engineering crews (backend, frontend, QA, devops, etc.) and talk to them like real teams. Each crew has a lead who asks clarifying questions, proposes approaches, delegates work to specialized members, and reports back. It's collaborative software development with AI — not a black box that spits out a PR.

```
You:  hire backend Add Stripe payments
Lead: What payment provider specifics? One-time charges, subscriptions, or both?
      I'll have the Architect explore your codebase for existing payment patterns.
You:  Stripe. Design for subs but implement one-time first.
Lead: Smart. Architect is done — here's the proposed approach:
      • POST /api/checkout — create PaymentIntent
      • Webhook handler for payment confirmation
      • payments table with status tracking
      Want me to proceed?
You:  Ship it.
Lead: Backend Dev is on the API, DB Engineer on migrations.
      I'll check in when they're done.
      ...
Lead: All done. Tests passing. Ready to open a PR?
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

### Basic Usage

```bash
# Launch interactive REPL
shipwright

# Quick hire from the command line
shipwright hire backend "Add Stripe payments"

# Check on your crews
shipwright status
```

Inside the REPL, the flow is conversational:

```
shipwright> hire frontend Redesign the settings page
[frontend-redesign-the-settings-page] Crew hired!

Lead: I'll take a look at the existing settings page first. Let me have the
      UI Designer audit the current layout while the Frontend Dev checks the
      component structure.
      ...

shipwright> The color scheme should match our new brand guidelines
Lead: Got it. Can you share the brand colors, or should I extract them from
      your existing CSS variables?
```

---

## Features

### Crew-Based Model

Instead of a single AI agent, Shipwright organizes work into **crews** — teams of specialized AI developers led by a coordinator. You talk to the **Crew Lead**, who understands the big picture, asks the right questions, and delegates implementation to the right team members.

```
You ↔ Crew Lead ↔ Architect
                 ↔ Frontend Dev
                 ↔ Backend Dev
                 ↔ DB Engineer
```

The lead thinks about sequencing, integration points, and trade-offs. Members focus on their specialty. This mirrors how real engineering teams work.

### Built-in Crews

| Crew | Members | Best For |
|------|---------|----------|
| **fullstack** | Architect, Frontend Dev, Backend Dev, DB Engineer | End-to-end features |
| **frontend** | UI Designer, Frontend Dev, CSS Specialist | UI/UX work |
| **backend** | API Architect, Backend Dev, DB Engineer | APIs, services, data |
| **qa** | Test Engineer, Manual Tester, Performance Tester | Testing and quality |
| **devops** | Infra Engineer, CI/CD Specialist, Monitoring | Infrastructure and deployment |
| **security** | Security Auditor, Penetration Tester | Security audits and hardening |
| **docs** | Technical Writer, API Docs Specialist | Documentation |
| **enterprise** | Project Lead → sub-crews | Large cross-domain projects |

Each member has a rich, senior-level system prompt with engineering philosophy, patterns, anti-patterns, and code review standards — not just a one-liner.

### Conversational Interface

Crew leads are not passive relays. They:

- Ask clarifying questions before starting work
- Propose approaches and trade-offs with recommendations
- Push back on requests that create tech debt
- Report progress as members complete tasks
- Iterate on your feedback

### Delegation

When the lead decides work needs to happen, it delegates to members using structured blocks:

```
[DELEGATE:architect]
Explore the codebase and identify all payment-related code.
Look for existing Stripe integrations or payment models.
[/DELEGATE]

[DELEGATE:backend]
Implement the /api/checkout endpoint using the architect's spec.
[/DELEGATE]
```

Multiple delegations run in parallel. Results feed back to the lead, who can delegate additional rounds (up to 5 per chat interaction).

### Enterprise Mode

For large projects that span multiple domains, enterprise mode adds a third level:

```
You ↔ Project Lead ↔ Backend Crew (Lead + Members)
                   ↔ Frontend Crew (Lead + Members)
                   ↔ DevOps Crew (Lead + Members)
```

```bash
shipwright hire enterprise "Build complete SaaS billing system"
```

The Project Lead analyzes scope, spawns sub-crews as needed, and coordinates between them. Each sub-crew operates independently with its own lead and members. Hard-capped at 3 levels deep.

### Plugin System

Extend Shipwright with custom crews and specialists.

**Specialists** are domain experts you can recruit into any active crew:

```bash
# List what's installed
shipwright> installed

# Recruit a specialist into an active crew
shipwright> recruit stripe-specialist into backend-payments

# Inspect a crew or specialist
shipwright> inspect stripe-specialist
```

See [Creating Custom Crews](#creating-custom-crews) for the full format.

### Multiple Interfaces

- **Interactive CLI** — REPL with markdown rendering, tab completion, readline history, streaming output, and delegation progress
- **Telegram Bot** — Per-chat crew management with user allowlisting
- **Discord Bot** — Per-channel crew management with `!` command prefix

```bash
shipwright                # Interactive REPL
shipwright --telegram     # Telegram bot mode
shipwright --discord      # Discord bot mode
```

### Persistent State

Crews survive restarts. Conversation history, active crews, task records, and session IDs are saved to `.shipwright/state/` and restored automatically. Each interface (CLI, Telegram chat, Discord channel) maintains independent state.

---

## Architecture

```
┌──────────────────────────────────────────────────┐
│                   User Message                    │
└──────────────┬───────────────────────────────────┘
               │
┌──────────────▼───────────────────────────────────┐
│         Interface (CLI / Telegram / Discord)       │
└──────────────┬───────────────────────────────────┘
               │
┌──────────────▼───────────────────────────────────┐
│           Router (command parsing + routing)       │
│  hire, fire, status, talk, ship, shop, recruit    │
└──────────────┬───────────────────────────────────┘
               │
┌──────────────▼───────────────────────────────────┐
│           Crew (orchestration + delegation)        │
│  delegation loop, parallel execution, worktrees   │
└──────────┬───────────────┬───────────────────────┘
           │               │
┌──────────▼──────┐ ┌──────▼──────────────────────┐
│   Crew Lead     │ │   Members (Architect, Dev,   │
│ (conversation,  │ │   DB Engineer, etc.)         │
│  coordination)  │ │   Each a Claude Code session  │
└─────────────────┘ └──────────────────────────────┘
```

### Module Breakdown

```
shipwright/
├── main.py                  # CLI entry point, arg parsing
├── config.py                # Config loading (env + YAML + plugins)
├── sdk_patch.py             # Monkey-patch SDK for unknown message types
├── crew/
│   ├── registry.py          # Built-in crews + custom crew resolution
│   ├── crew.py              # Crew + EnterpriseCrew orchestration
│   ├── member.py            # CrewMember — wraps Claude Code SDK session
│   └── lead.py              # CrewLead — conversational coordinator
├── conversation/
│   ├── session.py           # Message history and conversation state
│   └── router.py            # Command parsing + message routing
├── workspace/
│   ├── git.py               # Git worktree management, PR creation
│   └── project.py           # Project discovery (languages, frameworks)
├── interfaces/
│   ├── cli.py               # Interactive REPL with markdown rendering
│   ├── telegram.py          # Telegram bot (per-chat state)
│   └── discord.py           # Discord bot (per-channel state)
├── persistence/
│   └── store.py             # JSON state save/restore
└── utils/
    └── logging.py           # Structured logging
```

### Claude Code SDK Integration

Every crew member and crew lead is a Claude Code SDK session. Shipwright doesn't call the Anthropic API directly — it uses the local Claude Code installation and your existing subscription. No extra API costs.

```python
from claude_code_sdk import query, ClaudeCodeOptions

async for message in query(
    prompt="Implement the checkout endpoint",
    options=ClaudeCodeOptions(
        allowed_tools=["Read", "Edit", "Write", "Bash"],
        permission_mode="bypassPermissions",
        max_turns=50,
        model="claude-sonnet-4-6",
        system_prompt="You are a senior backend developer...",
        cwd="/path/to/worktree",
    ),
):
    # TextBlock, ToolUseBlock, ThinkingBlock, ResultMessage
    pass
```

Leads get read-only tools (Read, Glob, Grep) so they can explore the codebase without modifying it. Members get write tools appropriate to their role.

---

## Creating Custom Crews

### Plugin Directory Structure

Place custom crews and specialists in either location:

```
./shipwright/crews/       # Project-local (takes priority)
~/.shipwright/crews/      # User-global
```

Each plugin is a directory with a `crew.yaml`:

```
my-specialist/
├── crew.yaml
└── references/          # Optional: docs loaded into member context
    ├── api-guide.md
    └── patterns.md
```

### Specialist Definition

```yaml
kind: specialist
name: stripe-specialist
description: "Expert in Stripe payments integration"
role: "Stripe Payments Specialist"
prompt: |
  You are an expert in Stripe payments integration. You know the Stripe API
  inside out — PaymentIntents, Checkout Sessions, Webhooks, Subscriptions.
  Always use the latest Stripe API patterns.
tools: [Read, Edit, Write, Bash, Glob, Grep]
max_turns: 60
references: true   # Loads ./references/*.md into member context
```

### Crew Definition

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

### YAML Config (Alternative)

Define custom crews in `shipwright.yaml` or `shipwright.yml` at your project root:

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
        model: claude-sonnet-4-6   # Optional per-member model override
```

### References System

Specialists with `references: true` automatically load all `.md` files from a `references/` directory alongside their `crew.yaml`. This is useful for bundling API docs, coding patterns, or domain knowledge that the specialist should have in context.

### Resolution Order

When looking up a crew or specialist by name:

1. **Project-local** — `./shipwright/crews/`
2. **User-global** — `~/.shipwright/crews/`
3. **YAML config** — `./shipwright.yaml`
4. **Built-in** — Hardcoded crew definitions

Project-local always wins, letting you override built-in crews per-project.

---

## CLI Commands Reference

### From the Shell

```bash
shipwright                          # Interactive REPL
shipwright hire <type> <objective>  # Quick hire a crew
shipwright status                   # Show active crews
shipwright talk <crew-id>           # Talk to a specific crew
shipwright fire <crew-id>           # Dismiss a crew
shipwright --telegram               # Run Telegram bot
shipwright --discord                # Run Discord bot
shipwright "<message>"              # Send message to active crew
```

### Inside the REPL

| Command | Aliases | Description |
|---------|---------|-------------|
| `hire <type> <objective>` | `start`, `create` | Hire a new crew |
| `fire <crew-id>` | `dismiss`, `stop`, `remove` | Dismiss a crew and clean up its worktree |
| `status` | `crews`, `list`, `board` | Show all active crews |
| `talk to <crew-id>` | `switch to`, `use` | Switch active crew |
| `log <crew-id>` | `history` | Show conversation history for a crew |
| `ship` | `pr`, `open pr`, `create pr` | Create a PR from the active crew's work |
| `shop` | `browse`, `marketplace`, `available` | List all available crew types |
| `installed` | `plugins`, `custom` | List installed custom crews and specialists |
| `inspect <name>` | | Show detailed info about a crew or specialist |
| `recruit <specialist> into <crew-id>` | | Add a specialist to an active crew |
| `help` | `?`, `commands` | Show available commands |

Any text that isn't a command is sent as a message to the active crew.

---

## Configuration

### Environment Variables

Create a `.env` file in your project root:

```bash
# Model (default: claude-sonnet-4-6)
SHIPWRIGHT_MODEL=claude-sonnet-4-6

# Permission mode for Claude Code SDK (default: bypassPermissions)
SHIPWRIGHT_PERMISSION_MODE=bypassPermissions

# Max QA fix cycles (default: 3)
MAX_FIX_ATTEMPTS=3

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

Project-level configuration for custom crews. Placed at the project root. See [Creating Custom Crews](#creating-custom-crews) for the full format.

### Plugin Directories

```
./shipwright/crews/      # Project-local plugins (highest priority)
~/.shipwright/crews/     # User-global plugins
```

Each subdirectory containing a `crew.yaml` is automatically discovered and loaded.

---

## How It Works

### The Delegation Loop

Every `chat()` interaction follows this loop:

1. User message is sent to the **Crew Lead** (a Claude Code SDK session with read-only tools)
2. Lead responds — possibly with `[DELEGATE:member_name]` blocks
3. Delegation blocks are parsed and executed (parallel if multiple)
4. Member results are fed back to the lead as context
5. Lead responds again — may delegate more work or respond to the user
6. Loop continues until no delegations remain or 5 rounds are reached

```
User message
    → Lead responds (may delegate)
        → Members execute tasks
            → Results fed back to lead
                → Lead responds (may delegate again)
                    → ... (up to 5 rounds)
                        → Final response to user
```

### SDK Patch

The Claude Code SDK raises `MessageParseError` for unknown message types like `rate_limit_event`, which kills the async stream. Shipwright monkey-patches `parse_message` to return `None` for unknown types instead, allowing the stream to continue gracefully. Applied once at import time via `shipwright/sdk_patch.py`.

### Git Worktree Isolation

Each crew works in its own git worktree on a dedicated branch (`shipwright/<crew-id>`):

- Multiple crews can work simultaneously without conflicts
- The main branch stays clean
- Each crew's work can be shipped as a separate PR
- Worktrees are cleaned up automatically when a crew is dismissed

Enterprise mode creates nested branches: `shipwright/enterprise-xxx/backend`, etc.

---

## Contributing

```bash
# Clone and install in dev mode
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
