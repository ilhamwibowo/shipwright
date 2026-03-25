"""Tests for configuration loading."""

import os
from pathlib import Path

import pytest

from dev_agent.config import Config, load_config


class TestConfig:
    def test_defaults(self):
        config = Config()
        assert config.anthropic_api_key == ""
        assert config.max_fix_attempts == 3
        assert config.max_budget_per_agent_usd == 5.00
        assert config.agent_model == "claude-sonnet-4-6"
        assert config.agent_timeout_seconds == 600

    def test_workspace_dir(self, tmp_path: Path):
        config = Config(repo_root=tmp_path)
        assert config.workspace_dir == tmp_path / ".dev-agent-workspace"

    def test_frozen(self):
        config = Config()
        with pytest.raises(AttributeError):
            config.anthropic_api_key = "new-key"  # type: ignore

    def test_load_config_from_env(self, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
        monkeypatch.setenv("AGENT_MODEL", "claude-opus-4-6")
        monkeypatch.setenv("MAX_FIX_ATTEMPTS", "5")
        monkeypatch.setenv("AGENT_TIMEOUT_SECONDS", "300")
        monkeypatch.setenv("DISCORD_BOT_TOKEN", "discord-token")

        config = load_config()
        assert config.anthropic_api_key == "sk-test"
        assert config.agent_model == "claude-opus-4-6"
        assert config.max_fix_attempts == 5
        assert config.agent_timeout_seconds == 300
        assert config.discord_bot_token == "discord-token"

    def test_discord_config_defaults(self):
        config = Config()
        assert config.discord_bot_token == ""
        assert config.discord_channel_id == ""
