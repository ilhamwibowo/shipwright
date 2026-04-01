"""Tests for the roadmap system: parsing, execution loop, resume, progress tracking."""

import asyncio
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from shipwright.config import Config, MemberDef
from shipwright.company.company import Company, Team
from shipwright.company.employee import (
    Employee,
    EmployeeStatus,
    MemberResult,
    Roadmap,
    RoadmapTask,
    RoadmapTaskStatus,
    parse_roadmap_block,
    parse_execute_roadmap,
)
from shipwright.company.roles import get_role_def


# ---------------------------------------------------------------------------
# Roadmap parsing
# ---------------------------------------------------------------------------


class TestParseRoadmapBlock:
    def test_parse_numbered_list(self):
        text = (
            "Here's my plan:\n\n"
            "[ROADMAP]\n"
            "1. Explore codebase\n"
            "2. Design the schema\n"
            "3. Implement the service\n"
            "4. Add API endpoints\n"
            "5. Write tests\n"
            "[/ROADMAP]\n\n"
            "Let me know if this looks good."
        )
        clean, roadmap = parse_roadmap_block(text)

        assert roadmap is not None
        assert len(roadmap.tasks) == 5
        assert roadmap.tasks[0].index == 1
        assert roadmap.tasks[0].description == "Explore codebase"
        assert roadmap.tasks[4].index == 5
        assert roadmap.tasks[4].description == "Write tests"
        assert "ROADMAP" not in clean
        assert "Let me know" in clean
        assert "plan" in clean

    def test_parse_dash_list(self):
        text = (
            "[ROADMAP]\n"
            "- Set up database models\n"
            "- Build REST API\n"
            "- Create frontend\n"
            "[/ROADMAP]"
        )
        clean, roadmap = parse_roadmap_block(text)

        assert roadmap is not None
        assert len(roadmap.tasks) == 3
        # Dash lists get auto-indexed
        assert roadmap.tasks[0].index == 1
        assert roadmap.tasks[1].index == 2
        assert roadmap.tasks[2].index == 3
        assert roadmap.tasks[0].description == "Set up database models"

    def test_no_roadmap_block(self):
        text = "Just a normal response with no roadmap."
        clean, roadmap = parse_roadmap_block(text)

        assert roadmap is None
        assert clean == text

    def test_empty_roadmap_block(self):
        text = "[ROADMAP]\n\n[/ROADMAP]"
        clean, roadmap = parse_roadmap_block(text)

        assert roadmap is None

    def test_mixed_numbering_reindexed(self):
        text = (
            "[ROADMAP]\n"
            "5. Task A\n"
            "10. Task B\n"
            "15. Task C\n"
            "[/ROADMAP]"
        )
        clean, roadmap = parse_roadmap_block(text)

        assert roadmap is not None
        assert len(roadmap.tasks) == 3
        assert roadmap.tasks[0].index == 1
        assert roadmap.tasks[1].index == 2
        assert roadmap.tasks[2].index == 3

    def test_surrounding_text_preserved(self):
        text = (
            "I'll create a roadmap for this.\n\n"
            "[ROADMAP]\n"
            "1. Task one\n"
            "[/ROADMAP]\n\n"
            "Approve this and I'll start."
        )
        clean, roadmap = parse_roadmap_block(text)

        assert roadmap is not None
        assert "I'll create a roadmap" in clean
        assert "Approve this" in clean

    def test_parse_with_period_after_number(self):
        text = "[ROADMAP]\n1) First task\n2) Second task\n[/ROADMAP]"
        clean, roadmap = parse_roadmap_block(text)

        assert roadmap is not None
        assert len(roadmap.tasks) == 2
        assert roadmap.tasks[0].description == "First task"


class TestParseExecuteRoadmap:
    def test_detects_execute_signal(self):
        text = "Starting execution now. [EXECUTE_ROADMAP]"
        clean, should_execute = parse_execute_roadmap(text)

        assert should_execute is True
        assert "EXECUTE_ROADMAP" not in clean
        assert "Starting execution" in clean

    def test_no_signal(self):
        text = "Just a normal response."
        clean, should_execute = parse_execute_roadmap(text)

        assert should_execute is False
        assert clean == text


# ---------------------------------------------------------------------------
# Roadmap dataclass
# ---------------------------------------------------------------------------


class TestRoadmap:
    def _make_roadmap(self, n: int = 3) -> Roadmap:
        tasks = [
            RoadmapTask(index=i + 1, description=f"Task {i + 1}")
            for i in range(n)
        ]
        return Roadmap(tasks=tasks, original_request="Build something big")

    def test_current_task_index(self):
        rm = self._make_roadmap()
        assert rm.current_task_index == 1

        rm.tasks[0].status = RoadmapTaskStatus.DONE
        assert rm.current_task_index == 2

        rm.tasks[1].status = RoadmapTaskStatus.DONE
        assert rm.current_task_index == 3

        rm.tasks[2].status = RoadmapTaskStatus.DONE
        assert rm.current_task_index is None

    def test_done_count(self):
        rm = self._make_roadmap()
        assert rm.done_count == 0

        rm.tasks[0].status = RoadmapTaskStatus.DONE
        assert rm.done_count == 1

    def test_is_complete(self):
        rm = self._make_roadmap(2)
        assert rm.is_complete is False

        rm.tasks[0].status = RoadmapTaskStatus.DONE
        rm.tasks[1].status = RoadmapTaskStatus.DONE
        assert rm.is_complete is True

    def test_accumulated_context(self):
        rm = self._make_roadmap(2)
        rm.tasks[0].status = RoadmapTaskStatus.DONE
        rm.tasks[0].handoff_artifact = "Artifact content for task 1"

        ctx = rm.accumulated_context
        assert "Task 1" in ctx
        assert "Artifact content" in ctx
        # Task 2 not done, should not appear
        assert "Task 2" not in ctx

    def test_status_display(self):
        rm = self._make_roadmap(3)
        rm.tasks[0].status = RoadmapTaskStatus.DONE
        rm.tasks[1].status = RoadmapTaskStatus.RUNNING

        display = rm.status_display()
        assert "[x]" in display
        assert "[~]" in display
        assert "[ ]" in display
        assert "1/3" in display

    def test_status_display_paused(self):
        from shipwright.company.employee import RoadmapState
        rm = self._make_roadmap()
        rm.paused = True
        rm.state = RoadmapState.PAUSED

        display = rm.status_display()
        assert "Paused" in display

    def test_serialization_round_trip(self):
        rm = self._make_roadmap(2)
        rm.original_request = "Build billing"
        rm.approved = True
        rm.tasks[0].status = RoadmapTaskStatus.DONE
        rm.tasks[0].output_summary = "Done: created models"
        rm.tasks[0].handoff_artifact = "artifact content"

        data = rm.to_dict()
        restored = Roadmap.from_dict(data)

        assert restored.original_request == "Build billing"
        assert restored.approved is True
        assert len(restored.tasks) == 2
        assert restored.tasks[0].status == RoadmapTaskStatus.DONE
        assert restored.tasks[0].output_summary == "Done: created models"
        assert restored.tasks[0].handoff_artifact == "artifact content"
        assert restored.tasks[1].status == RoadmapTaskStatus.PENDING


class TestRoadmapTask:
    def test_to_dict(self):
        task = RoadmapTask(
            index=1,
            description="Build API",
            status=RoadmapTaskStatus.DONE,
            output_summary="Created endpoints",
            handoff_artifact="context data",
        )
        d = task.to_dict()
        assert d["index"] == 1
        assert d["description"] == "Build API"
        assert d["status"] == "done"

    def test_from_dict(self):
        d = {
            "index": 2,
            "description": "Write tests",
            "status": "failed",
            "output_summary": "Error occurred",
            "handoff_artifact": "",
        }
        task = RoadmapTask.from_dict(d)
        assert task.index == 2
        assert task.status == RoadmapTaskStatus.FAILED

    def test_truncation(self):
        task = RoadmapTask(
            index=1,
            description="Test",
            output_summary="x" * 5000,
            handoff_artifact="y" * 5000,
        )
        d = task.to_dict()
        assert len(d["output_summary"]) == 2000
        assert len(d["handoff_artifact"]) == 3000


# ---------------------------------------------------------------------------
# Company — roadmap integration
# ---------------------------------------------------------------------------


class TestCompanyRoadmap:
    def test_roadmap_persists_in_company_state(self, config: Config):
        company = Company(config=config)
        roadmap = Roadmap(
            tasks=[
                RoadmapTask(index=1, description="Task A"),
                RoadmapTask(index=2, description="Task B"),
            ],
            original_request="Big project",
            approved=True,
        )
        company.active_roadmap = roadmap

        data = company.to_dict()
        assert "active_roadmap" in data
        assert len(data["active_roadmap"]["tasks"]) == 2

        restored = Company.from_dict(data, config)
        assert restored.active_roadmap is not None
        assert restored.active_roadmap.approved is True
        assert len(restored.active_roadmap.tasks) == 2
        assert restored.active_roadmap.tasks[0].description == "Task A"

    def test_no_roadmap_in_state(self, config: Config):
        company = Company(config=config)
        data = company.to_dict()
        assert "active_roadmap" not in data

        restored = Company.from_dict(data, config)
        assert restored.active_roadmap is None


class TestCTOChatRoadmap:
    """Test that cto_chat correctly parses roadmap blocks from CTO output."""

    @pytest.mark.asyncio
    async def test_cto_outputs_roadmap(self, config: Config):
        """When CTO outputs a [ROADMAP] block, it's stored and presented."""
        company = Company(config=config)
        cto = company.ensure_cto()

        cto_response = (
            "This is a big project. Here's my plan:\n\n"
            "[ROADMAP]\n"
            "1. Explore existing code\n"
            "2. Design the schema\n"
            "3. Implement the service\n"
            "[/ROADMAP]\n\n"
            "Approve this and I'll start."
        )

        mock_result = MemberResult(
            output=cto_response,
            session_id="session-cto",
            num_turns=1,
        )

        with patch.object(cto, "run", new_callable=AsyncMock, return_value=mock_result):
            response = await company.cto_chat(message="Build billing system")

        assert company.active_roadmap is not None
        assert len(company.active_roadmap.tasks) == 3
        assert company.active_roadmap.original_request == "Build billing system"
        assert company.active_roadmap.approved is False
        assert "go" in response.lower() or "approve" in response.lower()

    @pytest.mark.asyncio
    async def test_cto_normal_response_no_roadmap(self, config: Config):
        """Normal CTO responses should not create a roadmap."""
        company = Company(config=config)
        cto = company.ensure_cto()

        mock_result = MemberResult(
            output="Sure, I'll add a health check endpoint right away.",
            session_id="session-cto",
            num_turns=1,
        )

        with patch.object(cto, "run", new_callable=AsyncMock, return_value=mock_result):
            response = await company.cto_chat(message="Add a health check")

        assert company.active_roadmap is None


class TestExecuteRoadmap:
    """Test the roadmap execution loop."""

    @pytest.mark.asyncio
    async def test_execute_all_tasks(self, config: Config):
        """Roadmap executes all tasks in sequence."""
        company = Company(config=config)
        cto = company.ensure_cto()

        roadmap = Roadmap(
            tasks=[
                RoadmapTask(index=1, description="Task one"),
                RoadmapTask(index=2, description="Task two"),
            ],
            original_request="Build it",
            approved=True,
        )
        company.active_roadmap = roadmap

        mock_result = MemberResult(
            output="Done with this task.",
            session_id="session-cto",
            num_turns=1,
        )

        call_count = 0

        async def mock_run(**kwargs):
            nonlocal call_count
            call_count += 1
            return mock_result

        with patch.object(cto, "run", side_effect=mock_run):
            with patch.object(cto, "save_handoff_artifact", return_value=None):
                result = await company.execute_roadmap()

        assert "complete" in result.lower()
        assert company.active_roadmap is None  # cleared after completion
        assert call_count == 2

    @pytest.mark.asyncio
    async def test_execute_not_approved(self, config: Config):
        company = Company(config=config)
        roadmap = Roadmap(
            tasks=[RoadmapTask(index=1, description="Task")],
            original_request="Do it",
            approved=False,
        )
        company.active_roadmap = roadmap

        result = await company.execute_roadmap()
        assert "not yet approved" in result.lower()

    @pytest.mark.asyncio
    async def test_execute_no_roadmap(self, config: Config):
        company = Company(config=config)
        result = await company.execute_roadmap()
        assert "No active roadmap" in result

    @pytest.mark.asyncio
    async def test_execute_pauses_on_failure(self, config: Config):
        """Roadmap pauses when a task fails."""
        company = Company(config=config)
        cto = company.ensure_cto()

        roadmap = Roadmap(
            tasks=[
                RoadmapTask(index=1, description="Task one"),
                RoadmapTask(index=2, description="Task two"),
            ],
            original_request="Build it",
            approved=True,
        )
        company.active_roadmap = roadmap

        async def mock_run(**kwargs):
            raise RuntimeError("Something broke")

        with patch.object(cto, "run", side_effect=mock_run):
            result = await company.execute_roadmap()

        assert company.active_roadmap is not None
        assert company.active_roadmap.paused is True
        assert company.active_roadmap.tasks[0].status == RoadmapTaskStatus.FAILED
        assert "failed" in result.lower()

    @pytest.mark.asyncio
    async def test_execute_budget_exceeded(self, config: Config):
        """Roadmap pauses when budget is exceeded."""
        from dataclasses import replace
        budget_config = replace(config, budget_limit_usd=0.01)
        company = Company(config=budget_config)
        cto = company.ensure_cto()
        cto.cost_total_usd = 1.0  # Already over budget

        roadmap = Roadmap(
            tasks=[RoadmapTask(index=1, description="Task one")],
            original_request="Build it",
            approved=True,
        )
        company.active_roadmap = roadmap

        result = await company.execute_roadmap()

        assert company.active_roadmap.paused is True
        assert "budget" in result.lower()

    @pytest.mark.asyncio
    async def test_execute_resumes_from_middle(self, config: Config):
        """Roadmap resumes from where it left off."""
        company = Company(config=config)
        cto = company.ensure_cto()

        roadmap = Roadmap(
            tasks=[
                RoadmapTask(
                    index=1,
                    description="Task one",
                    status=RoadmapTaskStatus.DONE,
                    output_summary="Done",
                    handoff_artifact="Prior context",
                ),
                RoadmapTask(index=2, description="Task two"),
                RoadmapTask(index=3, description="Task three"),
            ],
            original_request="Build it",
            approved=True,
        )
        company.active_roadmap = roadmap

        call_count = 0

        async def mock_run(**kwargs):
            nonlocal call_count
            call_count += 1
            return MemberResult(output="Done.", session_id="s", num_turns=1)

        with patch.object(cto, "run", side_effect=mock_run):
            with patch.object(cto, "save_handoff_artifact", return_value=None):
                result = await company.execute_roadmap()

        # Only tasks 2 and 3 should have been executed
        assert call_count == 2
        assert "complete" in result.lower()

    @pytest.mark.asyncio
    async def test_progress_callback(self, config: Config):
        """on_progress is called during execution."""
        company = Company(config=config)
        cto = company.ensure_cto()

        roadmap = Roadmap(
            tasks=[RoadmapTask(index=1, description="Task one")],
            original_request="Build it",
            approved=True,
        )
        company.active_roadmap = roadmap

        progress_messages: list[str] = []

        mock_result = MemberResult(output="Done.", session_id="s", num_turns=1)

        with patch.object(cto, "run", new_callable=AsyncMock, return_value=mock_result):
            with patch.object(cto, "save_handoff_artifact", return_value=None):
                await company.execute_roadmap(
                    on_progress=lambda msg: progress_messages.append(msg),
                )

        assert len(progress_messages) > 0
        assert any("Task one" in m for m in progress_messages)

    @pytest.mark.asyncio
    async def test_roadmap_task_complete_callback(self, config: Config):
        """on_roadmap_task_complete is called after each task."""
        company = Company(config=config)
        cto = company.ensure_cto()

        roadmap = Roadmap(
            tasks=[
                RoadmapTask(index=1, description="Task one"),
                RoadmapTask(index=2, description="Task two"),
            ],
            original_request="Build it",
            approved=True,
        )
        company.active_roadmap = roadmap

        completions: list[tuple[int, int, str]] = []

        mock_result = MemberResult(output="Done.", session_id="s", num_turns=1)

        with patch.object(cto, "run", new_callable=AsyncMock, return_value=mock_result):
            with patch.object(cto, "save_handoff_artifact", return_value=None):
                await company.execute_roadmap(
                    on_roadmap_task_complete=lambda idx, total, desc: completions.append(
                        (idx, total, desc)
                    ),
                )

        assert len(completions) == 2
        assert completions[0] == (1, 2, "Task one")
        assert completions[1] == (2, 2, "Task two")

    @pytest.mark.asyncio
    async def test_hires_during_roadmap(self, config: Config):
        """CTO can hire during roadmap execution."""
        company = Company(config=config)
        cto = company.ensure_cto()

        roadmap = Roadmap(
            tasks=[RoadmapTask(index=1, description="Build API")],
            original_request="Build it",
            approved=True,
        )
        company.active_roadmap = roadmap

        cto_response = (
            "I'll hire a backend dev for this.\n"
            "[HIRE:backend-dev:Alex]\n"
            "[DELEGATE:Alex]\n"
            "Build the API endpoints.\n"
            "[/DELEGATE]"
        )
        mock_cto_result = MemberResult(
            output=cto_response, session_id="s", num_turns=1,
        )
        mock_emp_result = MemberResult(
            output="API built.", session_id="s2", num_turns=2,
        )

        call_count = 0

        async def mock_run(**kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return mock_cto_result
            # Second call is employee work (delegation), third is review
            return mock_emp_result

        with patch.object(Employee, "run", side_effect=mock_run):
            with patch.object(cto, "save_handoff_artifact", return_value=None):
                result = await company.execute_roadmap()

        # Alex should have been hired
        assert "Alex" in company.employees


# ---------------------------------------------------------------------------
# Router — roadmap commands
# ---------------------------------------------------------------------------


class TestRouterRoadmap:
    """Test router handling of roadmap commands."""

    def _make_router(self, config: Config):
        from shipwright.conversation.router import Router
        from shipwright.conversation.session import Session

        session = Session(id="test")
        return Router(config=config, session=session)

    @pytest.mark.asyncio
    async def test_roadmap_status_no_roadmap(self, config: Config):
        router = self._make_router(config)
        response = await router.handle_message("roadmap")
        assert "No active roadmap" in response

    @pytest.mark.asyncio
    async def test_roadmap_status_with_roadmap(self, config: Config):
        router = self._make_router(config)
        router.company.ensure_cto()
        router.company.active_roadmap = Roadmap(
            tasks=[
                RoadmapTask(index=1, description="Task A", status=RoadmapTaskStatus.DONE),
                RoadmapTask(index=2, description="Task B"),
            ],
            original_request="Build it",
        )
        response = await router.handle_message("roadmap")
        assert "Task A" in response
        assert "Task B" in response
        assert "[x]" in response

    @pytest.mark.asyncio
    async def test_go_no_roadmap_falls_through(self, config: Config):
        """'go' with no roadmap falls through to conversational."""
        router = self._make_router(config)
        cto = router.company.ensure_cto()

        mock_result = MemberResult(
            output="I'm not sure what you want me to start.",
            session_id="s",
            num_turns=1,
        )
        with patch.object(cto, "run", new_callable=AsyncMock, return_value=mock_result):
            response = await router.handle_message("go")
        # Should fall through to CTO chat since no roadmap
        assert response  # some response was given

    @pytest.mark.asyncio
    async def test_approve_starts_roadmap(self, config: Config):
        router = self._make_router(config)
        cto = router.company.ensure_cto()

        router.company.active_roadmap = Roadmap(
            tasks=[RoadmapTask(index=1, description="Do work")],
            original_request="Build it",
            approved=False,
        )

        mock_result = MemberResult(output="Done.", session_id="s", num_turns=1)
        with patch.object(cto, "run", new_callable=AsyncMock, return_value=mock_result):
            with patch.object(cto, "save_handoff_artifact", return_value=None):
                response = await router.handle_message("approve")

        assert "complete" in response.lower()

    @pytest.mark.asyncio
    async def test_continue_resumes_paused(self, config: Config):
        router = self._make_router(config)
        cto = router.company.ensure_cto()

        from shipwright.company.employee import RoadmapState
        router.company.active_roadmap = Roadmap(
            tasks=[
                RoadmapTask(index=1, description="Task A", status=RoadmapTaskStatus.FAILED),
            ],
            original_request="Build it",
            approved=True,
            paused=True,
            state=RoadmapState.PAUSED,
        )

        mock_result = MemberResult(output="Fixed.", session_id="s", num_turns=1)
        with patch.object(cto, "run", new_callable=AsyncMock, return_value=mock_result):
            with patch.object(cto, "save_handoff_artifact", return_value=None):
                response = await router.handle_message("continue")

        assert "complete" in response.lower()
        # Failed task should have been retried
        assert router.company.active_roadmap is None

    @pytest.mark.asyncio
    async def test_continue_no_paused_roadmap(self, config: Config):
        """'continue' with no paused roadmap falls through."""
        router = self._make_router(config)
        cto = router.company.ensure_cto()

        mock_result = MemberResult(
            output="Nothing to continue.",
            session_id="s",
            num_turns=1,
        )
        with patch.object(cto, "run", new_callable=AsyncMock, return_value=mock_result):
            response = await router.handle_message("continue")
        assert response  # falls through to conversational
