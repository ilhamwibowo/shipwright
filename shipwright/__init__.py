"""Shipwright — virtual engineering crews powered by Claude Code SDK."""

__version__ = "2.0.0"

# Patch SDK to handle unknown message types gracefully (must be first)
import shipwright.sdk_patch  # noqa: F401
