"""Configuration loaded from environment variables and shipwright.yaml."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

load_dotenv()


@dataclass(frozen=True)
class MemberDef:
    """Definition of a crew member from config."""

    role: str
    prompt: str
    tools: list[str] = field(default_factory=lambda: ["Read", "Glob", "Grep"])
    max_turns: int = 50
    model: str | None = None


@dataclass(frozen=True)
class CrewDef:
    """Definition of a crew from config or built-in registry."""

    name: str
    lead_prompt: str
    members: dict[str, MemberDef] = field(default_factory=dict)
    model: str | None = None
    description: str = ""
    source: str = "builtin"


@dataclass(frozen=True)
class SpecialistDef:
    """A specialist that can be recruited into a crew or hired standalone."""

    name: str
    description: str
    member_def: MemberDef
    source: str = "plugin"
    source_path: Path | None = None


@dataclass(frozen=True)
class Config:
    # Telegram
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""
    telegram_allowed_users: str = ""

    # Discord
    discord_bot_token: str = ""
    discord_channel_id: str = ""

    repo_root: Path = field(default_factory=lambda: Path.cwd())

    max_fix_attempts: int = 3
    model: str = "claude-sonnet-4-6"
    permission_mode: str = "bypassPermissions"

    # Custom crew defs from shipwright.yaml
    custom_crews: dict[str, CrewDef] = field(default_factory=dict)

    # Specialists loaded from plugin directories
    custom_specialists: dict[str, SpecialistDef] = field(default_factory=dict)

    @property
    def data_dir(self) -> Path:
        return self.repo_root / ".shipwright"

    @property
    def state_dir(self) -> Path:
        return self.data_dir / "state"


def _parse_crew_defs(raw: dict[str, Any]) -> dict[str, CrewDef]:
    """Parse crew definitions from a YAML dict."""
    crews: dict[str, CrewDef] = {}
    for name, cdef in raw.items():
        members: dict[str, MemberDef] = {}
        for mname, mdef in cdef.get("members", {}).items():
            members[mname] = MemberDef(
                role=mdef.get("role", mname),
                prompt=mdef.get("prompt", ""),
                tools=mdef.get("tools", ["Read", "Glob", "Grep"]),
                max_turns=mdef.get("max_turns", 50),
                model=mdef.get("model"),
            )
        crews[name] = CrewDef(
            name=name,
            lead_prompt=cdef.get("lead", f"You are the {name} crew lead."),
            members=members,
            model=cdef.get("model"),
        )
    return crews


def _load_yaml_config(repo_root: Path) -> dict[str, CrewDef]:
    """Load crew definitions from shipwright.yaml if it exists."""
    yaml_path = repo_root / "shipwright.yaml"
    if not yaml_path.exists():
        yaml_path = repo_root / "shipwright.yml"
    if not yaml_path.exists():
        return {}

    try:
        import yaml
    except ImportError:
        # PyYAML is optional — fall back silently
        return {}

    raw = yaml.safe_load(yaml_path.read_text())
    if not raw or not isinstance(raw, dict):
        return {}

    crews = _parse_crew_defs(raw.get("crews", {}))
    # Mark source as "yaml"
    for name, cdef in crews.items():
        crews[name] = CrewDef(
            name=cdef.name,
            lead_prompt=cdef.lead_prompt,
            members=cdef.members,
            model=cdef.model,
            description=cdef.description,
            source="yaml",
        )
    return crews


def _load_references(references_dir: Path) -> str:
    """Load all .md files from a references/ directory into a single string."""
    if not references_dir.is_dir():
        return ""

    parts: list[str] = []
    for md_file in sorted(references_dir.glob("*.md")):
        try:
            content = md_file.read_text().strip()
            if content:
                parts.append(f"### {md_file.stem}\n\n{content}")
        except OSError:
            continue

    if not parts:
        return ""
    return "## Reference Documents\n\n" + "\n\n---\n\n".join(parts)


def _load_plugin_yaml(plugin_dir: Path) -> dict[str, Any] | None:
    """Load and parse a crew.yaml from a plugin directory."""
    yaml_path = plugin_dir / "crew.yaml"
    if not yaml_path.exists():
        yaml_path = plugin_dir / "crew.yml"
    if not yaml_path.exists():
        return None

    try:
        import yaml
    except ImportError:
        return None

    try:
        raw = yaml.safe_load(yaml_path.read_text())
        if not raw or not isinstance(raw, dict):
            return None
        return raw
    except Exception:
        return None


def _load_plugin_crew(
    plugin_dir: Path, raw: dict[str, Any], source: str,
) -> CrewDef:
    """Load a crew definition from a plugin directory's crew.yaml."""
    refs_content = ""
    refs_dir = plugin_dir / "references"
    if raw.get("references", False) or refs_dir.is_dir():
        refs_content = _load_references(refs_dir)

    members: dict[str, MemberDef] = {}
    for mname, mdef in raw.get("members", {}).items():
        prompt = mdef.get("prompt", "")
        if refs_content:
            prompt = f"{refs_content}\n\n---\n\n{prompt}"
        members[mname] = MemberDef(
            role=mdef.get("role", mname),
            prompt=prompt,
            tools=mdef.get("tools", ["Read", "Glob", "Grep"]),
            max_turns=mdef.get("max_turns", 50),
            model=mdef.get("model"),
        )

    return CrewDef(
        name=raw.get("name", plugin_dir.name),
        lead_prompt=raw.get("lead", f"You are the {plugin_dir.name} crew lead."),
        members=members,
        model=raw.get("model"),
        description=raw.get("description", ""),
        source=source,
    )


def _load_plugin_specialist(
    plugin_dir: Path, raw: dict[str, Any], source: str,
) -> SpecialistDef:
    """Load a specialist definition from a plugin directory's crew.yaml."""
    refs_content = ""
    refs_dir = plugin_dir / "references"
    if raw.get("references", False) or refs_dir.is_dir():
        refs_content = _load_references(refs_dir)

    prompt = raw.get("prompt", "")
    if refs_content:
        prompt = f"{refs_content}\n\n---\n\n{prompt}"

    member_def = MemberDef(
        role=raw.get("role", raw.get("name", plugin_dir.name)),
        prompt=prompt,
        tools=raw.get("tools", ["Read", "Glob", "Grep"]),
        max_turns=raw.get("max_turns", 50),
        model=raw.get("model"),
    )

    return SpecialistDef(
        name=raw.get("name", plugin_dir.name),
        description=raw.get("description", ""),
        member_def=member_def,
        source=source,
        source_path=plugin_dir,
    )


def _scan_plugin_dir(
    base_dir: Path,
    source: str,
    crews: dict[str, CrewDef],
    specialists: dict[str, SpecialistDef],
) -> None:
    """Scan a directory for plugin crews and specialists.

    Only adds entries that don't already exist (preserves resolution order).
    """
    if not base_dir.is_dir():
        return

    for plugin_dir in sorted(base_dir.iterdir()):
        if not plugin_dir.is_dir():
            continue

        raw = _load_plugin_yaml(plugin_dir)
        if raw is None:
            continue

        kind = raw.get("kind", "crew")
        name = raw.get("name", plugin_dir.name)

        if kind == "specialist":
            if name not in specialists:
                specialists[name] = _load_plugin_specialist(plugin_dir, raw, source)
        else:
            if name not in crews:
                crews[name] = _load_plugin_crew(plugin_dir, raw, source)


def _scan_all_plugin_dirs(
    repo_root: Path,
) -> tuple[dict[str, CrewDef], dict[str, SpecialistDef]]:
    """Scan all plugin directories in resolution order.

    Resolution order (first wins):
    1. Project-local: ./shipwright/crews/
    2. User-global: ~/.shipwright/crews/
    """
    crews: dict[str, CrewDef] = {}
    specialists: dict[str, SpecialistDef] = {}

    # 1. Project-local
    _scan_plugin_dir(repo_root / "shipwright" / "crews", "project", crews, specialists)

    # 2. User-global
    _scan_plugin_dir(Path.home() / ".shipwright" / "crews", "user", crews, specialists)

    return crews, specialists


def load_config() -> Config:
    repo_root = Path(os.environ.get("REPO_ROOT", Path.cwd()))

    # Load from shipwright.yaml
    yaml_crews = _load_yaml_config(repo_root)

    # Scan plugin directories
    plugin_crews, plugin_specialists = _scan_all_plugin_dirs(repo_root)

    # Merge: yaml crews take priority over plugin crews
    merged_crews = {**plugin_crews, **yaml_crews}

    return Config(
        telegram_bot_token=os.environ.get("TELEGRAM_BOT_TOKEN", ""),
        telegram_chat_id=os.environ.get("TELEGRAM_CHAT_ID", ""),
        telegram_allowed_users=os.environ.get("TELEGRAM_ALLOWED_USERS", ""),
        discord_bot_token=os.environ.get("DISCORD_BOT_TOKEN", ""),
        discord_channel_id=os.environ.get("DISCORD_CHANNEL_ID", ""),
        repo_root=repo_root,
        max_fix_attempts=int(os.environ.get("MAX_FIX_ATTEMPTS", "3")),
        model=os.environ.get("SHIPWRIGHT_MODEL", "claude-sonnet-4-6"),
        permission_mode=os.environ.get("SHIPWRIGHT_PERMISSION_MODE", "bypassPermissions"),
        custom_crews=merged_crews,
        custom_specialists=plugin_specialists,
    )
