"""Tests for harness design patterns: context resets, evaluator role, configurable iteration depth."""

from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from shipwright.config import Config, MemberDef
from shipwright.company.company import Company
from shipwright.company.employee import (
    Employee,
    EmployeeStatus,
    LeadResponse,
    MemberResult,
    Task,
    parse_delegations,
)
from shipwright.company.roles import BUILTIN_ROLES, ROLE_DISPLAY_NAMES, get_role_def


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_employee(tmp_path: Path, **overrides) -> Employee:
    defaults = dict(
        id="alex-backend-dev",
        name="Alex",
        role="backend-dev",
        role_def=MemberDef(
            role="Backend Developer",
            prompt="You write backend code.",
            tools=["Read", "Edit", "Write", "Bash"],
            max_turns=80,
        ),
        cwd=str(tmp_path),
        model="claude-sonnet-4-6",
        permission_mode="bypassPermissions",
        context_reset_threshold=30,
    )
    defaults.update(overrides)
    return Employee(**defaults)


# ---------------------------------------------------------------------------
# 1. Context Resets with Structured Handoff Artifacts
# ---------------------------------------------------------------------------


class TestContextReset:
    def test_needs_context_reset_false_when_below_threshold(self, tmp_path: Path):
        emp = _make_employee(tmp_path, context_reset_threshold=30)
        emp._cumulative_turns = 10
        assert emp._needs_context_reset() is False

    def test_needs_context_reset_true_at_80_percent(self, tmp_path: Path):
        emp = _make_employee(tmp_path, context_reset_threshold=30)
        emp._cumulative_turns = 24  # 80% of 30
        assert emp._needs_context_reset() is True

    def test_needs_context_reset_true_above_threshold(self, tmp_path: Path):
        emp = _make_employee(tmp_path, context_reset_threshold=30)
        emp._cumulative_turns = 35
        assert emp._needs_context_reset() is True

    def test_needs_context_reset_disabled_when_zero(self, tmp_path: Path):
        emp = _make_employee(tmp_path, context_reset_threshold=0)
        emp._cumulative_turns = 100
        assert emp._needs_context_reset() is False

    def test_build_handoff_artifact_format(self, tmp_path: Path):
        emp = _make_employee(tmp_path)
        emp._cumulative_turns = 25
        emp.cost_total_usd = 0.1234
        emp._conversation.append({"role": "user", "text": "Build the API"})
        emp._conversation.append({"role": "employee", "text": "Done"})
        emp.task_history.append(Task(
            id="t1", description="Build REST endpoints",
            assigned_to="Alex", status="done",
        ))

        artifact = emp._build_handoff_artifact("Continue API work")
        assert "# Handoff Artifact" in artifact
        assert "Alex" in artifact
        assert "## Summary" in artifact
        assert "## State" in artifact
        assert "## Recent Tasks" in artifact
        assert "## Recent Conversation" in artifact
        assert "## Next Steps" in artifact
        assert "## Key Decisions" in artifact
        assert "25 turns" in artifact
        assert "Build REST endpoints" in artifact

    def test_save_handoff_artifact_creates_file(self, tmp_path: Path):
        emp = _make_employee(tmp_path)
        emp._cumulative_turns = 25
        emp.task_history.append(Task(
            id="abc123", description="Build API",
            assigned_to="Alex", status="done",
        ))

        artifact_path = emp.save_handoff_artifact(
            "Build API", data_dir=tmp_path / ".shipwright",
        )

        assert artifact_path is not None
        assert artifact_path.exists()
        assert "alex_abc123.md" == artifact_path.name
        content = artifact_path.read_text()
        assert "# Handoff Artifact" in content

    def test_save_handoff_artifact_creates_handoffs_dir(self, tmp_path: Path):
        emp = _make_employee(tmp_path)
        data_dir = tmp_path / ".shipwright"
        assert not (data_dir / "handoffs").exists()

        emp.save_handoff_artifact(data_dir=data_dir)
        assert (data_dir / "handoffs").exists()

    def test_context_reset_clears_session(self, tmp_path: Path):
        emp = _make_employee(tmp_path)
        emp._session_id = "session-abc"
        emp._cumulative_turns = 25
        emp._conversation.append({"role": "user", "text": "hello"})

        artifact_path = emp.context_reset(
            task_description="test", data_dir=tmp_path / ".shipwright",
        )

        assert emp._session_id is None
        assert emp._cumulative_turns == 0
        assert emp._conversation == []
        assert artifact_path is not None
        assert artifact_path.exists()

    def test_load_handoff_context_reads_file(self, tmp_path: Path):
        emp = _make_employee(tmp_path)
        artifact = tmp_path / "handoff.md"
        artifact.write_text("# Test Handoff\nSome context here.")

        content = emp._load_handoff_context(artifact)
        assert "# Test Handoff" in content
        assert "Some context here." in content

    def test_load_handoff_context_missing_file(self, tmp_path: Path):
        emp = _make_employee(tmp_path)
        content = emp._load_handoff_context(tmp_path / "nonexistent.md")
        assert content == ""

    def test_cumulative_turns_persisted_in_serialization(self, tmp_path: Path):
        emp = _make_employee(tmp_path)
        emp._cumulative_turns = 42
        emp.context_reset_threshold = 50

        data = emp.to_dict()
        assert data["cumulative_turns"] == 42
        assert data["context_reset_threshold"] == 50

        restored = Employee.from_dict(
            data, emp.role_def, str(tmp_path),
            "claude-sonnet-4-6", "bypassPermissions",
        )
        assert restored._cumulative_turns == 42
        assert restored.context_reset_threshold == 50

    def test_config_passes_threshold_to_employee(self, tmp_path: Path):
        config = Config(
            repo_root=tmp_path,
            context_reset_threshold=50,
            sessions_dir=tmp_path / "sessions",
        )
        company = Company(config=config)
        role_def = get_role_def("backend-dev")
        emp = company.hire("backend-dev", role_def)
        assert emp.context_reset_threshold == 50


# ---------------------------------------------------------------------------
# 2. Dedicated Evaluator Role
# ---------------------------------------------------------------------------


class TestEvaluatorRole:
    def test_evaluator_exists_in_builtin_roles(self):
        assert "evaluator" in BUILTIN_ROLES

    def test_evaluator_display_name(self):
        assert "evaluator" in ROLE_DISPLAY_NAMES
        assert ROLE_DISPLAY_NAMES["evaluator"] == "Evaluator"

    def test_evaluator_role_def_properties(self):
        role_def = get_role_def("evaluator")
        assert role_def.role == "Evaluator"
        assert role_def.tools == ["Read", "Glob", "Grep"]  # Read-only
        assert role_def.max_turns == 30

    def test_evaluator_prompt_contains_grading_criteria(self):
        role_def = get_role_def("evaluator")
        prompt = role_def.prompt
        assert "Correctness" in prompt
        assert "Code Quality" in prompt
        assert "Completeness" in prompt
        assert "Integration" in prompt

    def test_evaluator_prompt_contains_scoring(self):
        role_def = get_role_def("evaluator")
        prompt = role_def.prompt
        assert "1-5" in prompt or "1/5" in prompt or "X/5" in prompt

    def test_evaluator_prompt_contains_verdicts(self):
        role_def = get_role_def("evaluator")
        prompt = role_def.prompt
        assert "APPROVE" in prompt
        assert "REVISE" in prompt
        assert "REJECT" in prompt

    def test_evaluator_prompt_is_read_only(self):
        role_def = get_role_def("evaluator")
        prompt = role_def.prompt
        assert "READ-ONLY" in prompt

    def test_evaluator_prompt_critique_focus(self):
        role_def = get_role_def("evaluator")
        prompt = role_def.prompt
        assert "CRITIQUE" in prompt or "critique" in prompt.lower()

    def test_cto_prompt_mentions_evaluator(self):
        role_def = get_role_def("cto")
        prompt = role_def.prompt
        assert "evaluator" in prompt.lower()

    def test_evaluator_can_be_hired(self, config: Config):
        company = Company(config=config)
        role_def = get_role_def("evaluator")
        emp = company.hire("evaluator", role_def, name="Reviewer")
        assert emp.name == "Reviewer"
        assert emp.role == "evaluator"
        assert emp.role_def.role == "Evaluator"


# ---------------------------------------------------------------------------
# 3. Configurable Iteration Depth
# ---------------------------------------------------------------------------


class TestConfigurableIterationDepth:
    def test_default_max_revision_rounds(self):
        config = Config()
        assert config.max_revision_rounds == 3

    def test_custom_max_revision_rounds(self):
        config = Config(max_revision_rounds=5)
        assert config.max_revision_rounds == 5

    def test_default_context_reset_threshold(self):
        config = Config()
        assert config.context_reset_threshold == 30

    def test_custom_context_reset_threshold(self):
        config = Config(context_reset_threshold=50)
        assert config.context_reset_threshold == 50

    @pytest.mark.asyncio
    async def test_delegation_loop_respects_config_rounds(self, config: Config):
        """Delegation loop stops after config.max_revision_rounds."""
        config = Config(
            repo_root=config.repo_root,
            max_revision_rounds=1,
            sessions_dir=config.sessions_dir,
        )
        company = Company(config=config)

        lead_emp = company.hire("team-lead", get_role_def("team-lead"), name="Alex")
        member_emp = company.hire("backend-dev", get_role_def("backend-dev"), name="Blake")

        company.create_team("core")
        company.assign_to_team("Alex", "core")
        company.assign_to_team("Blake", "core")
        company.promote_to_lead("Alex", "core")

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
            result = await company.assign_work("core", "Do some work")

        assert "maximum rounds" in result.lower()

    @pytest.mark.asyncio
    async def test_higher_max_rounds_allows_more_iterations(self, config: Config):
        """With higher max_revision_rounds, more iterations are permitted."""
        config = Config(
            repo_root=config.repo_root,
            max_revision_rounds=5,
            sessions_dir=config.sessions_dir,
        )
        company = Company(config=config)

        lead_emp = company.hire("team-lead", get_role_def("team-lead"), name="Alex")
        member_emp = company.hire("backend-dev", get_role_def("backend-dev"), name="Blake")

        company.create_team("core")
        company.assign_to_team("Alex", "core")
        company.assign_to_team("Blake", "core")
        company.promote_to_lead("Alex", "core")

        call_count = 0

        async def mock_respond_as_lead(user_message, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count <= 3:
                return LeadResponse(
                    text="More work.\n\n[DELEGATE:Blake]\nDo work.\n[/DELEGATE]"
                )
            return LeadResponse(text="All done. Here are the results.")

        member_result = MemberResult(output="Done.", total_cost_usd=0.01)

        with (
            patch.object(lead_emp, "respond_as_lead", side_effect=mock_respond_as_lead),
            patch.object(member_emp, "run", new_callable=AsyncMock, return_value=member_result),
        ):
            result = await company.assign_work("core", "Do some work")

        # With max_rounds=5, it should have completed before hitting the limit
        assert "maximum rounds" not in result.lower()
        # Lead was called multiple times (initial + reviews)
        assert call_count >= 3
