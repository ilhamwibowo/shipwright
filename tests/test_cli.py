"""Tests for CLI rendering and output utilities."""

import time

from shipwright.interfaces.cli import (
    BOLD,
    CYAN,
    DIM,
    GREEN,
    RED,
    RESET,
    YELLOW,
    CLIOutput,
    Spinner,
    render_markdown,
)


# ---------------------------------------------------------------------------
# Markdown rendering
# ---------------------------------------------------------------------------

class TestRenderMarkdown:
    def test_plain_text_unchanged(self):
        text = "Hello world"
        result = render_markdown(text)
        assert "Hello world" in result

    def test_bold(self):
        result = render_markdown("This is **bold** text")
        assert BOLD in result
        assert "bold" in result
        assert RESET in result

    def test_h1(self):
        result = render_markdown("# Title")
        assert BOLD in result
        assert CYAN in result
        assert "Title" in result

    def test_h2(self):
        result = render_markdown("## Subtitle")
        assert BOLD in result
        assert CYAN in result
        assert "Subtitle" in result

    def test_h3(self):
        result = render_markdown("### Section")
        assert BOLD in result
        assert "Section" in result

    def test_inline_code(self):
        result = render_markdown("Use `pip install` here")
        assert DIM in result
        assert "pip install" in result

    def test_code_block(self):
        text = "Before\n```python\nprint('hi')\n```\nAfter"
        result = render_markdown(text)
        assert "Before" in result
        assert "After" in result
        assert "print('hi')" in result

    def test_horizontal_rule(self):
        result = render_markdown("Above\n---\nBelow")
        assert "Above" in result
        assert "Below" in result

    def test_mixed_formatting(self):
        text = "# Header\n\nSome **bold** and `code` text.\n\n---"
        result = render_markdown(text)
        assert "Header" in result
        assert "bold" in result
        assert "code" in result

    def test_multiline_code_block(self):
        text = "```\nline 1\nline 2\nline 3\n```"
        result = render_markdown(text)
        assert "line 1" in result
        assert "line 2" in result
        assert "line 3" in result

    def test_empty_string(self):
        assert render_markdown("") == ""

    def test_blockquote(self):
        result = render_markdown("> keep going")
        assert "keep going" in result
        assert "▌" in result

    def test_bullet_list(self):
        result = render_markdown("- first item")
        assert "first item" in result
        assert "•" in result

    def test_ordered_list(self):
        result = render_markdown("1. first item")
        assert "first item" in result
        assert "1." in result


# ---------------------------------------------------------------------------
# CLIOutput
# ---------------------------------------------------------------------------

class TestCLIOutput:
    def test_streamed_flag_initially_false(self):
        ui = CLIOutput()
        assert ui.streamed is False

    def test_on_text_sets_streamed(self, capsys):
        ui = CLIOutput()
        ui.on_text("hello")
        assert ui.streamed is True

    def test_on_text_prints_cyan(self, capsys):
        ui = CLIOutput()
        ui.on_text("hello")
        captured = capsys.readouterr()
        assert CYAN in captured.out
        assert "hello" in captured.out

    def test_on_text_prints_speaker_label(self, capsys):
        ui = CLIOutput()
        ui.start_thinking("CTO", "cto")
        ui.on_text("hello")
        captured = capsys.readouterr()
        assert "[CTO]" in captured.out

    def test_finish_response_resets(self, capsys):
        ui = CLIOutput()
        ui.on_text("hello")
        ui.finish_response()
        assert ui.spinner.active is False

    def test_start_thinking_resets_streamed(self):
        ui = CLIOutput()
        ui._got_text = True
        ui.start_thinking()
        assert ui._got_text is False

    def test_start_thinking_records_time(self):
        ui = CLIOutput()
        ui.start_thinking()
        assert ui._start_time > 0.0

    def test_elapsed_after_start(self):
        ui = CLIOutput()
        ui.start_thinking()
        time.sleep(0.05)
        assert ui.elapsed >= 0.04

    def test_elapsed_zero_before_start(self):
        ui = CLIOutput()
        assert ui.elapsed == 0.0

    def test_on_delegation_start_output(self, capsys):
        ui = CLIOutput()
        ui.on_delegation_start("architect", "Explore the codebase", 1, 5)
        captured = capsys.readouterr()
        assert "Architect" in captured.out
        assert "Explore the codebase" in captured.out
        assert DIM in captured.out

    def test_on_delegation_end_success(self, capsys):
        ui = CLIOutput()
        ui.on_delegation_end("architect", 12.3, False)
        captured = capsys.readouterr()
        assert "Architect" in captured.out
        assert "12.3s" in captured.out
        assert GREEN in captured.out
        assert DIM in captured.out

    def test_on_delegation_end_error(self, capsys):
        ui = CLIOutput()
        ui.on_delegation_end("developer", 5.0, True)
        captured = capsys.readouterr()
        assert "Developer" in captured.out
        assert RED in captured.out

    def test_on_progress_output(self, capsys):
        ui = CLIOutput()
        ui.on_progress("Reviewing results...")
        captured = capsys.readouterr()
        assert "Reviewing results..." in captured.out
        assert DIM in captured.out


class TestCLIOutputEventFeed:
    """Test event feed separation in CLIOutput."""

    def test_event_count_starts_at_zero(self):
        ui = CLIOutput()
        assert ui._event_count == 0

    def test_delegation_start_increments_events(self, capsys):
        ui = CLIOutput()
        ui.on_delegation_start("architect", "Explore codebase", 1, 5)
        assert ui._event_count == 1
        captured = capsys.readouterr()
        # Event feed uses dim indented text
        assert DIM in captured.out

    def test_delegation_end_increments_events(self, capsys):
        ui = CLIOutput()
        ui.on_delegation_end("architect", 12.3, False)
        assert ui._event_count == 1

    def test_progress_increments_events(self, capsys):
        ui = CLIOutput()
        ui.on_progress("Reviewing results...")
        assert ui._event_count == 1

    def test_start_thinking_resets_event_count(self):
        ui = CLIOutput()
        ui._event_count = 5
        ui.start_thinking()
        assert ui._event_count == 0


class TestStatusStrip:
    """Test the status strip rendering."""

    def test_status_strip_empty_company(self):
        from shipwright.interfaces.cli import _render_status_strip
        from shipwright.conversation.router import Router
        from shipwright.conversation.session import Session
        from shipwright.config import Config
        from pathlib import Path
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            config = Config(repo_root=Path(tmp), sessions_dir=Path(tmp) / "sessions")
            session = Session(id="test")
            router = Router(config=config, session=session)
            strip = _render_status_strip(router)
            assert "CTO offline" in strip

    def test_status_strip_with_cto(self):
        from shipwright.interfaces.cli import _render_status_strip
        from shipwright.conversation.router import Router
        from shipwright.conversation.session import Session
        from shipwright.config import Config
        from pathlib import Path
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            config = Config(repo_root=Path(tmp), sessions_dir=Path(tmp) / "sessions")
            session = Session(id="test")
            router = Router(config=config, session=session)
            router.company.ensure_cto()
            strip = _render_status_strip(router)
            assert "CTO" in strip

    def test_status_strip_mentions_events(self):
        from shipwright.interfaces.cli import _render_status_strip
        from shipwright.conversation.router import Router
        from shipwright.conversation.session import Session
        from shipwright.config import Config
        from pathlib import Path
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            config = Config(repo_root=Path(tmp), sessions_dir=Path(tmp) / "sessions")
            router = Router(config=config, session=Session(id="test"))
            router.company.ensure_cto()
            router._log_event("hire", "Alex", "Backend Developer")
            strip = _render_status_strip(router)
            assert "events 1" in strip


class TestControlRoomPanels:
    def test_render_control_header_contains_bridge_metadata(self):
        from shipwright.interfaces.cli import _render_control_header
        from shipwright.conversation.router import Router
        from shipwright.conversation.session import Session
        from shipwright.config import Config
        from pathlib import Path
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            config = Config(repo_root=Path(tmp), sessions_dir=Path(tmp) / "sessions")
            router = Router(config=config, session=Session(id="test"), session_name="bridge")
            header = _render_control_header(router, "bridge")
            assert "COMMAND BRIDGE" in header
            assert "session" in header

    def test_render_operator_hints_empty_company(self):
        from shipwright.interfaces.cli import _render_operator_hints
        from shipwright.conversation.router import Router
        from shipwright.conversation.session import Session
        from shipwright.config import Config
        from pathlib import Path
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            config = Config(repo_root=Path(tmp), sessions_dir=Path(tmp) / "sessions")
            router = Router(config=config, session=Session(id="test"))
            hints = _render_operator_hints(router)
            assert "Tell the CTO what to build" in hints

    def test_render_cycle_footer_includes_ops_and_roadmap(self):
        from shipwright.interfaces.cli import _render_cycle_footer
        from shipwright.conversation.router import Router
        from shipwright.conversation.session import Session
        from shipwright.config import Config
        from shipwright.company.employee import Roadmap, RoadmapState, RoadmapTask
        from pathlib import Path
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            config = Config(repo_root=Path(tmp), sessions_dir=Path(tmp) / "sessions")
            router = Router(config=config, session=Session(id="test"))
            router.company.active_roadmap = Roadmap(
                tasks=[RoadmapTask(index=1, description="Build API")],
                original_request="Build API",
                approved=True,
                state=RoadmapState.RUNNING,
            )

            ui = CLIOutput()
            ui._event_count = 2
            ui._start_time = time.time() - 1.2

            footer = _render_cycle_footer(ui, router)
            assert "2 ops" in footer
            assert "0/1 running" in footer


class TestPromptRendering:
    def test_build_prompt_uses_cto_brackets(self):
        from shipwright.interfaces.cli import _build_prompt
        from shipwright.conversation.router import Router
        from shipwright.conversation.session import Session
        from shipwright.config import Config
        from pathlib import Path
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            config = Config(repo_root=Path(tmp), sessions_dir=Path(tmp) / "sessions")
            router = Router(config=config, session=Session(id="test"))
            router.company.ensure_cto()
            prompt = _build_prompt(router)
            assert "[CTO]" in prompt

    def test_build_prompt_uses_named_employee_brackets(self):
        from shipwright.interfaces.cli import _build_prompt
        from shipwright.conversation.router import Router
        from shipwright.conversation.session import Session
        from shipwright.config import Config
        from shipwright.company.roles import get_role_def
        from pathlib import Path
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            config = Config(repo_root=Path(tmp), sessions_dir=Path(tmp) / "sessions")
            router = Router(config=config, session=Session(id="test"))
            router.company.hire("backend-dev", get_role_def("backend-dev"), name="GeminiDev")
            prompt = _build_prompt(router)
            assert "[GeminiDev]" in prompt
            assert "Backend Developer" in prompt


class TestControlPanels:
    def test_render_session_panel(self):
        from shipwright.interfaces.cli import _render_session_panel
        from shipwright.conversation.router import Router
        from shipwright.conversation.session import Session
        from shipwright.config import Config
        from pathlib import Path
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            config = Config(repo_root=Path(tmp), sessions_dir=Path(tmp) / "sessions")
            router = Router(config=config, session=Session(id="session-a"), session_name="session-a")
            router.company.ensure_cto()
            panel = _render_session_panel(router)
            assert "SESSION" in panel
            assert "session-a" in panel
            assert "messages" in panel.lower()

    def test_render_roadmap_panel_empty(self):
        from shipwright.interfaces.cli import _render_roadmap_panel
        from shipwright.conversation.router import Router
        from shipwright.conversation.session import Session
        from shipwright.config import Config
        from pathlib import Path
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            config = Config(repo_root=Path(tmp), sessions_dir=Path(tmp) / "sessions")
            router = Router(config=config, session=Session(id="session-a"))
            panel = _render_roadmap_panel(router)
            assert "ROADMAP" in panel
            assert "No active roadmap" in panel

    def test_render_roadmap_panel_with_tasks(self):
        from shipwright.interfaces.cli import _render_roadmap_panel
        from shipwright.conversation.router import Router
        from shipwright.conversation.session import Session
        from shipwright.config import Config
        from shipwright.company.employee import Roadmap, RoadmapTask, RoadmapState, RoadmapTaskStatus
        from pathlib import Path
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            config = Config(repo_root=Path(tmp), sessions_dir=Path(tmp) / "sessions")
            router = Router(config=config, session=Session(id="session-a"))
            router.company.ensure_cto()
            router.company.active_roadmap = Roadmap(
                tasks=[
                    RoadmapTask(index=1, description="Build API", status=RoadmapTaskStatus.DONE),
                    RoadmapTask(index=2, description="Write tests", status=RoadmapTaskStatus.RUNNING),
                    RoadmapTask(index=3, description="Ship it"),
                ],
                original_request="Build an API",
                approved=True,
                state=RoadmapState.RUNNING,
            )
            panel = _render_roadmap_panel(router)
            assert "ROADMAP" in panel
            assert "Build an API" in panel
            assert "Build API" in panel
            assert "Write tests" in panel

    def test_print_startup_renders_dashboard(self, capsys):
        from shipwright.interfaces.cli import _print_startup
        from shipwright.conversation.router import Router
        from shipwright.conversation.session import Session
        from shipwright.config import Config
        from shipwright.company.roles import get_role_def
        from pathlib import Path
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            config = Config(repo_root=Path(tmp), sessions_dir=Path(tmp) / "sessions")
            router = Router(config=config, session=Session(id="session-a"), session_name="session-a")
            router.company.ensure_cto()
            router.company.hire("backend-dev", get_role_def("backend-dev"), name="GeminiDev")
            router._log_event("hire", "GeminiDev", "Backend Developer")
            _print_startup(router, "session-a")
            captured = capsys.readouterr()
            assert "COMMAND BRIDGE" in captured.out
            assert "OPS" in captured.out
            assert "SESSION" in captured.out
            assert "ROADMAP" in captured.out
            assert "CREW" in captured.out


class TestSpinner:
    def test_initial_state(self):
        s = Spinner()
        assert s.active is False

    def test_stop_when_not_running(self):
        s = Spinner()
        s.stop()
        assert s.active is False
