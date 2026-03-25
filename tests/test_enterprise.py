"""Tests for enterprise mode: EnterpriseCrew, sub-crew spawning, depth cap."""

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from shipwright.config import Config, CrewDef, MemberDef
from shipwright.crew.crew import (
    Crew,
    CrewStatus,
    EnterpriseCrew,
    MAX_HIERARCHY_DEPTH,
    TaskRecord,
)
from shipwright.crew.lead import LeadResponse, parse_delegations
from shipwright.crew.member import MemberResult
from shipwright.crew.registry import BUILTIN_CREWS, get_crew_def, list_crew_types


# ---------------------------------------------------------------------------
# Enterprise registry
# ---------------------------------------------------------------------------

class TestEnterpriseRegistry:
    def test_enterprise_in_builtin_crews(self):
        assert "enterprise" in BUILTIN_CREWS

    def test_enterprise_crew_def_has_sub_crew_members(self):
        edef = BUILTIN_CREWS["enterprise"]
        assert "backend" in edef.members
        assert "frontend" in edef.members
        assert "fullstack" in edef.members
        assert "qa" in edef.members
        assert "devops" in edef.members
        assert "security" in edef.members
        assert "docs" in edef.members

    def test_enterprise_in_list_crew_types(self):
        types = list_crew_types()
        assert "enterprise" in types

    def test_get_enterprise_crew_def(self):
        crew_def = get_crew_def("enterprise")
        assert crew_def.name == "enterprise"
        assert "Project Lead" in crew_def.lead_prompt


# ---------------------------------------------------------------------------
# EnterpriseCrew creation
# ---------------------------------------------------------------------------

class TestEnterpriseCrewCreation:
    def test_create_enterprise_crew(self):
        crew_def = get_crew_def("enterprise")
        config = Config()
        crew = EnterpriseCrew.create(
            "enterprise", crew_def, config, objective="Build billing system"
        )
        assert isinstance(crew, EnterpriseCrew)
        assert crew.crew_type == "enterprise"
        assert crew.objective == "Build billing system"
        assert crew.status == CrewStatus.IDLE
        assert crew.depth == 1
        assert crew.sub_crews == {}

    def test_enterprise_ensure_members_is_noop(self, tmp_path: Path):
        crew_def = get_crew_def("enterprise")
        config = Config(repo_root=tmp_path)
        crew = EnterpriseCrew.create(
            "enterprise", crew_def, config, objective="Test"
        )
        crew._ensure_members()
        # Enterprise crew doesn't populate members — it spawns sub-crews
        assert crew.members == {}

    def test_max_hierarchy_depth_constant(self):
        assert MAX_HIERARCHY_DEPTH == 3


# ---------------------------------------------------------------------------
# Sub-crew spawning
# ---------------------------------------------------------------------------

class TestSubCrewSpawning:
    @pytest.fixture
    def enterprise_crew(self, tmp_path: Path):
        crew_def = get_crew_def("enterprise")
        config = Config(repo_root=tmp_path)
        crew = EnterpriseCrew.create(
            "enterprise", crew_def, config, objective="Build billing"
        )
        return crew

    @pytest.mark.asyncio
    async def test_delegate_spawns_subcrew(self, enterprise_crew):
        """Delegating to a known crew type spawns a full Crew."""
        crew = enterprise_crew

        # Mock the sub-crew's chat method
        with patch.object(Crew, "chat", new_callable=AsyncMock) as mock_chat:
            mock_chat.return_value = "Backend work completed. API endpoints created."
            result = await crew.delegate("backend", "Build REST API")

        assert not result.is_error
        assert "Backend work completed" in result.output
        assert "backend" in crew.sub_crews
        assert crew.sub_crews["backend"].crew_type == "backend"

    @pytest.mark.asyncio
    async def test_delegate_unknown_crew_type(self, enterprise_crew):
        """Delegating to an unknown crew type returns an error."""
        result = await enterprise_crew.delegate("nonexistent_crew", "Do stuff")
        assert result.is_error
        assert "Unknown crew type" in result.output

    @pytest.mark.asyncio
    async def test_delegate_prevents_enterprise_nesting(self, enterprise_crew):
        """Cannot delegate to 'enterprise' — no recursive enterprise crews."""
        result = await enterprise_crew.delegate("enterprise", "Nest forever")
        assert result.is_error
        assert "Cannot nest enterprise" in result.output

    @pytest.mark.asyncio
    async def test_depth_cap_enforced(self, tmp_path: Path):
        """Delegation is refused when depth >= MAX_HIERARCHY_DEPTH."""
        crew_def = get_crew_def("enterprise")
        config = Config(repo_root=tmp_path)
        crew = EnterpriseCrew(
            id="test-deep",
            crew_type="enterprise",
            objective="Test depth",
            config=config,
            crew_def=crew_def,
            depth=MAX_HIERARCHY_DEPTH,  # Already at max
        )

        result = await crew.delegate("backend", "Should be refused")
        assert result.is_error
        assert "maximum hierarchy depth" in result.output

    @pytest.mark.asyncio
    async def test_depth_at_limit_minus_one_works(self, tmp_path: Path):
        """Delegation works when depth < MAX_HIERARCHY_DEPTH."""
        crew_def = get_crew_def("enterprise")
        config = Config(repo_root=tmp_path)
        crew = EnterpriseCrew(
            id="test-deep",
            crew_type="enterprise",
            objective="Test depth",
            config=config,
            crew_def=crew_def,
            depth=MAX_HIERARCHY_DEPTH - 1,
        )

        with patch.object(Crew, "chat", new_callable=AsyncMock) as mock_chat:
            mock_chat.return_value = "Done."
            result = await crew.delegate("backend", "Should work")

        assert not result.is_error

    @pytest.mark.asyncio
    async def test_task_records_tracked(self, enterprise_crew):
        """Sub-crew delegations create TaskRecords."""
        crew = enterprise_crew

        with patch.object(Crew, "chat", new_callable=AsyncMock) as mock_chat:
            mock_chat.return_value = "Done."
            await crew.delegate("backend", "Build API")

        assert len(crew.task_records) == 1
        rec = crew.task_records[0]
        assert rec.member_name == "backend"
        assert rec.task == "Build API"
        assert rec.status == "done"
        assert rec.started_at is not None
        assert rec.finished_at is not None

    @pytest.mark.asyncio
    async def test_failed_subcrew_tracked(self, enterprise_crew):
        """Failed sub-crew delegation is recorded properly."""
        crew = enterprise_crew

        with patch.object(Crew, "chat", new_callable=AsyncMock) as mock_chat:
            mock_chat.side_effect = RuntimeError("SDK crashed")
            result = await crew.delegate("backend", "Build API")

        assert result.is_error
        assert len(crew.task_records) == 1
        assert crew.task_records[0].status == "failed"


# ---------------------------------------------------------------------------
# Enterprise delegation loop
# ---------------------------------------------------------------------------

class TestEnterpriseDelegationLoop:
    @pytest.fixture
    def enterprise_crew(self, tmp_path: Path):
        crew_def = get_crew_def("enterprise")
        config = Config(repo_root=tmp_path)
        crew = EnterpriseCrew.create(
            "enterprise", crew_def, config, objective="Build billing"
        )
        return crew

    @pytest.mark.asyncio
    async def test_chat_with_delegation(self, enterprise_crew):
        """Full chat loop: project lead delegates to sub-crew."""
        crew = enterprise_crew

        lead_resp_1 = LeadResponse(
            text=(
                "I'll have the backend crew build the API.\n\n"
                "[DELEGATE:backend]\n"
                "Build REST endpoints for billing.\n"
                "[/DELEGATE]"
            )
        )
        lead_resp_2 = LeadResponse(
            text="The backend crew completed the API. Here's the summary."
        )

        call_count = 0

        async def mock_respond(user_message, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return lead_resp_1
            return lead_resp_2

        subcrew_result = MemberResult(output="API built successfully.")

        with (
            patch.object(crew.lead, "respond", side_effect=mock_respond),
            patch.object(
                crew, "_delegate_to_subcrew",
                new_callable=AsyncMock,
                return_value=subcrew_result,
            ),
        ):
            result = await crew.chat("Build the billing system")

        assert "backend crew" in result
        assert "completed" in result.lower() or "summary" in result.lower()

    @pytest.mark.asyncio
    async def test_parallel_subcrew_delegation(self, enterprise_crew):
        """Project lead delegates to multiple sub-crews in parallel."""
        crew = enterprise_crew

        lead_resp_1 = LeadResponse(
            text=(
                "I'll assign both crews in parallel.\n\n"
                "[DELEGATE:backend]\nBuild the API.\n[/DELEGATE]\n"
                "[DELEGATE:frontend]\nBuild the UI.\n[/DELEGATE]"
            )
        )
        lead_resp_2 = LeadResponse(text="Both crews completed their work.")

        call_count = 0

        async def mock_respond(user_message, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return lead_resp_1
            return lead_resp_2

        async def mock_delegate_subcrew(crew_type, task, context=""):
            return MemberResult(output=f"{crew_type} work done.")

        with (
            patch.object(crew.lead, "respond", side_effect=mock_respond),
            patch.object(
                crew, "_delegate_to_subcrew",
                side_effect=mock_delegate_subcrew,
            ),
        ):
            result = await crew.chat("Build billing")

        assert "completed" in result.lower()


# ---------------------------------------------------------------------------
# Enterprise summary and serialization
# ---------------------------------------------------------------------------

class TestEnterpriseSerialization:
    def test_summary_includes_sub_crews(self, tmp_path: Path):
        crew_def = get_crew_def("enterprise")
        config = Config(repo_root=tmp_path)
        crew = EnterpriseCrew.create(
            "enterprise", crew_def, config, objective="Build billing"
        )

        # Add a fake sub-crew
        backend_def = get_crew_def("backend")
        sub = Crew(
            id="enterprise-billing/backend",
            crew_type="backend",
            objective="Build API",
            config=config,
            crew_def=backend_def,
        )
        sub.task_records.append(
            TaskRecord(member_name="developer", task="Build API", status="done")
        )
        crew.sub_crews["backend"] = sub

        summary = crew.summary
        assert "Sub-crews:" in summary
        assert "backend" in summary

    def test_to_dict_includes_enterprise_fields(self, tmp_path: Path):
        crew_def = get_crew_def("enterprise")
        config = Config(repo_root=tmp_path)
        crew = EnterpriseCrew.create(
            "enterprise", crew_def, config, objective="Build billing"
        )

        data = crew.to_dict()
        assert data["is_enterprise"] is True
        assert data["depth"] == 1
        assert "sub_crews" in data

    def test_from_dict_restores_enterprise(self, tmp_path: Path):
        crew_def = get_crew_def("enterprise")
        config = Config(repo_root=tmp_path)
        crew = EnterpriseCrew.create(
            "enterprise", crew_def, config, objective="Build billing"
        )
        crew.task_records.append(
            TaskRecord(member_name="backend", task="Build API", status="done")
        )

        data = crew.to_dict()
        restored = EnterpriseCrew.from_dict(data, crew_def, config)

        assert isinstance(restored, EnterpriseCrew)
        assert restored.crew_type == "enterprise"
        assert restored.depth == 1
        assert len(restored.task_records) == 1


# ---------------------------------------------------------------------------
# Router enterprise integration
# ---------------------------------------------------------------------------

class TestRouterEnterprise:
    def test_hire_enterprise_returns_warning(self):
        from shipwright.conversation.router import Router
        from shipwright.conversation.session import Session

        config = Config()
        session = Session(id="test")
        router = Router(config=config, session=session)

        result = router._hire_crew("enterprise", "Build complete billing")
        assert "Enterprise mode" in result
        assert "5-10x more tokens" in result
        # Crew ID is "enterprise-build-complete-billing", check any key contains enterprise
        crew_id = list(router.crews.keys())[0]
        assert "enterprise" in crew_id
        assert isinstance(router.crews[crew_id], EnterpriseCrew)

    def test_hire_standard_crew_unchanged(self):
        from shipwright.conversation.router import Router
        from shipwright.conversation.session import Session

        config = Config()
        session = Session(id="test")
        router = Router(config=config, session=session)

        result = router._hire_crew("backend", "Add payments")
        assert "Enterprise mode" not in result
        assert "Team:" in result
        crew_id = list(router.crews.keys())[0]
        assert not isinstance(router.crews[crew_id], EnterpriseCrew)

    def test_router_serialization_enterprise(self):
        from shipwright.conversation.router import Router
        from shipwright.conversation.session import Session

        config = Config()
        session = Session(id="test")
        router = Router(config=config, session=session)
        router._hire_crew("enterprise", "Build billing")

        data = router.to_dict()
        crew_data = list(data["crews"].values())[0]
        assert crew_data["is_enterprise"] is True

        restored = Router.from_dict(data, config)
        crew_id = list(restored.crews.keys())[0]
        assert isinstance(restored.crews[crew_id], EnterpriseCrew)

    def test_help_mentions_enterprise(self):
        from shipwright.conversation.router import Router
        from shipwright.conversation.session import Session

        config = Config()
        session = Session(id="test")
        router = Router(config=config, session=session)
        help_text = router._help()
        assert "enterprise" in help_text.lower()
