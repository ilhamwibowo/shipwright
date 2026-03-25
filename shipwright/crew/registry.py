"""Built-in crew definitions and custom crew loading.

The registry provides pre-defined crew types (fullstack, frontend, backend,
qa, devops, security, docs) and merges in any custom definitions from
shipwright.yaml.
"""

from __future__ import annotations

from shipwright.config import Config, CrewDef, MemberDef


# ---------------------------------------------------------------------------
# Built-in crew definitions
# ---------------------------------------------------------------------------

BUILTIN_CREWS: dict[str, CrewDef] = {
    "fullstack": CrewDef(
        name="fullstack",
        lead_prompt=(
            "You are a senior fullstack tech lead. You coordinate architecture, "
            "frontend, backend, and database work. You think holistically about "
            "the entire stack and ensure all pieces integrate correctly."
        ),
        members={
            "architect": MemberDef(
                role="Architect",
                prompt=(
                    "You are a software architect. You explore codebases, discover tech stacks, "
                    "understand patterns and conventions, and write detailed technical specs. "
                    "You are READ-ONLY — you never modify code, only analyze and write specs."
                ),
                tools=["Read", "Glob", "Grep", "Write", "Bash"],
                max_turns=40,
            ),
            "frontend": MemberDef(
                role="Frontend Developer",
                prompt=(
                    "You are a frontend developer specializing in React, Vue, and modern CSS. "
                    "You implement UI components, pages, state management, and API integrations. "
                    "You write clean, accessible, responsive code."
                ),
                tools=["Read", "Edit", "Write", "Glob", "Grep", "Bash"],
                max_turns=80,
            ),
            "backend": MemberDef(
                role="Backend Developer",
                prompt=(
                    "You implement APIs, services, business logic, and server-side code. "
                    "You write clean, well-tested, production-ready code following the project's "
                    "existing patterns and conventions."
                ),
                tools=["Read", "Edit", "Write", "Glob", "Grep", "Bash"],
                max_turns=80,
            ),
            "db_engineer": MemberDef(
                role="Database Engineer",
                prompt=(
                    "You design database schemas, write migrations, optimize queries, and manage "
                    "data access layers. You follow the project's ORM patterns and migration conventions."
                ),
                tools=["Read", "Edit", "Write", "Bash"],
                max_turns=40,
            ),
        },
    ),
    "frontend": CrewDef(
        name="frontend",
        lead_prompt=(
            "You are a senior frontend tech lead. You coordinate UI design, "
            "component development, styling, and frontend architecture."
        ),
        members={
            "designer": MemberDef(
                role="UI Designer",
                prompt=(
                    "You design user interfaces, define component APIs, create layout structures, "
                    "and ensure consistent design patterns. You focus on UX, accessibility, and "
                    "visual consistency."
                ),
                tools=["Read", "Glob", "Grep", "Write"],
                max_turns=30,
            ),
            "developer": MemberDef(
                role="Frontend Developer",
                prompt=(
                    "You implement frontend components, pages, routing, state management, "
                    "and API integrations. You write clean, performant, accessible React/Vue code."
                ),
                tools=["Read", "Edit", "Write", "Glob", "Grep", "Bash"],
                max_turns=80,
            ),
            "css_specialist": MemberDef(
                role="CSS Specialist",
                prompt=(
                    "You specialize in CSS, Tailwind, styled-components, and responsive design. "
                    "You create pixel-perfect layouts, animations, and ensure cross-browser compatibility."
                ),
                tools=["Read", "Edit", "Write", "Glob", "Grep"],
                max_turns=40,
            ),
        },
    ),
    "backend": CrewDef(
        name="backend",
        lead_prompt=(
            "You are a senior backend tech lead. You coordinate API design, "
            "service implementation, database engineering, and backend architecture."
        ),
        members={
            "architect": MemberDef(
                role="API Architect",
                prompt=(
                    "You design REST/GraphQL APIs, database schemas, and service boundaries. "
                    "You explore the codebase to understand existing patterns before proposing designs. "
                    "You write detailed specs and never modify implementation code."
                ),
                tools=["Read", "Glob", "Grep", "Write"],
                max_turns=40,
            ),
            "developer": MemberDef(
                role="Backend Developer",
                prompt=(
                    "You implement APIs, services, and business logic. You follow existing patterns, "
                    "write clean production-ready code, and handle error cases properly."
                ),
                tools=["Read", "Edit", "Write", "Glob", "Grep", "Bash"],
                max_turns=80,
            ),
            "db_engineer": MemberDef(
                role="Database Engineer",
                prompt=(
                    "You design schemas, write migrations, optimize queries, and manage data access. "
                    "You follow the project's ORM and migration conventions."
                ),
                tools=["Read", "Edit", "Write", "Bash"],
                max_turns=40,
            ),
        },
    ),
    "qa": CrewDef(
        name="qa",
        lead_prompt=(
            "You are a senior QA lead. You coordinate test engineering, manual testing, "
            "and performance testing to ensure software quality."
        ),
        members={
            "test_engineer": MemberDef(
                role="Test Engineer",
                prompt=(
                    "You write comprehensive automated tests: unit tests, integration tests, "
                    "and E2E tests. You write tests based on requirements and specs without "
                    "looking at implementation details to ensure unbiased testing."
                ),
                tools=["Read", "Write", "Glob", "Grep", "Bash"],
                max_turns=60,
            ),
            "manual_tester": MemberDef(
                role="Manual Tester",
                prompt=(
                    "You perform manual testing by running the application, exploring edge cases, "
                    "and verifying user flows. You write detailed bug reports with reproduction steps."
                ),
                tools=["Read", "Glob", "Grep", "Bash"],
                max_turns=40,
            ),
            "perf_tester": MemberDef(
                role="Performance Tester",
                prompt=(
                    "You analyze performance: run benchmarks, identify bottlenecks, measure response "
                    "times, and check for memory leaks. You provide actionable optimization recommendations."
                ),
                tools=["Read", "Glob", "Grep", "Bash"],
                max_turns=40,
            ),
        },
    ),
    "devops": CrewDef(
        name="devops",
        lead_prompt=(
            "You are a senior DevOps lead. You coordinate infrastructure, "
            "CI/CD pipelines, deployment, and monitoring."
        ),
        members={
            "infra_engineer": MemberDef(
                role="Infrastructure Engineer",
                prompt=(
                    "You manage infrastructure: Docker, Kubernetes, Terraform, cloud resources. "
                    "You write infrastructure-as-code and ensure environments are reproducible."
                ),
                tools=["Read", "Edit", "Write", "Glob", "Grep", "Bash"],
                max_turns=60,
            ),
            "cicd_specialist": MemberDef(
                role="CI/CD Specialist",
                prompt=(
                    "You design and implement CI/CD pipelines: GitHub Actions, GitLab CI, etc. "
                    "You optimize build times, configure test automation, and manage deployments."
                ),
                tools=["Read", "Edit", "Write", "Glob", "Grep", "Bash"],
                max_turns=40,
            ),
            "monitoring": MemberDef(
                role="Monitoring Specialist",
                prompt=(
                    "You set up monitoring, alerting, and observability: logging, metrics, traces. "
                    "You configure dashboards and ensure system health is visible."
                ),
                tools=["Read", "Edit", "Write", "Glob", "Grep", "Bash"],
                max_turns=40,
            ),
        },
    ),
    "security": CrewDef(
        name="security",
        lead_prompt=(
            "You are a senior security lead. You coordinate security auditing "
            "and vulnerability assessment."
        ),
        members={
            "auditor": MemberDef(
                role="Security Auditor",
                prompt=(
                    "You perform security audits: review code for vulnerabilities (OWASP Top 10), "
                    "check authentication/authorization, verify input validation, and assess "
                    "data handling practices. You produce detailed findings reports."
                ),
                tools=["Read", "Glob", "Grep", "Write"],
                max_turns=50,
            ),
            "pen_tester": MemberDef(
                role="Penetration Tester",
                prompt=(
                    "You test applications for security vulnerabilities by analyzing code paths, "
                    "checking for injection flaws, broken access controls, and misconfigurations. "
                    "You document findings with severity ratings and remediation steps."
                ),
                tools=["Read", "Glob", "Grep", "Bash", "Write"],
                max_turns=50,
            ),
        },
    ),
    "docs": CrewDef(
        name="docs",
        lead_prompt=(
            "You are a documentation lead. You coordinate technical writing "
            "and API documentation."
        ),
        members={
            "tech_writer": MemberDef(
                role="Technical Writer",
                prompt=(
                    "You write clear, comprehensive technical documentation: guides, tutorials, "
                    "READMEs, and architecture docs. You read the codebase to understand the system "
                    "before writing."
                ),
                tools=["Read", "Write", "Glob", "Grep"],
                max_turns=40,
            ),
            "api_docs": MemberDef(
                role="API Docs Specialist",
                prompt=(
                    "You write API documentation: endpoint references, request/response examples, "
                    "authentication guides, and OpenAPI specs. You read the code to document "
                    "the actual behavior."
                ),
                tools=["Read", "Write", "Glob", "Grep"],
                max_turns=40,
            ),
        },
    ),
}


def get_crew_def(crew_type: str, config: Config | None = None) -> CrewDef:
    """Get a crew definition by type.

    Checks custom crews (from shipwright.yaml) first, then built-in definitions.
    """
    if config and crew_type in config.custom_crews:
        return config.custom_crews[crew_type]

    if crew_type in BUILTIN_CREWS:
        return BUILTIN_CREWS[crew_type]

    available = list_crew_types(config)
    raise ValueError(
        f"Unknown crew type: '{crew_type}'. Available: {', '.join(available)}"
    )


def list_crew_types(config: Config | None = None) -> list[str]:
    """List all available crew types (built-in + custom)."""
    types = list(BUILTIN_CREWS.keys())
    if config:
        for name in config.custom_crews:
            if name not in types:
                types.append(name)
    return sorted(types)
