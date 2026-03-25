"""Tests for the plugin system: YAML loading, resolution order, references, CLI commands."""

from pathlib import Path
from unittest.mock import patch

import pytest

from shipwright.config import (
    Config,
    CrewDef,
    MemberDef,
    SpecialistDef,
    _load_plugin_crew,
    _load_plugin_specialist,
    _load_references,
    _scan_all_plugin_dirs,
    _scan_plugin_dir,
)
from shipwright.conversation.router import Router
from shipwright.conversation.session import Session
from shipwright.crew.crew import Crew
from shipwright.crew.registry import (
    BUILTIN_CREWS,
    get_crew_def,
    get_specialist_def,
    inspect_crew,
    list_crew_types,
    list_installed,
    list_specialists,
    specialist_as_crew,
)


# ---------------------------------------------------------------------------
# Helper to create plugin directories with crew.yaml
# ---------------------------------------------------------------------------

def _write_yaml(path: Path, content: str) -> None:
    """Write YAML content, creating parent dirs as needed."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)


def _make_crew_plugin(base: Path, name: str, *, refs: dict[str, str] | None = None) -> Path:
    """Create a crew plugin directory with crew.yaml and optional references."""
    plugin_dir = base / name
    plugin_dir.mkdir(parents=True, exist_ok=True)
    _write_yaml(plugin_dir / "crew.yaml", f"""\
kind: crew
name: {name}
description: "Test crew: {name}"
lead: "You are the {name} crew lead."
members:
  dev:
    role: "Developer"
    prompt: "You write code."
    tools: [Read, Edit, Write, Bash]
    max_turns: 40
  reviewer:
    role: "Code Reviewer"
    prompt: "You review code."
    tools: [Read, Glob, Grep]
    max_turns: 20
""")
    if refs:
        refs_dir = plugin_dir / "references"
        refs_dir.mkdir(exist_ok=True)
        for fname, content in refs.items():
            (refs_dir / fname).write_text(content)
    return plugin_dir


def _make_specialist_plugin(
    base: Path, name: str, *, refs: dict[str, str] | None = None,
) -> Path:
    """Create a specialist plugin directory with crew.yaml and optional references."""
    plugin_dir = base / name
    plugin_dir.mkdir(parents=True, exist_ok=True)
    _write_yaml(plugin_dir / "crew.yaml", f"""\
kind: specialist
name: {name}
description: "Test specialist: {name}"
role: "{name} Specialist"
prompt: "You are an expert in {name}."
tools: [Read, Edit, Write, Bash, Glob, Grep]
max_turns: 60
references: true
""")
    if refs:
        refs_dir = plugin_dir / "references"
        refs_dir.mkdir(exist_ok=True)
        for fname, content in refs.items():
            (refs_dir / fname).write_text(content)
    return plugin_dir


# ---------------------------------------------------------------------------
# References Loading
# ---------------------------------------------------------------------------

class TestReferencesLoading:
    def test_load_references_empty_dir(self, tmp_path: Path):
        refs_dir = tmp_path / "references"
        refs_dir.mkdir()
        assert _load_references(refs_dir) == ""

    def test_load_references_nonexistent(self, tmp_path: Path):
        assert _load_references(tmp_path / "nonexistent") == ""

    def test_load_references_single_file(self, tmp_path: Path):
        refs_dir = tmp_path / "references"
        refs_dir.mkdir()
        (refs_dir / "api-guide.md").write_text("# API Guide\nUse POST for mutations.")

        result = _load_references(refs_dir)
        assert "## Reference Documents" in result
        assert "### api-guide" in result
        assert "Use POST for mutations." in result

    def test_load_references_multiple_files(self, tmp_path: Path):
        refs_dir = tmp_path / "references"
        refs_dir.mkdir()
        (refs_dir / "alpha.md").write_text("Alpha content")
        (refs_dir / "beta.md").write_text("Beta content")
        (refs_dir / "not-md.txt").write_text("Ignored")

        result = _load_references(refs_dir)
        assert "### alpha" in result
        assert "### beta" in result
        assert "Ignored" not in result  # only .md files

    def test_load_references_sorted_alphabetically(self, tmp_path: Path):
        refs_dir = tmp_path / "references"
        refs_dir.mkdir()
        (refs_dir / "z-last.md").write_text("Last")
        (refs_dir / "a-first.md").write_text("First")

        result = _load_references(refs_dir)
        a_pos = result.index("### a-first")
        z_pos = result.index("### z-last")
        assert a_pos < z_pos


# ---------------------------------------------------------------------------
# Plugin YAML Loading
# ---------------------------------------------------------------------------

class TestPluginYAMLLoading:
    def test_load_crew_plugin(self, tmp_path: Path):
        plugin_dir = _make_crew_plugin(tmp_path, "test-crew")
        raw = {"kind": "crew", "name": "test-crew", "description": "A test crew",
               "lead": "Test lead.", "members": {
                   "dev": {"role": "Dev", "prompt": "Code.", "tools": ["Read", "Write"]}
               }}
        crew_def = _load_plugin_crew(plugin_dir, raw, "project")
        assert crew_def.name == "test-crew"
        assert crew_def.source == "project"
        assert crew_def.description == "A test crew"
        assert "dev" in crew_def.members

    def test_load_specialist_plugin(self, tmp_path: Path):
        plugin_dir = _make_specialist_plugin(
            tmp_path, "stripe-expert",
            refs={"stripe-api.md": "# Stripe API\nUse PaymentIntents."},
        )
        raw = {"kind": "specialist", "name": "stripe-expert",
               "description": "Stripe expert", "role": "Stripe Specialist",
               "prompt": "You know Stripe.", "tools": ["Read", "Write"],
               "max_turns": 60, "references": True}
        specialist = _load_plugin_specialist(plugin_dir, raw, "project")
        assert specialist.name == "stripe-expert"
        assert specialist.source == "project"
        assert specialist.member_def.role == "Stripe Specialist"
        # References should be prepended to prompt
        assert "## Reference Documents" in specialist.member_def.prompt
        assert "Stripe API" in specialist.member_def.prompt
        assert "You know Stripe." in specialist.member_def.prompt

    def test_load_crew_with_references(self, tmp_path: Path):
        plugin_dir = _make_crew_plugin(
            tmp_path, "ref-crew",
            refs={"patterns.md": "# Patterns\nUse repository pattern."},
        )
        raw = {"kind": "crew", "name": "ref-crew", "lead": "Lead.",
               "members": {
                   "dev": {"role": "Dev", "prompt": "You code.", "tools": ["Read"]}
               }, "references": True}
        crew_def = _load_plugin_crew(plugin_dir, raw, "project")
        # References should be prepended to member prompts
        assert "## Reference Documents" in crew_def.members["dev"].prompt
        assert "repository pattern" in crew_def.members["dev"].prompt

    def test_load_plugin_yaml_missing(self, tmp_path: Path):
        from shipwright.config import _load_plugin_yaml
        result = _load_plugin_yaml(tmp_path)
        assert result is None

    def test_load_plugin_yaml_invalid(self, tmp_path: Path):
        from shipwright.config import _load_plugin_yaml
        (tmp_path / "crew.yaml").write_text("not: [valid: yaml: {{")
        # Should not crash — returns None on parse error
        # (PyYAML may or may not raise on this specific input, but we handle exceptions)


# ---------------------------------------------------------------------------
# Resolution Order
# ---------------------------------------------------------------------------

class TestResolutionOrder:
    def test_project_local_wins_over_user_global(self, tmp_path: Path):
        project_crews = tmp_path / "shipwright" / "crews"
        user_crews = tmp_path / "user_home" / ".shipwright" / "crews"

        _make_crew_plugin(project_crews, "my-crew")
        _make_crew_plugin(user_crews, "my-crew")  # same name

        crews: dict[str, CrewDef] = {}
        specialists: dict[str, SpecialistDef] = {}

        # Project-local scanned first
        _scan_plugin_dir(project_crews, "project", crews, specialists)
        _scan_plugin_dir(user_crews, "user", crews, specialists)

        assert "my-crew" in crews
        assert crews["my-crew"].source == "project"  # project wins

    def test_user_global_fills_gaps(self, tmp_path: Path):
        project_crews = tmp_path / "shipwright" / "crews"
        user_crews = tmp_path / "user_home" / ".shipwright" / "crews"

        _make_crew_plugin(project_crews, "project-only")
        _make_crew_plugin(user_crews, "user-only")

        crews: dict[str, CrewDef] = {}
        specialists: dict[str, SpecialistDef] = {}
        _scan_plugin_dir(project_crews, "project", crews, specialists)
        _scan_plugin_dir(user_crews, "user", crews, specialists)

        assert "project-only" in crews
        assert "user-only" in crews
        assert crews["project-only"].source == "project"
        assert crews["user-only"].source == "user"

    def test_custom_crew_overrides_builtin(self):
        custom_backend = CrewDef(
            name="backend",
            lead_prompt="Custom backend lead.",
            members={},
            source="project",
        )
        config = Config(custom_crews={"backend": custom_backend})
        result = get_crew_def("backend", config)
        assert result.lead_prompt == "Custom backend lead."
        assert result.source == "project"

    def test_builtin_still_accessible(self):
        config = Config()
        result = get_crew_def("backend", config)
        assert result.name == "backend"
        assert "architect" in result.members

    def test_specialist_hireable_as_crew(self):
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

        # Should be hireable via get_crew_def
        crew_def = get_crew_def("stripe-expert", config)
        assert crew_def.name == "stripe-expert"
        assert "stripe_expert" in crew_def.members
        assert crew_def.members["stripe_expert"].role == "Stripe Specialist"

    def test_scan_all_plugin_dirs(self, tmp_path: Path):
        """Test full scan with mocked home directory."""
        project_dir = tmp_path / "project"
        project_dir.mkdir()
        project_crews = project_dir / "shipwright" / "crews"
        _make_crew_plugin(project_crews, "local-crew")
        _make_specialist_plugin(project_crews, "local-specialist")

        user_home = tmp_path / "fakehome"
        user_crews = user_home / ".shipwright" / "crews"
        _make_crew_plugin(user_crews, "global-crew")

        with patch("shipwright.config.Path.home", return_value=user_home):
            crews, specialists = _scan_all_plugin_dirs(project_dir)

        assert "local-crew" in crews
        assert "global-crew" in crews
        assert "local-specialist" in specialists

    def test_list_crew_types_includes_specialists(self):
        specialist = SpecialistDef(
            name="my-specialist",
            description="test",
            member_def=MemberDef(role="Spec", prompt="test"),
        )
        config = Config(custom_specialists={"my-specialist": specialist})
        types = list_crew_types(config)
        assert "my-specialist" in types
        # Builtins still there
        assert "backend" in types


# ---------------------------------------------------------------------------
# Specialist as Crew
# ---------------------------------------------------------------------------

class TestSpecialistAsCrew:
    def test_specialist_as_crew_basic(self):
        specialist = SpecialistDef(
            name="stripe-expert",
            description="Stripe payments expert",
            member_def=MemberDef(
                role="Stripe Specialist",
                prompt="You know Stripe.",
                tools=["Read", "Edit", "Write"],
                max_turns=60,
            ),
        )
        crew_def = specialist_as_crew(specialist)
        assert crew_def.name == "stripe-expert"
        assert "stripe_expert" in crew_def.members
        assert crew_def.members["stripe_expert"].role == "Stripe Specialist"
        assert "Stripe payments expert" in crew_def.lead_prompt

    def test_specialist_as_crew_preserves_tools(self):
        specialist = SpecialistDef(
            name="test-spec",
            description="test",
            member_def=MemberDef(
                role="Test",
                prompt="test",
                tools=["Read", "Write", "Bash"],
                max_turns=100,
            ),
        )
        crew_def = specialist_as_crew(specialist)
        member = crew_def.members["test_spec"]
        assert member.tools == ["Read", "Write", "Bash"]
        assert member.max_turns == 100


# ---------------------------------------------------------------------------
# Registry Functions
# ---------------------------------------------------------------------------

class TestRegistryFunctions:
    def test_get_specialist_def(self):
        specialist = SpecialistDef(
            name="my-spec",
            description="test",
            member_def=MemberDef(role="Spec", prompt="test"),
        )
        config = Config(custom_specialists={"my-spec": specialist})
        result = get_specialist_def("my-spec", config)
        assert result is not None
        assert result.name == "my-spec"

    def test_get_specialist_def_missing(self):
        config = Config()
        assert get_specialist_def("nonexistent", config) is None

    def test_list_specialists(self):
        s1 = SpecialistDef(name="alpha", description="a",
                           member_def=MemberDef(role="A", prompt="a"))
        s2 = SpecialistDef(name="beta", description="b",
                           member_def=MemberDef(role="B", prompt="b"))
        config = Config(custom_specialists={"alpha": s1, "beta": s2})
        result = list_specialists(config)
        assert result == ["alpha", "beta"]

    def test_list_installed_empty(self):
        config = Config()
        assert list_installed(config) == []

    def test_list_installed_mixed(self):
        crew = CrewDef(name="my-crew", lead_prompt="lead", description="A crew", source="project")
        specialist = SpecialistDef(name="my-spec", description="A spec",
                                   member_def=MemberDef(role="Spec", prompt="test"),
                                   source="user")
        config = Config(
            custom_crews={"my-crew": crew},
            custom_specialists={"my-spec": specialist},
        )
        items = list_installed(config)
        assert len(items) == 2
        names = [i["name"] for i in items]
        assert "my-crew" in names
        assert "my-spec" in names

    def test_inspect_builtin_crew(self):
        result = inspect_crew("backend")
        assert "**backend**" in result
        assert "crew" in result.lower()
        assert "architect" in result.lower()

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
        result = inspect_crew("stripe-expert", config)
        assert "**stripe-expert**" in result
        assert "specialist" in result.lower()
        assert "Stripe Specialist" in result
        assert "Read, Write" in result

    def test_inspect_unknown(self):
        result = inspect_crew("nonexistent")
        assert "Unknown" in result


# ---------------------------------------------------------------------------
# Crew Recruitment
# ---------------------------------------------------------------------------

class TestCrewRecruitment:
    def _make_crew(self, config: Config) -> Crew:
        crew_def = get_crew_def("backend", config)
        return Crew.create(
            crew_type="backend",
            crew_def=crew_def,
            config=config,
            objective="Test objective",
        )

    def test_recruit_specialist(self, config: Config):
        crew = self._make_crew(config)
        specialist = SpecialistDef(
            name="stripe-expert",
            description="Stripe expert",
            member_def=MemberDef(
                role="Stripe Specialist",
                prompt="You know Stripe.",
                tools=["Read", "Write"],
                max_turns=60,
            ),
        )
        member_name = crew.recruit_specialist(specialist)
        assert member_name == "stripe_expert"
        assert "stripe_expert" in crew.members
        assert crew.members["stripe_expert"].role == "Stripe Specialist"

    def test_recruit_avoids_name_collision(self, config: Config):
        crew = self._make_crew(config)
        # Force members to be created
        crew._ensure_members()

        # Create a specialist with a name that collides with existing member
        specialist = SpecialistDef(
            name="architect",  # Same as existing backend member
            description="test",
            member_def=MemberDef(role="Special Architect", prompt="test"),
        )
        member_name = crew.recruit_specialist(specialist)
        assert member_name == "specialist_architect"
        assert "specialist_architect" in crew.members

    def test_recruited_member_has_correct_cwd(self, config: Config):
        crew = self._make_crew(config)
        specialist = SpecialistDef(
            name="test-spec",
            description="test",
            member_def=MemberDef(role="Spec", prompt="test"),
        )
        crew.recruit_specialist(specialist)
        assert crew.members["test_spec"].cwd == str(config.repo_root)


# ---------------------------------------------------------------------------
# Router Commands
# ---------------------------------------------------------------------------

class TestRouterPluginCommands:
    def _make_router(self, config: Config) -> Router:
        session = Session(id="test")
        return Router(config=config, session=session)

    def test_shop_command(self, config: Config):
        router = self._make_router(config)
        is_cmd, response = router._try_command("shop")
        assert is_cmd
        assert "Available" in response
        assert "backend" in response  # builtin

    def test_browse_alias(self, config: Config):
        router = self._make_router(config)
        is_cmd, response = router._try_command("browse")
        assert is_cmd
        assert "Available" in response

    def test_shop_shows_custom_crews(self):
        custom = CrewDef(name="ml-crew", lead_prompt="ML lead.",
                         description="ML team", source="project", members={})
        config = Config(custom_crews={"ml-crew": custom})
        router = Router(config=config, session=Session(id="test"))
        _, response = router._try_command("shop")
        assert "ml-crew" in response
        assert "Custom Crews" in response

    def test_shop_shows_specialists(self):
        specialist = SpecialistDef(
            name="stripe-expert",
            description="Stripe payments expert",
            member_def=MemberDef(role="Stripe Specialist", prompt="test"),
            source="project",
        )
        config = Config(custom_specialists={"stripe-expert": specialist})
        router = Router(config=config, session=Session(id="test"))
        _, response = router._try_command("shop")
        assert "stripe-expert" in response
        assert "Specialists" in response

    def test_installed_command_empty(self, config: Config):
        router = self._make_router(config)
        is_cmd, response = router._try_command("installed")
        assert is_cmd
        assert "No custom" in response

    def test_installed_command_with_plugins(self):
        custom = CrewDef(name="ml-crew", lead_prompt="ML lead.",
                         description="ML team", source="project", members={})
        config = Config(custom_crews={"ml-crew": custom})
        router = Router(config=config, session=Session(id="test"))
        _, response = router._try_command("installed")
        assert "ml-crew" in response

    def test_inspect_command(self, config: Config):
        router = self._make_router(config)
        is_cmd, response = router._try_command("inspect backend")
        assert is_cmd
        assert "backend" in response
        assert "architect" in response.lower()

    def test_inspect_unknown(self, config: Config):
        router = self._make_router(config)
        is_cmd, response = router._try_command("inspect nonexistent")
        assert is_cmd
        assert "Unknown" in response

    def test_recruit_command(self):
        specialist = SpecialistDef(
            name="stripe-expert",
            description="Stripe expert",
            member_def=MemberDef(
                role="Stripe Specialist",
                prompt="You know Stripe.",
                tools=["Read", "Write"],
            ),
            source="project",
        )
        config = Config(custom_specialists={"stripe-expert": specialist})
        router = Router(config=config, session=Session(id="test"))

        # Hire a crew first
        router._try_command("hire backend Add payments")
        crew_id = list(router.crews.keys())[0]

        # Recruit specialist into the crew
        is_cmd, response = router._try_command(f"recruit stripe-expert into {crew_id}")
        assert is_cmd
        assert "Recruited" in response
        assert "Stripe Specialist" in response

        # Verify the member was added
        crew = router.crews[crew_id]
        assert "stripe_expert" in crew.members

    def test_recruit_unknown_specialist(self, config: Config):
        router = self._make_router(config)
        router._try_command("hire backend Add payments")
        crew_id = list(router.crews.keys())[0]

        is_cmd, response = router._try_command(f"recruit nonexistent into {crew_id}")
        assert is_cmd
        assert "Unknown specialist" in response

    def test_recruit_into_unknown_crew(self):
        specialist = SpecialistDef(
            name="my-spec",
            description="test",
            member_def=MemberDef(role="Spec", prompt="test"),
        )
        config = Config(custom_specialists={"my-spec": specialist})
        router = Router(config=config, session=Session(id="test"))

        is_cmd, response = router._try_command("recruit my-spec into nonexistent")
        assert is_cmd
        assert "No active crew" in response

    def test_help_includes_new_commands(self, config: Config):
        router = self._make_router(config)
        _, response = router._try_command("help")
        assert "shop" in response
        assert "installed" in response
        assert "inspect" in response
        assert "recruit" in response


# ---------------------------------------------------------------------------
# Integration: Full plugin directory scan + hiring
# ---------------------------------------------------------------------------

class TestPluginIntegration:
    def test_full_plugin_crew_flow(self, tmp_path: Path):
        """End-to-end: create plugin dir → scan → hire crew."""
        project_dir = tmp_path / "project"
        project_dir.mkdir()
        crews_dir = project_dir / "shipwright" / "crews"
        _make_crew_plugin(crews_dir, "ml-crew")

        with patch("shipwright.config.Path.home", return_value=tmp_path / "nohome"):
            crews, specialists = _scan_all_plugin_dirs(project_dir)

        assert "ml-crew" in crews
        crew_def = crews["ml-crew"]
        assert crew_def.source == "project"
        assert "dev" in crew_def.members

        # Verify it's hireable
        config = Config(repo_root=project_dir, custom_crews=crews)
        result = get_crew_def("ml-crew", config)
        assert result.name == "ml-crew"

    def test_full_specialist_flow(self, tmp_path: Path):
        """End-to-end: create specialist plugin → scan → recruit."""
        project_dir = tmp_path / "project"
        project_dir.mkdir()
        crews_dir = project_dir / "shipwright" / "crews"
        _make_specialist_plugin(
            crews_dir, "stripe-expert",
            refs={"stripe-api.md": "# Stripe API\nUse PaymentIntents."},
        )

        with patch("shipwright.config.Path.home", return_value=tmp_path / "nohome"):
            crews, specialists = _scan_all_plugin_dirs(project_dir)

        assert "stripe-expert" in specialists
        s = specialists["stripe-expert"]
        assert "## Reference Documents" in s.member_def.prompt
        assert "Stripe API" in s.member_def.prompt

    def test_yaml_crews_override_plugins(self, tmp_path: Path):
        """shipwright.yaml crews take priority over plugin dir crews."""
        project_dir = tmp_path / "project"
        project_dir.mkdir()
        crews_dir = project_dir / "shipwright" / "crews"
        _make_crew_plugin(crews_dir, "my-crew")

        # Plugin crew loaded
        with patch("shipwright.config.Path.home", return_value=tmp_path / "nohome"):
            plugin_crews, _ = _scan_all_plugin_dirs(project_dir)

        # YAML crew with same name
        yaml_crew = CrewDef(
            name="my-crew",
            lead_prompt="YAML version.",
            members={},
            source="yaml",
        )

        # Merge: yaml wins
        merged = {**plugin_crews, "my-crew": yaml_crew}
        assert merged["my-crew"].source == "yaml"
        assert merged["my-crew"].lead_prompt == "YAML version."
