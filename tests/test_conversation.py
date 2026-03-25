"""Tests for conversation session and router."""

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from shipwright.config import Config
from shipwright.conversation.router import Router
from shipwright.conversation.session import Message, Session


# ---------------------------------------------------------------------------
# Session
# ---------------------------------------------------------------------------

class TestSession:
    def test_add_messages(self):
        session = Session(id="test")
        session.add_user_message("hello")
        session.add_lead_message("hi there", crew_id="backend-1")
        session.add_system_message("Crew hired.")

        assert len(session.messages) == 3
        assert session.messages[0].role == "user"
        assert session.messages[1].role == "lead"
        assert session.messages[2].role == "system"

    def test_get_recent(self):
        session = Session(id="test")
        for i in range(30):
            session.add_user_message(f"msg {i}")

        recent = session.get_recent(10)
        assert len(recent) == 10
        assert recent[0].text == "msg 20"

    def test_get_crew_messages(self):
        session = Session(id="test")
        session.add_lead_message("a", crew_id="crew-a")
        session.add_lead_message("b", crew_id="crew-b")
        session.add_lead_message("c", crew_id="crew-a")

        msgs = session.get_crew_messages("crew-a")
        assert len(msgs) == 2
        assert msgs[0].text == "a"
        assert msgs[1].text == "c"

    def test_format_history(self):
        session = Session(id="test")
        session.add_user_message("hello")
        session.add_lead_message("hi there")
        formatted = session.format_history()
        assert "You:" in formatted
        assert "Lead:" in formatted

    def test_serialization(self):
        session = Session(id="test")
        session.add_user_message("hello")
        session.add_lead_message("hi", crew_id="crew-1")
        session.active_crew_id = "crew-1"

        data = session.to_dict()
        assert data["id"] == "test"
        assert len(data["messages"]) == 2

        restored = Session.from_dict(data)
        assert restored.id == "test"
        assert len(restored.messages) == 2
        assert restored.active_crew_id == "crew-1"


class TestMessage:
    def test_message_defaults(self):
        msg = Message(role="user", text="hello")
        assert msg.crew_id is None
        assert msg.timestamp > 0

    def test_message_roundtrip(self):
        msg = Message(role="lead", text="response", crew_id="crew-1")
        data = msg.to_dict()
        restored = Message.from_dict(data)
        assert restored.role == "lead"
        assert restored.text == "response"
        assert restored.crew_id == "crew-1"


# ---------------------------------------------------------------------------
# Router
# ---------------------------------------------------------------------------

class TestRouter:
    def _make_router(self, config: Config) -> Router:
        session = Session(id="test")
        return Router(config=config, session=session)

    def test_help_command(self, config: Config):
        router = self._make_router(config)
        is_cmd, response = router._try_command("help")
        assert is_cmd
        assert "Commands" in response

    def test_status_no_crews(self, config: Config):
        router = self._make_router(config)
        is_cmd, response = router._try_command("status")
        assert is_cmd
        assert "No active crews" in response

    def test_hire_command(self, config: Config):
        router = self._make_router(config)
        is_cmd, response = router._try_command("hire backend Add Stripe payments")
        assert is_cmd
        assert "Hired" in response
        assert "backend" in response
        assert len(router.crews) == 1
        assert router.session.active_crew_id is not None

    def test_hire_unknown_type(self, config: Config):
        router = self._make_router(config)
        is_cmd, response = router._try_command("hire nonexistent do stuff")
        assert is_cmd
        assert "Unknown crew type" in response

    def test_fire_command(self, config: Config):
        router = self._make_router(config)
        router._try_command("hire backend Add payments")
        crew_id = list(router.crews.keys())[0]

        is_cmd, response = router._try_command(f"fire {crew_id}")
        assert is_cmd
        assert "Fired" in response
        assert len(router.crews) == 0

    def test_switch_crew(self, config: Config):
        router = self._make_router(config)
        router._try_command("hire backend Add payments")
        router._try_command("hire frontend Build dashboard")

        crew_ids = list(router.crews.keys())
        first_id = crew_ids[0]

        is_cmd, response = router._try_command(f"talk to {first_id}")
        assert is_cmd
        assert router.session.active_crew_id == first_id

    def test_status_with_crews(self, config: Config):
        router = self._make_router(config)
        router._try_command("hire backend Add payments")

        is_cmd, response = router._try_command("status")
        assert is_cmd
        assert "Active Crews" in response

    def test_no_crew_suggests_hire(self, config: Config):
        router = self._make_router(config)
        response = router._suggest_hire("do something")
        assert "hire" in response.lower()

    def test_natural_hire_patterns(self, config: Config):
        router = self._make_router(config)

        # "hire a backend crew for X"
        is_cmd, _ = router._try_command("hire a backend crew for payments")
        assert is_cmd

    def test_crew_log_command(self, config: Config):
        router = self._make_router(config)
        router._try_command("hire backend Add payments")
        crew_id = list(router.crews.keys())[0]

        is_cmd, response = router._try_command(f"log {crew_id}")
        assert is_cmd
        # No messages yet
        assert "No conversation" in response

    def test_router_serialization(self, config: Config):
        router = self._make_router(config)
        router._try_command("hire backend Add payments")
        router.session.add_user_message("hello")

        data = router.to_dict()
        assert "session" in data
        assert "crews" in data
        assert len(data["crews"]) == 1

        restored = Router.from_dict(data, config)
        assert len(restored.crews) == 1
        assert len(restored.session.messages) >= 1

    def test_partial_crew_match(self, config: Config):
        router = self._make_router(config)
        router._try_command("hire backend Add payments")
        crew_id = list(router.crews.keys())[0]

        # Partial match should work
        partial = crew_id[:10]
        is_cmd, response = router._try_command(f"talk to {partial}")
        assert is_cmd
        assert "Now talking" in response
