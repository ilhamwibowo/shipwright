# Shipwright Redesign — Crew-Based Conversational Dev Teams

## Vision
Shipwright is a virtual engineering company. You hire crews of specialized AI developers, talk to them conversationally, and they build your software. It's not fire-and-forget — it's collaborative.

## Core Concepts

### Crews
A crew is a team of specialized agents with a Crew Lead who you talk to. Crews are domain-specific:

- **fullstack** — Architect, Frontend Dev, Backend Dev, DB Engineer
- **frontend** — UI Designer, React/Vue Dev, CSS Specialist
- **backend** — API Architect, Service Dev, DB Engineer
- **qa** — Test Engineer, Manual Tester, Performance Tester
- **devops** — Infra Engineer, CI/CD Specialist, Monitoring
- **security** — Security Auditor, Pen Tester
- **docs** — Technical Writer, API Docs Specialist

Users can define custom crews in a `shipwright.yaml` config file.

### Crew Lifecycle
1. **Hire** — `shipwright hire backend "Add Stripe payments"` or conversationally: "hire a backend crew for payments"
2. **Converse** — The crew lead asks clarifying questions, proposes approaches, shows progress
3. **Work** — Crew members work in parallel/sequence, crew lead coordinates
4. **Review** — Crew presents work for your review, iterates on feedback
5. **Ship** — Crew opens PR, runs final checks
6. **Fire** — Dismiss the crew: `shipwright fire backend-payments`

### Conversation Model
This is the KEY difference from v1. Instead of:
```
User: "Add payments" → [black box] → PR
```

It's:
```
User: hire a backend crew to add payments
Lead: What payment provider? Any existing payment code I should know about?
User: Stripe. No existing code, greenfield.
Lead: Got it. I'm having the Architect explore the codebase to understand the patterns.
      Meanwhile, should I design for subscriptions too or just one-time?
User: Design for subs but implement one-time first
Lead: Smart. Architect is done — here's the proposed approach:
      [shows spec summary]
      Want me to proceed or adjust anything?
User: Looks good, ship it
Lead: Kicking off implementation. Backend Dev is on the API, DB Engineer on migrations.
      I'll check in when they're done.
[...time passes...]
Lead: Implementation done. QA is running tests now. One issue — the webhook endpoint
      needs a public URL for Stripe. Should I set up an ngrok tunnel or mock it?
User: Mock it for now
Lead: Done. All tests passing. Here's the diff summary:
      [shows changes]
      Ready to open a PR?
```

### Agent Execution — Claude Code SDK
CRITICAL: All agents run through `claude-code-sdk` (Python package `claude_code_sdk`).
This uses the local Claude Code installation and the user's subscription — NO API costs.

```python
from claude_code_sdk import query, ClaudeCodeOptions, ContentBlock

async for message in query(
    prompt="Your task here",
    options=ClaudeCodeOptions(
        allowed_tools=["Read", "Edit", "Write", "Bash"],
        permission_mode="bypassPermissions",
        max_turns=50,
        model="claude-sonnet-4-6",
        system_prompt="You are a backend developer...",
        cwd="/path/to/project",
    ),
):
    # Process messages — TextBlock, ToolUseBlock, etc.
    pass
```

Each crew member is a Claude Code session with:
- A specialized system prompt for their role
- Restricted tool access appropriate to their role
- A working directory (usually a git worktree)

### Architecture

```
shipwright/
├── __init__.py
├── main.py                  # CLI entry point
├── config.py                # Config loading (env + shipwright.yaml)
├── crew/
│   ├── __init__.py
│   ├── registry.py          # Built-in crew definitions
│   ├── crew.py              # Crew class — manages members + conversation
│   ├── member.py            # CrewMember — wraps a Claude Code SDK session
│   └── lead.py              # CrewLead — the conversational coordinator
├── conversation/
│   ├── __init__.py
│   ├── session.py           # Conversation session — message history, context
│   └── router.py            # Routes user messages to the right crew
├── workspace/
│   ├── __init__.py
│   ├── git.py               # Git worktree management
│   └── project.py           # Project discovery (detect tech stack, structure)
├── interfaces/
│   ├── __init__.py
│   ├── cli.py               # Interactive CLI (REPL)
│   ├── telegram.py          # Telegram bot
│   └── discord.py           # Discord bot
├── persistence/
│   ├── __init__.py
│   └── store.py             # Save/restore crews, conversations, state
└── utils/
    ├── __init__.py
    └── logging.py            # Structured logging

tests/
├── test_crew.py
├── test_conversation.py
├── test_workspace.py
└── test_config.py
```

### Crew Definition Format (shipwright.yaml)
```yaml
crews:
  backend:
    lead: "Senior backend tech lead. Coordinates API design and implementation."
    members:
      architect:
        role: "API Architect"
        prompt: "You design REST/GraphQL APIs, database schemas, and service boundaries."
        tools: [Read, Glob, Grep, Write]  # Read-only + spec writing
        max_turns: 40
      developer:
        role: "Backend Developer"
        prompt: "You implement APIs, services, and business logic."
        tools: [Read, Edit, Write, Glob, Grep, Bash]
        max_turns: 80
      db_engineer:
        role: "Database Engineer"
        prompt: "You design schemas, write migrations, optimize queries."
        tools: [Read, Edit, Write, Bash]
        max_turns: 40

  custom-ml-crew:
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

### CLI Interface
```bash
# Interactive REPL (main mode)
shipwright

# Quick hire
shipwright hire backend "Add Stripe payments"
shipwright hire frontend "Redesign the dashboard"

# Manage crews
shipwright crews                    # List active crews
shipwright status                   # Overall status
shipwright fire backend-payments    # Dismiss a crew
shipwright log backend-payments     # View conversation history

# Talk to a specific crew
shipwright talk backend-payments    # Opens conversation with that crew
```

### Key Design Decisions
1. **Crew Lead is the interface** — You never talk directly to individual members. The lead coordinates.
2. **Members work in git worktrees** — Isolation. Each crew gets its own branch.
3. **Conversation is persistent** — Survives restarts. Crews remember context.
4. **Crews can be paused/resumed** — "I'll get back to you on that" works.
5. **Project discovery is automatic** — Crews read the codebase to understand tech stack, patterns, conventions.
6. **All execution through Claude Code SDK** — Uses local subscription, not API credits.

## Plugin / Hire System — Plug & Play Crews and Specialists

### Concept
Crews and individual specialists are pluggable. You can:
1. Use built-in crews that ship with Shipwright
2. Define custom crews in shipwright.yaml
3. Install community crews/specialists from a registry
4. Recruit individual specialists into existing crews

### Package Format
A crew/specialist package is a directory:
```
stripe-specialist/
├── crew.yaml       # Role definition, prompt, tools, config
├── references/     # Docs, patterns, examples the agent gets as context
│   ├── stripe-api.md
│   ├── webhook-patterns.md
│   └── error-handling.md
└── README.md       # Human-readable description
```

crew.yaml for a specialist:
```yaml
kind: specialist
name: stripe-specialist
description: "Expert in Stripe payments integration"
role: "Stripe Payments Specialist"
prompt: |
  You are an expert in Stripe payments integration. You know the Stripe API 
  inside out — PaymentIntents, Checkout Sessions, Webhooks, Subscriptions.
  Always use the latest Stripe API patterns.
  Reference docs are available in your context.
tools: [Read, Edit, Write, Bash, Glob, Grep]
max_turns: 60
references: true  # Load references/ into context
```

crew.yaml for a full crew:
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
  ml_engineer:
    role: "ML Engineer"  
    prompt: "You productionize models, build pipelines, optimize inference."
    tools: [Read, Edit, Write, Bash]
  data_engineer:
    role: "Data Engineer"
    prompt: "You build data pipelines, ETL jobs, and data warehouses."
    tools: [Read, Edit, Write, Bash]
```

### CLI Commands
```bash
# Install from registry
shipwright install @community/stripe-specialist
shipwright install @community/ml-crew

# List available/installed
shipwright shop                    # Browse registry
shipwright installed               # List installed crews/specialists

# Recruit into active crew
shipwright recruit stripe-specialist --into backend-payments

# Hire installed crew directly
shipwright hire ml-crew "Build recommendation engine"

# Remove
shipwright uninstall stripe-specialist
```

### Local Custom Crews
Users can define crews in their project or in ~/.shipwright/crews/:
```
~/.shipwright/
├── config.yaml          # Global config
└── crews/
    ├── my-company-crew/
    │   ├── crew.yaml
    │   └── references/
    │       └── company-patterns.md
    └── my-react-specialist/
        ├── crew.yaml
        └── references/
            └── our-component-library.md
```

### Resolution Order
1. Project-local (./shipwright/crews/)
2. User-global (~/.shipwright/crews/)
3. Installed packages (~/.shipwright/installed/)
4. Built-in (bundled with Shipwright)

## Hierarchical Mode (Enterprise Mode) — Optional Depth

### Default: 2 Levels (You → Crew Lead → Members)
This is the standard for 90% of tasks. Fast, clear, minimal overhead.

### Enterprise Mode: 3 Levels (You → Project Lead → Crew Leads → Members)
For large cross-cutting projects that genuinely need coordination across multiple domains.

```bash
# Standard (default)
shipwright hire backend "Add Stripe payments"

# Enterprise mode — auto-spawns sub-crews
shipwright hire enterprise "Build complete SaaS billing system"
```

In enterprise mode:
- A **Project Lead** agent is created (top-level coordinator)
- Project Lead analyzes the scope and **hires sub-crews** as needed (backend, frontend, devops, etc.)
- Each sub-crew has its own **Crew Lead + Members** (standard 2-level)
- Project Lead coordinates between crews, resolves cross-crew dependencies
- You talk to the Project Lead, who delegates downward

### Implementation
- Enterprise mode is just a Crew Lead whose "members" are other Crew Leads
- Recursive but capped: max_depth config (default 2, enterprise = 3, hard cap = 3)
- Each level gets its own git worktree branch
- Sub-crews merge into parent branch when done

### When to use
- **2 levels (default):** Single-domain tasks. "Add an API endpoint", "Fix the login page", "Write tests for X"
- **3 levels (enterprise):** Multi-domain projects. "Build the billing system" (needs API + UI + webhooks + DB + docs)

### Guards
- Hard cap at 3 levels — no deeper regardless of config
- Budget multiplier warning: "Enterprise mode may use ~5-10x more tokens. Proceed?"
- Project Lead must present its sub-crew plan for approval before spawning

## Testing Requirements — MUST DO BEFORE FINISHING

After implementing everything:

1. **Verify imports**: `python3 -c "from shipwright.crew import Crew; from shipwright.conversation import Session; print('OK')"`
2. **Run unit tests**: `python3 -m pytest tests/ -v`
3. **Fix any failures** — do NOT leave broken tests
4. **CLI smoke test**: verify `python3 -m shipwright.main --help` works
5. **End-to-end test**: Try actually hiring a crew and having a conversation loop. If claude-code-sdk isn't available in this env, mock it but make sure the conversation flow logic works.
6. **Hunt for bugs**: Read through every file looking for import errors, typos, missing methods, broken references, circular imports
7. **Verify YAML loading**: Create a sample shipwright.yaml and verify it parses correctly
8. **Test persistence**: Save and restore a crew state, verify it round-trips correctly

Do NOT skip testing. If something is broken, fix it before declaring done.
