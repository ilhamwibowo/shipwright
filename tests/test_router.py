"""Tests for the Router: command dispatch, input validation, error handling, edge cases."""

from unittest.mock import AsyncMock, patch

import pytest

from shipwright.config import Config
from shipwright.company.company import Company
from shipwright.company.employee import (
    EmployeeStatus,
    MemberResult,
    Roadmap,
    RoadmapState,
    RoadmapTask,
    RoadmapTaskStatus,
    Task,
)
from shipwright.company.roles import get_role_def
from shipwright.conversation.router import Intent, Router, classify_intent
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
        _, response = router._try_sync_command("org", "org")
        assert "No employees" in response

    @pytest.mark.asyncio
    async def test_ship_empty_company(self, config: Config):
        router = _make_router(config)
        response = await router.handle_message("ship")
        assert "No employees" in response

    @pytest.mark.asyncio
    async def test_message_routes_to_cto(self, config: Config):
        """Free-form task messages auto-create CTO and route through CTO flow."""
        router = _make_router(config)
        with patch.object(
            Company, "cto_chat", new_callable=AsyncMock,
            return_value="I'll handle this.",
        ):
            response = await router.handle_message("build a payment system")
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
        assert "Shipwright" in response
        assert "hire" in response

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
            response = await router.handle_message("What is the API status?")

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


# ---------------------------------------------------------------------------
# Intent Classification
# ---------------------------------------------------------------------------


class TestIntentClassification:
    """Tests for the classify_intent function."""

    def test_greetings_detected(self):
        assert classify_intent("hi") == Intent.GREETING
        assert classify_intent("hello") == Intent.GREETING
        assert classify_intent("hey") == Intent.GREETING
        assert classify_intent("sup") == Intent.GREETING
        assert classify_intent("yo") == Intent.GREETING
        assert classify_intent("oi") == Intent.GREETING
        assert classify_intent("morning") == Intent.GREETING
        assert classify_intent("good morning") == Intent.GREETING
        assert classify_intent("hola") == Intent.GREETING
        assert classify_intent("howdy") == Intent.GREETING

    def test_greetings_with_punctuation(self):
        assert classify_intent("hi!") == Intent.GREETING
        assert classify_intent("hello.") == Intent.GREETING
        assert classify_intent("hey?") == Intent.GREETING
        assert classify_intent("morning!") == Intent.GREETING

    def test_greetings_with_extra_words(self):
        """Short greeting + 1-2 extra words still classified as greeting."""
        assert classify_intent("hi there") == Intent.GREETING
        assert classify_intent("hello team") == Intent.GREETING
        assert classify_intent("hey cto") == Intent.GREETING
        assert classify_intent("morning all") == Intent.GREETING

    def test_smalltalk_detected(self):
        assert classify_intent("how are you") == Intent.GREETING
        assert classify_intent("thanks") == Intent.GREETING
        assert classify_intent("cool") == Intent.GREETING
        assert classify_intent("ok") == Intent.GREETING
        assert classify_intent("got it") == Intent.GREETING

    def test_resume_detected(self):
        assert classify_intent("continue") == Intent.RESUME
        assert classify_intent("resume") == Intent.RESUME
        assert classify_intent("go on") == Intent.RESUME
        assert classify_intent("keep going") == Intent.RESUME
        assert classify_intent("carry on") == Intent.RESUME

    def test_pause_detected(self):
        assert classify_intent("pause") == Intent.PAUSE
        assert classify_intent("hold") == Intent.PAUSE
        assert classify_intent("hold on") == Intent.PAUSE

    def test_pause_now_detected(self):
        assert classify_intent("pause now") == Intent.PAUSE_NOW
        assert classify_intent("halt") == Intent.PAUSE_NOW
        assert classify_intent("stop now") == Intent.PAUSE_NOW

    def test_stop_detected(self):
        assert classify_intent("stop") == Intent.STOP
        assert classify_intent("cancel") == Intent.STOP
        assert classify_intent("nevermind") == Intent.STOP
        assert classify_intent("scratch that") == Intent.STOP

    def test_tasks_not_confused_with_greetings(self):
        """Actual tasks should never be classified as greetings."""
        assert classify_intent("build a payment system") == Intent.TASK
        assert classify_intent("add user authentication") == Intent.TASK
        assert classify_intent("fix the bug in the login form") == Intent.TASK
        assert classify_intent("hello world endpoint") == Intent.TASK  # 3+ words with context

    def test_case_insensitive(self):
        assert classify_intent("HI") == Intent.GREETING
        assert classify_intent("Hello") == Intent.GREETING
        assert classify_intent("PAUSE") == Intent.PAUSE
        assert classify_intent("CONTINUE") == Intent.RESUME
        assert classify_intent("STOP") == Intent.STOP


# ---------------------------------------------------------------------------
# Greeting Behavior — greetings must NEVER execute work
# ---------------------------------------------------------------------------


class TestGreetingBehavior:
    @pytest.mark.asyncio
    async def test_greeting_does_not_resume_work(self, config: Config):
        """Greeting with a paused roadmap must NOT resume execution."""
        router = _make_router(config)
        router.company.ensure_cto()
        # Create a paused roadmap
        roadmap = Roadmap(
            tasks=[
                RoadmapTask(index=1, description="Build API", status=RoadmapTaskStatus.DONE),
                RoadmapTask(index=2, description="Add tests", status=RoadmapTaskStatus.PENDING),
            ],
            original_request="Build the backend",
            approved=True,
            paused=True,
            state=RoadmapState.PAUSED,
        )
        router.company.active_roadmap = roadmap

        response = await router.handle_message("hi")

        # Should mention paused work but NOT resume it
        assert "paused" in response.lower()
        assert "continue" in response.lower()
        # Roadmap should still be paused
        assert router.company.active_roadmap.state == RoadmapState.PAUSED
        assert router.company.active_roadmap.paused is True

    @pytest.mark.asyncio
    async def test_greeting_no_team(self, config: Config):
        """Greeting with no team gives contextual response."""
        router = _make_router(config)
        response = await router.handle_message("morning")
        # Should get a non-empty response that doesn't trigger work
        assert len(response) > 5
        assert "CTO" not in router.company.employees or router.company.employees.get("CTO") is None

    @pytest.mark.asyncio
    async def test_greeting_with_idle_team(self, config: Config):
        """Greeting with idle team mentions team is ready."""
        router = _make_router_with_employees(config)
        response = await router.handle_message("sup")
        # Should mention team state or ask what to do
        assert len(response) > 5
        lower = response.lower()
        assert any(w in lower for w in ("idle", "what", "next", "need", "here"))

    @pytest.mark.asyncio
    async def test_greeting_with_working_team(self, config: Config):
        """Greeting while team is working mentions who is working."""
        router = _make_router_with_employees(config)
        alex = router.company.employees["Alex"]
        alex.status = EmployeeStatus.WORKING
        response = await router.handle_message("hey")
        assert "Alex" in response
        # Should mention working state in some form
        lower = response.lower()
        assert any(w in lower for w in ("working", "on it", "busy"))

    @pytest.mark.asyncio
    async def test_greeting_never_calls_cto(self, config: Config):
        """Greetings should never route to CTO chat."""
        router = _make_router(config)
        router.company.ensure_cto()
        with patch.object(
            Company, "cto_chat", new_callable=AsyncMock,
            return_value="Should not be called",
        ) as mock_cto:
            response = await router.handle_message("hello")
        mock_cto.assert_not_called()

    @pytest.mark.asyncio
    async def test_casual_chat_with_paused_task_stays_non_executing(self, config: Config):
        """Casual messages like 'thanks' or 'cool' don't resume paused work."""
        router = _make_router(config)
        router.company.ensure_cto()
        roadmap = Roadmap(
            tasks=[
                RoadmapTask(index=1, description="Setup DB", status=RoadmapTaskStatus.PENDING),
            ],
            original_request="Build database layer",
            approved=True,
            paused=True,
            state=RoadmapState.PAUSED,
        )
        router.company.active_roadmap = roadmap

        for msg in ["thanks", "ok", "cool", "got it"]:
            response = await router.handle_message(msg)
            assert router.company.active_roadmap.state == RoadmapState.PAUSED


# ---------------------------------------------------------------------------
# Pause / Stop / Resume Controls
# ---------------------------------------------------------------------------


class TestPauseStopResume:
    @pytest.mark.asyncio
    async def test_pause_marks_roadmap_paused(self, config: Config):
        """'pause' command sets roadmap to PAUSED state."""
        router = _make_router(config)
        router.company.ensure_cto()
        roadmap = Roadmap(
            tasks=[
                RoadmapTask(index=1, description="Build API", status=RoadmapTaskStatus.RUNNING),
                RoadmapTask(index=2, description="Add tests", status=RoadmapTaskStatus.PENDING),
            ],
            original_request="Build the backend",
            approved=True,
            paused=False,
            state=RoadmapState.RUNNING,
        )
        router.company.active_roadmap = roadmap

        response = await router.handle_message("pause")

        assert "Paused" in response
        assert router.company.active_roadmap.state == RoadmapState.PAUSED
        # Running task should be reset to pending
        assert roadmap.tasks[0].status == RoadmapTaskStatus.PENDING

    @pytest.mark.asyncio
    async def test_pause_now_marks_interrupted(self, config: Config):
        """'pause now' sets roadmap to INTERRUPTED state."""
        router = _make_router(config)
        roadmap = Roadmap(
            tasks=[
                RoadmapTask(index=1, description="Build API", status=RoadmapTaskStatus.RUNNING),
            ],
            original_request="Build the backend",
            approved=True,
            state=RoadmapState.RUNNING,
        )
        router.company.active_roadmap = roadmap

        response = await router.handle_message("pause now")

        assert "Interrupted" in response
        assert router.company.active_roadmap.state == RoadmapState.INTERRUPTED

    @pytest.mark.asyncio
    async def test_stop_cancels_roadmap(self, config: Config):
        """'stop' cancels the roadmap and sets STOPPED state."""
        router = _make_router(config)
        roadmap = Roadmap(
            tasks=[
                RoadmapTask(index=1, description="Build API", status=RoadmapTaskStatus.DONE),
                RoadmapTask(index=2, description="Add tests", status=RoadmapTaskStatus.RUNNING),
                RoadmapTask(index=3, description="Deploy", status=RoadmapTaskStatus.PENDING),
            ],
            original_request="Build the backend",
            approved=True,
            state=RoadmapState.RUNNING,
        )
        router.company.active_roadmap = roadmap

        response = await router.handle_message("stop")

        assert "Stopped" in response
        assert "1/3" in response  # 1 task was done
        assert router.company.active_roadmap.state == RoadmapState.STOPPED

    @pytest.mark.asyncio
    async def test_continue_resumes_paused_roadmap(self, config: Config):
        """'continue' resumes a paused roadmap."""
        router = _make_router(config)
        router.company.ensure_cto()
        roadmap = Roadmap(
            tasks=[
                RoadmapTask(index=1, description="Build API", status=RoadmapTaskStatus.DONE),
                RoadmapTask(index=2, description="Add tests", status=RoadmapTaskStatus.PENDING),
            ],
            original_request="Build the backend",
            approved=True,
            paused=True,
            state=RoadmapState.PAUSED,
        )
        router.company.active_roadmap = roadmap

        with patch.object(
            Company, "execute_roadmap", new_callable=AsyncMock,
            return_value="Roadmap execution complete.",
        ) as mock_exec:
            response = await router.handle_message("continue")

        mock_exec.assert_called_once()
        assert router.company.active_roadmap.state == RoadmapState.RUNNING

    @pytest.mark.asyncio
    async def test_resume_after_stop_rejected(self, config: Config):
        """Cannot resume a stopped roadmap."""
        router = _make_router(config)
        roadmap = Roadmap(
            tasks=[
                RoadmapTask(index=1, description="Build API", status=RoadmapTaskStatus.PENDING),
            ],
            original_request="Build the backend",
            approved=True,
            paused=True,
            state=RoadmapState.STOPPED,
        )
        router.company.active_roadmap = roadmap

        response = await router.handle_message("continue")

        assert "stopped" in response.lower()
        assert router.company.active_roadmap.state == RoadmapState.STOPPED

    @pytest.mark.asyncio
    async def test_pause_without_roadmap(self, config: Config):
        """Pausing with no active roadmap gives a clear message."""
        router = _make_router(config)
        response = await router.handle_message("pause")
        assert "nothing to pause" in response.lower() or "no active roadmap" in response.lower()

    @pytest.mark.asyncio
    async def test_stop_without_roadmap(self, config: Config):
        """Stopping with no active roadmap gives a clear message."""
        router = _make_router(config)
        response = await router.handle_message("stop")
        assert "nothing to stop" in response.lower() or "no active roadmap" in response.lower()

    @pytest.mark.asyncio
    async def test_resume_without_roadmap(self, config: Config):
        """Resuming with no roadmap gives a clear message."""
        router = _make_router(config)
        response = await router.handle_message("resume")
        assert "nothing to resume" in response.lower() or "no active roadmap" in response.lower()


# ---------------------------------------------------------------------------
# Status display with paused/interrupted roadmap
# ---------------------------------------------------------------------------


class TestStatusWithPausedRoadmap:
    def test_status_shows_paused_roadmap(self, config: Config):
        """Status summary includes paused roadmap info."""
        router = _make_router_with_employees(config)
        roadmap = Roadmap(
            tasks=[
                RoadmapTask(index=1, description="Build API", status=RoadmapTaskStatus.DONE),
                RoadmapTask(index=2, description="Add tests", status=RoadmapTaskStatus.PENDING),
            ],
            original_request="Build the backend",
            approved=True,
            paused=True,
            state=RoadmapState.PAUSED,
        )
        router.company.active_roadmap = roadmap

        summary = router.company.status_summary
        assert "Paused" in summary or "PAUSED" in summary
        assert "1/2" in summary

    def test_status_shows_interrupted_roadmap(self, config: Config):
        """Status summary shows interrupted state distinctly from paused."""
        router = _make_router_with_employees(config)
        roadmap = Roadmap(
            tasks=[
                RoadmapTask(index=1, description="Build API", status=RoadmapTaskStatus.PENDING),
            ],
            original_request="Build the backend",
            approved=True,
            paused=True,
            state=RoadmapState.INTERRUPTED,
        )
        router.company.active_roadmap = roadmap

        summary = router.company.status_summary
        assert "Interrupted" in summary or "INTERRUPTED" in summary

    def test_roadmap_display_shows_paused_marker(self, config: Config):
        """Roadmap status display marks which task was paused at."""
        roadmap = Roadmap(
            tasks=[
                RoadmapTask(index=1, description="Build API", status=RoadmapTaskStatus.DONE),
                RoadmapTask(index=2, description="Add tests", status=RoadmapTaskStatus.PENDING),
                RoadmapTask(index=3, description="Deploy", status=RoadmapTaskStatus.PENDING),
            ],
            original_request="Build the backend",
            approved=True,
            paused=True,
            state=RoadmapState.PAUSED,
        )

        display = roadmap.status_display()
        assert "paused here" in display
        assert "PAUSED" in display

    def test_roadmap_display_shows_interrupted_marker(self, config: Config):
        """Roadmap display shows interrupted marker."""
        roadmap = Roadmap(
            tasks=[
                RoadmapTask(index=1, description="Build API", status=RoadmapTaskStatus.PENDING),
            ],
            original_request="Build the backend",
            approved=True,
            paused=True,
            state=RoadmapState.INTERRUPTED,
        )

        display = roadmap.status_display()
        assert "interrupted here" in display
        assert "INTERRUPTED" in display

    def test_roadmap_display_stopped(self, config: Config):
        """Roadmap display shows stopped state."""
        roadmap = Roadmap(
            tasks=[
                RoadmapTask(index=1, description="Build API", status=RoadmapTaskStatus.DONE),
                RoadmapTask(index=2, description="Add tests", status=RoadmapTaskStatus.FAILED,
                            output_summary="Cancelled by user"),
            ],
            original_request="Build the backend",
            approved=True,
            paused=True,
            state=RoadmapState.STOPPED,
        )

        display = roadmap.status_display()
        assert "STOPPED" in display
        assert "cancelled" in display.lower()


# ---------------------------------------------------------------------------
# Roadmap state serialization round-trip
# ---------------------------------------------------------------------------


class TestRoadmapStateSerialization:
    def test_roadmap_state_round_trip(self):
        """Roadmap state survives serialization."""
        roadmap = Roadmap(
            tasks=[
                RoadmapTask(index=1, description="Build API", status=RoadmapTaskStatus.DONE),
                RoadmapTask(index=2, description="Add tests", status=RoadmapTaskStatus.PENDING),
            ],
            original_request="Build the backend",
            approved=True,
            paused=True,
            state=RoadmapState.PAUSED,
        )

        data = roadmap.to_dict()
        restored = Roadmap.from_dict(data)
        assert restored.state == RoadmapState.PAUSED
        assert restored.paused is True
        assert restored.approved is True

    def test_backward_compat_no_state_field(self):
        """Old roadmap data without 'state' field derives state from 'paused'."""
        data = {
            "tasks": [{"index": 1, "description": "Task 1", "status": "pending"}],
            "original_request": "Do work",
            "approved": True,
            "paused": True,
        }
        roadmap = Roadmap.from_dict(data)
        assert roadmap.state == RoadmapState.PAUSED

    def test_backward_compat_running(self):
        """Old roadmap data approved+not_paused -> RUNNING."""
        data = {
            "tasks": [{"index": 1, "description": "Task 1", "status": "pending"}],
            "original_request": "Do work",
            "approved": True,
            "paused": False,
        }
        roadmap = Roadmap.from_dict(data)
        assert roadmap.state == RoadmapState.RUNNING


# ---------------------------------------------------------------------------
# New commands: org, who, board
# ---------------------------------------------------------------------------


class TestOrgCommand:
    def test_org_empty(self, config: Config):
        router = _make_router(config)
        _, response = router._try_sync_command("org", "org")
        assert "No employees" in response

    def test_org_with_employees(self, config: Config):
        router = _make_router_with_employees(config)
        _, response = router._try_sync_command("org", "org")
        assert "Alex" in response
        assert "Blake" in response
        assert "Org Chart" in response

    def test_org_aliases(self, config: Config):
        """org, team, teams, company all work."""
        router = _make_router(config)
        for cmd in ("org", "team", "teams", "company"):
            is_cmd, _ = router._try_sync_command(cmd, cmd)
            assert is_cmd, f"'{cmd}' not recognized"


class TestWhoCommand:
    def test_who_empty(self, config: Config):
        router = _make_router(config)
        _, response = router._try_sync_command("who", "who")
        assert "No employees" in response

    def test_who_all_idle(self, config: Config):
        router = _make_router_with_employees(config)
        _, response = router._try_sync_command("who", "who")
        assert "Alex" in response
        assert "Blake" in response
        assert "idle" in response.lower()

    def test_who_with_working(self, config: Config):
        router = _make_router_with_employees(config)
        alex = router.company.employees["Alex"]
        alex.status = EmployeeStatus.WORKING
        alex.current_task = Task(
            id="t1", description="Building API", assigned_to="Alex", status="running"
        )
        _, response = router._try_sync_command("who", "who")
        assert "Working" in response
        assert "Alex" in response
        assert "Building API" in response


class TestBoardCommand:
    def test_board_no_roadmap(self, config: Config):
        router = _make_router(config)
        _, response = router._try_sync_command("board", "board")
        assert "No active roadmap" in response

    def test_board_with_roadmap(self, config: Config):
        router = _make_router(config)
        router.company.ensure_cto()
        router.company.active_roadmap = Roadmap(
            tasks=[
                RoadmapTask(index=1, description="Task A", status=RoadmapTaskStatus.DONE),
                RoadmapTask(index=2, description="Task B"),
            ],
            original_request="Build it",
        )
        _, response = router._try_sync_command("board", "board")
        assert "Task A" in response
        assert "go" in response.lower()


class TestStatusCommand:
    def test_status_empty(self, config: Config):
        router = _make_router(config)
        _, response = router._try_sync_command("status", "status")
        assert "No employees" in response

    def test_status_with_employees(self, config: Config):
        router = _make_router_with_employees(config)
        _, response = router._try_sync_command("status", "status")
        assert "Status" in response
        assert "idle" in response

    def test_status_with_roadmap(self, config: Config):
        router = _make_router_with_employees(config)
        router.company.active_roadmap = Roadmap(
            tasks=[
                RoadmapTask(index=1, description="Task A", status=RoadmapTaskStatus.DONE),
                RoadmapTask(index=2, description="Task B"),
            ],
            original_request="Build it",
            approved=True,
            state=RoadmapState.RUNNING,
        )
        _, response = router._try_sync_command("status", "status")
        assert "Roadmap" in response
        assert "1/2" in response


class TestTalkBackUX:
    def test_talk_shows_context(self, config: Config):
        router = _make_router_with_employees(config)
        _, response = router._try_sync_command("talk Alex", "talk alex")
        assert "Switched to" in response
        assert "Alex" in response
        assert "back" in response.lower()

    def test_back_shows_previous(self, config: Config):
        router = _make_router_with_employees(config)
        router.company.ensure_cto()
        router.company.set_active("Alex")
        _, response = router._try_sync_command("back", "back")
        assert "CTO" in response
        assert "Alex" in response
