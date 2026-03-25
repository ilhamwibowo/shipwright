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

    return _parse_crew_defs(raw.get("crews", {}))


def load_config() -> Config:
    repo_root = Path(os.environ.get("REPO_ROOT", Path.cwd()))
    custom_crews = _load_yaml_config(repo_root)

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
        custom_crews=custom_crews,
    )
