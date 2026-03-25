"""Persistence — save and restore crews, conversations, and state.

State is stored as JSON files in .shipwright/state/ within the project root.
Each session (CLI, Telegram, Discord) gets its own state file.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from shipwright.config import Config

logger = logging.getLogger("shipwright.persistence")


def _state_path(config: Config, session_id: str) -> Path:
    return config.state_dir / f"{session_id}.json"


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
    """Load router state from disk. Returns None if not found."""
    path = _state_path(config, session_id)
    if not path.exists():
        return None

    try:
        data = json.loads(path.read_text())
        logger.debug("State loaded: %s", path)
        return data
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
    if not config.state_dir.exists():
        return []
    return [
        p.stem for p in config.state_dir.glob("*.json")
    ]
