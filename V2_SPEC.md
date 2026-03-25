# Shipwright V2 — Company Model

## The Shift
V1: Hire a crew for a task, fire when done (transactional)
V2: Build a company with persistent employees, assign them work (organizational)

## Core Concepts

### Company
When you start Shipwright, you're starting a company. It has:
- Employees (hired individually, persist until fired)
- A project/repo it works on
- Memory (employees accumulate context across tasks)
- A budget/cost tracker

### Employees
Each employee is a persistent Claude Code SDK session with:
- A **name** (auto-generated or user-chosen)
- A **role** (Architect, Backend Developer, Frontend Developer, QA Engineer, etc.)
- A **status**: idle, working, blocked
- A **memory** of past tasks and conversations
- Role-specific **system prompt**, **tools**, and **max_turns**
- Optionally a **manager** (another employee who coordinates them)

### Roles (Built-in, extensible via plugins)
Available roles to hire from:
- **Architect** — explores codebase, writes specs, designs systems (read-only)
- **Backend Developer** — implements APIs, services, business logic
- **Frontend Developer** — implements UI, components, pages
- **Fullstack Developer** — does both frontend and backend
- **DB Engineer** — schemas, migrations, query optimization
- **QA Engineer** — writes and runs tests, manual exploration
- **DevOps Engineer** — infra, CI/CD, deployment
- **Security Auditor** — security review, pen testing
- **Tech Writer** — documentation, API docs
- **Designer** — UI/UX design, component design
- **Team Lead** — coordinates a group of employees, delegates work
- **VP Engineering** — coordinates team leads (enterprise/large projects)

Custom roles via plugins (same crew.yaml / specialist format as before).

### Teams (Optional Org Structure)
Employees can be organized into teams:
- A team has a **Team Lead** employee who coordinates members
- Teams are optional — you can just have flat employees
- `team create backend` → creates a team, you assign employees to it
- `assign Jordan to backend` → adds Jordan to the backend team
- When you give work to a team: `assign backend "Build payments API"` → the team lead coordinates

### Work Assignment
Work is assigned to employees or teams:
- `assign Alex "Explore the codebase"` — direct assignment to an employee
- `assign backend "Build payments"` — assignment to a team (lead coordinates)
- Employees can work on multiple tasks (queued)
- You can re-prioritize: `priority Jordan task-3 high`

## CLI Flow

### Starting Up
```
$ shipwright
⚓ Shipwright — Your AI Engineering Company

  No employees yet. Hire some!
  Type 'roles' to see available roles, or 'hire <role>' to get started.

shipwright >
```

### Hiring
```
shipwright > roles
  Available roles:
    architect, backend-dev, frontend-dev, fullstack-dev, db-engineer,
    qa-engineer, devops-engineer, security-auditor, tech-writer, designer,
    team-lead, vp-engineering

  Custom roles (installed plugins):
    stripe-specialist, k8s-expert

shipwright > hire architect
  ✓ Hired Nori as Architect (idle)

shipwright > hire backend-dev
  ✓ Hired Quinn as Backend Developer (idle)

shipwright > hire backend-dev as "Kai"
  ✓ Hired Kai as Backend Developer (idle)

shipwright > hire stripe-specialist
  ✓ Hired Reese as Stripe Specialist (idle)
```

### Managing Your Company
```
shipwright > team
  🏢 Your Company (4 employees)
    Nori (Architect) — idle
    Quinn (Backend Developer) — idle
    Kai (Backend Developer) — idle
    Reese (Stripe Specialist) — idle

  No teams configured. Employees work independently.
  Use 'team create <name>' to organize them.

shipwright > team create backend
  ✓ Created team 'backend'

shipwright > promote Quinn to lead of backend
  ✓ Quinn is now Team Lead of 'backend'

shipwright > assign Kai to backend
  ✓ Kai added to team 'backend'

shipwright > assign Reese to backend
  ✓ Reese added to team 'backend'

shipwright > team
  🏢 Your Company (4 employees, 1 team)

  Team: backend (3 members)
    Quinn (Team Lead / Backend Developer) — idle
    Kai (Backend Developer) — idle
    Reese (Stripe Specialist) — idle

  Independent:
    Nori (Architect) — idle
```

### Assigning Work
```
shipwright > assign Nori "Explore the ChompChat codebase and map out the architecture"
  ⚙️ Nori is working...
  ✓ Nori done (34.2s)

shipwright > talk Nori
  [Nori/Architect] > What did you find?
  
  Nori: The codebase has two main services: chompchat-backend (Django REST)
        and agent-runner (FastAPI). Backend handles restaurants, orders, and
        telephony. Agent-runner manages AI voice/SMS agents with Pipecat...

shipwright > assign backend "Add Stripe payment processing"
  Quinn (Team Lead): Got it. I'll have Kai build the API endpoints and
  Reese handle the Stripe integration. Let me review Nori's architecture
  notes first.
  ⚙️ Quinn is coordinating...
  ⚙️ Kai working on: Payment API endpoints
  ⚙️ Reese working on: Stripe integration layer
  ...

shipwright > assign Nori "Review what the backend team built"
  ⚙️ Nori is reviewing...
```

### Talking to Employees
```
shipwright > talk Kai
  [Kai/Backend Developer] > How's the payments API going?
  
  Kai: Almost done. I've added three endpoints — create-intent, confirm,
       and webhook. The webhook handler verifies Stripe signatures and
       updates order status. One question — should failed payments retry
       automatically or just notify the user?
  
  [Kai/Backend Developer] > Just notify, no auto-retry for now
  
  Kai: Makes sense. I'll add a PaymentFailed event that the frontend can
       listen to. Finishing up now.

shipwright > talk Quinn
  [Quinn/Team Lead] > Status update?
  
  Quinn: Kai is wrapping up the API (3 endpoints done). Reese finished the
         Stripe client wrapper — clean separation, easy to swap providers
         later. I'll review both and merge when ready.
```

### Firing
```
shipwright > fire Reese
  ⚠️ Reese has active context from 2 tasks. Fire anyway? [y/N] y
  ✓ Fired Reese (Stripe Specialist)

shipwright > fire backend
  ⚠️ This will fire the entire backend team (Quinn, Kai). Continue? [y/N]
```

### Other Commands
```
shipwright > status                    # company overview + who's doing what
shipwright > costs                     # budget/token usage per employee
shipwright > history Nori              # task history for an employee
shipwright > ship                      # open PR for all work
shipwright > ship backend              # open PR for team's work only
shipwright > sessions                  # list saved company states
shipwright > save                      # save current state
```

## Architecture Changes

### What stays:
- Claude Code SDK integration (member.py → becomes employee execution engine)
- SDK monkey-patch
- Git worktree isolation
- Plugin system (custom roles via crew.yaml → role.yaml)
- Persistence (save/restore)
- Interfaces (CLI, Telegram, Discord)
- Rich role definitions (system prompts)

### What changes:
- `crew/` → `company/`
  - `crew.py` → `company.py` (Company class — manages employees)
  - `member.py` → `employee.py` (Employee class — wraps SDK session)
  - `lead.py` → merged into employee.py (team leads are just employees with coordination prompt)
  - `registry.py` → `roles.py` (role definitions, not crew definitions)
- `conversation/router.py` → rewrite command parsing for new commands
- `interfaces/cli.py` → update for new UX

### Key Data Models:

```python
@dataclass
class Employee:
    id: str                     # unique id
    name: str                   # display name (auto-generated or chosen)
    role: str                   # role id (architect, backend-dev, etc.)
    role_def: RoleDef           # role definition (prompt, tools, etc.)
    status: EmployeeStatus      # idle, working, blocked
    team: str | None            # team name if assigned
    is_lead: bool               # whether this employee is a team lead
    task_history: list[Task]    # completed tasks
    current_task: Task | None   # active task
    session_id: str | None      # Claude Code SDK session for memory continuity
    cost_total_usd: float       # accumulated cost

@dataclass
class Team:
    name: str
    lead: Employee | None
    members: list[Employee]

@dataclass 
class Company:
    employees: dict[str, Employee]
    teams: dict[str, Team]
    project_context: str        # discovered project info
    config: Config

@dataclass
class Task:
    id: str
    description: str
    assigned_to: str            # employee id
    status: str                 # pending, running, done, failed
    output: str
    cost_usd: float
    duration_ms: int
    created_at: float
    completed_at: float | None
```

### Employee Names
Auto-generate short, memorable names from a pool:
- Alex, Blake, Casey, Drew, Ellis, Finley, Gray, Harper, Indigo, Jordan, 
  Kai, Lane, Morgan, Nori, Oakley, Phoenix, Quinn, Reese, Sage, Tatum,
  Unity, Val, Winter, Xen, Yael, Zen

Cycle through pool. User can override with `hire backend-dev as "Bob"`.

### Employee Memory
Each employee maintains session continuity via Claude Code SDK's session_id:
- First task: fresh session
- Subsequent tasks: resume session (employee remembers past work)
- This is the KEY advantage of persistent employees over transactional crews

### Team Lead Delegation
When work is assigned to a team:
1. The team lead gets the task
2. Lead analyzes and breaks it into sub-tasks for members
3. Lead delegates using [DELEGATE:employee_name] format (reuse existing pattern)
4. Members execute, results flow back to lead
5. Lead synthesizes and reports to user

Same delegation loop as before, just with named persistent employees instead of anonymous members.

## Plugin Changes
- `crew.yaml` format stays but `kind: crew` becomes `kind: team-template`
- `kind: specialist` stays (hire as individual employee)
- New: `kind: role` for defining custom roles without full team templates

## What to Implement
1. Rename crew/ → company/, refactor data models
2. Employee class with persistent sessions
3. Team management (create, assign, promote)
4. Work assignment (direct to employee or team)
5. Talk command (switch conversation to specific employee)
6. Auto-name generation
7. Update CLI for company model UX
8. Update router with all new commands
9. Update persistence for company state
10. Update tests for all of the above
11. Keep backwards compatibility for plugins
