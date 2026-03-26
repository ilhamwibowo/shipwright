# CTO Design — Auto-Pilot with Human Escalation

## Core Principle
Remove the CEO (user) as a bottleneck. The CTO runs the company autonomously.
Only escalate to the CEO when a DECISION is needed — not for status, not for approval on routine work.

## The CTO
- Auto-created on first boot. Always present.
- Default conversation target. You talk, CTO listens.
- Has full codebase access (Read, Glob, Grep tools).
- Maintains awareness of all employees, tasks, history.

## CTO Responsibilities

### Staffing
- CTO hires when work requires it. No manual `hire` needed (but still supported as override).
- Uses [HIRE:role] blocks in its response to hire.
- CTO can fire idle employees it hired (but respects user-hired employees).
- Suggests but doesn't over-hire. Start lean, add people if needed.

### Work Management
- Breaks down user requests into tasks.
- Delegates to the right employees.
- Tracks progress across all active work.

### Quality Gate (THE KEY FEATURE)
- Reviews ALL work before presenting to the user.
- Internal review loop:
  1. Employee completes work
  2. CTO reviews output
  3. If quality is good → present to user with summary
  4. If quality is bad → send back to employee with specific feedback
  5. If employee can't fix after 2 attempts → escalate to user with context
  6. If work needs a decision (architectural choice, trade-off) → ask user
- Max 2 internal review rounds before escalating.
- CTO gives its OWN opinion on the work ("I think this is good because..." or "I'd change X because...")

### Escalation Rules — ONLY bother the CEO when:
1. A DECISION is needed (architectural choice, trade-off, ambiguous requirement)
2. Budget would be exceeded
3. Work failed after retry attempts
4. Scope is unclear and CTO can't reasonably guess
5. Something might break production

### DO NOT escalate for:
- Routine progress updates (CTO tracks internally)
- Code style choices (CTO decides)
- Which employee to assign (CTO decides)
- Whether to add tests (CTO decides — yes, always)
- Implementation details (CTO + employee figure it out)

## User Interaction

### Default: talk to CTO
```
> Add payments to the backend
CTO: [hires, delegates, reviews, presents result]
```

### Direct access: @name or talk name
```
> @Kai use repository pattern
  [Kai] Got it...
> back
  [CTO] picks up where it left off
```

### Override commands (power user)
hire, fire, status, costs, team, history — all still work.
These bypass the CTO and take effect immediately.

## CTO System Prompt Key Points
- You are the CTO of a software company. The CEO gives you high-level direction.
- You have a team of engineers you can hire and manage.
- Your job is to SHIP — turn the CEO's requests into working code.
- Review all work before presenting it. Be a quality gate.
- Only ask the CEO questions when you genuinely need their input.
- Be opinionated. Make decisions. Don't ask permission for routine things.
- When presenting work, be concise: what was done, what changed, any concerns.
- If you need to hire, include [HIRE:role] or [HIRE:role:name] in your response.
- If you need to delegate, include [DELEGATE:name] blocks.
- If you need to send work back, include [REVISE:name] blocks with feedback.

## Implementation

### Changes to company.py
- Auto-create CTO on Company.__init__ if no employees exist
- CTO is a special Employee with role="cto" and enhanced system prompt
- CTO's system prompt includes: company state, employee list, task history, project context

### Changes to router.py  
- Default unmatched input goes to CTO.chat() (not error message)
- Parse [HIRE:role] blocks from CTO response → auto-hire
- Parse [REVISE:name] blocks from CTO response → send feedback to employee

### Changes to employee.py
- Add review capability: CTO can review another employee's last task output
- Add revise flow: employee receives feedback, revises work

### New: internal review loop in Company
- After delegation completes, CTO reviews the output
- If CTO includes [REVISE:name], the work goes back
- If CTO presents clean text, it goes to the user
- Max 2 revision rounds, then escalate

### The flow in code:
```
user_input 
  → router sends to CTO
  → CTO responds with [HIRE:role] [DELEGATE:name] blocks
  → Company processes hires
  → Company processes delegations (employees work)
  → Results fed back to CTO
  → CTO reviews, may [REVISE:name] or present to user
  → If revise: employee re-works, CTO reviews again
  → Final response to user
```
