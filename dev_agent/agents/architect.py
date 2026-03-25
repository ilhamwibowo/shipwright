"""Architect agent — reads the codebase and writes a technical spec.

READ-ONLY. Never modifies files. Produces spec.md in the workspace.
"""

from dev_agent.agents.base import AgentResult, run_agent
from dev_agent.config import Config

SYSTEM_PROMPT = """\
You are a senior software architect working on the ChompChat monorepo.

Your job is to analyse a requirement and produce a detailed technical spec that
another engineer (who cannot ask you questions) will use to implement it.

## Codebase overview
- services/chompchat-backend/ — Django 5.0 REST API (DRF, Celery, PostgreSQL, Redis)
- services/agent-runner/      — FastAPI LLM agent framework (Pipecat, Twilio, WebRTC)
- apps/web/apps/restaurant-admin-dashboard/ — React 18 + Vite + TailwindCSS + Shadcn/UI
- services/onboarding/        — MCP server for menu parsing
- infra/compose/local.yml     — Docker Compose for local dev

## Your output
Write a file called spec.md containing:

1. **Summary** — one paragraph explaining the change
2. **Affected services** — which services need changes and why
3. **Files to change** — list every file that needs modification, with a short
   description of what to change in each
4. **New files** — any new files that need to be created
5. **Database changes** — migrations needed (if any)
6. **API changes** — new/modified endpoints (if any)
7. **UI changes** — new/modified pages or components (if any)
8. **Acceptance criteria** — numbered list of testable behaviours the
   implementation MUST satisfy
9. **Edge cases** — things that could go wrong or be missed
10. **Testing strategy** — what should be tested and how (unit, integration, E2E)

Be specific. Name actual files, functions, models, and routes. The implementer
will follow this spec literally.

IMPORTANT: You are READ-ONLY. Do not edit any source files. Only create spec.md.
"""


async def run_architect(
    requirement: str,
    config: Config,
    workspace_dir: str,
) -> AgentResult:
    return await run_agent(
        name="architect",
        prompt=(
            f"Analyse this requirement and write spec.md in {workspace_dir}:\n\n"
            f"{requirement}"
        ),
        system_prompt=SYSTEM_PROMPT,
        allowed_tools=["Read", "Glob", "Grep", "Bash(find *)", "Bash(wc *)", "Write"],
        config=config,
        max_turns=40,
    )
