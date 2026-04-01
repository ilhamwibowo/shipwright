"""Persistence — save and restore crews, conversations, and state.

State is stored as JSON files in ~/.shipwright/sessions/.
Each session gets its own state file: ~/.shipwright/sessions/<name>.json
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from pathlib import Path

from shipwright.config import Config

logger = logging.getLogger("shipwright.persistence")


_DEFAULT_SESSION = "default"
_DEFAULT_PREFIX = "default__"


def _slugify_workspace_name(name: str) -> str:
    """Build a stable, filesystem-safe workspace label."""
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", name.strip()).strip("-").lower()
    return slug or "workspace"


def _storage_session_id(config: Config, session_id: str) -> str:
    """Map the user-facing session name to a storage key.

    `default` is scoped by repo root so different workspaces do not share the
    same state file accidentally.
    """
    if session_id != _DEFAULT_SESSION:
        return session_id

    repo_root = str(config.repo_root.resolve())
    digest = hashlib.sha1(repo_root.encode("utf-8")).hexdigest()[:10]
    slug = _slugify_workspace_name(config.repo_root.name)
    return f"{_DEFAULT_PREFIX}{slug}__{digest}"


def _state_path(config: Config, session_id: str) -> Path:
    storage_id = _storage_session_id(config, session_id)
    return config.sessions_dir / f"{storage_id}.json"


def save_state(data: dict, config: Config, session_id: str = "default") -> None:
    """Save router state to disk."""
    path = _state_path(config, session_id)
    path.parent.mkdir(parents=True, exist_ok=True)

    try:
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=2, default=str))
        tmp.replace(path)
        logger.debug("State saved: %s", path)
    except Exception:
        logger.warning("Failed to save state to %s", path, exc_info=True)


def load_state(config: Config, session_id: str = "default") -> dict | None:
    """Load router state from disk. Returns None if not found or corrupted."""
    path = _state_path(config, session_id)
    if not path.exists():
        return None

    try:
        raw = path.read_text()
        if not raw.strip():
            logger.warning("Empty state file: %s", path)
            return None
        data = json.loads(raw)
        if not isinstance(data, dict):
            logger.warning("State file is not a JSON object: %s", path)
            return None
        logger.debug("State loaded: %s", path)
        return data
    except json.JSONDecodeError as e:
        logger.warning("Corrupted JSON in state file %s: %s", path, e)
        return None
    except Exception:
        logger.warning("Failed to load state from %s", path, exc_info=True)
        return None


def clear_state(config: Config, session_id: str = "default") -> None:
    """Remove persisted state."""
    path = _state_path(config, session_id)
    if path.exists():
        path.unlink()
        logger.info("State cleared: %s", path)


def list_sessions(config: Config) -> list[str]:
    """List all saved session IDs."""
    if not config.sessions_dir.exists():
        return []

    current_default = _storage_session_id(config, _DEFAULT_SESSION)
    sessions: list[str] = []
    for path in config.sessions_dir.glob("*.json"):
        stem = path.stem
        if stem == current_default:
            sessions.append(_DEFAULT_SESSION)
            continue
        if stem.startswith(_DEFAULT_PREFIX):
            continue
        sessions.append(stem)
    return sorted(set(sessions))
