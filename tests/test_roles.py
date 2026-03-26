"""Tests for the V2 roles module: builtin roles, resolution, and inspection."""

from pathlib import Path

import pytest

from shipwright.config import Config, CrewDef, MemberDef, SpecialistDef
from shipwright.company.roles import (
    BUILTIN_ROLES,
    ROLE_DISPLAY_NAMES,
    RoleDef,
    get_role_def,
    inspect_role,
    list_roles,
)


# ---------------------------------------------------------------------------
# Built-in roles
# ---------------------------------------------------------------------------


class TestBuiltinRoles:
    EXPECTED_ROLES = {
        "cto",
        "architect",
        "backend-dev",
        "frontend-dev",
        "fullstack-dev",
        "db-engineer",
        "qa-engineer",
        "devops-engineer",
        "security-auditor",
        "tech-writer",
        "designer",
        "team-lead",
        "evaluator",
    }

    def test_all_roles_exist(self):
        assert set(BUILTIN_ROLES.keys()) == self.EXPECTED_ROLES

    def test_get_role_def_works_for_each(self):
        for role_id in self.EXPECTED_ROLES:
            role_def = get_role_def(role_id)
            assert isinstance(role_def, MemberDef)
            assert role_def.role  # non-empty display name
            assert role_def.prompt  # non-empty prompt

    def test_unknown_role_raises_valueerror(self):
        with pytest.raises(ValueError, match="Unknown role"):
            get_role_def("nonexistent-role")

    def test_role_display_names_match_keys(self):
        for role_id in BUILTIN_ROLES:
            assert role_id in ROLE_DISPLAY_NAMES

    def test_role_def_alias_is_member_def(self):
        assert RoleDef is MemberDef


# ---------------------------------------------------------------------------
# Role resolution with custom roles
# ---------------------------------------------------------------------------


class TestRoleResolution:
    def test_custom_specialist_overrides_builtin(self):
        specialist = SpecialistDef(
            name="architect",  # same as builtin
            description="Custom architect",
            member_def=MemberDef(
                role="Custom Architect",
                prompt="You are a custom architect.",
                tools=["Read", "Write"],
                max_turns=30,
            ),
            source="project",
        )
        config = Config(custom_specialists={"architect": specialist})
        result = get_role_def("architect", config)
        assert result.role == "Custom Architect"

    def test_specialist_hireable_as_role(self):
        specialist = SpecialistDef(
            name="stripe-expert",
            description="Stripe payments expert",
            member_def=MemberDef(
                role="Stripe Specialist",
                prompt="You know Stripe.",
                tools=["Read", "Write"],
                max_turns=60,
            ),
            source="project",
        )
        config = Config(custom_specialists={"stripe-expert": specialist})
        result = get_role_def("stripe-expert", config)
        assert result.role == "Stripe Specialist"

    def test_custom_crew_treated_as_role(self):
        custom_crew = CrewDef(
            name="ml-crew",
            lead_prompt="You lead the ML team.",
            members={},
            source="project",
        )
        config = Config(custom_crews={"ml-crew": custom_crew})
        result = get_role_def("ml-crew", config)
        assert result.role == "ml-crew"
        assert "ML team" in result.prompt

    def test_list_roles_includes_custom(self):
        specialist = SpecialistDef(
            name="stripe-expert",
            description="test",
            member_def=MemberDef(role="Spec", prompt="test"),
        )
        custom_crew = CrewDef(
            name="ml-crew",
            lead_prompt="ML lead.",
            members={},
        )
        config = Config(
            custom_specialists={"stripe-expert": specialist},
            custom_crews={"ml-crew": custom_crew},
        )
        roles = list_roles(config)
        assert "stripe-expert" in roles
        assert "ml-crew" in roles
        # Builtins still present
        assert "architect" in roles
        assert "backend-dev" in roles

    def test_list_roles_sorted(self):
        roles = list_roles()
        assert roles == sorted(roles)

    def test_builtin_accessible_without_config(self):
        result = get_role_def("backend-dev")
        assert result.role == "Backend Developer"


# ---------------------------------------------------------------------------
# Inspect role
# ---------------------------------------------------------------------------


class TestInspectRole:
    def test_inspect_builtin_role(self):
        result = inspect_role("backend-dev")
        assert "Backend Developer" in result
        assert "builtin role" in result
        assert "backend-dev" in result

    def test_inspect_specialist(self):
        specialist = SpecialistDef(
            name="stripe-expert",
            description="Stripe payments expert",
            member_def=MemberDef(
                role="Stripe Specialist",
                prompt="You know Stripe.",
                tools=["Read", "Write"],
                max_turns=60,
            ),
            source="project",
        )
        config = Config(custom_specialists={"stripe-expert": specialist})
        result = inspect_role("stripe-expert", config)
        assert "stripe-expert" in result
        assert "specialist" in result
        assert "Stripe Specialist" in result
        assert "Read, Write" in result

    def test_inspect_unknown_role(self):
        result = inspect_role("nonexistent-role")
        assert "Unknown role" in result
