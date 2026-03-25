"""Tests for crew module: Crew, CrewLead, CrewMember, and registry."""

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from shipwright.config import Config, CrewDef, MemberDef
from shipwright.crew.crew import Crew, CrewStatus, TaskRecord
from shipwright.crew.lead import CrewLead, _build_lead_system_prompt
from shipwright.crew.member import CrewMember, MemberResult
from shipwright.crew.registry import BUILTIN_CREWS, get_crew_def, list_crew_types


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

class TestRegistry:
    def test_builtin_crews_exist(self):
        expected = {"fullstack", "frontend", "backend", "qa", "devops", "security", "docs"}
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
