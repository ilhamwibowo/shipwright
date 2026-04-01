"""Tests for session management, round-trip persistence, and named sessions (V2)."""

from pathlib import Path

import pytest

from shipwright.config import Config
from shipwright.conversation.router import Router
from shipwright.conversation.session import Session
from shipwright.company.company import Company
from shipwright.company.roles import get_role_def
from shipwright.persistence.store import (
    clear_state,
    list_sessions,
    load_state,
    save_state,
)


# ---------------------------------------------------------------------------
# Round-trip persistence
# ---------------------------------------------------------------------------


class TestRoundTrip:
    """Test full round-trip: create router -> hire employee -> save -> load -> verify."""

    def test_router_round_trip_with_employee(self, config: Config):
        """Router with a hired employee survives save/load cycle."""
        session = Session(id="test")
        router = Router(config=config, session=session, session_name="test")
        router._try_sync_command("hire backend-dev", "hire backend-dev")

        assert len(router.company.employees) == 1
        emp_name = list(router.company.employees.keys())[0]

        # Save
        save_state(router.to_dict(), config, session_id="test")

        # Load
        data = load_state(config, session_id="test")
        assert data is not None

        restored = Router.from_dict(data, config)
        assert len(restored.company.employees) == 1
        assert emp_name in restored.company.employees
        assert restored.company.employees[emp_name].role == "backend-dev"

    def test_round_trip_preserves_conversation(self, config: Config):
        """Conversation history survives save/load."""
        session = Session(id="test")
        router = Router(config=config, session=session)
        router._try_sync_command("hire backend-dev", "hire backend-dev")
        router.session.add_user_message("What's the plan?")
        router.session.add_lead_message(
            "I'll start with the API.",
            crew_id=list(router.company.employees.keys())[0],
        )

        save_state(router.to_dict(), config, session_id="convo-test")
        data = load_state(config, session_id="convo-test")
        restored = Router.from_dict(data, config)

        assert len(restored.session.messages) >= 2
        texts = [m.text for m in restored.session.messages]
        assert "What's the plan?" in texts
        assert "I'll start with the API." in texts

    def test_round_trip_preserves_teams(self, config: Config):
        """Teams survive save/load."""
        session = Session(id="test")
        router = Router(config=config, session=session)
        router._try_sync_command("hire backend-dev", "hire backend-dev")
        router._try_sync_command("hire frontend-dev", "hire frontend-dev")
        emp_names = list(router.company.employees.keys())

        router.company.create_team("core")
        router.company.assign_to_team(emp_names[0], "core")
        router.company.promote_to_lead(emp_names[0], "core")

        save_state(router.to_dict(), config, session_id="team-test")
        data = load_state(config, session_id="team-test")
        restored = Router.from_dict(data, config)

        assert "core" in restored.company.teams
        assert restored.company.teams["core"].lead == emp_names[0]
        assert restored.company.employees[emp_names[0]].is_lead is True

    def test_round_trip_with_multiple_employees(self, config: Config):
        """Multiple employees survive save/load."""
        session = Session(id="test")
        router = Router(config=config, session=session)
        router._try_sync_command("hire backend-dev", "hire backend-dev")
        router._try_sync_command("hire frontend-dev", "hire frontend-dev")

        assert len(router.company.employees) == 2

        save_state(router.to_dict(), config, session_id="multi")
        data = load_state(config, session_id="multi")
        restored = Router.from_dict(data, config)

        assert len(restored.company.employees) == 2
        roles = {e.role for e in restored.company.employees.values()}
        assert roles == {"backend-dev", "frontend-dev"}

    def test_round_trip_session_name_preserved(self, config: Config):
        """session_name is preserved through save/load."""
        session = Session(id="myproject")
        router = Router(config=config, session=session, session_name="myproject")
        router._try_sync_command("hire backend-dev", "hire backend-dev")

        save_state(router.to_dict(), config, session_id="myproject")
        data = load_state(config, session_id="myproject")
        restored = Router.from_dict(data, config)
        assert restored.session_name == "myproject"


# ---------------------------------------------------------------------------
# Named sessions
# ---------------------------------------------------------------------------


class TestNamedSessions:
    """Test multiple named sessions."""

    def test_save_and_load_named_session(self, config: Config):
        data = {"session": {"id": "proj1"}, "company": {}}
        save_state(data, config, session_id="project-alpha")

        loaded = load_state(config, session_id="project-alpha")
        assert loaded is not None
        assert loaded["session"]["id"] == "proj1"

    def test_multiple_sessions_independent(self, config: Config):
        """Different sessions maintain independent state."""
        session_a = Session(id="alpha")
        router_a = Router(config=config, session=session_a, session_name="alpha")
        router_a._try_sync_command("hire backend-dev", "hire backend-dev")

        session_b = Session(id="beta")
        router_b = Router(config=config, session=session_b, session_name="beta")
        router_b._try_sync_command("hire frontend-dev", "hire frontend-dev")
        router_b._try_sync_command("hire qa-engineer", "hire qa-engineer")

        save_state(router_a.to_dict(), config, session_id="alpha")
        save_state(router_b.to_dict(), config, session_id="beta")

        data_a = load_state(config, session_id="alpha")
        data_b = load_state(config, session_id="beta")

        restored_a = Router.from_dict(data_a, config)
        restored_b = Router.from_dict(data_b, config)

        assert len(restored_a.company.employees) == 1
        assert len(restored_b.company.employees) == 2
        roles_a = {e.role for e in restored_a.company.employees.values()}
        roles_b = {e.role for e in restored_b.company.employees.values()}
        assert roles_a == {"backend-dev"}
        assert roles_b == {"frontend-dev", "qa-engineer"}

    def test_default_session_name(self, config: Config):
        session = Session(id="test")
        router = Router(config=config, session=session)
        assert router.session_name == "default"

    def test_list_sessions_shows_all(self, config: Config):
        save_state({"a": 1}, config, session_id="alpha")
        save_state({"b": 2}, config, session_id="beta")
        save_state({"c": 3}, config, session_id="gamma")

        sessions = list_sessions(config)
        assert set(sessions) >= {"alpha", "beta", "gamma"}


# ---------------------------------------------------------------------------
# Session management commands
# ---------------------------------------------------------------------------


class TestSessionCommands:
    """Test session management commands in router."""

    def _make_router(self, config: Config) -> Router:
        session = Session(id="test")
        return Router(config=config, session=session, session_name="default")

    def test_sessions_command_empty(self, config: Config):
        router = self._make_router(config)
        is_cmd, response = router._try_sync_command("sessions", "sessions")
        assert is_cmd
        assert "No saved sessions" in response

    def test_sessions_command_lists_sessions(self, config: Config):
        save_state({"a": 1}, config, session_id="alpha")
        save_state({"b": 2}, config, session_id="beta")

        router = self._make_router(config)
        is_cmd, response = router._try_sync_command("sessions", "sessions")
        assert is_cmd
        assert "alpha" in response
        assert "beta" in response

    def test_sessions_command_marks_active(self, config: Config):
        save_state({"a": 1}, config, session_id="default")

        router = self._make_router(config)
        is_cmd, response = router._try_sync_command("sessions", "sessions")
        assert is_cmd
        assert "(active)" in response

    def test_session_save_command(self, config: Config):
        router = self._make_router(config)
        router._try_sync_command("hire backend-dev", "hire backend-dev")

        is_cmd, response = router._try_sync_command(
            "session save my-snapshot", "session save my-snapshot",
        )
        assert is_cmd
        assert "my-snapshot" in response

        loaded = load_state(config, session_id="my-snapshot")
        assert loaded is not None
        assert len(loaded["company"]["employees"]) == 1

    def test_session_load_command(self, config: Config):
        # Create and save a session
        router1 = self._make_router(config)
        router1._try_sync_command("hire backend-dev", "hire backend-dev")
        save_state(router1.to_dict(), config, session_id="saved-session")

        # Start a fresh router
        router2 = self._make_router(config)
        assert len(router2.company.employees) == 0

        # Load the saved session
        is_cmd, response = router2._try_sync_command(
            "session load saved-session", "session load saved-session",
        )
        assert is_cmd
        assert "Loaded session" in response
        assert "1 employee(s)" in response
        assert len(router2.company.employees) == 1
        assert router2.session_name == "saved-session"

    def test_session_load_nonexistent(self, config: Config):
        router = self._make_router(config)
        is_cmd, response = router._try_sync_command(
            "session load nonexistent", "session load nonexistent",
        )
        assert is_cmd
        assert "No session named" in response

    def test_session_clear_command(self, config: Config):
        router = self._make_router(config)
        router._try_sync_command("hire backend-dev", "hire backend-dev")
        router._try_sync_command("hire frontend-dev", "hire frontend-dev")
        assert len(router.company.employees) == 2

        # First call asks for confirmation
        is_cmd, response = router._try_sync_command("session clear", "session clear")
        assert is_cmd
        assert "confirm" in response.lower()
        assert len(router.company.employees) == 2

        # Second call with confirm actually clears
        is_cmd, response = router._try_sync_command(
            "session clear confirm", "session clear confirm"
        )
        assert is_cmd
        assert "cleared" in response.lower()
        assert len(router.company.employees) == 0

    def test_session_clear_preserves_session_id(self, config: Config):
        router = self._make_router(config)
        original_id = router.session.id

        # Empty company clears immediately
        router._try_sync_command("session clear", "session clear")
        assert router.session.id == original_id

    def test_help_includes_session_commands(self, config: Config):
        router = self._make_router(config)
        is_cmd, response = router._try_sync_command("help", "help")
        assert is_cmd
        assert "sessions" in response or "session" in response.lower()
        assert "save" in response
        assert "session load" in response


# ---------------------------------------------------------------------------
# State directory
# ---------------------------------------------------------------------------


class TestStateDirectory:
    """Test that state is stored in the correct directory."""

    def test_state_in_sessions_dir(self, config: Config):
        save_state({"test": True}, config, session_id="test")
        assert (config.sessions_dir / "test.json").exists()

    def test_clear_state_removes_file(self, config: Config):
        save_state({"test": True}, config, session_id="to-delete")
        assert load_state(config, session_id="to-delete") is not None

        clear_state(config, session_id="to-delete")
        assert load_state(config, session_id="to-delete") is None

    def test_list_sessions_after_clear(self, config: Config):
        save_state({"a": 1}, config, session_id="keep")
        save_state({"b": 2}, config, session_id="remove")

        clear_state(config, session_id="remove")
        sessions = list_sessions(config)

        assert "keep" in sessions
        assert "remove" not in sessions


# ---------------------------------------------------------------------------
# CLI extract session flag
# ---------------------------------------------------------------------------


class TestExtractSessionFlag:
    """Test the --session flag extraction from main.py."""

    def test_no_session_flag(self):
        from shipwright.main import _extract_session_flag
        name, args = _extract_session_flag(["hire", "backend-dev"])
        assert name == "default"
        assert args == ["hire", "backend-dev"]

    def test_session_flag_at_start(self):
        from shipwright.main import _extract_session_flag
        name, args = _extract_session_flag(["--session", "myproj", "hire", "backend-dev"])
        assert name == "myproj"
        assert args == ["hire", "backend-dev"]

    def test_session_flag_at_end(self):
        from shipwright.main import _extract_session_flag
        name, args = _extract_session_flag(["hire", "backend-dev", "--session", "myproj"])
        assert name == "myproj"
        assert args == ["hire", "backend-dev"]

    def test_session_flag_in_middle(self):
        from shipwright.main import _extract_session_flag
        name, args = _extract_session_flag(["hire", "--session", "myproj", "backend-dev"])
        assert name == "myproj"
        assert args == ["hire", "backend-dev"]

    def test_empty_args(self):
        from shipwright.main import _extract_session_flag
        name, args = _extract_session_flag([])
        assert name == "default"
        assert args == []
