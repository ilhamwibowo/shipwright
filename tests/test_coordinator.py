"""Tests for coordinator JSON parsing, git helpers, and task management."""

import json
import subprocess
from pathlib import Path

import pytest

from dev_agent.coordinator import (
    TeamState,
    Task,
    _extract_json,
    _slug,
    _git,
    _get_default_branch,
    _create_worktree,
    _cleanup_worktree,
    _commit,
)
from dev_agent.agents.base import TokenUsage


# ---------------------------------------------------------------------------
# JSON extraction
# ---------------------------------------------------------------------------

class TestExtractJson:
    def test_clean_json(self):
        text = '{"reply": "hello", "new_tasks": []}'
        assert json.loads(_extract_json(text)) == {"reply": "hello", "new_tasks": []}

    def test_json_in_markdown_fences(self):
        text = '```json\n{"reply": "hi", "new_tasks": []}\n```'
        result = json.loads(_extract_json(text))
        assert result["reply"] == "hi"

    def test_json_with_leading_text(self):
        text = 'Here is my response: {"reply": "ok", "new_tasks": []}'
        result = json.loads(_extract_json(text))
        assert result["reply"] == "ok"

    def test_json_with_trailing_text(self):
        text = '{"reply": "ok", "new_tasks": []} I hope that helps!'
        result = json.loads(_extract_json(text))
        assert result["reply"] == "ok"

    def test_no_json(self):
        text = "Just a plain text response with no JSON."
        result = _extract_json(text)
        assert result == text.strip()

    def test_nested_json(self):
        text = '{"reply": "here", "new_tasks": [{"description": "test", "steps": []}]}'
        result = json.loads(_extract_json(text))
        assert len(result["new_tasks"]) == 1

    def test_empty_string(self):
        assert _extract_json("") == ""

    def test_multiple_code_fences(self):
        text = '```\nsome code\n```\n```json\n{"reply": "yes", "new_tasks": []}\n```'
        result = json.loads(_extract_json(text))
        assert result["reply"] == "yes"


# ---------------------------------------------------------------------------
# Slug generation
# ---------------------------------------------------------------------------

class TestSlug:
    def test_basic(self):
        assert _slug("Add user registration") == "add-user-registration"

    def test_max_length(self):
        long_text = " ".join(["word"] * 20)
        result = _slug(long_text)
        assert len(result) <= 40

    def test_special_chars(self):
        result = _slug("Fix bug #123 in auth/login!")
        assert "#" not in result
        assert "!" not in result
        assert "/" not in result

    def test_empty(self):
        assert _slug("") == "task"

    def test_max_words(self):
        text = "one two three four five six seven eight"
        result = _slug(text)
        assert "seven" not in result  # max 6 words


# ---------------------------------------------------------------------------
# Git helpers
# ---------------------------------------------------------------------------

class TestGitHelpers:
    def test_git_status(self, sample_repo: Path):
        result = _git(["status", "--porcelain"], str(sample_repo))
        assert result == ""  # clean repo

    def test_git_log(self, sample_repo: Path):
        result = _git(["log", "--oneline"], str(sample_repo))
        assert "init" in result

    def test_git_invalid_command(self, sample_repo: Path):
        with pytest.raises(RuntimeError, match="git"):
            _git(["not-a-command"], str(sample_repo))

    def test_get_default_branch(self, sample_repo: Path):
        # Fresh repo with only main/master
        branch = _get_default_branch(str(sample_repo))
        assert branch in ("main", "master")

    def test_commit_no_changes(self, sample_repo: Path):
        # Should not error on clean repo
        _commit(str(sample_repo), "empty commit")
        # No new commit since there were no changes
        log = _git(["log", "--oneline"], str(sample_repo))
        assert log.count("\n") == 0  # still just one commit

    def test_commit_with_changes(self, sample_repo: Path):
        (sample_repo / "new_file.txt").write_text("hello")
        _commit(str(sample_repo), "add new file")
        log = _git(["log", "--oneline"], str(sample_repo))
        assert "add new file" in log

    def test_worktree_lifecycle(self, sample_repo: Path):
        branch = "dev-agent/test-branch"
        wt_path = _create_worktree(str(sample_repo), branch)
        assert Path(wt_path).exists()

        # Verify it's a valid worktree
        current_branch = _git(["branch", "--show-current"], wt_path)
        assert current_branch == branch

        # Cleanup
        _cleanup_worktree(str(sample_repo), wt_path, branch)
        assert not Path(wt_path).exists()


# ---------------------------------------------------------------------------
# Task / TeamState serialization
# ---------------------------------------------------------------------------

class TestTaskSerialization:
    def test_task_roundtrip(self):
        task = Task(id=1, description="Test task", status="done", pr_url="https://example.com/pr/1")
        task.usage = TokenUsage(input_tokens=1000, output_tokens=500, api_calls=3)

        data = task.to_dict()
        restored = Task.from_dict(data)

        assert restored.id == 1
        assert restored.description == "Test task"
        assert restored.status == "done"
        assert restored.pr_url == "https://example.com/pr/1"
        assert restored.usage.input_tokens == 1000
        assert restored.usage.output_tokens == 500

    def test_team_state_roundtrip(self):
        state = TeamState()
        state.add_task("Task 1", {"steps": [], "needs_worktree": False})
        state.add_task("Task 2", {"steps": [], "needs_worktree": True})
        state.conversation.append({"role": "user", "text": "hello"})

        data = state.to_dict()
        restored = TeamState.from_dict(data)

        assert len(restored.tasks) == 2
        assert restored.next_id == 3
        assert len(restored.conversation) == 1

    def test_team_state_summary(self):
        state = TeamState()
        task = state.add_task("Build feature X", {"steps": []})
        task.status = "running"

        summary = state.summary
        assert "#1" in summary
        assert "running" in summary
        assert "Build feature X" in summary

    def test_empty_state_summary(self):
        state = TeamState()
        assert state.summary == "No tasks yet."
