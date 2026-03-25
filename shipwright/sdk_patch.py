"""Monkey-patch the Claude Code SDK to handle unknown message types gracefully.

The SDK raises MessageParseError for unknown message types like 'rate_limit_event',
which kills the async generator stream. This patch makes it return None instead,
allowing the stream to continue.

We must patch BOTH:
1. message_parser.parse_message (the module-level function)
2. client module's local reference (imported via `from .message_parser import parse_message`)

Apply once at import time via: import shipwright.sdk_patch
"""

from __future__ import annotations

import logging

from claude_code_sdk._internal import message_parser, client
from claude_code_sdk._errors import MessageParseError

logger = logging.getLogger("shipwright.sdk_patch")

_original_parse = message_parser.parse_message


def _patched_parse(data):
    """Parse message, returning None for unknown types instead of raising."""
    try:
        return _original_parse(data)
    except MessageParseError as exc:
        msg_type = data.get("type", "?") if isinstance(data, dict) else "?"
        logger.debug("Skipping unknown SDK message type: %s", msg_type)
        return None


# Patch both the module and any cached references in the client
message_parser.parse_message = _patched_parse
client.parse_message = _patched_parse
