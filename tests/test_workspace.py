"""Tests for workspace: git worktree management and project discovery."""

from pathlib import Path

import pytest

from shipwright.workspace.git import (
    GitError,
    _git,
    cleanup_worktree,
    commit,
    create_worktree,
    get_current_branch,
    get_default_branch,
    get_log,
    get_status,
    slug,
)
from shipwright.workspace.project import ProjectInfo, discover_project


# ---------------------------------------------------------------------------
# Git helpers
# ---------------------------------------------------------------------------

class TestGitHelpers:
    def test_git_status(self, sample_repo: Path):
        result = _git(["status", "--porcelain"], sample_repo)
        assert result == ""

    def test_git_log(self, sample_repo: Path):
        result = _git(["log", "--oneline"], sample_repo)
        assert "init" in result

    def test_git_invalid_command(self, sample_repo: Path):
        with pytest.raises(GitError, match="git"):
            _git(["not-a-command"], sample_repo)

    def test_get_default_branch(self, sample_repo: Path):
        branch = get_default_branch(sample_repo)
        assert branch in ("main", "master")

    def test_get_current_branch(self, sample_repo: Path):
        branch = get_current_branch(sample_repo)
        assert branch in ("main", "master")

    def test_get_status_clean(self, sample_repo: Path):
        status = get_status(sample_repo)
        assert status == ""

    def test_get_log(self, sample_repo: Path):
        log = get_log(sample_repo)
        assert "init" in log

    def test_commit_no_changes(self, sample_repo: Path):
        commit(sample_repo, "empty commit")
        log = get_log(sample_repo)
        assert log.count("\n") == 0  # still just one commit

    def test_commit_with_changes(self, sample_repo: Path):
        (sample_repo / "new_file.txt").write_text("hello")
        commit(sample_repo, "add new file")
        log = get_log(sample_repo)
        assert "add new file" in log

    def test_worktree_lifecycle(self, sample_repo: Path):
        branch = "shipwright/test-branch"
        wt_path = create_worktree(sample_repo, branch)
        assert wt_path.exists()

        current = get_current_branch(wt_path)
        assert current == branch

        cleanup_worktree(sample_repo, wt_path, branch)
        assert not wt_path.exists()


class TestSlug:
    def test_basic(self):
        assert slug("Add user registration") == "add-user-registration"

    def test_max_length(self):
        long_text = " ".join(["word"] * 20)
        result = slug(long_text)
        assert len(result) <= 40

    def test_special_chars(self):
        result = slug("Fix bug #123 in auth/login!")
        assert "#" not in result
        assert "!" not in result
        assert "/" not in result

    def test_empty(self):
        assert slug("") == "task"

    def test_max_words(self):
        text = "one two three four five six seven eight"
        result = slug(text)
        assert "seven" not in result


# ---------------------------------------------------------------------------
# Project discovery
# ---------------------------------------------------------------------------

class TestProjectDiscovery:
    def test_python_project(self, python_project: Path):
        info = discover_project(python_project)
        assert "Python" in info.languages
        assert "FastAPI" in info.frameworks
        assert info.has_docker
        assert any("pytest" in cmd for cmd in info.test_commands)

    def test_node_project(self, node_project: Path):
        info = discover_project(node_project)
        assert "TypeScript" in info.languages
        assert "React" in info.frameworks
        assert "npm" in info.package_managers
        assert any("jest" in cmd for cmd in info.test_commands)

    def test_empty_dir(self, tmp_path: Path):
        info = discover_project(tmp_path)
        assert info.languages == []
        assert info.frameworks == []

    def test_nonexistent_dir(self, tmp_path: Path):
        info = discover_project(tmp_path / "nonexistent")
        assert info.languages == []

    def test_to_prompt_context(self, python_project: Path):
        info = discover_project(python_project)
        ctx = info.to_prompt_context()
        assert "Python" in ctx
        assert "FastAPI" in ctx

    def test_summary(self, python_project: Path):
        info = discover_project(python_project)
        assert info.summary  # non-empty
        assert "Python" in info.summary
