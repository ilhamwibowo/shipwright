"""Conversation session — persistent message history and context.

A session represents an ongoing conversation between the user and one
or more crews. It tracks messages, active crews, and provides context
to the router.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field


@dataclass
class Message:
    """A single message in the conversation."""

    role: str  # "user", "lead", "system"
    text: str
    crew_id: str | None = None
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> dict:
        return {
            "role": self.role,
            "text": self.text,
            "crew_id": self.crew_id,
            "timestamp": self.timestamp,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Message":
        return cls(
            role=data["role"],
            text=data["text"],
            crew_id=data.get("crew_id"),
            timestamp=data.get("timestamp", time.time()),
        )


@dataclass
class Session:
    """A conversation session with persistent message history.

    Sessions survive restarts and provide context across interactions.
    Each interface (CLI, Telegram, Discord) has its own session.
    """

    id: str
    messages: list[Message] = field(default_factory=list)
    active_crew_id: str | None = None
    created_at: float = field(default_factory=time.time)

    def add_user_message(self, text: str) -> Message:
        msg = Message(role="user", text=text, crew_id=self.active_crew_id)
        self.messages.append(msg)
        return msg

    def add_lead_message(self, text: str, crew_id: str | None = None) -> Message:
        msg = Message(role="lead", text=text, crew_id=crew_id or self.active_crew_id)
        self.messages.append(msg)
        return msg

    def add_system_message(self, text: str) -> Message:
        msg = Message(role="system", text=text)
        self.messages.append(msg)
        return msg

    def get_recent(self, n: int = 20) -> list[Message]:
        """Get the N most recent messages."""
        return self.messages[-n:]

    def get_crew_messages(self, crew_id: str, n: int = 20) -> list[Message]:
        """Get recent messages for a specific crew."""
        crew_msgs = [m for m in self.messages if m.crew_id == crew_id]
        return crew_msgs[-n:]

    def format_history(self, n: int = 20) -> str:
        """Format recent messages as a readable conversation."""
        recent = self.get_recent(n)
        if not recent:
            return "(no conversation history)"

        lines = []
        for msg in recent:
            prefix = {"user": "You", "lead": "Lead", "system": "System"}.get(
                msg.role, msg.role
            )
            crew_tag = f" [{msg.crew_id}]" if msg.crew_id else ""
            lines.append(f"{prefix}{crew_tag}: {msg.text[:500]}")
        return "\n".join(lines)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "messages": [m.to_dict() for m in self.messages[-100:]],
            "active_crew_id": self.active_crew_id,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Session":
        session = cls(
            id=data["id"],
            created_at=data.get("created_at", time.time()),
        )
        session.messages = [Message.from_dict(m) for m in data.get("messages", [])]
        session.active_crew_id = data.get("active_crew_id")
        return session
