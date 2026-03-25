"""Tests for state persistence."""

from pathlib import Path

import pytest

from shipwright.config import Config
from shipwright.persistence.store import (
    clear_state,
    list_sessions,
    load_state,
    save_state,
)


class TestPersistence:
    def test_save_and_load(self, config: Config):
        data = {
            "session": {"id": "test", "messages": [{"role": "user", "text": "hi"}]},
            "crews": {},
        }
        save_state(data, config, session_id="test")

        loaded = load_state(config, session_id="test")
        assert loaded is not None
        assert loaded["session"]["id"] == "test"

    def test_load_missing(self, config: Config):
        result = load_state(config, session_id="nonexistent")
        assert result is None

    def test_clear(self, config: Config):
        save_state({"test": True}, config, session_id="test")
        assert load_state(config, session_id="test") is not None

        clear_state(config, session_id="test")
        assert load_state(config, session_id="test") is None

    def test_list_sessions(self, config: Config):
        save_state({"a": 1}, config, session_id="session-a")
        save_state({"b": 2}, config, session_id="session-b")

        sessions = list_sessions(config)
        assert "session-a" in sessions
        assert "session-b" in sessions

    def test_list_sessions_empty(self, config: Config):
        sessions = list_sessions(config)
        assert sessions == []

    def test_save_overwrites(self, config: Config):
        save_state({"version": 1}, config, session_id="test")
        save_state({"version": 2}, config, session_id="test")

        loaded = load_state(config, session_id="test")
        assert loaded["version"] == 2

    def test_atomic_write(self, config: Config):
        """Verify no .tmp files are left behind."""
        save_state({"test": True}, config, session_id="test")
        tmp_files = list(config.state_dir.glob("*.tmp"))
        assert len(tmp_files) == 0
