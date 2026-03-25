"""Tests for main.py: CLI entry point, claude CLI check, session flag extraction."""

from unittest.mock import patch

import pytest

from shipwright.main import _check_claude_cli, _extract_session_flag


class TestExtractSessionFlag:
    def test_no_session_flag(self):
        name, remaining = _extract_session_flag(["hire", "backend-dev"])
        assert name == "default"
        assert remaining == ["hire", "backend-dev"]

    def test_session_flag(self):
        name, remaining = _extract_session_flag(["--session", "myproject", "hire", "backend-dev"])
        assert name == "myproject"
        assert remaining == ["hire", "backend-dev"]

    def test_session_flag_at_end(self):
        name, remaining = _extract_session_flag(["status", "--session", "myproject"])
        assert name == "myproject"
        assert remaining == ["status"]

    def test_empty_args(self):
        name, remaining = _extract_session_flag([])
        assert name == "default"
        assert remaining == []

    def test_session_flag_missing_value(self):
        with pytest.raises(SystemExit):
            _extract_session_flag(["--session"])


class TestCheckClaudeCLI:
    def test_claude_found(self):
        with patch("shutil.which", return_value="/usr/local/bin/claude"):
            # Should not raise
            _check_claude_cli()

    def test_claude_not_found(self):
        with patch("shutil.which", return_value=None):
            with pytest.raises(SystemExit):
                _check_claude_cli()
