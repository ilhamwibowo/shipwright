"""Task state persistence — save/restore state to JSON so it survives restarts."""

from __future__ import annotations

import json
import logging
from pathlib import Path

from dev_agent.config import Config

logger = logging.getLogger(__name__)

STATE_FILENAME = "state.json"


def _state_path(config: Config) -> Path:
    return config.workspace_dir / STATE_FILENAME


def save_state(state: object, config: Config) -> None:
    """Save TeamState to JSON on disk."""
    path = _state_path(config)
    path.parent.mkdir(parents=True, exist_ok=True)

    try:
        data = state.to_dict()
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=2, default=str))
        tmp.replace(path)
        logger.debug("State saved to %s", path)
    except Exception:
        logger.warning("Failed to save state to %s", path, exc_info=True)


def load_state(config: Config) -> dict | None:
    """Load TeamState dict from JSON on disk. Returns None if not found."""
    path = _state_path(config)
    if not path.exists():
        return None

    try:
        data = json.loads(path.read_text())
        logger.info("State loaded from %s", path)
        return data
    except Exception:
        logger.warning("Failed to load state from %s", path, exc_info=True)
        return None


def clear_state(config: Config) -> None:
    """Remove persisted state file."""
    path = _state_path(config)
    if path.exists():
        path.unlink()
        logger.info("State cleared: %s", path)
