"""Shared test fixtures."""

import pytest
from pathlib import Path
from dev_agent.config import Config


@pytest.fixture
def config(tmp_path: Path) -> Config:
    """Config pointing to a temporary directory."""
    return Config(
        anthropic_api_key="test-key-not-real",
        repo_root=tmp_path,
        max_fix_attempts=2,
        max_budget_per_agent_usd=1.00,
        agent_model="claude-sonnet-4-6",
        agent_timeout_seconds=60,
    )


@pytest.fixture
def sample_repo(tmp_path: Path) -> Path:
    """Create a minimal git repo for testing."""
    import subprocess
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=repo, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=repo, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, capture_output=True)
    (repo / "README.md").write_text("# Test repo\n")
    subprocess.run(["git", "add", "-A"], cwd=repo, capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=repo, capture_output=True)
    return repo
