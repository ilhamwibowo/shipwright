"""Centralised configuration loaded from .env file and environment variables."""

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()


@dataclass(frozen=True)
class Config:
    anthropic_api_key: str = ""

    # Telegram
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""
    telegram_allowed_users: str = ""  # comma-separated usernames or user IDs

    repo_root: Path = field(default_factory=lambda: Path.cwd())

    max_fix_attempts: int = 3
    max_budget_per_agent_usd: float = 5.00
    agent_model: str = "claude-sonnet-4-6"

    @property
    def workspace_dir(self) -> Path:
        return self.repo_root / ".dev-agent-workspace"


def load_config() -> Config:
    return Config(
        anthropic_api_key=os.environ.get("ANTHROPIC_API_KEY", ""),
        telegram_bot_token=os.environ.get("TELEGRAM_BOT_TOKEN", ""),
        telegram_chat_id=os.environ.get("TELEGRAM_CHAT_ID", ""),
        telegram_allowed_users=os.environ.get("TELEGRAM_ALLOWED_USERS", ""),
        repo_root=Path(os.environ.get("REPO_ROOT", Path.cwd())),
        max_fix_attempts=int(os.environ.get("MAX_FIX_ATTEMPTS", "3")),
        max_budget_per_agent_usd=float(
            os.environ.get("MAX_BUDGET_PER_AGENT_USD", "5.00")
        ),
        agent_model=os.environ.get("AGENT_MODEL", "claude-sonnet-4-6"),
    )
