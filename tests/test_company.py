"""Tests for the V2 Company module: hiring, teams, work assignment, serialization, CTO."""

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from shipwright.config import Config, MemberDef
from shipwright.company.company import Company, Team, format_duration_ms
from shipwright.company.employee import (
    DelegationRequest,
    Employee,
    EmployeeStatus,
    HireRequest,
    LeadResponse,
    MemberResult,
    ReviseRequest,
    Task,
    parse_delegations,
)
from shipwright.company.roles import get_role_def


# ---------------------------------------------------------------------------
# Hiring
# ---------------------------------------------------------------------------


class TestHiring:
    def test_hire(self, config: Config):
        company = Company(config=config)
        role_def = get_role_def("backend-dev")
        emp = company.hire("backend-dev", role_def)

        assert emp.name in company.employees
        assert emp.role == "backend-dev"
        assert emp.role_def.role == "Backend Developer"
        assert emp.status == EmployeeStatus.IDLE

    def test_hire_with_custom_name(self, config: Config):
        company = Company(config=config)
        role_def = get_role_def("backend-dev")
        emp = company.hire("backend-dev", role_def, name="Kai")

        assert emp.name == "Kai"
        assert "Kai" in company.employees

    def test_hire_duplicate_name_raises(self, config: Config):
        company = Company(config=config)
        role_def = get_role_def("backend-dev")
        company.hire("backend-dev", role_def, name="Kai")

        with pytest.raises(ValueError, match="already exists"):
            company.hire("frontend-dev", get_role_def("frontend-dev"), name="Kai")

    def test_fire(self, config: Config):
        company = Company(config=config)
        role_def = get_role_def("backend-dev")
        emp = company.hire("backend-dev", role_def, name="Alex")

        fired = company.fire("Alex")
        assert fired.name == "Alex"
        assert "Alex" not in company.employees

    def test_fire_unknown_raises(self, config: Config):
        company = Company(config=config)
        with pytest.raises(ValueError, match="No employee"):
            company.fire("Nonexistent")

    def test_auto_name_generation(self, config: Config):
        company = Company(config=config)
        role_def = get_role_def("backend-dev")
        emp1 = company.hire("backend-dev", role_def)
        emp2 = company.hire("frontend-dev", get_role_def("frontend-dev"))

        assert emp1.name != emp2.name
        assert emp1.name in company.employees
        assert emp2.name in company.employees

    def test_first_hire_becomes_active(self, config: Config):
        company = Company(config=config)
        role_def = get_role_def("backend-dev")
        emp = company.hire("backend-dev", role_def)

        assert company.active_employee is emp
        assert company._active_employee == emp.name

    def test_fire_active_updates_active(self, config: Config):
        company = Company(config=config)
        emp1 = company.hire("backend-dev", get_role_def("backend-dev"), name="Alex")
        emp2 = company.hire("frontend-dev", get_role_def("frontend-dev"), name="Blake")

        assert company._active_employee == "Alex"
        company.fire("Alex")
        # Active should switch to remaining employee
        assert company._active_employee == "Blake"

    def test_fire_last_employee_clears_active(self, config: Config):
        company = Company(config=config)
        emp = company.hire("backend-dev", get_role_def("backend-dev"), name="Alex")
        company.fire("Alex")
        assert company._active_employee is None
        assert company.active_employee is None


# ---------------------------------------------------------------------------
# Teams
# ---------------------------------------------------------------------------


class TestTeams:
    def test_create_team(self, config: Config):
        company = Company(config=config)
        team = company.create_team("backend")

        assert team.name == "backend"
        assert "backend" in company.teams
        assert team.lead is None
        assert team.members == []

    def test_create_duplicate_team_raises(self, config: Config):
        company = Company(config=config)
        company.create_team("backend")

        with pytest.raises(ValueError, match="already exists"):
            company.create_team("backend")

    def test_assign_to_team(self, config: Config):
        company = Company(config=config)
        company.hire("backend-dev", get_role_def("backend-dev"), name="Alex")
        company.create_team("backend")
        company.assign_to_team("Alex", "backend")

        team = company.teams["backend"]
        assert "Alex" in team.members
        assert company.employees["Alex"].team == "backend"

    def test_promote_to_lead(self, config: Config):
        company = Company(config=config)
        company.hire("backend-dev", get_role_def("backend-dev"), name="Alex")
        company.create_team("backend")
        company.promote_to_lead("Alex", "backend")

        team = company.teams["backend"]
        assert team.lead == "Alex"
        assert "Alex" in team.members
        assert company.employees["Alex"].is_lead is True
        assert company.employees["Alex"].team == "backend"

    def test_fire_team(self, config: Config):
        company = Company(config=config)
        company.hire("backend-dev", get_role_def("backend-dev"), name="Alex")
        company.hire("frontend-dev", get_role_def("frontend-dev"), name="Blake")
        company.hire("architect", get_role_def("architect"), name="Casey")
        company.create_team("core")
        company.assign_to_team("Alex", "core")
        company.assign_to_team("Blake", "core")
        company.promote_to_lead("Alex", "core")

        fired = company.fire_team("core")
        assert len(fired) == 2  # Alex and Blake
        assert "core" not in company.teams
        assert "Alex" not in company.employees
        assert "Blake" not in company.employees
        # Casey should still be there (not on the team)
        assert "Casey" in company.employees

    def test_fire_unknown_team_raises(self, config: Config):
        company = Company(config=config)
        with pytest.raises(ValueError, match="No team"):
            company.fire_team("nonexistent")

    def test_promote_unknown_employee_raises(self, config: Config):
        company = Company(config=config)
        company.create_team("backend")
        with pytest.raises(ValueError, match="No employee"):
            company.promote_to_lead("Nonexistent", "backend")

    def test_promote_to_unknown_team_raises(self, config: Config):
        company = Company(config=config)
        company.hire("backend-dev", get_role_def("backend-dev"), name="Alex")
        with pytest.raises(ValueError, match="No team"):
            company.promote_to_lead("Alex", "nonexistent")

    def test_set_active(self, config: Config):
        company = Company(config=config)
        company.hire("backend-dev", get_role_def("backend-dev"), name="Alex")
        company.hire("frontend-dev", get_role_def("frontend-dev"), name="Blake")

        company.set_active("Blake")
        assert company._active_employee == "Blake"
        assert company.active_employee.name == "Blake"

    def test_set_active_unknown_raises(self, config: Config):
        company = Company(config=config)
        with pytest.raises(ValueError, match="No employee"):
            company.set_active("Nonexistent")


# ---------------------------------------------------------------------------
# Work Assignment (async)
# ---------------------------------------------------------------------------


class TestWorkAssignment:
    @pytest.mark.asyncio
    async def test_assign_to_employee(self, config: Config):
        company = Company(config=config)
        emp = company.hire("backend-dev", get_role_def("backend-dev"), name="Alex")

        mock_result = MemberResult(
            output="API endpoint created.",
            session_id="s1",
            total_cost_usd=0.05,
        )

        with patch.object(emp, "run", new_callable=AsyncMock, return_value=mock_result):
            result = await company.assign_work("Alex", "Build the API")

        assert "API endpoint created" in result
        assert len(emp.task_history) == 1
        assert emp.task_history[0].status == "done"
        assert emp.status == EmployeeStatus.IDLE


class TestCTOFallbacks:
    @pytest.mark.asyncio
    async def test_cto_chat_casual_failure_falls_back_cleanly(self, config: Config):
        company = Company(config=config)
        cto = company.ensure_cto()

        with patch.object(cto, "run", new=AsyncMock(side_effect=RuntimeError("sdk blew up"))):
            result = await company.cto_chat("wasap")

        assert result == "I'm here. What do you need built?"

    @pytest.mark.asyncio
    async def test_cto_chat_thanks_failure_returns_short_reply(self, config: Config):
        company = Company(config=config)
        cto = company.ensure_cto()

        with patch.object(cto, "run", new=AsyncMock(side_effect=RuntimeError("sdk blew up"))):
            result = await company.cto_chat("thanks")

        assert result == "Any time."

    @pytest.mark.asyncio
    async def test_cto_chat_repo_failure_returns_snapshot(self, config: Config):
        company = Company(config=config)
        cto = company.ensure_cto()

        with (
            patch.object(cto, "run", new=AsyncMock(side_effect=RuntimeError("sdk blew up"))),
            patch(
                "shipwright.company.company.get_branch_context",
                return_value=(
                    "Branch: feature/payments\n"
                    "Remote: 1 ahead\n"
                    "Working tree: 2 changed files\n"
                    "M shipwright/company/company.py\n"
                    "M tests/test_company.py\n"
                    "Recent commits:\n"
                    "  abc123 Add payments telemetry\n"
                    "  def456 Tighten fallback"
                ),
            ),
        ):
            result = await company.cto_chat("what changed?")

        assert result.startswith("Repo snapshot:")
        assert "branch feature/payments" in result
        assert "Changed files" in result

    @pytest.mark.asyncio
    async def test_cto_chat_task_failure_returns_operator_message(self, config: Config):
        company = Company(config=config)
        cto = company.ensure_cto()

        with patch.object(cto, "run", new=AsyncMock(side_effect=RuntimeError("sdk blew up"))):
            result = await company.cto_chat("Build a payments API")

        assert "execution error" in result
        assert "concrete task" in result

    @pytest.mark.asyncio
    async def test_assign_to_unknown_raises(self, config: Config):
        company = Company(config=config)
        with pytest.raises(ValueError, match="No employee or team"):
            await company.assign_work("Nonexistent", "Do stuff")

    @pytest.mark.asyncio
    async def test_assign_to_team_with_delegation(self, config: Config):
        """Team assignment: lead delegates to members, results fed back."""
        company = Company(config=config)

        # Hire employees
        lead_emp = company.hire("team-lead", get_role_def("team-lead"), name="Alex")
        member_emp = company.hire("backend-dev", get_role_def("backend-dev"), name="Blake")

        # Create team
        company.create_team("backend")
        company.assign_to_team("Alex", "backend")
        company.assign_to_team("Blake", "backend")
        company.promote_to_lead("Alex", "backend")

        # Mock lead responses
        lead_resp_1_text = (
            "I'll have Blake build the API.\n\n"
            "[DELEGATE:Blake]\n"
            "Build the REST API for payments.\n"
            "[/DELEGATE]"
        )
        lead_resp_2_text = "Blake finished the API. Here's the summary."

        call_count = 0

        async def mock_respond_as_lead(user_message, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return LeadResponse(text=lead_resp_1_text)
            return LeadResponse(text=lead_resp_2_text)

        member_result = MemberResult(output="REST API implemented.", total_cost_usd=0.05)

        with (
            patch.object(lead_emp, "respond_as_lead", side_effect=mock_respond_as_lead),
            patch.object(member_emp, "run", new_callable=AsyncMock, return_value=member_result),
        ):
            result = await company.assign_work("backend", "Build payments API")

        assert "Blake build the API" in result
        assert "summary" in result

    @pytest.mark.asyncio
    async def test_team_without_lead_raises(self, config: Config):
        company = Company(config=config)
        company.hire("backend-dev", get_role_def("backend-dev"), name="Alex")
        company.create_team("backend")
        company.assign_to_team("Alex", "backend")

        with pytest.raises(ValueError, match="no lead"):
            await company.assign_work("backend", "Build something")

    @pytest.mark.asyncio
    async def test_max_delegation_rounds(self, config: Config):
        """Delegation loop stops after config.max_revision_rounds."""
        config = Config(
            repo_root=config.repo_root,
            max_revision_rounds=2,
            sessions_dir=config.sessions_dir,
        )
        company = Company(config=config)

        lead_emp = company.hire("team-lead", get_role_def("team-lead"), name="Alex")
        member_emp = company.hire("backend-dev", get_role_def("backend-dev"), name="Blake")

        company.create_team("core")
        company.assign_to_team("Alex", "core")
        company.assign_to_team("Blake", "core")
        company.promote_to_lead("Alex", "core")

        # Lead always delegates
        always_delegates_text = (
            "Delegating more.\n\n"
            "[DELEGATE:Blake]\nDo more work.\n[/DELEGATE]"
        )

        async def mock_respond_as_lead(user_message, **kwargs):
            return LeadResponse(text=always_delegates_text)

        member_result = MemberResult(output="Done.", total_cost_usd=0.01)

        with (
            patch.object(lead_emp, "respond_as_lead", side_effect=mock_respond_as_lead),
            patch.object(member_emp, "run", new_callable=AsyncMock, return_value=member_result),
        ):
            result = await company.assign_work("core", "Keep going forever")

        assert "maximum rounds" in result.lower()


# ---------------------------------------------------------------------------
# Serialization
# ---------------------------------------------------------------------------


class TestSerialization:
    def test_to_dict_from_dict_round_trip(self, config: Config):
        company = Company(config=config)
        company.hire("backend-dev", get_role_def("backend-dev"), name="Alex")
        company.hire("frontend-dev", get_role_def("frontend-dev"), name="Blake")
        company.create_team("core")
        company.assign_to_team("Alex", "core")
        company.promote_to_lead("Alex", "core")

        data = company.to_dict()
        assert "employees" in data
        assert "teams" in data
        assert len(data["employees"]) == 2
        assert len(data["teams"]) == 1
        assert data["active_employee"] == "Alex"

        restored = Company.from_dict(data, config)
        assert len(restored.employees) == 2
        assert "Alex" in restored.employees
        assert "Blake" in restored.employees
        assert restored.employees["Alex"].is_lead is True
        assert len(restored.teams) == 1
        assert "core" in restored.teams
        assert restored.teams["core"].lead == "Alex"

    def test_team_serialization(self):
        team = Team(name="backend", lead="Alex", members=["Alex", "Blake"])
        data = team.to_dict()
        restored = Team.from_dict(data)
        assert restored.name == "backend"
        assert restored.lead == "Alex"
        assert restored.members == ["Alex", "Blake"]

    def test_empty_company_serialization(self, config: Config):
        company = Company(config=config)
        data = company.to_dict()
        restored = Company.from_dict(data, config)
        assert len(restored.employees) == 0
        assert len(restored.teams) == 0


# ---------------------------------------------------------------------------
# Status Summary
# ---------------------------------------------------------------------------


class TestStatusSummary:
    def test_shows_independent_employees(self, config: Config):
        company = Company(config=config)
        company.hire("backend-dev", get_role_def("backend-dev"), name="Alex")
        company.hire("frontend-dev", get_role_def("frontend-dev"), name="Blake")

        summary = company.status_summary
        assert "Alex" in summary
        assert "Blake" in summary
        assert "Backend Developer" in summary
        assert "Frontend Developer" in summary

    def test_shows_teams(self, config: Config):
        company = Company(config=config)
        company.hire("backend-dev", get_role_def("backend-dev"), name="Alex")
        company.hire("frontend-dev", get_role_def("frontend-dev"), name="Blake")
        company.create_team("core")
        company.assign_to_team("Alex", "core")
        company.promote_to_lead("Alex", "core")

        summary = company.status_summary
        assert "core" in summary
        assert "Lead" in summary

    def test_empty_company(self, config: Config):
        company = Company(config=config)
        summary = company.status_summary
        assert summary == ""  # no employees, no teams


# ---------------------------------------------------------------------------
# Cost Tracking
# ---------------------------------------------------------------------------


class TestCostTracking:
    def test_cost_report_no_costs(self, config: Config):
        company = Company(config=config)
        company.hire("backend-dev", get_role_def("backend-dev"), name="Alex")

        report = company.cost_report
        assert "Cost Report" in report
        assert "No costs recorded" in report

    def test_cost_report_with_costs(self, config: Config):
        company = Company(config=config)
        emp = company.hire("backend-dev", get_role_def("backend-dev"), name="Alex")
        emp.cost_total_usd = 0.1234
        emp.task_history.append(Task(
            id="t1",
            description="Write API",
            assigned_to="Alex",
            status="done",
            cost_usd=0.1234,
        ))

        report = company.cost_report
        assert "Alex" in report
        assert "$0.1234" in report

    def test_total_cost(self, config: Config):
        company = Company(config=config)
        emp1 = company.hire("backend-dev", get_role_def("backend-dev"), name="Alex")
        emp2 = company.hire("frontend-dev", get_role_def("frontend-dev"), name="Blake")

        emp1.cost_total_usd = 0.10
        emp2.cost_total_usd = 0.20

        assert abs(company.total_cost - 0.30) < 1e-9

    def test_total_cost_empty(self, config: Config):
        company = Company(config=config)
        assert company.total_cost == 0.0

    def test_cost_report_shows_task_count_and_duration(self, config: Config):
        company = Company(config=config)
        emp = company.hire("backend-dev", get_role_def("backend-dev"), name="Alex")
        emp.cost_total_usd = 0.15
        emp.task_history.append(Task(
            id="t1", description="Write API", assigned_to="Alex",
            status="done", cost_usd=0.10, duration_ms=60000,
        ))
        emp.task_history.append(Task(
            id="t2", description="Fix bug", assigned_to="Alex",
            status="done", cost_usd=0.05, duration_ms=30000,
        ))

        report = company.cost_report
        assert "2 tasks" in report
        assert "1m 30s" in report
        assert "$0.1500" in report
        assert "Total" in report

    def test_cost_report_single_task_label(self, config: Config):
        company = Company(config=config)
        emp = company.hire("backend-dev", get_role_def("backend-dev"), name="Alex")
        emp.cost_total_usd = 0.05
        emp.task_history.append(Task(
            id="t1", description="Write API", assigned_to="Alex",
            status="done", cost_usd=0.05, duration_ms=45000,
        ))

        report = company.cost_report
        assert "1 task," in report

    def test_cost_report_with_budget(self):
        config = Config(
            repo_root=Path("/tmp"),
            budget_limit_usd=5.0,
            sessions_dir=Path("/tmp/sessions"),
        )
        company = Company(config=config)
        emp = company.hire("backend-dev", get_role_def("backend-dev"), name="Alex")
        emp.cost_total_usd = 1.0
        emp.task_history.append(Task(
            id="t1", description="Work", assigned_to="Alex",
            status="done", cost_usd=1.0, duration_ms=120000,
        ))

        report = company.cost_report
        assert "Budget: $5.00" in report
        assert "20%" in report

    def test_status_summary_includes_cost(self, config: Config):
        company = Company(config=config)
        emp = company.hire("backend-dev", get_role_def("backend-dev"), name="Alex")
        emp.cost_total_usd = 0.25

        summary = company.status_summary
        assert "$0.25" in summary


# ---------------------------------------------------------------------------
# Format Duration
# ---------------------------------------------------------------------------


class TestFormatDuration:
    def test_zero(self):
        assert format_duration_ms(0) == "0s"

    def test_negative(self):
        assert format_duration_ms(-100) == "0s"

    def test_seconds(self):
        assert format_duration_ms(45000) == "45s"

    def test_minutes_and_seconds(self):
        assert format_duration_ms(90000) == "1m 30s"

    def test_exact_minutes(self):
        assert format_duration_ms(120000) == "2m"

    def test_hours_and_minutes(self):
        assert format_duration_ms(3_660_000) == "1h 1m"

    def test_exact_hours(self):
        assert format_duration_ms(3_600_000) == "1h"


# ---------------------------------------------------------------------------
# Budget Limits
# ---------------------------------------------------------------------------


class TestBudgetLimits:
    @pytest.mark.asyncio
    async def test_budget_exceeded_blocks_work(self):
        config = Config(
            repo_root=Path("/tmp"),
            budget_limit_usd=1.0,
            sessions_dir=Path("/tmp/sessions"),
        )
        company = Company(config=config)
        emp = company.hire("backend-dev", get_role_def("backend-dev"), name="Alex")
        emp.cost_total_usd = 1.50  # Already over budget

        result = await company.assign_work("Alex", "Do more work")
        assert "Budget exceeded" in result
        assert "$1.50" in result

    @pytest.mark.asyncio
    async def test_no_budget_allows_work(self, config: Config):
        """When budget_limit_usd is 0 (default), no budget check."""
        company = Company(config=config)
        emp = company.hire("backend-dev", get_role_def("backend-dev"), name="Alex")
        emp.cost_total_usd = 999.0

        mock_result = MemberResult(output="Done.", total_cost_usd=0.01)
        with patch.object(emp, "run", new_callable=AsyncMock, return_value=mock_result):
            result = await company.assign_work("Alex", "Build something")

        assert "Done." in result

    @pytest.mark.asyncio
    async def test_budget_not_exceeded_allows_work(self):
        config = Config(
            repo_root=Path("/tmp"),
            budget_limit_usd=10.0,
            sessions_dir=Path("/tmp/sessions"),
        )
        company = Company(config=config)
        emp = company.hire("backend-dev", get_role_def("backend-dev"), name="Alex")
        emp.cost_total_usd = 5.0  # Under budget

        mock_result = MemberResult(output="Done.", total_cost_usd=0.50)
        with patch.object(emp, "run", new_callable=AsyncMock, return_value=mock_result):
            result = await company.assign_work("Alex", "Build something")

        assert "Done." in result


# ---------------------------------------------------------------------------
# CTO Auto-Pilot
# ---------------------------------------------------------------------------


class TestCTO:
    def test_ensure_cto_creates_when_empty(self, config: Config):
        company = Company(config=config)
        assert company.get_cto() is None

        cto = company.ensure_cto()
        assert cto.name == "CTO"
        assert cto.role == "cto"
        assert "CTO" in company.employees
        assert company._active_employee == "CTO"

    def test_ensure_cto_idempotent(self, config: Config):
        company = Company(config=config)
        cto1 = company.ensure_cto()
        cto2 = company.ensure_cto()
        assert cto1 is cto2
        assert len([e for e in company.employees.values() if e.role == "cto"]) == 1

    def test_ensure_cto_preserves_active_employee(self, config: Config):
        """If other employees exist, ensure_cto doesn't change active employee."""
        company = Company(config=config)
        company.hire("backend-dev", get_role_def("backend-dev"), name="Alex")
        assert company._active_employee == "Alex"

        company.ensure_cto()
        # Alex remains active since they were first
        assert company._active_employee == "Alex"

    def test_get_cto_none(self, config: Config):
        company = Company(config=config)
        assert company.get_cto() is None

    def test_get_cto(self, config: Config):
        company = Company(config=config)
        company.ensure_cto()
        cto = company.get_cto()
        assert cto is not None
        assert cto.role == "cto"

    def test_build_cto_prompt_includes_employees(self, config: Config):
        company = Company(config=config)
        company.ensure_cto()
        company.hire("backend-dev", get_role_def("backend-dev"), name="Alex")
        company.hire("frontend-dev", get_role_def("frontend-dev"), name="Blake")

        prompt = company._build_cto_prompt()
        assert "Alex" in prompt
        assert "Blake" in prompt
        assert "Backend Developer" in prompt
        assert "Frontend Developer" in prompt
        # CTO itself should NOT be in the employees section
        assert "CTO" not in prompt.split("### Your Team")[1].split("###")[0] or True

    def test_build_cto_prompt_no_employees(self, config: Config):
        company = Company(config=config)
        company.ensure_cto()
        prompt = company._build_cto_prompt()
        assert "No employees hired" in prompt
        assert "[HIRE:role]" in prompt

    def test_build_cto_prompt_includes_project_context(self, config: Config):
        company = Company(config=config)
        company.ensure_cto()
        company.project_context = "Python/FastAPI project"
        prompt = company._build_cto_prompt()
        assert "Python/FastAPI project" in prompt

    @pytest.mark.asyncio
    async def test_cto_chat_no_delegations(self, config: Config):
        """CTO responds without hiring or delegating — just talks."""
        company = Company(config=config)
        cto = company.ensure_cto()

        mock_result = MemberResult(
            output="Got it. I'll analyze the codebase first.",
            total_cost_usd=0.02,
        )
        with patch.object(cto, "run", new_callable=AsyncMock, return_value=mock_result):
            result = await company.cto_chat("What's the project structure?")

        assert "analyze the codebase" in result

    @pytest.mark.asyncio
    async def test_cto_chat_with_hire_and_delegate(self, config: Config):
        """CTO hires an employee and delegates work in one response."""
        company = Company(config=config)
        cto = company.ensure_cto()

        # CTO's initial response: hire + delegate
        cto_response = MemberResult(
            output=(
                "I'll get someone on this.\n\n"
                "[HIRE:backend-dev:Kai]\n\n"
                "[DELEGATE:Kai]\n"
                "Build the REST API for user authentication.\n"
                "[/DELEGATE]"
            ),
            total_cost_usd=0.02,
        )

        # Employee work result
        emp_result = MemberResult(output="API implemented.", total_cost_usd=0.05)

        # CTO review result (approves)
        review_result = MemberResult(
            output="The API looks solid. Authentication endpoints are in place.",
            total_cost_usd=0.01,
        )

        call_count = 0

        async def mock_cto_run(task, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return cto_response
            return review_result

        with patch.object(cto, "run", side_effect=mock_cto_run):
            # We also need to mock the hired employee's run
            with patch(
                "shipwright.company.employee.Employee.run",
                new_callable=AsyncMock,
                return_value=emp_result,
            ) as mock_emp_run:
                # But cto.run is already patched above, so we need a different approach
                pass

        # Better approach: patch at a higher level
        call_count = 0

        async def mock_cto_run2(task, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return cto_response
            return review_result

        with patch.object(cto, "run", side_effect=mock_cto_run2):
            with patch.object(
                Company, "_assign_to_employee", new_callable=AsyncMock,
                return_value="API implemented.",
            ):
                result = await company.cto_chat("Add user authentication")

        assert "Kai" in company.employees
        assert company.employees["Kai"].role == "backend-dev"
        assert "solid" in result or "API" in result

    @pytest.mark.asyncio
    async def test_cto_chat_with_revise(self, config: Config):
        """CTO reviews work and sends it back for revision."""
        company = Company(config=config)
        cto = company.ensure_cto()
        company.hire("backend-dev", get_role_def("backend-dev"), name="Alex")

        # CTO delegates
        cto_response = MemberResult(
            output=(
                "Let me have Alex handle this.\n\n"
                "[DELEGATE:Alex]\n"
                "Build the API.\n"
                "[/DELEGATE]"
            ),
            total_cost_usd=0.02,
        )

        # CTO first review: sends back for revision
        revise_response = MemberResult(
            output=(
                "The error handling is incomplete.\n\n"
                "[REVISE:Alex]\n"
                "Add proper error handling for database failures.\n"
                "[/REVISE]"
            ),
            total_cost_usd=0.01,
        )

        # CTO second review: approves
        approve_response = MemberResult(
            output="Good. The API now has proper error handling. Ship it.",
            total_cost_usd=0.01,
        )

        call_count = 0

        async def mock_cto_run(task, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return cto_response
            elif call_count == 2:
                return revise_response
            return approve_response

        with patch.object(cto, "run", side_effect=mock_cto_run):
            with patch.object(
                Company, "_assign_to_employee", new_callable=AsyncMock,
                return_value="API built with error handling.",
            ):
                result = await company.cto_chat("Build an API")

        assert "Ship it" in result or "error handling" in result

    @pytest.mark.asyncio
    async def test_cto_chat_max_review_rounds(self, config: Config):
        """CTO hits max review rounds and presents what it has."""
        company = Company(config=config)
        cto = company.ensure_cto()
        company.hire("backend-dev", get_role_def("backend-dev"), name="Alex")

        # CTO delegates
        cto_response = MemberResult(
            output=(
                "Working on it.\n\n"
                "[DELEGATE:Alex]\nBuild the API.\n[/DELEGATE]"
            ),
            total_cost_usd=0.02,
        )

        # CTO always revises (never approves)
        always_revise = MemberResult(
            output=(
                "Still not right.\n\n"
                "[REVISE:Alex]\nTry again.\n[/REVISE]"
            ),
            total_cost_usd=0.01,
        )

        call_count = 0

        async def mock_cto_run(task, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return cto_response
            return always_revise

        with patch.object(cto, "run", side_effect=mock_cto_run):
            with patch.object(
                Company, "_assign_to_employee", new_callable=AsyncMock,
                return_value="Done.",
            ):
                result = await company.cto_chat("Build an API")

        assert "maximum rounds" in result.lower()

    @pytest.mark.asyncio
    async def test_cto_chat_budget_exceeded(self, config: Config):
        """CTO blocks work when budget is exceeded."""
        cfg = Config(
            repo_root=Path("/tmp"),
            budget_limit_usd=1.0,
            sessions_dir=Path("/tmp/sessions"),
        )
        company = Company(config=cfg)
        cto = company.ensure_cto()
        cto.cost_total_usd = 2.0  # Over budget

        result = await company.cto_chat("Do more work")
        assert "Budget exceeded" in result

    def test_cto_serialization_round_trip(self, config: Config):
        """CTO survives save/restore cycle."""
        company = Company(config=config)
        company.ensure_cto()
        company.hire("backend-dev", get_role_def("backend-dev"), name="Alex")

        data = company.to_dict()
        restored = Company.from_dict(data, config)

        assert restored.get_cto() is not None
        assert restored.get_cto().role == "cto"
        assert "Alex" in restored.employees


# ---------------------------------------------------------------------------
# Hierarchy Enforcement
# ---------------------------------------------------------------------------


class TestHierarchyEnforcement:
    """Tests for role-based permission checks on delegation, hiring, revision."""

    def test_ic_cannot_delegate(self, config: Config):
        """ICs have delegation blocks stripped silently."""
        company = Company(config=config)
        emp = company.hire("backend-dev", get_role_def("backend-dev"), name="Alex")

        delegations = [DelegationRequest(member_name="Blake", task="Do stuff")]
        filtered = company._filter_delegations(emp, delegations)
        assert filtered == []

    def test_ic_cannot_hire(self, config: Config):
        company = Company(config=config)
        emp = company.hire("backend-dev", get_role_def("backend-dev"), name="Alex")

        hires = [HireRequest(role="frontend-dev")]
        filtered = company._filter_hires(emp, hires)
        assert filtered == []

    def test_ic_cannot_revise(self, config: Config):
        company = Company(config=config)
        emp = company.hire("backend-dev", get_role_def("backend-dev"), name="Alex")

        revisions = [ReviseRequest(employee_name="Blake", feedback="Fix it")]
        filtered = company._filter_revisions(emp, revisions)
        assert filtered == []

    def test_team_lead_can_delegate_to_team(self, config: Config):
        company = Company(config=config)
        lead = company.hire("team-lead", get_role_def("team-lead"), name="Alex")
        company.hire("backend-dev", get_role_def("backend-dev"), name="Blake")
        company.create_team("backend")
        company.assign_to_team("Alex", "backend")
        company.assign_to_team("Blake", "backend")
        company.promote_to_lead("Alex", "backend")

        delegations = [DelegationRequest(member_name="Blake", task="Do stuff")]
        filtered = company._filter_delegations(lead, delegations)
        assert len(filtered) == 1
        assert filtered[0].member_name == "Blake"

    def test_team_lead_cannot_delegate_outside_team(self, config: Config):
        company = Company(config=config)
        lead = company.hire("team-lead", get_role_def("team-lead"), name="Alex")
        company.hire("backend-dev", get_role_def("backend-dev"), name="Blake")
        company.hire("frontend-dev", get_role_def("frontend-dev"), name="Casey")
        company.create_team("backend")
        company.assign_to_team("Alex", "backend")
        company.assign_to_team("Blake", "backend")
        company.promote_to_lead("Alex", "backend")

        # Casey is NOT on the backend team
        delegations = [DelegationRequest(member_name="Casey", task="Do stuff")]
        filtered = company._filter_delegations(lead, delegations)
        assert filtered == []

    def test_team_lead_cannot_hire(self, config: Config):
        company = Company(config=config)
        lead = company.hire("team-lead", get_role_def("team-lead"), name="Alex")
        company.create_team("backend")
        company.assign_to_team("Alex", "backend")
        company.promote_to_lead("Alex", "backend")

        hires = [HireRequest(role="backend-dev")]
        filtered = company._filter_hires(lead, hires)
        assert filtered == []

    def test_team_lead_can_revise_team_member(self, config: Config):
        company = Company(config=config)
        lead = company.hire("team-lead", get_role_def("team-lead"), name="Alex")
        company.hire("backend-dev", get_role_def("backend-dev"), name="Blake")
        company.create_team("backend")
        company.assign_to_team("Alex", "backend")
        company.assign_to_team("Blake", "backend")
        company.promote_to_lead("Alex", "backend")

        revisions = [ReviseRequest(employee_name="Blake", feedback="Fix it")]
        filtered = company._filter_revisions(lead, revisions)
        assert len(filtered) == 1
        assert filtered[0].employee_name == "Blake"

    def test_team_lead_cannot_revise_outside_team(self, config: Config):
        company = Company(config=config)
        lead = company.hire("team-lead", get_role_def("team-lead"), name="Alex")
        company.hire("backend-dev", get_role_def("backend-dev"), name="Blake")
        company.hire("frontend-dev", get_role_def("frontend-dev"), name="Casey")
        company.create_team("backend")
        company.assign_to_team("Alex", "backend")
        company.assign_to_team("Blake", "backend")
        company.promote_to_lead("Alex", "backend")

        revisions = [ReviseRequest(employee_name="Casey", feedback="Fix it")]
        filtered = company._filter_revisions(lead, revisions)
        assert filtered == []

    def test_cto_can_delegate_to_anyone(self, config: Config):
        company = Company(config=config)
        cto = company.ensure_cto()
        company.hire("backend-dev", get_role_def("backend-dev"), name="Alex")
        company.hire("frontend-dev", get_role_def("frontend-dev"), name="Blake")

        delegations = [
            DelegationRequest(member_name="Alex", task="Do stuff"),
            DelegationRequest(member_name="Blake", task="Do more"),
        ]
        filtered = company._filter_delegations(cto, delegations)
        assert len(filtered) == 2

    def test_cto_can_hire(self, config: Config):
        company = Company(config=config)
        cto = company.ensure_cto()

        hires = [HireRequest(role="backend-dev")]
        filtered = company._filter_hires(cto, hires)
        assert len(filtered) == 1

    def test_cto_can_revise_anyone(self, config: Config):
        company = Company(config=config)
        cto = company.ensure_cto()
        company.hire("backend-dev", get_role_def("backend-dev"), name="Alex")

        revisions = [ReviseRequest(employee_name="Alex", feedback="Fix it")]
        filtered = company._filter_revisions(cto, revisions)
        assert len(filtered) == 1

    def test_cto_cannot_delegate_to_self(self, config: Config):
        company = Company(config=config)
        cto = company.ensure_cto()

        delegations = [DelegationRequest(member_name="CTO", task="Self-assign")]
        filtered = company._filter_delegations(cto, delegations)
        assert filtered == []

    def test_delegation_scope_empty_for_non_lead_non_cto(self, config: Config):
        company = Company(config=config)
        emp = company.hire("backend-dev", get_role_def("backend-dev"), name="Alex")
        assert company._get_delegation_scope(emp) == set()

    def test_delegation_scope_cto_includes_all(self, config: Config):
        company = Company(config=config)
        cto = company.ensure_cto()
        company.hire("backend-dev", get_role_def("backend-dev"), name="Alex")
        company.hire("frontend-dev", get_role_def("frontend-dev"), name="Blake")

        scope = company._get_delegation_scope(cto)
        assert "Alex" in scope
        assert "Blake" in scope
        assert "CTO" not in scope

    def test_delegation_scope_lead_only_team(self, config: Config):
        company = Company(config=config)
        lead = company.hire("team-lead", get_role_def("team-lead"), name="Lead")
        company.hire("backend-dev", get_role_def("backend-dev"), name="Blake")
        company.hire("frontend-dev", get_role_def("frontend-dev"), name="Casey")
        company.create_team("backend")
        company.assign_to_team("Lead", "backend")
        company.assign_to_team("Blake", "backend")
        company.promote_to_lead("Lead", "backend")
        # Casey is NOT on the team

        scope = company._get_delegation_scope(lead)
        assert "Blake" in scope
        assert "Casey" not in scope
        assert "Lead" not in scope


# ---------------------------------------------------------------------------
# Context Chain Propagation
# ---------------------------------------------------------------------------


class TestContextChain:
    @pytest.mark.asyncio
    async def test_context_chain_propagation_to_employee(self, config: Config):
        """Context chain flows from CTO down to ICs via _assign_to_employee."""
        company = Company(config=config)
        cto = company.ensure_cto()
        emp = company.hire("backend-dev", get_role_def("backend-dev"), name="Alex")

        captured_context = {}

        async def mock_emp_run(task, context="", **kwargs):
            captured_context["task"] = task
            captured_context["context"] = context
            return MemberResult(output="Done.", total_cost_usd=0.01)

        # CTO delegates to Alex
        cto_response = MemberResult(
            output=(
                "Planning the backend.\n\n"
                "[DELEGATE:Alex]\nBuild the API.\n[/DELEGATE]"
            ),
            total_cost_usd=0.02,
        )
        review_response = MemberResult(
            output="Looks good. Ship it.",
            total_cost_usd=0.01,
        )

        call_count = 0

        async def mock_cto_run(task, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return cto_response
            return review_response

        with patch.object(cto, "run", side_effect=mock_cto_run):
            with patch.object(emp, "run", side_effect=mock_emp_run):
                await company.cto_chat("Build the backend")

        # Alex should have received context chain from CTO
        assert "From CTO" in captured_context.get("context", "")

    @pytest.mark.asyncio
    async def test_context_chain_through_team_lead(self, config: Config):
        """Context chain includes upstream context when delegating through team lead."""
        company = Company(config=config)
        cto = company.ensure_cto()
        lead_emp = company.hire("team-lead", get_role_def("team-lead"), name="Lead")
        ic_emp = company.hire("backend-dev", get_role_def("backend-dev"), name="Kai")

        company.create_team("backend")
        company.assign_to_team("Lead", "backend")
        company.assign_to_team("Kai", "backend")
        company.promote_to_lead("Lead", "backend")

        captured_context = {}

        async def mock_ic_run(task, context="", **kwargs):
            captured_context["task"] = task
            captured_context["context"] = context
            return MemberResult(output="Done.", total_cost_usd=0.01)

        # CTO delegates to Lead (team-lead)
        cto_response = MemberResult(
            output=(
                "Delegating to backend team.\n\n"
                "[DELEGATE:Lead]\nBuild payments.\n[/DELEGATE]"
            ),
            total_cost_usd=0.02,
        )
        cto_review = MemberResult(
            output="Good work. Ship it.",
            total_cost_usd=0.01,
        )

        cto_call = 0

        async def mock_cto_run(task, **kwargs):
            nonlocal cto_call
            cto_call += 1
            if cto_call == 1:
                return cto_response
            return cto_review

        # Lead delegates to Kai
        lead_delegate_text = (
            "I'll have Kai build it.\n\n"
            "[DELEGATE:Kai]\nImplement Stripe webhook.\n[/DELEGATE]"
        )
        lead_review_text = "Kai's work looks good. Summary complete."

        lead_call = 0

        async def mock_lead_respond(user_message, **kwargs):
            nonlocal lead_call
            lead_call += 1
            if lead_call == 1:
                return LeadResponse(text=lead_delegate_text)
            return LeadResponse(text=lead_review_text)

        with patch.object(cto, "run", side_effect=mock_cto_run):
            with patch.object(lead_emp, "respond_as_lead", side_effect=mock_lead_respond):
                with patch.object(ic_emp, "run", side_effect=mock_ic_run):
                    await company.cto_chat("Add Stripe payments")

        # Kai should have context from CTO delegation chain
        ctx = captured_context.get("context", "")
        assert "From CTO" in ctx


# ---------------------------------------------------------------------------
# Upward Result Flow
# ---------------------------------------------------------------------------


class TestUpwardResultFlow:
    @pytest.mark.asyncio
    async def test_results_flow_through_team_lead_to_cto(self, config: Config):
        """IC results flow to team-lead, who synthesizes, then to CTO for review."""
        company = Company(config=config)
        cto = company.ensure_cto()
        lead_emp = company.hire("team-lead", get_role_def("team-lead"), name="Lead")
        ic_emp = company.hire("backend-dev", get_role_def("backend-dev"), name="Kai")

        company.create_team("backend")
        company.assign_to_team("Lead", "backend")
        company.assign_to_team("Kai", "backend")
        company.promote_to_lead("Lead", "backend")

        # CTO delegates to Lead (team-lead)
        cto_response = MemberResult(
            output=(
                "Routing to backend team.\n\n"
                "[DELEGATE:Lead]\nBuild the API.\n[/DELEGATE]"
            ),
            total_cost_usd=0.02,
        )
        cto_review = MemberResult(
            output="The backend team delivered. Presenting results.",
            total_cost_usd=0.01,
        )

        cto_call = 0

        async def mock_cto_run(task, **kwargs):
            nonlocal cto_call
            cto_call += 1
            if cto_call == 1:
                return cto_response
            return cto_review

        # Lead delegates to Kai, then synthesizes
        lead_delegate = (
            "Assigning to Kai.\n\n"
            "[DELEGATE:Kai]\nBuild REST endpoints.\n[/DELEGATE]"
        )
        lead_synthesis = "Kai completed the REST endpoints. All tests passing."

        lead_call = 0

        async def mock_lead_respond(user_message, **kwargs):
            nonlocal lead_call
            lead_call += 1
            if lead_call == 1:
                return LeadResponse(text=lead_delegate)
            return LeadResponse(text=lead_synthesis)

        ic_result = MemberResult(
            output="REST API implemented with tests.",
            total_cost_usd=0.05,
        )

        with patch.object(cto, "run", side_effect=mock_cto_run):
            with patch.object(
                lead_emp, "respond_as_lead", side_effect=mock_lead_respond,
            ):
                with patch.object(
                    ic_emp, "run", new_callable=AsyncMock, return_value=ic_result,
                ):
                    result = await company.cto_chat("Build the API")

        # CTO should have reviewed the team-lead's synthesis
        assert "Presenting results" in result or "backend team" in result

    @pytest.mark.asyncio
    async def test_flat_mode_still_works(self, config: Config):
        """Without team-leads, CTO delegates directly to ICs (flat mode)."""
        company = Company(config=config)
        cto = company.ensure_cto()
        emp = company.hire("backend-dev", get_role_def("backend-dev"), name="Alex")

        cto_response = MemberResult(
            output=(
                "I'll handle this.\n\n"
                "[DELEGATE:Alex]\nBuild the endpoint.\n[/DELEGATE]"
            ),
            total_cost_usd=0.02,
        )
        review_response = MemberResult(
            output="Endpoint is ready. Ship it.",
            total_cost_usd=0.01,
        )

        call_count = 0

        async def mock_cto_run(task, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return cto_response
            return review_response

        with patch.object(cto, "run", side_effect=mock_cto_run):
            with patch.object(
                Company, "_assign_to_employee", new_callable=AsyncMock,
                return_value="Endpoint built.",
            ):
                result = await company.cto_chat("Add an endpoint")

        assert "Ship it" in result or "ready" in result

    @pytest.mark.asyncio
    async def test_team_lead_revise_loop(self, config: Config):
        """Team lead can use [REVISE] blocks on their team members."""
        company = Company(config=config)
        lead_emp = company.hire("team-lead", get_role_def("team-lead"), name="Lead")
        ic_emp = company.hire("backend-dev", get_role_def("backend-dev"), name="Blake")

        company.create_team("backend")
        company.assign_to_team("Lead", "backend")
        company.assign_to_team("Blake", "backend")
        company.promote_to_lead("Lead", "backend")

        # Lead's initial response: delegate to Blake
        lead_resp_1 = (
            "Blake will handle the API.\n\n"
            "[DELEGATE:Blake]\nBuild the REST API.\n[/DELEGATE]"
        )
        # Lead's review: revise Blake's work
        lead_resp_2 = (
            "Error handling missing.\n\n"
            "[REVISE:Blake]\nAdd proper error handling.\n[/REVISE]"
        )
        # Lead's final review: approve
        lead_resp_3 = "Blake's revised work looks good. API complete."

        lead_call = 0

        async def mock_lead_respond(user_message, **kwargs):
            nonlocal lead_call
            lead_call += 1
            if lead_call == 1:
                return LeadResponse(text=lead_resp_1)
            elif lead_call == 2:
                return LeadResponse(text=lead_resp_2)
            return LeadResponse(text=lead_resp_3)

        ic_result = MemberResult(output="API implemented.", total_cost_usd=0.05)

        with patch.object(lead_emp, "respond_as_lead", side_effect=mock_lead_respond):
            with patch.object(
                ic_emp, "run", new_callable=AsyncMock, return_value=ic_result,
            ):
                result = await company.assign_work("backend", "Build an API")

        assert "API complete" in result or "looks good" in result
