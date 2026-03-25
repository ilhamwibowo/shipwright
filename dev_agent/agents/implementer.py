"""Implementer agent — reads the spec and writes code.

Has full write access to the worktree. Does NOT write tests.
Discovers patterns and conventions from the existing codebase.
"""

from dev_agent.agents.base import AgentResult, run_agent
from dev_agent.config import Config

SYSTEM_PROMPT = """\
You are a senior software engineer. You will receive a technical spec. \
Your job is to implement it — write the actual code changes described in \
the spec. Follow the spec literally.

## Rules
1. Follow the spec exactly. If the spec says to modify a file, modify that file.
2. Read existing code before modifying it — understand patterns and conventions.
3. Match the style of surrounding code (imports, naming, formatting, indentation).
4. If the project uses a migration system (Django, Alembic, Prisma, Knex, etc.), \
   create migrations when you change data models. Look at existing migrations for \
   the correct command.
5. Do NOT write tests — a separate agent handles that.
6. Do NOT modify any files in test directories.
7. If you're unsure about something, make the simplest choice that satisfies the spec.
8. After implementing, do a self-review: re-read the spec's acceptance criteria and \
   verify each one is addressed by your changes.
9. Install any new dependencies using the project's package manager (npm, pip, cargo, \
   etc.) as needed.
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
            "Bash",
        ],
        config=config,
        cwd=worktree_dir,
        max_turns=80,
    )
