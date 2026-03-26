"""Tests for the V2 Employee module: Employee, Task, delegation parsing, name pool, CTO parsing."""

import time

import pytest

from shipwright.config import MemberDef
from shipwright.company.employee import (
    DelegationRequest,
    Employee,
    EmployeeStatus,
    HireRequest,
    NAME_POOL,
    ReviseRequest,
    Task,
    next_name,
    parse_delegations,
    parse_hire_blocks,
    parse_revise_blocks,
)


# ---------------------------------------------------------------------------
# Employee
# ---------------------------------------------------------------------------


class TestEmployee:
    def _make_employee(self, **overrides) -> Employee:
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
            cwd="/tmp",
            model="claude-sonnet-4-6",
            permission_mode="bypassPermissions",
        )
        defaults.update(overrides)
        return Employee(**defaults)

    def test_properties(self):
        emp = self._make_employee()
        assert emp.name == "Alex"
        assert emp.role == "backend-dev"
        assert emp.display_role == "Backend Developer"
        assert emp.status == EmployeeStatus.IDLE
        assert emp.session_id is None
        assert emp.is_lead is False
        assert emp.team is None
        assert emp.cost_total_usd == 0.0

    def test_display_role_when_lead(self):
        emp = self._make_employee(is_lead=True, team="backend")
        assert "Team Lead" in emp.display_role
        assert "Backend Developer" in emp.display_role

    def test_display_role_not_lead(self):
        emp = self._make_employee(is_lead=False)
        assert emp.display_role == "Backend Developer"

    def test_serialization_round_trip(self):
        emp = self._make_employee()
        emp._session_id = "session-abc"
        emp.cost_total_usd = 0.1234
        emp.is_lead = True
        emp.team = "backend"
        emp._conversation.append({"role": "user", "text": "hello"})
        emp._conversation.append({"role": "employee", "text": "hi there"})
        emp.task_history.append(Task(
            id="t1",
            description="Write API",
            assigned_to="Alex",
            status="done",
            output="Endpoint created",
            cost_usd=0.05,
        ))

        data = emp.to_dict()
        assert data["id"] == "alex-backend-dev"
        assert data["name"] == "Alex"
        assert data["role"] == "backend-dev"
        assert data["session_id"] == "session-abc"
        assert data["cost_total_usd"] == 0.1234
        assert data["is_lead"] is True
        assert data["team"] == "backend"
        assert len(data["conversation"]) == 2
        assert len(data["task_history"]) == 1

        role_def = MemberDef(
            role="Backend Developer",
            prompt="You write backend code.",
            tools=["Read", "Edit", "Write", "Bash"],
            max_turns=80,
        )
        restored = Employee.from_dict(
            data, role_def, "/tmp", "claude-sonnet-4-6", "bypassPermissions",
        )
        assert restored.id == "alex-backend-dev"
        assert restored.name == "Alex"
        assert restored.role == "backend-dev"
        assert restored._session_id == "session-abc"
        assert restored.cost_total_usd == 0.1234
        assert restored.is_lead is True
        assert restored.team == "backend"
        assert len(restored._conversation) == 2
        assert len(restored.task_history) == 1
        assert restored.task_history[0].description == "Write API"

    def test_reset_session(self):
        emp = self._make_employee()
        emp._session_id = "session-123"
        emp._conversation.append({"role": "user", "text": "hello"})

        emp.reset_session()
        assert emp._session_id is None
        assert emp._conversation == []

    def test_conversation_history_returns_copy(self):
        emp = self._make_employee()
        emp._conversation.append({"role": "user", "text": "hello"})
        history = emp.conversation_history
        assert len(history) == 1
        # Modifying the returned list shouldn't affect internal state
        history.clear()
        assert len(emp._conversation) == 1


# ---------------------------------------------------------------------------
# Task
# ---------------------------------------------------------------------------


class TestTask:
    def test_to_dict_from_dict_round_trip(self):
        task = Task(
            id="t1",
            description="Build the checkout form",
            assigned_to="Alex",
            status="done",
            output="Form built successfully",
            cost_usd=0.05,
            duration_ms=12000,
            created_at=1000.0,
            completed_at=1012.0,
        )

        data = task.to_dict()
        assert data["id"] == "t1"
        assert data["description"] == "Build the checkout form"
        assert data["assigned_to"] == "Alex"
        assert data["status"] == "done"
        assert data["output"] == "Form built successfully"
        assert data["cost_usd"] == 0.05
        assert data["duration_ms"] == 12000

        restored = Task.from_dict(data)
        assert restored.id == "t1"
        assert restored.description == "Build the checkout form"
        assert restored.assigned_to == "Alex"
        assert restored.status == "done"
        assert restored.output == "Form built successfully"
        assert restored.cost_usd == 0.05
        assert restored.duration_ms == 12000
        assert restored.created_at == 1000.0
        assert restored.completed_at == 1012.0

    def test_output_truncated_in_serialization(self):
        long_output = "x" * 5000
        task = Task(
            id="t2",
            description="Long output task",
            assigned_to="Blake",
            output=long_output,
        )
        data = task.to_dict()
        assert len(data["output"]) == 2000

    def test_defaults(self):
        task = Task(id="t3", description="Test", assigned_to="Alex")
        assert task.status == "pending"
        assert task.output == ""
        assert task.cost_usd == 0.0
        assert task.duration_ms == 0
        assert task.completed_at is None
        assert task.created_at > 0


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
# Name Pool
# ---------------------------------------------------------------------------


class TestNamePool:
    def test_next_name_returns_first_available(self):
        name = next_name(set())
        assert name == NAME_POOL[0]  # "Alex"

    def test_next_name_skips_used(self):
        used = {"Alex", "Blake"}
        name = next_name(used)
        assert name == "Casey"

    def test_cycles_through_pool(self):
        used = set(NAME_POOL)
        name = next_name(used)
        # Should start appending numbers
        assert any(name.startswith(n) for n in NAME_POOL)
        assert "2" in name

    def test_handles_all_used_case(self):
        # Exhaust all names from the pool — should still return something
        used = set()
        for _ in range(len(NAME_POOL) + 5):
            name = next_name(used)
            assert name not in used
            used.add(name)

    def test_pool_is_nonempty(self):
        assert len(NAME_POOL) > 0


# ---------------------------------------------------------------------------
# EmployeeStatus
# ---------------------------------------------------------------------------


class TestEmployeeStatus:
    def test_enum_values(self):
        assert EmployeeStatus.IDLE.value == "idle"
        assert EmployeeStatus.WORKING.value == "working"
        assert EmployeeStatus.BLOCKED.value == "blocked"

    def test_string_comparison(self):
        assert EmployeeStatus.IDLE == "idle"
        assert EmployeeStatus.WORKING == "working"


# ---------------------------------------------------------------------------
# CTO Hire Block Parsing
# ---------------------------------------------------------------------------


class TestHireParsing:
    def test_no_hires(self):
        text = "I'll analyze this and get back to you."
        clean, hires = parse_hire_blocks(text)
        assert clean == text
        assert hires == []

    def test_single_hire(self):
        text = "We need a backend developer. [HIRE:backend-dev]"
        clean, hires = parse_hire_blocks(text)
        assert "[HIRE" not in clean
        assert "backend developer" in clean
        assert len(hires) == 1
        assert hires[0].role == "backend-dev"
        assert hires[0].name is None

    def test_hire_with_name(self):
        text = "Let me get someone on this. [HIRE:frontend-dev:Kai]"
        clean, hires = parse_hire_blocks(text)
        assert "[HIRE" not in clean
        assert len(hires) == 1
        assert hires[0].role == "frontend-dev"
        assert hires[0].name == "Kai"

    def test_multiple_hires(self):
        text = (
            "I'll need two people for this.\n"
            "[HIRE:backend-dev]\n"
            "[HIRE:frontend-dev:Sage]"
        )
        clean, hires = parse_hire_blocks(text)
        assert len(hires) == 2
        assert hires[0].role == "backend-dev"
        assert hires[0].name is None
        assert hires[1].role == "frontend-dev"
        assert hires[1].name == "Sage"

    def test_hire_block_stripped_from_clean_text(self):
        text = "Before [HIRE:architect] After"
        clean, hires = parse_hire_blocks(text)
        assert "Before" in clean
        assert "After" in clean
        assert "[HIRE" not in clean
        assert len(hires) == 1

    def test_hire_with_hyphenated_role(self):
        text = "[HIRE:qa-engineer]"
        _, hires = parse_hire_blocks(text)
        assert len(hires) == 1
        assert hires[0].role == "qa-engineer"


# ---------------------------------------------------------------------------
# CTO Revise Block Parsing
# ---------------------------------------------------------------------------


class TestReviseParsing:
    def test_no_revisions(self):
        text = "This looks great. Ship it!"
        clean, revisions = parse_revise_blocks(text)
        assert clean == text
        assert revisions == []

    def test_single_revision(self):
        text = (
            "The API needs work.\n\n"
            "[REVISE:Alex]\n"
            "The error handling is missing. Add try/catch around the DB calls.\n"
            "[/REVISE]"
        )
        clean, revisions = parse_revise_blocks(text)
        assert "API needs work" in clean
        assert "[REVISE" not in clean
        assert len(revisions) == 1
        assert revisions[0].employee_name == "Alex"
        assert "error handling" in revisions[0].feedback

    def test_multiple_revisions(self):
        text = (
            "Two things need fixing.\n\n"
            "[REVISE:Alex]\n"
            "Add input validation.\n"
            "[/REVISE]\n\n"
            "[REVISE:Blake]\n"
            "Fix the CSS alignment.\n"
            "[/REVISE]"
        )
        clean, revisions = parse_revise_blocks(text)
        assert "Two things" in clean
        assert len(revisions) == 2
        assert revisions[0].employee_name == "Alex"
        assert revisions[1].employee_name == "Blake"
        assert "validation" in revisions[0].feedback
        assert "CSS" in revisions[1].feedback

    def test_empty_feedback_ignored(self):
        text = "[REVISE:Alex]\n\n[/REVISE]"
        _, revisions = parse_revise_blocks(text)
        assert revisions == []

    def test_revise_block_stripped_from_clean_text(self):
        text = (
            "Before.\n\n"
            "[REVISE:Alex]\nFix this.\n[/REVISE]\n\n"
            "After."
        )
        clean, revisions = parse_revise_blocks(text)
        assert "Before." in clean
        assert "After." in clean
        assert "[REVISE" not in clean
        assert len(revisions) == 1

    def test_multiline_feedback(self):
        text = (
            "[REVISE:Blake]\n"
            "1. Fix the error handling.\n"
            "2. Add input validation.\n"
            "3. Write tests for edge cases.\n"
            "[/REVISE]"
        )
        _, revisions = parse_revise_blocks(text)
        assert len(revisions) == 1
        assert "1. Fix" in revisions[0].feedback
        assert "3. Write" in revisions[0].feedback
