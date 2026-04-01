"""Tests for conversation session and router (V2)."""

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
        session.add_system_message("Employee hired.")

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
        session.add_lead_message("hi", crew_id="emp-1")
        session.active_crew_id = "emp-1"

        data = session.to_dict()
        assert data["id"] == "test"
        assert len(data["messages"]) == 2

        restored = Session.from_dict(data)
        assert restored.id == "test"
        assert len(restored.messages) == 2
        assert restored.active_crew_id == "emp-1"


class TestMessage:
    def test_message_defaults(self):
        msg = Message(role="user", text="hello")
        assert msg.crew_id is None
        assert msg.timestamp > 0

    def test_message_roundtrip(self):
        msg = Message(role="lead", text="response", crew_id="emp-1")
        data = msg.to_dict()
        restored = Message.from_dict(data)
        assert restored.role == "lead"
        assert restored.text == "response"
        assert restored.crew_id == "emp-1"


# ---------------------------------------------------------------------------
# Router
# ---------------------------------------------------------------------------


class TestRouter:
    def _make_router(self, config: Config) -> Router:
        session = Session(id="test")
        return Router(config=config, session=session)

    def test_help_command(self, config: Config):
        router = self._make_router(config)
        is_cmd, response = router._try_sync_command("help", "help")
        assert is_cmd
        assert "Shipwright" in response
        assert "hire" in response

    def test_roles_command(self, config: Config):
        router = self._make_router(config)
        is_cmd, response = router._try_sync_command("roles", "roles")
        assert is_cmd
        assert "Available Roles" in response
        assert "architect" in response
        assert "backend-dev" in response

    def test_hire_command(self, config: Config):
        router = self._make_router(config)
        is_cmd, response = router._try_sync_command("hire backend-dev", "hire backend-dev")
        assert is_cmd
        assert "Hired" in response
        assert len(router.company.employees) == 1

    def test_hire_with_custom_name(self, config: Config):
        router = self._make_router(config)
        is_cmd, response = router._try_sync_command(
            'hire backend-dev as "Kai"', 'hire backend-dev as "kai"',
        )
        assert is_cmd
        assert "Kai" in response
        assert "Kai" in router.company.employees

    def test_hire_unknown_role(self, config: Config):
        router = self._make_router(config)
        is_cmd, response = router._try_sync_command("hire nonexistent", "hire nonexistent")
        assert is_cmd
        assert "Unknown role" in response

    def test_fire_command(self, config: Config):
        router = self._make_router(config)
        router._try_sync_command("hire backend-dev", "hire backend-dev")
        emp_name = list(router.company.employees.keys())[0]

        # First call asks for confirmation
        is_cmd, response = router._try_sync_command(f"fire {emp_name}", f"fire {emp_name.lower()}")
        assert is_cmd
        assert "confirm" in response.lower()
        assert len(router.company.employees) == 1

        # Second call with confirm actually fires
        is_cmd, response = router._try_sync_command(
            f"fire {emp_name} confirm", f"fire {emp_name.lower()} confirm"
        )
        assert is_cmd
        assert "Fired" in response
        assert len(router.company.employees) == 0

    def test_team_create_command(self, config: Config):
        router = self._make_router(config)
        is_cmd, response = router._try_sync_command("team create backend", "team create backend")
        assert is_cmd
        assert "Created team" in response
        assert "backend" in router.company.teams

    def test_promote_command(self, config: Config):
        router = self._make_router(config)
        router._try_sync_command("hire backend-dev", "hire backend-dev")
        emp_name = list(router.company.employees.keys())[0]
        router._try_sync_command("team create core", "team create core")

        is_cmd, response = router._try_sync_command(
            f"promote {emp_name} to lead of core",
            f"promote {emp_name.lower()} to lead of core",
        )
        assert is_cmd
        assert "Team Lead" in response

    def test_assign_to_team_command(self, config: Config):
        router = self._make_router(config)
        router._try_sync_command("hire backend-dev", "hire backend-dev")
        emp_name = list(router.company.employees.keys())[0]
        router._try_sync_command("team create core", "team create core")

        # This goes through handle_message (async path) for "assign X to Y"
        # but _try_sync_command won't match. It's parsed in handle_message.
        # Test the direct method instead:
        response = router._assign_to_team_cmd(emp_name, "core")
        assert "added to team" in response

    def test_talk_command(self, config: Config):
        router = self._make_router(config)
        router._try_sync_command("hire backend-dev", "hire backend-dev")
        router._try_sync_command("hire frontend-dev", "hire frontend-dev")
        emp_names = list(router.company.employees.keys())

        is_cmd, response = router._try_sync_command(
            f"talk {emp_names[1]}", f"talk {emp_names[1].lower()}",
        )
        assert is_cmd
        assert "Switched to" in response

    def test_status_command(self, config: Config):
        router = self._make_router(config)
        is_cmd, response = router._try_sync_command("status", "status")
        assert is_cmd
        assert "No employees" in response

    def test_status_with_employees(self, config: Config):
        router = self._make_router(config)
        router._try_sync_command("hire backend-dev", "hire backend-dev")

        is_cmd, response = router._try_sync_command("status", "status")
        assert is_cmd
        assert "Status" in response
        assert "idle" in response

    def test_costs_command(self, config: Config):
        router = self._make_router(config)
        is_cmd, response = router._try_sync_command("costs", "costs")
        assert is_cmd
        assert "Cost Report" in response

    def test_cost_aliases(self, config: Config):
        router = self._make_router(config)
        for cmd in ("costs", "cost", "spending", "budget"):
            is_cmd, _ = router._try_sync_command(cmd, cmd)
            assert is_cmd, f"Command '{cmd}' not recognized"

    def test_costs_command_shows_task_count(self, config: Config):
        from shipwright.company.employee import Task

        router = self._make_router(config)
        router._try_sync_command("hire backend-dev", "hire backend-dev")
        emp_name = list(router.company.employees.keys())[0]
        emp = router.company.employees[emp_name]
        emp.cost_total_usd = 0.10
        emp.task_history.append(Task(
            id="t1", description="Write API", assigned_to=emp_name,
            status="done", cost_usd=0.10, duration_ms=60000,
        ))

        is_cmd, response = router._try_sync_command("costs", "costs")
        assert is_cmd
        assert "1 task" in response
        assert "$0.10" in response
        assert "1m" in response

    def test_history_command(self, config: Config):
        router = self._make_router(config)
        router._try_sync_command("hire backend-dev", "hire backend-dev")
        emp_name = list(router.company.employees.keys())[0]

        is_cmd, response = router._try_sync_command(
            f"history {emp_name}", f"history {emp_name.lower()}",
        )
        assert is_cmd
        assert "No task history" in response

    def test_shop_command(self, config: Config):
        router = self._make_router(config)
        is_cmd, response = router._try_sync_command("shop", "shop")
        assert is_cmd
        assert "Available" in response
        assert "architect" in response

    def test_installed_command(self, config: Config):
        router = self._make_router(config)
        is_cmd, response = router._try_sync_command("installed", "installed")
        assert is_cmd
        assert "No custom" in response

    def test_inspect_command(self, config: Config):
        router = self._make_router(config)
        is_cmd, response = router._try_sync_command("inspect backend-dev", "inspect backend-dev")
        assert is_cmd
        assert "Backend Developer" in response

    def test_inspect_unknown(self, config: Config):
        router = self._make_router(config)
        is_cmd, response = router._try_sync_command("inspect nonexistent", "inspect nonexistent")
        assert is_cmd
        assert "Unknown" in response

    def test_sessions_command(self, config: Config):
        router = self._make_router(config)
        is_cmd, response = router._try_sync_command("sessions", "sessions")
        assert is_cmd
        assert "No saved sessions" in response

    def test_team_overview_command(self, config: Config):
        router = self._make_router(config)
        is_cmd, response = router._try_sync_command("org", "org")
        assert is_cmd
        assert "No employees" in response

    def test_no_employee_suggests_hire(self, config: Config):
        router = self._make_router(config)
        response = router._suggest_hire("do something")
        assert "hire" in response.lower()


# ---------------------------------------------------------------------------
# Router Serialization
# ---------------------------------------------------------------------------


class TestRouterSerialization:
    def test_to_dict_from_dict_round_trip(self, config: Config):
        session = Session(id="test")
        router = Router(config=config, session=session, session_name="myproject")
        router._try_sync_command("hire backend-dev", "hire backend-dev")
        router.session.add_user_message("hello")

        data = router.to_dict()
        assert "session" in data
        assert "company" in data
        assert data["session_name"] == "myproject"

        restored = Router.from_dict(data, config)
        assert len(restored.company.employees) == 1
        assert restored.session_name == "myproject"
        assert len(restored.session.messages) >= 1

    def test_empty_router_round_trip(self, config: Config):
        session = Session(id="test")
        router = Router(config=config, session=session)

        data = router.to_dict()
        restored = Router.from_dict(data, config)
        assert len(restored.company.employees) == 0

    def test_backward_compat_old_crew_state(self, config: Config):
        """Old crew-based state is ignored gracefully."""
        data = {
            "session": {"id": "test", "messages": [], "active_crew_id": None},
            "crews": {"some-crew-id": {"crew_type": "backend", "objective": "Test"}},
            "session_name": "test",
        }
        restored = Router.from_dict(data, config)
        # Old crew data is ignored; company is empty
        assert len(restored.company.employees) == 0
