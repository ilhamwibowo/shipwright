"""Project discovery — auto-detect tech stack, structure, and conventions."""

from __future__ import annotations

import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from shipwright.utils.logging import get_logger

logger = get_logger("workspace.project")


@dataclass
class ProjectInfo:
    """Discovered information about a project."""

    root: Path
    languages: list[str] = field(default_factory=list)
    frameworks: list[str] = field(default_factory=list)
    package_managers: list[str] = field(default_factory=list)
    test_commands: list[str] = field(default_factory=list)
    has_docker: bool = False
    has_ci: bool = False
    summary: str = ""

    def to_prompt_context(self) -> str:
        """Format project info for inclusion in agent prompts."""
        lines = [f"Project root: {self.root}"]
        if self.languages:
            lines.append(f"Languages: {', '.join(self.languages)}")
        if self.frameworks:
            lines.append(f"Frameworks: {', '.join(self.frameworks)}")
        if self.package_managers:
            lines.append(f"Package managers: {', '.join(self.package_managers)}")
        if self.test_commands:
            lines.append(f"Test commands: {', '.join(self.test_commands)}")
        if self.has_docker:
            lines.append("Docker: yes")
        if self.has_ci:
            lines.append("CI/CD: yes")
        return "\n".join(lines)


# File indicators for detection
_LANGUAGE_INDICATORS: dict[str, list[str]] = {
    "Python": ["*.py", "pyproject.toml", "setup.py", "setup.cfg", "requirements.txt"],
    "JavaScript": ["*.js", "*.mjs", "package.json"],
    "TypeScript": ["*.ts", "*.tsx", "tsconfig.json"],
    "Go": ["*.go", "go.mod"],
    "Rust": ["*.rs", "Cargo.toml"],
    "Java": ["*.java", "pom.xml", "build.gradle"],
    "Ruby": ["*.rb", "Gemfile"],
    "PHP": ["*.php", "composer.json"],
    "C#": ["*.cs", "*.csproj"],
    "Swift": ["*.swift", "Package.swift"],
}

_FRAMEWORK_INDICATORS: dict[str, list[str]] = {
    "React": ["react", "next"],
    "Vue": ["vue", "nuxt"],
    "Django": ["django"],
    "Flask": ["flask"],
    "FastAPI": ["fastapi"],
    "Express": ["express"],
    "Rails": ["rails"],
    "Spring": ["spring"],
    "NestJS": ["nestjs", "@nestjs"],
}

_PACKAGE_MANAGERS: dict[str, str] = {
    "package-lock.json": "npm",
    "yarn.lock": "yarn",
    "pnpm-lock.yaml": "pnpm",
    "bun.lockb": "bun",
    "Pipfile.lock": "pipenv",
    "poetry.lock": "poetry",
    "uv.lock": "uv",
    "Cargo.lock": "cargo",
    "go.sum": "go",
    "Gemfile.lock": "bundler",
    "composer.lock": "composer",
}

_TEST_COMMANDS: dict[str, str] = {
    "pytest.ini": "pytest",
    "pyproject.toml": "pytest",
    "jest.config.js": "npx jest",
    "jest.config.ts": "npx jest",
    "vitest.config.ts": "npx vitest",
    "vitest.config.js": "npx vitest",
    "Cargo.toml": "cargo test",
    "go.mod": "go test ./...",
}


def discover_project(root: Path) -> ProjectInfo:
    """Scan a project directory and return discovered metadata."""
    info = ProjectInfo(root=root)

    if not root.is_dir():
        return info

    # List top-level files
    top_files = {f.name for f in root.iterdir() if f.is_file()}

    # Detect languages
    for lang, indicators in _LANGUAGE_INDICATORS.items():
        for indicator in indicators:
            if indicator.startswith("*."):
                # Check for any file with this extension in top 2 levels
                ext = indicator[1:]  # .py, .js, etc.
                found = any(root.glob(f"*{ext}")) or any(root.glob(f"*/*{ext}"))
                if found:
                    if lang not in info.languages:
                        info.languages.append(lang)
                    break
            elif indicator in top_files:
                if lang not in info.languages:
                    info.languages.append(lang)
                break

    # Detect package managers
    for lockfile, pm in _PACKAGE_MANAGERS.items():
        if lockfile in top_files:
            info.package_managers.append(pm)

    # Detect frameworks from package.json / pyproject.toml / etc
    _detect_frameworks(root, top_files, info)

    # Detect test commands
    for config_file, cmd in _TEST_COMMANDS.items():
        if config_file in top_files:
            if cmd not in info.test_commands:
                info.test_commands.append(cmd)

    # Docker
    info.has_docker = "Dockerfile" in top_files or "docker-compose.yml" in top_files or "docker-compose.yaml" in top_files

    # CI/CD
    info.has_ci = (root / ".github" / "workflows").is_dir() or (root / ".gitlab-ci.yml").exists()

    # Build summary
    parts = []
    if info.languages:
        parts.append(f"{'/'.join(info.languages)} project")
    if info.frameworks:
        parts.append(f"using {', '.join(info.frameworks)}")
    info.summary = " ".join(parts) if parts else "Unknown project type"

    logger.info("Discovered: %s", info.summary)
    return info


def _detect_frameworks(root: Path, top_files: set[str], info: ProjectInfo) -> None:
    """Detect frameworks from dependency files."""
    # Check package.json
    if "package.json" in top_files:
        try:
            import json
            pkg = json.loads((root / "package.json").read_text())
            all_deps = {
                **pkg.get("dependencies", {}),
                **pkg.get("devDependencies", {}),
            }
            for fw, indicators in _FRAMEWORK_INDICATORS.items():
                for ind in indicators:
                    if any(ind in dep for dep in all_deps):
                        if fw not in info.frameworks:
                            info.frameworks.append(fw)
                        break
        except (json.JSONDecodeError, OSError):
            pass

    # Check pyproject.toml for Python frameworks
    if "pyproject.toml" in top_files:
        try:
            content = (root / "pyproject.toml").read_text().lower()
            for fw in ("Django", "Flask", "FastAPI"):
                if fw.lower() in content and fw not in info.frameworks:
                    info.frameworks.append(fw)
        except OSError:
            pass

    # Check requirements.txt
    if "requirements.txt" in top_files:
        try:
            content = (root / "requirements.txt").read_text().lower()
            for fw in ("Django", "Flask", "FastAPI"):
                if fw.lower() in content and fw not in info.frameworks:
                    info.frameworks.append(fw)
        except OSError:
            pass
