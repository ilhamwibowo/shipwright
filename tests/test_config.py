"""Tests for configuration loading and shipwright.yaml parsing."""

from pathlib import Path

import pytest

from shipwright.config import Config, CrewDef, MemberDef, _parse_crew_defs, load_config


class TestConfig:
    def test_defaults(self):
        config = Config()
        assert config.telegram_bot_token == ""
        assert config.max_fix_attempts == 3
        assert config.model == "claude-sonnet-4-6"
        assert config.permission_mode == "bypassPermissions"

    def test_data_dir(self, tmp_path: Path):
        config = Config(repo_root=tmp_path)
        assert config.data_dir == tmp_path / ".shipwright"
        assert config.state_dir == tmp_path / ".shipwright" / "state"

    def test_frozen(self):
        config = Config()
        with pytest.raises(AttributeError):
            config.model = "new-model"  # type: ignore

    def test_load_config_from_env(self, monkeypatch):
        monkeypatch.setenv("SHIPWRIGHT_MODEL", "claude-opus-4-6")
        monkeypatch.setenv("MAX_FIX_ATTEMPTS", "5")
        monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "tg-token")
        monkeypatch.setenv("DISCORD_BOT_TOKEN", "discord-token")

        config = load_config()
        assert config.model == "claude-opus-4-6"
        assert config.max_fix_attempts == 5
        assert config.telegram_bot_token == "tg-token"
        assert config.discord_bot_token == "discord-token"


class TestCrewDefs:
    def test_parse_crew_defs(self):
        raw = {
            "ml-crew": {
                "lead": "ML engineering lead.",
                "members": {
                    "data_scientist": {
                        "role": "Data Scientist",
                        "prompt": "You explore data.",
                        "tools": ["Read", "Write", "Bash"],
                        "max_turns": 60,
                    },
                    "ml_engineer": {
                        "role": "ML Engineer",
                        "prompt": "You productionize models.",
                        "tools": ["Read", "Edit", "Write", "Bash"],
                    },
                },
            }
        }
        crews = _parse_crew_defs(raw)
        assert "ml-crew" in crews
        crew = crews["ml-crew"]
        assert crew.lead_prompt == "ML engineering lead."
        assert len(crew.members) == 2
        assert crew.members["data_scientist"].max_turns == 60
        assert crew.members["ml_engineer"].max_turns == 50  # default
        assert "Bash" in crew.members["data_scientist"].tools

    def test_member_def_defaults(self):
        member = MemberDef(role="Dev", prompt="You code.")
        assert member.tools == ["Read", "Glob", "Grep"]
        assert member.max_turns == 50
        assert member.model is None

    def test_crew_def_structure(self):
        crew = CrewDef(
            name="test",
            lead_prompt="Test lead.",
            members={
                "dev": MemberDef(role="Dev", prompt="You code."),
            },
        )
        assert crew.name == "test"
        assert "dev" in crew.members
