"""Tests for session management, round-trip persistence, and stale worktree handling."""

from pathlib import Path

import pytest

from shipwright.config import Config, CrewDef, MemberDef
from shipwright.conversation.router import Router
from shipwright.conversation.session import Session
from shipwright.crew.crew import Crew, CrewStatus, EnterpriseCrew, TaskRecord
from shipwright.crew.registry import get_crew_def
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
    """Test full round-trip: create router -> hire crew -> save -> load -> verify."""

    def test_router_round_trip_with_crew(self, config: Config):
        """Router with a hired crew survives save/load cycle."""
        session = Session(id="test")
        router = Router(config=config, session=session, session_name="test")
        router._try_command("hire backend Add Stripe payments")

        assert len(router.crews) == 1
        crew_id = list(router.crews.keys())[0]

        # Save
        save_state(router.to_dict(), config, session_id="test")

        # Load
        data = load_state(config, session_id="test")
        assert data is not None

        restored = Router.from_dict(data, config)
        assert len(restored.crews) == 1
        assert crew_id in restored.crews
        assert restored.crews[crew_id].crew_type == "backend"
        assert restored.crews[crew_id].objective == "Add Stripe payments"
        assert restored.session.active_crew_id == crew_id

    def test_round_trip_preserves_conversation(self, config: Config):
        """Conversation history survives save/load."""
        session = Session(id="test")
        router = Router(config=config, session=session)
        router._try_command("hire backend Add payments")
        router.session.add_user_message("What's the plan?")
        router.session.add_lead_message("I'll start with the API.", crew_id=list(router.crews.keys())[0])

        save_state(router.to_dict(), config, session_id="convo-test")
        data = load_state(config, session_id="convo-test")
        restored = Router.from_dict(data, config)

        # Messages should be preserved
        assert len(restored.session.messages) >= 2
        texts = [m.text for m in restored.session.messages]
        assert "What's the plan?" in texts
        assert "I'll start with the API." in texts

    def test_round_trip_preserves_task_records(self, config: Config):
        """Task records survive save/load."""
        crew_def = get_crew_def("backend")
        crew = Crew.create("backend", crew_def, config, objective="Add payments")
        crew.task_records.append(TaskRecord(
            member_name="developer",
            task="Write the API endpoint",
            status="done",
            output="Endpoint created at /api/payments",
            started_at=100.0,
            finished_at=120.0,
            cost_usd=0.05,
        ))
        crew.task_records.append(TaskRecord(
            member_name="db_engineer",
            task="Create payments table",
            status="done",
            output="Migration added",
            started_at=100.0,
            finished_at=115.0,
            cost_usd=0.03,
        ))

        data = crew.to_dict()
        restored = Crew.from_dict(data, crew_def, config)

        assert len(restored.task_records) == 2
        assert restored.task_records[0].member_name == "developer"
        assert restored.task_records[0].status == "done"
        assert restored.task_records[0].output == "Endpoint created at /api/payments"
        assert restored.task_records[0].cost_usd == 0.05
        assert restored.task_records[1].member_name == "db_engineer"

    def test_round_trip_preserves_lead_conversation(self, config: Config):
        """CrewLead conversation history survives save/load."""
        crew_def = get_crew_def("backend")
        crew = Crew.create("backend", crew_def, config, objective="Test")
        crew.lead._conversation.append({"role": "user", "text": "hello"})
        crew.lead._conversation.append({"role": "lead", "text": "hi there"})
        crew.lead._session_id = "session-123"

        data = crew.to_dict()
        restored = Crew.from_dict(data, crew_def, config)

        assert len(restored.lead._conversation) == 2
        assert restored.lead._session_id == "session-123"

    def test_round_trip_preserves_crew_status(self, config: Config):
        """Crew status survives save/load."""
        crew_def = get_crew_def("backend")
        crew = Crew.create("backend", crew_def, config, objective="Test")
        crew.status = CrewStatus.PAUSED

        data = crew.to_dict()
        restored = Crew.from_dict(data, crew_def, config)
        assert restored.status == CrewStatus.PAUSED

    def test_round_trip_with_multiple_crews(self, config: Config):
        """Multiple crews survive save/load."""
        session = Session(id="test")
        router = Router(config=config, session=session)
        router._try_command("hire backend Add payments")
        router._try_command("hire frontend Build dashboard")

        assert len(router.crews) == 2

        save_state(router.to_dict(), config, session_id="multi")
        data = load_state(config, session_id="multi")
        restored = Router.from_dict(data, config)

        assert len(restored.crews) == 2
        types = {c.crew_type for c in restored.crews.values()}
        assert types == {"backend", "frontend"}

    def test_round_trip_preserves_pr_url(self, config: Config):
        """PR URL survives save/load."""
        crew_def = get_crew_def("backend")
        crew = Crew.create("backend", crew_def, config, objective="Test")
        crew.pr_url = "https://github.com/test/repo/pull/42"
        crew.status = CrewStatus.DONE

        data = crew.to_dict()
        restored = Crew.from_dict(data, crew_def, config)
        assert restored.pr_url == "https://github.com/test/repo/pull/42"
        assert restored.status == CrewStatus.DONE

    def test_round_trip_session_name_preserved(self, config: Config):
        """session_name is preserved through save/load."""
        session = Session(id="myproject")
        router = Router(config=config, session=session, session_name="myproject")
        router._try_command("hire backend Test")

        save_state(router.to_dict(), config, session_id="myproject")
        data = load_state(config, session_id="myproject")
        restored = Router.from_dict(data, config)
        assert restored.session_name == "myproject"


# ---------------------------------------------------------------------------
# Stale worktree handling
# ---------------------------------------------------------------------------


class TestStaleWorktree:
    """Test stale worktree detection and handling."""

    def test_missing_worktree_marked_stale(self, config: Config):
        """Crew with worktree pointing to a nonexistent path is marked stale."""
        crew_def = get_crew_def("backend")
        crew = Crew.create("backend", crew_def, config, objective="Test")

        data = crew.to_dict()
        data["worktree_path"] = "/nonexistent/path/to/worktree"
        data["branch"] = "shipwright/test"

        restored = Crew.from_dict(data, crew_def, config)
        assert restored.is_stale
        assert restored.worktree_path is None
        assert restored._stale_worktree == "/nonexistent/path/to/worktree"

    def test_existing_worktree_not_stale(self, tmp_path: Path):
        """Crew with worktree pointing to an existing path is not stale."""
        config = Config(repo_root=tmp_path, sessions_dir=tmp_path / "sessions")
        crew_def = get_crew_def("backend")
        crew = Crew.create("backend", crew_def, config, objective="Test")

        # Create an actual directory to simulate a worktree
        wt_path = tmp_path / "worktree"
        wt_path.mkdir()

        data = crew.to_dict()
        data["worktree_path"] = str(wt_path)
        data["branch"] = "shipwright/test"

        restored = Crew.from_dict(data, crew_def, config)
        assert not restored.is_stale
        assert restored.worktree_path == wt_path

    def test_stale_crew_summary_shows_warning(self, config: Config):
        """Stale crew summary includes STALE status and original path."""
        crew_def = get_crew_def("backend")
        data = {
            "id": "backend-test",
            "crew_type": "backend",
            "objective": "Test stale",
            "status": "idle",
            "branch": "shipwright/backend-test",
            "worktree_path": "/old/worktree/path",
            "pr_url": None,
            "created_at": 1000.0,
            "lead": {"session_id": None, "conversation": []},
            "task_records": [],
        }

        crew = Crew.from_dict(data, crew_def, config)
        summary = crew.summary

        assert "stale" in summary.lower()
        assert "/old/worktree/path" in summary

    def test_stale_worktree_doesnt_crash_router(self, config: Config):
        """Router load with missing worktrees doesn't crash."""
        session = Session(id="test")
        router = Router(config=config, session=session)
        router._try_command("hire backend Add payments")

        data = router.to_dict()
        # Inject a fake worktree path
        crew_id = list(data["crews"].keys())[0]
        data["crews"][crew_id]["worktree_path"] = "/this/path/does/not/exist"
        data["crews"][crew_id]["branch"] = "shipwright/fake"

        # Should not crash
        restored = Router.from_dict(data, config)
        assert len(restored.crews) == 1
        crew = list(restored.crews.values())[0]
        assert crew.is_stale
        assert crew.worktree_path is None

    def test_stale_worktree_preserved_on_re_save(self, config: Config):
        """Stale worktree path is preserved when re-saving."""
        crew_def = get_crew_def("backend")
        data = {
            "id": "backend-test",
            "crew_type": "backend",
            "objective": "Test",
            "status": "idle",
            "branch": "shipwright/test",
            "worktree_path": "/old/path",
            "pr_url": None,
            "created_at": 1000.0,
            "lead": {"session_id": None, "conversation": []},
            "task_records": [],
        }

        crew = Crew.from_dict(data, crew_def, config)
        assert crew.is_stale

        # Re-serialize
        re_saved = crew.to_dict()
        assert re_saved["worktree_path"] == "/old/path"

    def test_enterprise_stale_worktree(self, config: Config):
        """EnterpriseCrew handles stale worktrees."""
        crew_def = get_crew_def("enterprise")
        data = {
            "id": "enterprise-test",
            "crew_type": "enterprise",
            "objective": "Test",
            "status": "idle",
            "branch": "shipwright/enterprise",
            "worktree_path": "/nonexistent",
            "pr_url": None,
            "created_at": 1000.0,
            "is_enterprise": True,
            "depth": 1,
            "lead": {"session_id": None, "conversation": []},
            "task_records": [],
            "sub_crews": {},
        }

        crew = EnterpriseCrew.from_dict(data, crew_def, config)
        assert crew.is_stale
        assert crew.worktree_path is None
        assert crew._stale_worktree == "/nonexistent"


# ---------------------------------------------------------------------------
# Named sessions
# ---------------------------------------------------------------------------


class TestNamedSessions:
    """Test multiple named sessions."""

    def test_save_and_load_named_session(self, config: Config):
        """Can save and load a named session."""
        data = {"session": {"id": "proj1"}, "crews": {}}
        save_state(data, config, session_id="project-alpha")

        loaded = load_state(config, session_id="project-alpha")
        assert loaded is not None
        assert loaded["session"]["id"] == "proj1"

    def test_multiple_sessions_independent(self, config: Config):
        """Different sessions maintain independent state."""
        session_a = Session(id="alpha")
        router_a = Router(config=config, session=session_a, session_name="alpha")
        router_a._try_command("hire backend Add payments")

        session_b = Session(id="beta")
        router_b = Router(config=config, session=session_b, session_name="beta")
        router_b._try_command("hire frontend Build dashboard")
        router_b._try_command("hire qa Test everything")

        save_state(router_a.to_dict(), config, session_id="alpha")
        save_state(router_b.to_dict(), config, session_id="beta")

        # Load and verify independence
        data_a = load_state(config, session_id="alpha")
        data_b = load_state(config, session_id="beta")

        restored_a = Router.from_dict(data_a, config)
        restored_b = Router.from_dict(data_b, config)

        assert len(restored_a.crews) == 1
        assert len(restored_b.crews) == 2
        assert list(restored_a.crews.values())[0].crew_type == "backend"
        types_b = {c.crew_type for c in restored_b.crews.values()}
        assert types_b == {"frontend", "qa"}

    def test_default_session_name(self, config: Config):
        """Default session name is 'default'."""
        session = Session(id="test")
        router = Router(config=config, session=session)
        assert router.session_name == "default"

    def test_list_sessions_shows_all(self, config: Config):
        """list_sessions returns all saved session names."""
        save_state({"a": 1}, config, session_id="alpha")
        save_state({"b": 2}, config, session_id="beta")
        save_state({"c": 3}, config, session_id="gamma")

        sessions = list_sessions(config)
        assert set(sessions) >= {"alpha", "beta", "gamma"}

    def test_sessions_stored_in_sessions_dir(self, config: Config):
        """Sessions are stored in config.sessions_dir."""
        save_state({"test": True}, config, session_id="test-session")

        expected_path = config.sessions_dir / "test-session.json"
        assert expected_path.exists()


# ---------------------------------------------------------------------------
# Session management commands
# ---------------------------------------------------------------------------


class TestSessionCommands:
    """Test session management commands in router."""

    def _make_router(self, config: Config) -> Router:
        session = Session(id="test")
        return Router(config=config, session=session, session_name="default")

    def test_sessions_command_empty(self, config: Config):
        """sessions command with no saved sessions."""
        router = self._make_router(config)
        is_cmd, response = router._try_command("sessions")
        assert is_cmd
        assert "No saved sessions" in response

    def test_sessions_command_lists_sessions(self, config: Config):
        """sessions command lists saved sessions."""
        save_state({"a": 1}, config, session_id="alpha")
        save_state({"b": 2}, config, session_id="beta")

        router = self._make_router(config)
        is_cmd, response = router._try_command("sessions")
        assert is_cmd
        assert "alpha" in response
        assert "beta" in response

    def test_sessions_command_marks_active(self, config: Config):
        """sessions command marks the active session."""
        save_state({"a": 1}, config, session_id="default")

        router = self._make_router(config)
        is_cmd, response = router._try_command("sessions")
        assert is_cmd
        assert "(active)" in response

    def test_session_save_command(self, config: Config):
        """session save creates a named session file."""
        router = self._make_router(config)
        router._try_command("hire backend Add payments")

        is_cmd, response = router._try_command("session save my-snapshot")
        assert is_cmd
        assert "my-snapshot" in response

        # Verify file was created
        loaded = load_state(config, session_id="my-snapshot")
        assert loaded is not None
        assert len(loaded["crews"]) == 1

    def test_session_load_command(self, config: Config):
        """session load restores state from named session."""
        # Create and save a session with a crew
        router1 = self._make_router(config)
        router1._try_command("hire backend Add payments")
        save_state(router1.to_dict(), config, session_id="saved-session")

        # Start a fresh router
        router2 = self._make_router(config)
        assert len(router2.crews) == 0

        # Load the saved session
        is_cmd, response = router2._try_command("session load saved-session")
        assert is_cmd
        assert "Loaded session" in response
        assert "1 crew(s)" in response
        assert len(router2.crews) == 1
        assert router2.session_name == "saved-session"

    def test_session_load_nonexistent(self, config: Config):
        """session load with unknown name returns error."""
        router = self._make_router(config)
        is_cmd, response = router._try_command("session load nonexistent")
        assert is_cmd
        assert "No session named" in response

    def test_session_load_with_stale_worktrees(self, config: Config):
        """session load warns about stale worktrees."""
        # Create a session with a fake worktree
        router1 = self._make_router(config)
        router1._try_command("hire backend Add payments")
        data = router1.to_dict()
        crew_id = list(data["crews"].keys())[0]
        data["crews"][crew_id]["worktree_path"] = "/nonexistent"
        data["crews"][crew_id]["branch"] = "shipwright/test"
        save_state(data, config, session_id="stale-session")

        # Load in fresh router
        router2 = self._make_router(config)
        is_cmd, response = router2._try_command("session load stale-session")
        assert is_cmd
        assert "stale" in response.lower()

    def test_session_clear_command(self, config: Config):
        """session clear resets all state."""
        router = self._make_router(config)
        router._try_command("hire backend Add payments")
        router._try_command("hire frontend Build dashboard")
        assert len(router.crews) == 2

        is_cmd, response = router._try_command("session clear")
        assert is_cmd
        assert "cleared" in response.lower()
        assert len(router.crews) == 0
        assert router.session.active_crew_id is None

    def test_session_clear_preserves_session_id(self, config: Config):
        """session clear keeps the session id."""
        router = self._make_router(config)
        original_id = router.session.id

        router._try_command("session clear")
        assert router.session.id == original_id

    def test_help_includes_session_commands(self, config: Config):
        """help output mentions session management commands."""
        router = self._make_router(config)
        is_cmd, response = router._try_command("help")
        assert is_cmd
        assert "sessions" in response
        assert "session save" in response
        assert "session load" in response
        assert "session clear" in response


# ---------------------------------------------------------------------------
# State directory
# ---------------------------------------------------------------------------


class TestStateDirectory:
    """Test that state is stored in the correct directory."""

    def test_state_in_sessions_dir(self, config: Config):
        """State files go to config.sessions_dir."""
        save_state({"test": True}, config, session_id="test")
        assert (config.sessions_dir / "test.json").exists()

    def test_clear_state_removes_file(self, config: Config):
        """clear_state removes the session file."""
        save_state({"test": True}, config, session_id="to-delete")
        assert load_state(config, session_id="to-delete") is not None

        clear_state(config, session_id="to-delete")
        assert load_state(config, session_id="to-delete") is None

    def test_list_sessions_after_clear(self, config: Config):
        """list_sessions reflects cleared sessions."""
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
        name, args = _extract_session_flag(["hire", "backend", "test"])
        assert name == "default"
        assert args == ["hire", "backend", "test"]

    def test_session_flag_at_start(self):
        from shipwright.main import _extract_session_flag
        name, args = _extract_session_flag(["--session", "myproj", "hire", "backend", "test"])
        assert name == "myproj"
        assert args == ["hire", "backend", "test"]

    def test_session_flag_at_end(self):
        from shipwright.main import _extract_session_flag
        name, args = _extract_session_flag(["hire", "backend", "test", "--session", "myproj"])
        assert name == "myproj"
        assert args == ["hire", "backend", "test"]

    def test_session_flag_in_middle(self):
        from shipwright.main import _extract_session_flag
        name, args = _extract_session_flag(["hire", "--session", "myproj", "backend", "test"])
        assert name == "myproj"
        assert args == ["hire", "backend", "test"]

    def test_empty_args(self):
        from shipwright.main import _extract_session_flag
        name, args = _extract_session_flag([])
        assert name == "default"
        assert args == []
