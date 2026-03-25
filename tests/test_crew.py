"""Tests for crew module: Crew, CrewLead, CrewMember, and registry."""

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from shipwright.config import Config, CrewDef, MemberDef
from shipwright.crew.crew import Crew, CrewStatus, TaskRecord
from shipwright.crew.lead import (
    CrewLead,
    DelegationRequest,
    LeadResponse,
    _build_lead_system_prompt,
    parse_delegations,
)
from shipwright.crew.member import CrewMember, MemberResult
from shipwright.crew.registry import BUILTIN_CREWS, get_crew_def, list_crew_types


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

class TestRegistry:
    def test_builtin_crews_exist(self):
        expected = {"fullstack", "frontend", "backend", "qa", "devops", "security", "docs", "enterprise"}
        assert expected == set(BUILTIN_CREWS.keys())

    def test_get_builtin_crew(self):
        crew_def = get_crew_def("backend")
        assert crew_def.name == "backend"
        assert "architect" in crew_def.members
        assert "developer" in crew_def.members

    def test_get_unknown_crew_raises(self):
        with pytest.raises(ValueError, match="Unknown crew type"):
            get_crew_def("nonexistent")

    def test_get_custom_crew(self):
        custom = CrewDef(
            name="custom",
            lead_prompt="Custom lead.",
            members={"dev": MemberDef(role="Dev", prompt="You code.")},
        )
        config = Config(custom_crews={"custom": custom})
        result = get_crew_def("custom", config)
        assert result.name == "custom"

    def test_custom_overrides_builtin(self):
        custom_backend = CrewDef(
            name="backend",
            lead_prompt="Custom backend lead.",
            members={},
        )
        config = Config(custom_crews={"backend": custom_backend})
        result = get_crew_def("backend", config)
        assert result.lead_prompt == "Custom backend lead."

    def test_list_crew_types(self):
        types = list_crew_types()
        assert "backend" in types
        assert "frontend" in types
        assert sorted(types) == types

    def test_list_includes_custom(self):
        custom = CrewDef(name="ml", lead_prompt="ML lead.", members={})
        config = Config(custom_crews={"ml": custom})
        types = list_crew_types(config)
        assert "ml" in types


# ---------------------------------------------------------------------------
# CrewMember
# ---------------------------------------------------------------------------

class TestCrewMember:
    def test_member_properties(self):
        mdef = MemberDef(
            role="Backend Dev",
            prompt="You write backend code.",
            tools=["Read", "Edit", "Write", "Bash"],
            max_turns=80,
        )
        member = CrewMember(name="dev", definition=mdef, cwd="/tmp")
        assert member.role == "Backend Dev"
        assert member.system_prompt == "You write backend code."
        assert "Edit" in member.allowed_tools
        assert member.max_turns == 80

    def test_reset_session(self):
        mdef = MemberDef(role="Dev", prompt="test")
        member = CrewMember(name="dev", definition=mdef, cwd="/tmp")
        member._session_id = "some-session"
        member.reset_session()
        assert member._session_id is None


# ---------------------------------------------------------------------------
# CrewLead
# ---------------------------------------------------------------------------

class TestCrewLead:
    def test_build_system_prompt(self):
        crew_def = CrewDef(
            name="backend",
            lead_prompt="Senior backend lead.",
            members={
                "dev": MemberDef(
                    role="Developer",
                    prompt="Implements APIs.",
                    tools=["Read", "Edit", "Write"],
                ),
            },
        )
        prompt = _build_lead_system_prompt(crew_def, "Python project")
        assert "backend" in prompt
        assert "Developer" in prompt
        assert "Implements APIs" in prompt
        assert "Python project" in prompt

    def test_lead_serialization(self):
        config = Config()
        crew_def = get_crew_def("backend")
        lead = CrewLead(crew_def=crew_def, config=config)
        lead._conversation.append({"role": "user", "text": "hello"})
        lead._session_id = "test-session"

        data = lead.to_dict()
        assert data["session_id"] == "test-session"
        assert len(data["conversation"]) == 1

        # Restore
        lead2 = CrewLead(crew_def=crew_def, config=config)
        lead2.restore_from_dict(data)
        assert lead2._session_id == "test-session"
        assert len(lead2._conversation) == 1

    def test_lead_reset(self):
        config = Config()
        crew_def = get_crew_def("backend")
        lead = CrewLead(crew_def=crew_def, config=config)
        lead._conversation.append({"role": "user", "text": "hello"})
        lead._session_id = "test-session"

        lead.reset()
        assert lead._session_id is None
        assert lead._conversation == []


# ---------------------------------------------------------------------------
# Crew
# ---------------------------------------------------------------------------

class TestCrew:
    def test_create(self):
        crew_def = get_crew_def("backend")
        config = Config()
        crew = Crew.create("backend", crew_def, config, objective="Add payments")

        assert crew.crew_type == "backend"
        assert crew.objective == "Add payments"
        assert crew.status == CrewStatus.IDLE
        assert "backend" in crew.id
        assert "payment" in crew.id

    def test_ensure_members(self, tmp_path: Path):
        crew_def = get_crew_def("backend")
        config = Config(repo_root=tmp_path)
        crew = Crew.create("backend", crew_def, config, objective="Test")

        assert crew.members == {}  # lazy
        crew._ensure_members()
        assert "architect" in crew.members
        assert "developer" in crew.members
        assert "db_engineer" in crew.members

    def test_crew_summary(self):
        crew_def = get_crew_def("backend")
        config = Config()
        crew = Crew.create("backend", crew_def, config, objective="Add payments")
        summary = crew.summary
        assert "backend" in summary
        assert "Add payments" in summary

    def test_crew_serialization(self, tmp_path: Path):
        crew_def = get_crew_def("backend")
        config = Config(repo_root=tmp_path)
        crew = Crew.create("backend", crew_def, config, objective="Add payments")
        crew.task_records.append(
            TaskRecord(member_name="developer", task="Write API", status="done")
        )

        data = crew.to_dict()
        assert data["crew_type"] == "backend"
        assert data["objective"] == "Add payments"
        assert len(data["task_records"]) == 1

        restored = Crew.from_dict(data, crew_def, config)
        assert restored.crew_type == "backend"
        assert restored.objective == "Add payments"
        assert len(restored.task_records) == 1

    async def test_delegate_unknown_member(self, tmp_path: Path):
        crew_def = get_crew_def("backend")
        config = Config(repo_root=tmp_path)
        crew = Crew.create("backend", crew_def, config, objective="Test")
        crew._ensure_members()

        with pytest.raises(ValueError, match="No member"):
            await crew.delegate("nonexistent", "do stuff")

    def test_status_transitions(self):
        crew_def = get_crew_def("backend")
        config = Config()
        crew = Crew.create("backend", crew_def, config, objective="Test")

        assert crew.status == CrewStatus.IDLE
        crew.pause()
        assert crew.status == CrewStatus.PAUSED
        crew.resume()
        assert crew.status == CrewStatus.IDLE

    def test_worktree_setup(self, sample_repo: Path):
        crew_def = get_crew_def("backend")
        config = Config(repo_root=sample_repo)
        crew = Crew.create("backend", crew_def, config, objective="Test worktree")

        wt = crew.setup_worktree()
        assert wt.exists()
        assert crew.branch is not None
        assert "shipwright" in crew.branch

        crew.cleanup()
        assert not wt.exists()


# ---------------------------------------------------------------------------
# Delegation Parsing
# ---------------------------------------------------------------------------

class TestDelegationParsing:
    def test_no_delegations(self):
        text = "I'll analyze this and get back to you with a plan."
        clean, delegations = parse_delegations(text)
        assert clean == text
        assert delegations == []

    def test_single_delegation(self):
        text = (
            "I'm having the architect look at the codebase.\n\n"
            "[DELEGATE:architect]\n"
            "Explore the codebase and identify all payment-related code.\n"
            "[/DELEGATE]"
        )
        clean, delegations = parse_delegations(text)
        assert "having the architect" in clean
        assert "[DELEGATE" not in clean
        assert len(delegations) == 1
        assert delegations[0].member_name == "architect"
        assert "payment-related" in delegations[0].task

    def test_multiple_delegations(self):
        text = (
            "Let me assign parallel tasks.\n\n"
            "[DELEGATE:frontend]\n"
            "Build the checkout form.\n"
            "[/DELEGATE]\n\n"
            "[DELEGATE:backend]\n"
            "Implement the /api/checkout endpoint.\n"
            "[/DELEGATE]"
        )
        clean, delegations = parse_delegations(text)
        assert "parallel tasks" in clean
        assert len(delegations) == 2
        assert delegations[0].member_name == "frontend"
        assert delegations[1].member_name == "backend"
        assert "checkout form" in delegations[0].task
        assert "/api/checkout" in delegations[1].task

    def test_multiline_task(self):
        text = (
            "[DELEGATE:developer]\n"
            "Step 1: Create the model.\n"
            "Step 2: Add migrations.\n"
            "Step 3: Write tests.\n"
            "[/DELEGATE]"
        )
        _, delegations = parse_delegations(text)
        assert len(delegations) == 1
        assert "Step 1" in delegations[0].task
        assert "Step 3" in delegations[0].task

    def test_empty_task_ignored(self):
        text = "[DELEGATE:architect]\n\n[/DELEGATE]"
        _, delegations = parse_delegations(text)
        assert delegations == []

    def test_delegation_block_stripped_from_clean_text(self):
        text = (
            "Before delegation.\n\n"
            "[DELEGATE:dev]\nDo stuff.\n[/DELEGATE]\n\n"
            "After delegation."
        )
        clean, delegations = parse_delegations(text)
        assert "Before delegation." in clean
        assert "After delegation." in clean
        assert "[DELEGATE" not in clean
        assert len(delegations) == 1


# ---------------------------------------------------------------------------
# Delegation Loop
# ---------------------------------------------------------------------------

class TestDelegationLoop:
    """Test the delegation loop in Crew.chat() with mocked lead/member responses."""

    @pytest.fixture
    def crew_with_members(self, tmp_path: Path):
        crew_def = get_crew_def("backend")
        config = Config(repo_root=tmp_path)
        crew = Crew.create("backend", crew_def, config, objective="Add payments")
        crew._ensure_members()
        return crew

    @pytest.mark.asyncio
    async def test_no_delegation_passthrough(self, crew_with_members):
        """When lead responds without delegation blocks, return directly."""
        crew = crew_with_members
        lead_text = "Sure, I can help with that. Let me explain the approach."

        with patch.object(crew.lead, "respond", new_callable=AsyncMock) as mock_respond:
            mock_respond.return_value = LeadResponse(text=lead_text)
            result = await crew.chat("Add Stripe payments")

        assert result == lead_text
        mock_respond.assert_called_once()

    @pytest.mark.asyncio
    async def test_single_delegation_round(self, crew_with_members):
        """Lead delegates once, gets result, responds without further delegation."""
        crew = crew_with_members

        lead_resp_1 = LeadResponse(
            text=(
                "I'm having the architect analyze the codebase.\n\n"
                "[DELEGATE:architect]\n"
                "Explore the project structure.\n"
                "[/DELEGATE]"
            )
        )
        lead_resp_2 = LeadResponse(
            text="The architect found a clean structure. Here's the plan..."
        )

        call_count = 0

        async def mock_respond(user_message, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return lead_resp_1
            return lead_resp_2

        member_result = MemberResult(output="Found FastAPI project with standard layout.")

        with (
            patch.object(crew.lead, "respond", side_effect=mock_respond),
            patch.object(crew, "delegate", new_callable=AsyncMock, return_value=member_result),
        ):
            result = await crew.chat("Analyze the codebase")

        assert "architect analyze" in result
        assert "clean structure" in result

    @pytest.mark.asyncio
    async def test_parallel_delegation(self, crew_with_members):
        """Multiple [DELEGATE] blocks trigger delegate_parallel."""
        crew = crew_with_members

        lead_resp_1 = LeadResponse(
            text=(
                "Assigning parallel work.\n\n"
                "[DELEGATE:developer]\nBuild the API.\n[/DELEGATE]\n"
                "[DELEGATE:db_engineer]\nCreate the schema.\n[/DELEGATE]"
            )
        )
        lead_resp_2 = LeadResponse(text="Both tasks are complete.")

        call_count = 0

        async def mock_respond(user_message, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return lead_resp_1
            return lead_resp_2

        parallel_results = {
            "developer": MemberResult(output="API built."),
            "db_engineer": MemberResult(output="Schema created."),
        }

        with (
            patch.object(crew.lead, "respond", side_effect=mock_respond),
            patch.object(
                crew, "delegate_parallel",
                new_callable=AsyncMock,
                return_value=parallel_results,
            ),
        ):
            result = await crew.chat("Build payments")

        assert "parallel work" in result
        assert "complete" in result

    @pytest.mark.asyncio
    async def test_multi_step_delegation(self, crew_with_members):
        """Lead delegates to architect, reviews, then delegates to developer."""
        crew = crew_with_members

        responses = [
            LeadResponse(
                text="First, architect.\n\n[DELEGATE:architect]\nAnalyze.\n[/DELEGATE]"
            ),
            LeadResponse(
                text="Good analysis. Now implement.\n\n[DELEGATE:developer]\nBuild it.\n[/DELEGATE]"
            ),
            LeadResponse(text="All done! Here's the summary."),
        ]

        call_count = 0

        async def mock_respond(user_message, **kwargs):
            nonlocal call_count
            call_count += 1
            return responses[min(call_count - 1, len(responses) - 1)]

        member_result = MemberResult(output="Work done.")

        with (
            patch.object(crew.lead, "respond", side_effect=mock_respond),
            patch.object(crew, "delegate", new_callable=AsyncMock, return_value=member_result),
        ):
            result = await crew.chat("Build payments")

        assert "All done" in result
        assert call_count == 3  # initial + 2 followups

    @pytest.mark.asyncio
    async def test_max_rounds_prevents_infinite_loop(self, crew_with_members):
        """Delegation loop stops after max_delegation_rounds."""
        crew = crew_with_members
        crew.max_delegation_rounds = 2

        # Lead always delegates — should be capped
        always_delegates = LeadResponse(
            text="Delegating.\n\n[DELEGATE:architect]\nDo more.\n[/DELEGATE]"
        )

        with (
            patch.object(
                crew.lead, "respond",
                new_callable=AsyncMock,
                return_value=always_delegates,
            ),
            patch.object(
                crew, "delegate",
                new_callable=AsyncMock,
                return_value=MemberResult(output="Done."),
            ),
        ):
            result = await crew.chat("Keep going forever")

        assert "maximum delegation rounds" in result.lower()

    @pytest.mark.asyncio
    async def test_delegation_feeds_results_to_lead(self, crew_with_members):
        """Verify that member results are fed back to the lead."""
        crew = crew_with_members

        lead_resp_1 = LeadResponse(
            text="Delegating.\n\n[DELEGATE:architect]\nAnalyze.\n[/DELEGATE]"
        )
        lead_resp_2 = LeadResponse(text="Done reviewing.")

        call_count = 0
        captured_followup = None

        async def mock_respond(user_message, **kwargs):
            nonlocal call_count, captured_followup
            call_count += 1
            if call_count == 1:
                return lead_resp_1
            captured_followup = user_message
            return lead_resp_2

        member_result = MemberResult(output="Found 3 API endpoints.")

        with (
            patch.object(crew.lead, "respond", side_effect=mock_respond),
            patch.object(crew, "delegate", new_callable=AsyncMock, return_value=member_result),
        ):
            await crew.chat("Analyze")

        assert captured_followup is not None
        assert "results from the team" in captured_followup.lower()
        assert "3 API endpoints" in captured_followup

    @pytest.mark.asyncio
    async def test_failed_member_result_forwarded(self, crew_with_members):
        """Failed member results are still reported to the lead."""
        crew = crew_with_members

        lead_resp_1 = LeadResponse(
            text="Delegating.\n\n[DELEGATE:developer]\nBuild it.\n[/DELEGATE]"
        )
        lead_resp_2 = LeadResponse(text="The developer hit an error. Let me try a different approach.")

        call_count = 0

        async def mock_respond(user_message, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return lead_resp_1
            return lead_resp_2

        failed_result = MemberResult(output="Error: module not found", is_error=True)

        with (
            patch.object(crew.lead, "respond", side_effect=mock_respond),
            patch.object(crew, "delegate", new_callable=AsyncMock, return_value=failed_result),
        ):
            result = await crew.chat("Build it")

        assert "different approach" in result

    @pytest.mark.asyncio
    async def test_delegation_callbacks_fired(self, crew_with_members):
        """on_delegation_start and on_delegation_end are called correctly."""
        crew = crew_with_members

        lead_resp_1 = LeadResponse(
            text="Delegating.\n\n[DELEGATE:architect]\nAnalyze.\n[/DELEGATE]"
        )
        lead_resp_2 = LeadResponse(text="Done.")

        call_count = 0

        async def mock_respond(user_message, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return lead_resp_1
            return lead_resp_2

        member_result = MemberResult(output="Analysis complete.")

        start_calls = []
        end_calls = []

        def on_start(name, task, round_num, max_rounds):
            start_calls.append((name, task, round_num, max_rounds))

        def on_end(name, duration, is_error):
            end_calls.append((name, duration, is_error))

        with (
            patch.object(crew.lead, "respond", side_effect=mock_respond),
            patch.object(crew, "delegate", new_callable=AsyncMock, return_value=member_result),
        ):
            await crew.chat(
                "Analyze",
                on_delegation_start=on_start,
                on_delegation_end=on_end,
            )

        assert len(start_calls) == 1
        assert start_calls[0][0] == "architect"
        assert start_calls[0][2] == 1  # round_num
        assert start_calls[0][3] == crew.max_delegation_rounds

        assert len(end_calls) == 1
        assert end_calls[0][0] == "architect"
        assert end_calls[0][2] is False  # not an error

    @pytest.mark.asyncio
    async def test_delegation_end_reports_error(self, crew_with_members):
        """on_delegation_end reports is_error=True for failed members."""
        crew = crew_with_members

        lead_resp_1 = LeadResponse(
            text="Delegating.\n\n[DELEGATE:developer]\nBuild.\n[/DELEGATE]"
        )
        lead_resp_2 = LeadResponse(text="Failed.")

        call_count = 0

        async def mock_respond(user_message, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return lead_resp_1
            return lead_resp_2

        failed_result = MemberResult(output="Error", is_error=True)
        end_calls = []

        with (
            patch.object(crew.lead, "respond", side_effect=mock_respond),
            patch.object(crew, "delegate", new_callable=AsyncMock, return_value=failed_result),
        ):
            await crew.chat(
                "Build",
                on_delegation_end=lambda name, dt, err: end_calls.append((name, err)),
            )

        assert len(end_calls) == 1
        assert end_calls[0][1] is True

    @pytest.mark.asyncio
    async def test_on_progress_callback(self, crew_with_members):
        """on_progress is called with 'Reviewing results...' after delegation."""
        crew = crew_with_members

        lead_resp_1 = LeadResponse(
            text="Delegating.\n\n[DELEGATE:architect]\nAnalyze.\n[/DELEGATE]"
        )
        lead_resp_2 = LeadResponse(text="Done.")

        call_count = 0

        async def mock_respond(user_message, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return lead_resp_1
            return lead_resp_2

        member_result = MemberResult(output="Analysis complete.")
        progress_calls = []

        with (
            patch.object(crew.lead, "respond", side_effect=mock_respond),
            patch.object(crew, "delegate", new_callable=AsyncMock, return_value=member_result),
        ):
            await crew.chat(
                "Analyze",
                on_progress=lambda msg: progress_calls.append(msg),
            )

        assert len(progress_calls) >= 1
        assert "Reviewing results..." in progress_calls

    @pytest.mark.asyncio
    async def test_parallel_delegation_callbacks(self, crew_with_members):
        """Callbacks fire for each member in parallel delegation."""
        crew = crew_with_members

        lead_resp_1 = LeadResponse(
            text=(
                "Parallel.\n\n"
                "[DELEGATE:developer]\nBuild API.\n[/DELEGATE]\n"
                "[DELEGATE:db_engineer]\nCreate schema.\n[/DELEGATE]"
            )
        )
        lead_resp_2 = LeadResponse(text="Both done.")

        call_count = 0

        async def mock_respond(user_message, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return lead_resp_1
            return lead_resp_2

        parallel_results = {
            "developer": MemberResult(output="API built."),
            "db_engineer": MemberResult(output="Schema created."),
        }

        start_names = []
        end_names = []

        with (
            patch.object(crew.lead, "respond", side_effect=mock_respond),
            patch.object(
                crew, "delegate_parallel",
                new_callable=AsyncMock,
                return_value=parallel_results,
            ),
        ):
            await crew.chat(
                "Build",
                on_delegation_start=lambda n, t, r, m: start_names.append(n),
                on_delegation_end=lambda n, d, e: end_names.append(n),
            )

        assert set(start_names) == {"developer", "db_engineer"}
        assert set(end_names) == {"developer", "db_engineer"}


# ---------------------------------------------------------------------------
# _last_task_duration
# ---------------------------------------------------------------------------

class TestLastTaskDuration:
    def test_returns_duration(self):
        crew_def = get_crew_def("backend")
        config = Config()
        crew = Crew.create("backend", crew_def, config, objective="Test")
        crew.task_records.append(
            TaskRecord(
                member_name="developer",
                task="Build API",
                status="done",
                started_at=100.0,
                finished_at=112.5,
            )
        )
        assert crew._last_task_duration("developer") == 12.5

    def test_returns_zero_when_no_records(self):
        crew_def = get_crew_def("backend")
        config = Config()
        crew = Crew.create("backend", crew_def, config, objective="Test")
        assert crew._last_task_duration("developer") == 0.0

    def test_returns_most_recent(self):
        crew_def = get_crew_def("backend")
        config = Config()
        crew = Crew.create("backend", crew_def, config, objective="Test")
        crew.task_records.append(
            TaskRecord(member_name="dev", task="t1", status="done",
                       started_at=100.0, finished_at=105.0)
        )
        crew.task_records.append(
            TaskRecord(member_name="dev", task="t2", status="done",
                       started_at=200.0, finished_at=220.0)
        )
        assert crew._last_task_duration("dev") == 20.0
