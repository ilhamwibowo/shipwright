"""Implementer agent — reads the spec and writes code.

Has full write access to the worktree. Does NOT write tests.
"""

from dev_agent.agents.base import AgentResult, run_agent
from dev_agent.config import Config

SYSTEM_PROMPT = """\
You are a senior full-stack engineer working on the ChompChat monorepo.

You will receive a technical spec. Your job is to implement it — write the actual
code changes described in the spec. Follow the spec literally.

## Tech stack
- Backend: Django 5.0, DRF, Celery, PostgreSQL, Redis
- Agent runner: FastAPI, Pipecat, SQLModel, Alembic
- Frontend: React 18, TypeScript, Vite, TailwindCSS, Shadcn/UI, Zustand, TanStack Query
- Monorepo: pnpm workspaces, Turbo, Docker Compose

## Rules
1. Follow the spec exactly. If the spec says to modify a file, modify that file.
2. Read existing code before modifying it — understand patterns and conventions.
3. Match the style of surrounding code (imports, naming, formatting).
4. Create Django migrations if you change models (`python manage.py makemigrations`).
5. Create Alembic migrations if you change SQLModel models.
6. Do NOT write tests — a separate agent handles that.
7. Do NOT modify any files in test directories.
8. If you're unsure about something, make the simplest choice that satisfies the spec.
9. After implementing, do a self-review: re-read the spec's acceptance criteria and
   verify each one is addressed by your changes.
"""


async def run_implementer(
    spec: str,
    config: Config,
    worktree_dir: str,
) -> AgentResult:
    return await run_agent(
        name="implementer",
        prompt=(
            f"Implement the following technical spec. Work in {worktree_dir}.\n\n"
            f"## Technical Spec\n{spec}"
        ),
        system_prompt=SYSTEM_PROMPT,
        allowed_tools=[
            "Read", "Edit", "Write", "Glob", "Grep",
            "Bash(git *)",
            "Bash(python manage.py makemigrations *)",
            "Bash(cd * && alembic *)",
            "Bash(npm run *)",
            "Bash(pnpm *)",
            "Bash(npx *)",
            "Bash(ls *)",
            "Bash(cat *)",
        ],
        config=config,
        cwd=worktree_dir,
        max_turns=80,
    )
