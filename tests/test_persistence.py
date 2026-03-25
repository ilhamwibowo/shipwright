"""Tests for task state persistence."""

from pathlib import Path

import pytest

from dev_agent.config import Config
from dev_agent.coordinator import Task, TeamState
from dev_agent.agents.base import TokenUsage
from dev_agent.persistence import save_state, load_state, clear_state


class TestPersistence:
    def test_save_and_load(self, config: Config):
        state = TeamState()
        task = state.add_task("Test task", {"steps": [], "needs_worktree": False})
        task.status = "done"
        task.usage = TokenUsage(input_tokens=100, output_tokens=50, api_calls=1)
        state.conversation.append({"role": "user", "text": "hi"})

        save_state(state, config)

        loaded = load_state(config)
        assert loaded is not None
        restored = TeamState.from_dict(loaded)
        assert len(restored.tasks) == 1
        assert restored.tasks[1].description == "Test task"
        assert restored.tasks[1].status == "done"
        assert restored.conversation[0]["text"] == "hi"

    def test_load_missing(self, config: Config):
        result = load_state(config)
        assert result is None

    def test_clear(self, config: Config):
        state = TeamState()
        state.add_task("Test", {})
        save_state(state, config)
        assert load_state(config) is not None

        clear_state(config)
        assert load_state(config) is None
