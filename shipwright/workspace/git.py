"""Git worktree management for crew isolation."""

from __future__ import annotations

import subprocess
from pathlib import Path

from shipwright.utils.logging import get_logger

logger = get_logger("workspace.git")


class GitError(RuntimeError):
    pass


def _git(args: list[str], cwd: str | Path) -> str:
    """Run a git command and return stdout."""
    try:
        r = subprocess.run(
            ["git", *args],
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=60,
        )
    except subprocess.TimeoutExpired:
        raise GitError(f"git {' '.join(args)}: timed out after 60s")
    except FileNotFoundError:
        raise GitError("git is not installed or not in PATH")
    if r.returncode != 0:
        raise GitError(f"git {' '.join(args)}: {r.stderr.strip()}")
    return r.stdout.strip()


def get_default_branch(repo: str | Path) -> str:
    """Detect the default branch (main or master)."""
    try:
        return _git(
            ["symbolic-ref", "refs/remotes/origin/HEAD", "--short"], repo
        ).split("/")[-1]
    except GitError:
        for branch in ("main", "master"):
            try:
                _git(["rev-parse", "--verify", branch], repo)
                return branch
            except GitError:
                continue
        return "main"


def slug(text: str) -> str:
    """Generate a URL-safe slug from text."""
    words = text.lower().split()[:6]
    s = "-".join("".join(c for c in w if c.isalnum()) for w in words)[:40]
    return s or "task"


def create_worktree(repo: str | Path, branch: str) -> Path:
    """Create a git worktree for isolated work. Returns the worktree path."""
    repo = str(repo)
    wt_name = branch.replace("/", "-")
    path = str(Path(repo).parent / f".shipwright-wt-{wt_name}")
    default_branch = get_default_branch(repo)

    # Clean up if existing
    for cmd in [["worktree", "remove", "--force", path], ["branch", "-D", branch]]:
        try:
            _git(cmd, repo)
        except GitError:
            pass

    _git(["worktree", "add", "-b", branch, path, default_branch], repo)
    logger.info("Created worktree at %s on branch %s", path, branch)
    return Path(path)


def cleanup_worktree(repo: str | Path, path: str | Path, branch: str) -> None:
    """Remove a worktree and its branch."""
    repo = str(repo)
    for cmd in [
        ["worktree", "remove", "--force", str(path)],
        ["branch", "-D", branch],
    ]:
        try:
            _git(cmd, repo)
        except GitError:
            pass
    logger.info("Cleaned up worktree %s", path)


def commit(path: str | Path, msg: str, no_verify: bool = False) -> None:
    """Stage all changes and commit if there are any.

    Args:
        no_verify: Skip pre-commit hooks (useful for worktree commits where
            the parent repo's hooks may not apply).
    """
    _git(["add", "-A"], path)
    if _git(["status", "--porcelain"], path):
        cmd = ["commit", "-m", msg]
        if no_verify:
            cmd.append("--no-verify")
        _git(cmd, path)


def push_branch(path: str | Path, branch: str) -> None:
    """Push branch to origin."""
    _git(["push", "-u", "origin", branch], path)


def create_pr(path: str | Path, branch: str, title: str, body: str) -> str:
    """Create a GitHub PR using gh CLI. Returns the PR URL."""
    try:
        r = subprocess.run(
            ["gh", "pr", "create", "--title", title, "--body", body, "--head", branch],
            cwd=str(path),
            capture_output=True,
            text=True,
            timeout=30,
        )
    except FileNotFoundError:
        raise GitError("GitHub CLI (gh) is not installed or not in PATH")
    except subprocess.TimeoutExpired:
        raise GitError("gh pr create timed out after 30s")
    if r.returncode != 0:
        raise GitError(f"gh pr create: {r.stderr.strip()}")
    return r.stdout.strip()


def get_status(repo: str | Path) -> str:
    """Get git status (porcelain format)."""
    return _git(["status", "--porcelain"], repo)


def get_current_branch(repo: str | Path) -> str:
    """Get the current branch name."""
    return _git(["branch", "--show-current"], repo)


def get_log(repo: str | Path, n: int = 10) -> str:
    """Get recent git log."""
    return _git(["log", "--oneline", f"-{n}"], repo)
