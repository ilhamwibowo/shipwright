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


def get_diff_stat(repo: str | Path) -> str:
    """Get compact diff stat showing changed files."""
    return _git(["diff", "--stat", "--stat-width=60"], repo)


def get_ahead_behind(repo: str | Path) -> tuple[int, int]:
    """Get commits ahead/behind tracking branch. Returns (ahead, behind)."""
    try:
        result = _git(
            ["rev-list", "--left-right", "--count", "@{upstream}...HEAD"], repo,
        )
        parts = result.split()
        if len(parts) == 2:
            return int(parts[1]), int(parts[0])  # ahead, behind
    except GitError:
        pass
    return 0, 0


def get_branch_context(repo: str | Path) -> str:
    """Build a comprehensive git context string for CTO prompts.

    Includes branch name, ahead/behind, working tree status, and recent
    commits.  Designed to be injected into the CTO's system prompt so it
    can answer repo-state questions without running git commands itself.
    """
    lines: list[str] = []

    try:
        branch = get_current_branch(repo)
        lines.append(f"Branch: {branch}")
    except GitError:
        lines.append("Branch: (unknown — detached HEAD or not a git repo)")
        return "\n".join(lines)

    # Ahead/behind remote
    ahead, behind = get_ahead_behind(repo)
    if ahead or behind:
        parts = []
        if ahead:
            parts.append(f"{ahead} ahead")
        if behind:
            parts.append(f"{behind} behind")
        lines.append(f"Remote: {', '.join(parts)}")

    # Working tree status
    try:
        status_output = get_status(repo)
        if status_output:
            file_lines = [l for l in status_output.strip().split("\n") if l.strip()]
            n = len(file_lines)
            lines.append(f"Working tree: {n} changed file{'s' if n != 1 else ''}")
            for fl in file_lines[:15]:
                lines.append(f"  {fl}")
            if n > 15:
                lines.append(f"  ... and {n - 15} more")
        else:
            lines.append("Working tree: clean")
    except GitError:
        pass

    # Branch-specific commits (vs default branch)
    try:
        default = get_default_branch(repo)
        if branch != default:
            try:
                branch_log = _git(
                    ["log", "--oneline", f"{default}..HEAD", "-10"], repo,
                )
                if branch_log:
                    commit_lines = branch_log.strip().split("\n")
                    count = len(commit_lines)
                    lines.append(
                        f"Branch commits ({count} since {default}):"
                    )
                    for cl in commit_lines[:8]:
                        lines.append(f"  {cl}")
                    if count > 8:
                        lines.append(f"  ... and {count - 8} more")
            except GitError:
                pass
    except GitError:
        pass

    # Recent commits (always)
    try:
        log = get_log(repo, 5)
        if log:
            lines.append("Recent commits:")
            for cl in log.strip().split("\n"):
                lines.append(f"  {cl}")
    except GitError:
        pass

    return "\n".join(lines)
