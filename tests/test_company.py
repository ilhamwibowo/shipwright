"""Tests for the V2 Company module: hiring, teams, work assignment, serialization."""

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from shipwright.config import Config, MemberDef
from shipwright.company.company import Company, Team, format_duration_ms
from shipwright.company.employee import (
    Employee,
    EmployeeStatus,
    LeadResponse,
    MemberResult,
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
        """Delegation loop stops after max_delegation_rounds."""
        company = Company(config=config)
        company.max_delegation_rounds = 2

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

        assert "maximum delegation rounds" in result.lower()


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
        assert "Team: core" in summary
        assert "Team Lead" in summary

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
