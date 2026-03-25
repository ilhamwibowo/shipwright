"""Shared test fixtures."""

import subprocess
from pathlib import Path

import pytest

from shipwright.config import Config


@pytest.fixture
def config(tmp_path: Path) -> Config:
    """Config pointing to a temporary directory."""
    return Config(
        repo_root=tmp_path,
        max_fix_attempts=2,
        model="claude-sonnet-4-6",
    )


@pytest.fixture
def sample_repo(tmp_path: Path) -> Path:
    """Create a minimal git repo for testing."""
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=repo, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=repo, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, capture_output=True)
    (repo / "README.md").write_text("# Test repo\n")
    subprocess.run(["git", "add", "-A"], cwd=repo, capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=repo, capture_output=True)
    return repo


@pytest.fixture
def python_project(tmp_path: Path) -> Path:
    """Create a minimal Python project for discovery testing."""
    proj = tmp_path / "project"
    proj.mkdir()
    (proj / "pyproject.toml").write_text('[project]\nname = "myapp"\ndependencies = ["fastapi"]\n')
    (proj / "requirements.txt").write_text("fastapi>=0.100\nuvicorn\n")
    (proj / "main.py").write_text("from fastapi import FastAPI\napp = FastAPI()\n")
    (proj / "tests").mkdir()
    (proj / "tests" / "test_main.py").write_text("def test_ok(): assert True\n")
    (proj / "Dockerfile").write_text("FROM python:3.12\n")
    return proj


@pytest.fixture
def node_project(tmp_path: Path) -> Path:
    """Create a minimal Node.js project for discovery testing."""
    import json

    proj = tmp_path / "project"
    proj.mkdir()
    pkg = {
        "name": "myapp",
        "dependencies": {"react": "^18.0", "next": "^14.0"},
        "devDependencies": {"jest": "^29.0"},
    }
    (proj / "package.json").write_text(json.dumps(pkg))
    (proj / "package-lock.json").write_text("{}")
    (proj / "tsconfig.json").write_text("{}")
    (proj / "src").mkdir()
    (proj / "src" / "index.ts").write_text("export default {}")
    (proj / "jest.config.js").write_text("module.exports = {}")
    return proj
