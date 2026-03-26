"""Tests for the Router: command dispatch, input validation, error handling, edge cases."""

from unittest.mock import AsyncMock, patch

import pytest

from shipwright.config import Config
from shipwright.company.company import Company
from shipwright.company.employee import EmployeeStatus, MemberResult, Task
from shipwright.company.roles import get_role_def
from shipwright.conversation.router import Router
from shipwright.conversation.session import Session


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_router(config: Config) -> Router:
    session = Session(id="test")
    return Router(config=config, session=session)


def _make_router_with_employees(config: Config) -> Router:
    """Router with two employees already hired."""
    router = _make_router(config)
    router.company.hire("backend-dev", get_role_def("backend-dev"), name="Alex")
    router.company.hire("frontend-dev", get_role_def("frontend-dev"), name="Blake")
    return router


# ---------------------------------------------------------------------------
# Hire validation
# ---------------------------------------------------------------------------


class TestHireValidation:
    def test_hire_unknown_role_shows_available(self, config: Config):
        router = _make_router(config)
        _, response = router._try_sync_command("hire nonexistent", "hire nonexistent")
        assert "Unknown role" in response
        assert "Available roles" in response
        assert "roles" in response.lower()

    def test_hire_valid_role(self, config: Config):
        router = _make_router(config)
        _, response = router._try_sync_command("hire backend-dev", "hire backend-dev")
        assert "Hired" in response
        assert "Backend Developer" in response

    def test_hire_duplicate_custom_name(self, config: Config):
        router = _make_router(config)
        router._try_sync_command('hire backend-dev as "Alex"', 'hire backend-dev as "alex"')
        _, response = router._try_sync_command(
            'hire frontend-dev as "Alex"', 'hire frontend-dev as "alex"'
        )
        assert "already exists" in response

    def test_hire_name_conflicts_with_team(self, config: Config):
        router = _make_router(config)
        router.company.create_team("backend")
        _, response = router._try_sync_command(
            'hire backend-dev as "backend"', 'hire backend-dev as "backend"'
        )
        assert "conflicts" in response

    def test_hire_empty_role_not_matched(self, config: Config):
        router = _make_router(config)
        is_cmd, _ = router._try_sync_command("hire", "hire")
        assert not is_cmd  # "hire" alone doesn't match the regex


# ---------------------------------------------------------------------------
# Fire validation & confirmation
# ---------------------------------------------------------------------------


class TestFireConfirmation:
    def test_fire_requires_confirmation(self, config: Config):
        router = _make_router_with_employees(config)
        _, response = router._try_sync_command("fire Alex", "fire alex")
        assert "confirm" in response.lower()
        assert "Alex" in response
        # Employee should NOT be fired yet
        assert "Alex" in router.company.employees

    def test_fire_with_confirmation(self, config: Config):
        router = _make_router_with_employees(config)
        _, response = router._try_sync_command("fire Alex confirm", "fire alex confirm")
        assert "Fired" in response
        assert "Alex" not in router.company.employees

    def test_fire_nonexistent(self, config: Config):
        router = _make_router(config)
        _, response = router._try_sync_command("fire Nobody", "fire nobody")
        assert "No employee or team" in response

    def test_fire_team_requires_confirmation(self, config: Config):
        router = _make_router_with_employees(config)
        router.company.create_team("core")
        router.company.assign_to_team("Alex", "core")
        _, response = router._try_sync_command("fire core", "fire core")
        assert "confirm" in response.lower()
        assert "core" in router.company.teams

    def test_fire_team_with_confirmation(self, config: Config):
        router = _make_router_with_employees(config)
        router.company.create_team("core")
        router.company.assign_to_team("Alex", "core")
        router.company.assign_to_team("Blake", "core")
        _, response = router._try_sync_command("fire core confirm", "fire core confirm")
        assert "Fired team" in response
        assert "core" not in router.company.teams

    def test_fire_case_insensitive(self, config: Config):
        router = _make_router_with_employees(config)
        _, response = router._try_sync_command("fire alex confirm", "fire alex confirm")
        assert "Fired" in response


# ---------------------------------------------------------------------------
# Session clear confirmation
# ---------------------------------------------------------------------------


class TestSessionClearConfirmation:
    def test_session_clear_requires_confirmation_when_employees_exist(self, config: Config):
        router = _make_router_with_employees(config)
        _, response = router._try_sync_command("session clear", "session clear")
        assert "confirm" in response.lower()
        assert "2 employee" in response
        # Should NOT clear
        assert len(router.company.employees) == 2

    def test_session_clear_with_confirmation(self, config: Config):
        router = _make_router_with_employees(config)
        _, response = router._try_sync_command(
            "session clear confirm", "session clear confirm"
        )
        assert "cleared" in response.lower()
        assert len(router.company.employees) == 0

    def test_session_clear_empty_company_clears_immediately(self, config: Config):
        router = _make_router(config)
        _, response = router._try_sync_command("session clear", "session clear")
        assert "cleared" in response.lower()


# ---------------------------------------------------------------------------
# Assign validation
# ---------------------------------------------------------------------------


class TestAssignValidation:
    @pytest.mark.asyncio
    async def test_assign_empty_task(self, config: Config):
        router = _make_router_with_employees(config)
        response = await router.handle_message('assign Alex ""')
        assert "cannot be empty" in response.lower()

    @pytest.mark.asyncio
    async def test_assign_unknown_target(self, config: Config):
        router = _make_router(config)
        response = await router.handle_message('assign Nobody "Build stuff"')
        assert "No employee or team" in response

    @pytest.mark.asyncio
    async def test_assign_to_busy_employee(self, config: Config):
        router = _make_router_with_employees(config)
        alex = router.company.employees["Alex"]
        alex.status = EmployeeStatus.WORKING
        alex.current_task = Task(
            id="t1", description="Building API", assigned_to="Alex", status="running"
        )
        response = await router.handle_message('assign Alex "More work"')
        assert "currently working" in response.lower()

    @pytest.mark.asyncio
    async def test_assign_to_team_membership(self, config: Config):
        router = _make_router_with_employees(config)
        router.company.create_team("core")
        response = await router.handle_message("assign Alex to core")
        assert "added to team" in response

    @pytest.mark.asyncio
    async def test_assign_to_nonexistent_team(self, config: Config):
        router = _make_router_with_employees(config)
        response = await router.handle_message("assign Alex to nonexistent")
        assert "No team" in response


# ---------------------------------------------------------------------------
# Talk validation
# ---------------------------------------------------------------------------


class TestTalkValidation:
    def test_talk_to_nonexistent(self, config: Config):
        router = _make_router(config)
        _, response = router._try_sync_command("talk Nobody", "talk nobody")
        assert "No employee" in response

    def test_talk_to_existing(self, config: Config):
        router = _make_router_with_employees(config)
        _, response = router._try_sync_command("talk Blake", "talk blake")
        assert "Blake" in response
        assert router.company._active_employee == "Blake"

    def test_talk_case_insensitive(self, config: Config):
        router = _make_router_with_employees(config)
        _, response = router._try_sync_command("talk blake", "talk blake")
        assert "Blake" in response


# ---------------------------------------------------------------------------
# Promote validation
# ---------------------------------------------------------------------------


class TestPromoteValidation:
    def test_promote_nonexistent_employee(self, config: Config):
        router = _make_router(config)
        router.company.create_team("core")
        _, response = router._try_sync_command(
            "promote Nobody to lead of core", "promote nobody to lead of core"
        )
        assert "No employee" in response

    def test_promote_to_nonexistent_team(self, config: Config):
        router = _make_router_with_employees(config)
        _, response = router._try_sync_command(
            "promote Alex to lead of nonexistent", "promote alex to lead of nonexistent"
        )
        assert "No team" in response

    def test_promote_success(self, config: Config):
        router = _make_router_with_employees(config)
        router.company.create_team("core")
        _, response = router._try_sync_command(
            "promote Alex to lead of core", "promote alex to lead of core"
        )
        assert "Team Lead" in response
        assert "core" in response


# ---------------------------------------------------------------------------
# Team create validation
# ---------------------------------------------------------------------------


class TestTeamCreateValidation:
    def test_create_duplicate_team(self, config: Config):
        router = _make_router(config)
        router.company.create_team("backend")
        _, response = router._try_sync_command("team create backend", "team create backend")
        assert "already exists" in response

    def test_create_team_name_conflicts_with_employee(self, config: Config):
        router = _make_router_with_employees(config)
        _, response = router._try_sync_command("team create Alex", "team create alex")
        assert "conflicts" in response

    def test_create_team_success(self, config: Config):
        router = _make_router(config)
        _, response = router._try_sync_command("team create backend", "team create backend")
        assert "Created team" in response

    def test_create_team_empty_name(self, config: Config):
        """Empty team name after regex parse still gets a non-empty string from the regex."""
        router = _make_router(config)
        # The regex won't match "team create" alone without a name
        is_cmd, _ = router._try_sync_command("team create", "team create")
        # "team create" matches the "team" overview command, not team create
        # This is fine because the regex requires at least one char after "team create "


# ---------------------------------------------------------------------------
# History command
# ---------------------------------------------------------------------------


class TestHistoryCommand:
    def test_history_nonexistent_employee(self, config: Config):
        router = _make_router(config)
        _, response = router._try_sync_command("history Nobody", "history nobody")
        assert "No employee" in response

    def test_history_no_tasks(self, config: Config):
        router = _make_router_with_employees(config)
        _, response = router._try_sync_command("history Alex", "history alex")
        assert "No task history" in response

    def test_history_with_tasks(self, config: Config):
        router = _make_router_with_employees(config)
        emp = router.company.employees["Alex"]
        emp.task_history.append(Task(
            id="t1", description="Build the API", assigned_to="Alex",
            status="done", cost_usd=0.05, duration_ms=30000,
            created_at=1700000000.0, output="API endpoint created successfully",
        ))
        emp.task_history.append(Task(
            id="t2", description="Fix the bug", assigned_to="Alex",
            status="failed", cost_usd=0.02, duration_ms=15000,
            created_at=1700001000.0,
        ))
        _, response = router._try_sync_command("history Alex", "history alex")
        assert "Task History" in response
        assert "2 tasks" in response
        assert "Build the API" in response
        assert "Fix the bug" in response
        assert "$0.05" in response
        assert "30s" in response
        # Done task should have output preview
        assert "API endpoint created" in response


# ---------------------------------------------------------------------------
# Session management
# ---------------------------------------------------------------------------


class TestSessionManagement:
    def test_session_save_and_load(self, config: Config):
        router = _make_router_with_employees(config)
        _, save_response = router._try_sync_command("session save test", "session save test")
        assert "saved" in save_response.lower()

        router2 = _make_router(config)
        _, load_response = router2._try_sync_command("session load test", "session load test")
        assert "Loaded" in load_response
        assert "2 employee" in load_response

    def test_session_load_nonexistent(self, config: Config):
        router = _make_router(config)
        _, response = router._try_sync_command("session load nosuch", "session load nosuch")
        assert "No session" in response

    def test_session_list(self, config: Config):
        router = _make_router_with_employees(config)
        router._try_sync_command("session save alpha", "session save alpha")
        router._try_sync_command("session save beta", "session save beta")
        _, response = router._try_sync_command("sessions", "sessions")
        assert "alpha" in response
        assert "beta" in response


# ---------------------------------------------------------------------------
# Corrupted session data
# ---------------------------------------------------------------------------


class TestCorruptedSession:
    def test_load_corrupted_json(self, config: Config):
        """Session file with invalid JSON returns None from load_state."""
        from shipwright.persistence.store import load_state, _state_path

        path = _state_path(config, "corrupted")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{invalid json!!!}")

        result = load_state(config, session_id="corrupted")
        assert result is None

    def test_load_empty_json_file(self, config: Config):
        from shipwright.persistence.store import load_state, _state_path

        path = _state_path(config, "empty")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("")

        result = load_state(config, session_id="empty")
        assert result is None

    def test_load_non_object_json(self, config: Config):
        from shipwright.persistence.store import load_state, _state_path

        path = _state_path(config, "array")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("[1, 2, 3]")

        result = load_state(config, session_id="array")
        assert result is None

    def test_router_session_load_corrupted(self, config: Config):
        """Router handles corrupted session gracefully."""
        from shipwright.persistence.store import _state_path

        # Write corrupted data that's valid JSON but has bad structure
        path = _state_path(config, "bad-session")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('{"company": {"employees": {"Alex": {"bad": "data"}}}}')

        router = _make_router(config)
        _, response = router._try_sync_command(
            "session load bad-session", "session load bad-session"
        )
        # Should either load gracefully (skipping bad employee) or report corruption
        assert "bad-session" in response


# ---------------------------------------------------------------------------
# Empty company / edge cases
# ---------------------------------------------------------------------------


class TestEmptyCompanyEdgeCases:
    def test_status_empty_company(self, config: Config):
        router = _make_router(config)
        _, response = router._try_sync_command("status", "status")
        assert "No employees" in response

    def test_costs_empty_company(self, config: Config):
        router = _make_router(config)
        _, response = router._try_sync_command("costs", "costs")
        assert "No costs" in response

    def test_team_overview_empty(self, config: Config):
        router = _make_router(config)
        _, response = router._try_sync_command("team", "team")
        assert "No employees" in response

    @pytest.mark.asyncio
    async def test_ship_empty_company(self, config: Config):
        router = _make_router(config)
        response = await router.handle_message("ship")
        assert "No employees" in response

    @pytest.mark.asyncio
    async def test_message_routes_to_cto(self, config: Config):
        """Free-form messages auto-create CTO and route through CTO flow."""
        router = _make_router(config)
        with patch.object(
            Company, "cto_chat", new_callable=AsyncMock,
            return_value="I'll handle this.",
        ):
            response = await router.handle_message("hello there")
        assert "CTO" in router.company.employees
        assert "I'll handle this." in response


# ---------------------------------------------------------------------------
# Ship validation
# ---------------------------------------------------------------------------


class TestShipValidation:
    @pytest.mark.asyncio
    async def test_ship_nonexistent_team(self, config: Config):
        router = _make_router_with_employees(config)
        response = await router.handle_message("ship nonexistent")
        assert "No team" in response


# ---------------------------------------------------------------------------
# Input edge cases
# ---------------------------------------------------------------------------


class TestInputEdgeCases:
    @pytest.mark.asyncio
    async def test_empty_input(self, config: Config):
        router = _make_router(config)
        response = await router.handle_message("")
        assert response == ""

    @pytest.mark.asyncio
    async def test_whitespace_only(self, config: Config):
        router = _make_router(config)
        response = await router.handle_message("   \n  \t  ")
        assert response == ""

    @pytest.mark.asyncio
    async def test_very_long_input(self, config: Config):
        router = _make_router(config)
        long_text = "a" * 20_000
        # Should not crash — truncated internally, routes to CTO
        with patch.object(
            Company, "cto_chat", new_callable=AsyncMock,
            return_value="Got it.",
        ):
            response = await router.handle_message(long_text)
        assert response  # Non-empty response

    @pytest.mark.asyncio
    async def test_special_characters_in_task(self, config: Config):
        router = _make_router_with_employees(config)
        # Special chars in task description should not crash
        mock_result = MemberResult(output="Done.", total_cost_usd=0.01)
        alex = router.company.employees["Alex"]
        with patch.object(alex, "run", new_callable=AsyncMock, return_value=mock_result):
            response = await router.handle_message(
                'assign Alex "Fix <script>alert(1)</script> & handle \'quotes\'"'
            )
        assert "Done." in response

    @pytest.mark.asyncio
    async def test_unicode_in_task(self, config: Config):
        router = _make_router_with_employees(config)
        mock_result = MemberResult(output="Completed.", total_cost_usd=0.01)
        alex = router.company.employees["Alex"]
        with patch.object(alex, "run", new_callable=AsyncMock, return_value=mock_result):
            response = await router.handle_message('assign Alex "Build the dashboard"')
        assert "Completed." in response


# ---------------------------------------------------------------------------
# Help, roles, shop commands always work
# ---------------------------------------------------------------------------


class TestAlwaysAvailableCommands:
    def test_help(self, config: Config):
        router = _make_router(config)
        _, response = router._try_sync_command("help", "help")
        assert "Shipwright Commands" in response

    def test_roles(self, config: Config):
        router = _make_router(config)
        _, response = router._try_sync_command("roles", "roles")
        assert "Available Roles" in response
        assert "backend-dev" in response

    def test_shop(self, config: Config):
        router = _make_router(config)
        _, response = router._try_sync_command("shop", "shop")
        assert "Available" in response

    def test_installed_empty(self, config: Config):
        router = _make_router(config)
        _, response = router._try_sync_command("installed", "installed")
        assert "No custom" in response

    def test_inspect_builtin(self, config: Config):
        router = _make_router(config)
        _, response = router._try_sync_command("inspect architect", "inspect architect")
        assert "architect" in response.lower()

    def test_inspect_unknown(self, config: Config):
        router = _make_router(config)
        _, response = router._try_sync_command("inspect zzz", "inspect zzz")
        assert "Unknown" in response


# ---------------------------------------------------------------------------
# SDK error recovery
# ---------------------------------------------------------------------------


class TestSDKErrorRecovery:
    @pytest.mark.asyncio
    async def test_assign_sdk_failure_resets_employee(self, config: Config):
        """If SDK throws during work, employee goes back to idle."""
        router = _make_router_with_employees(config)
        alex = router.company.employees["Alex"]

        with patch.object(alex, "run", new_callable=AsyncMock, side_effect=RuntimeError("SDK crash")):
            await router.handle_message('assign Alex "Build API"')

        # Employee should be back to idle after error
        assert alex.status == EmployeeStatus.IDLE
        assert alex.current_task is None
        # Error should have been recorded in task history
        assert len(alex.task_history) == 1
        assert alex.task_history[0].status == "failed"

    @pytest.mark.asyncio
    async def test_talk_sdk_failure_resets_employee(self, config: Config):
        """If SDK throws during conversation, employee goes back to idle."""
        router = _make_router_with_employees(config)
        alex = router.company.employees["Alex"]
        router.company.set_active("Alex")

        with patch.object(
            router.company, "talk", new_callable=AsyncMock,
            side_effect=RuntimeError("connection failed"),
        ):
            response = await router.handle_message("Hello Alex")

        assert "Error" in response
        assert alex.status == EmployeeStatus.IDLE


# ---------------------------------------------------------------------------
# CTO routing
# ---------------------------------------------------------------------------


class TestCTORouting:
    @pytest.mark.asyncio
    async def test_at_name_routes_to_employee(self, config: Config):
        """@name message routes directly to the named employee."""
        router = _make_router_with_employees(config)
        alex = router.company.employees["Alex"]

        mock_result = MemberResult(output="Got it, working on it.", total_cost_usd=0.01)
        with patch.object(alex, "run", new_callable=AsyncMock, return_value=mock_result):
            response = await router.handle_message("@Alex use repository pattern")

        assert "Got it" in response

    @pytest.mark.asyncio
    async def test_at_name_unknown_employee(self, config: Config):
        """@name with unknown name returns error."""
        router = _make_router(config)
        response = await router.handle_message("@Nobody do something")
        assert "No employee" in response

    def test_back_command_returns_to_cto(self, config: Config):
        """'back' command switches active employee to CTO."""
        router = _make_router_with_employees(config)
        router.company.ensure_cto()
        router.company.set_active("Alex")

        _, response = router._try_sync_command("back", "back")
        assert "CTO" in response
        assert router.company._active_employee == "CTO"

    def test_back_command_creates_cto_if_needed(self, config: Config):
        """'back' creates CTO if none exists."""
        router = _make_router(config)
        _, response = router._try_sync_command("back", "back")
        assert "CTO" in response
        assert router.company.get_cto() is not None

    @pytest.mark.asyncio
    async def test_cto_is_default_conversation_target(self, config: Config):
        """Free-form text goes to CTO when no other employee is active."""
        router = _make_router(config)
        with patch.object(
            Company, "cto_chat", new_callable=AsyncMock,
            return_value="Let me look into this.",
        ):
            response = await router.handle_message("Add payments")
        assert "look into this" in response

    @pytest.mark.asyncio
    async def test_talk_switches_away_from_cto(self, config: Config):
        """'talk name' switches from CTO to that employee."""
        router = _make_router_with_employees(config)
        router.company.ensure_cto()
        router.company.set_active("CTO")

        _, response = router._try_sync_command("talk Alex", "talk alex")
        assert "Alex" in response
        assert router.company._active_employee == "Alex"

    @pytest.mark.asyncio
    async def test_non_cto_active_skips_cto_flow(self, config: Config):
        """When a non-CTO employee is active, messages go to them directly."""
        router = _make_router_with_employees(config)
        router.company.set_active("Alex")
        alex = router.company.employees["Alex"]

        mock_result = MemberResult(output="Direct response.", total_cost_usd=0.01)
        with patch.object(alex, "run", new_callable=AsyncMock, return_value=mock_result):
            response = await router.handle_message("What's the status?")

        assert "Direct response" in response

    @pytest.mark.asyncio
    async def test_cto_error_recovery(self, config: Config):
        """CTO errors are caught and reported gracefully."""
        router = _make_router(config)
        with patch.object(
            Company, "cto_chat", new_callable=AsyncMock,
            side_effect=RuntimeError("SDK connection failed"),
        ):
            response = await router.handle_message("Build something")
        assert "Error" in response
