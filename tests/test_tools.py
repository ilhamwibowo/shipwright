"""Tests for tool definitions and executors."""

import os
from pathlib import Path

import pytest

from dev_agent.agents.tools import (
    TOOL_SCHEMAS,
    check_bash_allowed,
    execute_tool,
    get_tool_schemas,
    parse_allowed_tools,
)


class TestParseAllowedTools:
    def test_simple_tools(self):
        names, patterns = parse_allowed_tools(["Read", "Write", "Glob"])
        assert names == {"Read", "Write", "Glob"}
        assert patterns == []

    def test_bash_with_patterns(self):
        names, patterns = parse_allowed_tools(["Read", "Bash(git *)", "Bash(ls *)"])
        assert "Bash" in names
        assert "Read" in names
        assert "git *" in patterns
        assert "ls *" in patterns

    def test_unrestricted_bash(self):
        names, patterns = parse_allowed_tools(["Read", "Bash"])
        assert "Bash" in names
        assert patterns == []

    def test_mcp_tools_ignored(self):
        names, patterns = parse_allowed_tools(["Read", "mcp__playwright__click"])
        assert names == {"Read"}

    def test_empty(self):
        names, patterns = parse_allowed_tools([])
        assert names == set()
        assert patterns == []


class TestCheckBashAllowed:
    def test_empty_patterns_allows_all(self):
        assert check_bash_allowed("rm -rf /", [])

    def test_matching_pattern(self):
        assert check_bash_allowed("git status", ["git *"])
        assert check_bash_allowed("git diff --stat", ["git *"])

    def test_non_matching_pattern(self):
        assert not check_bash_allowed("rm -rf /", ["git *", "ls *"])

    def test_multiple_patterns(self):
        patterns = ["git *", "ls *", "cat *"]
        assert check_bash_allowed("git log", patterns)
        assert check_bash_allowed("ls -la", patterns)
        assert not check_bash_allowed("rm file", patterns)


class TestGetToolSchemas:
    def test_returns_matching_schemas(self):
        schemas = get_tool_schemas(["Read", "Write"])
        names = {s["name"] for s in schemas}
        assert names == {"Read", "Write"}

    def test_bash_patterns_included(self):
        schemas = get_tool_schemas(["Read", "Bash(git *)"])
        names = {s["name"] for s in schemas}
        assert "Bash" in names

    def test_all_schemas_valid(self):
        for name, schema in TOOL_SCHEMAS.items():
            assert "name" in schema
            assert "description" in schema
            assert "input_schema" in schema
            assert schema["input_schema"]["type"] == "object"


class TestToolExecutors:
    def test_read_file(self, tmp_path: Path):
        f = tmp_path / "test.txt"
        f.write_text("line 1\nline 2\nline 3\n")
        result = execute_tool("Read", {"file_path": str(f)}, str(tmp_path))
        assert "line 1" in result
        assert "line 2" in result

    def test_read_file_not_found(self, tmp_path: Path):
        result = execute_tool("Read", {"file_path": str(tmp_path / "nope.txt")}, str(tmp_path))
        assert "Error" in result

    def test_read_with_offset(self, tmp_path: Path):
        f = tmp_path / "test.txt"
        f.write_text("line 1\nline 2\nline 3\n")
        result = execute_tool("Read", {"file_path": str(f), "offset": 2, "limit": 1}, str(tmp_path))
        assert "line 2" in result
        assert "line 1" not in result

    def test_write_file(self, tmp_path: Path):
        path = str(tmp_path / "new.txt")
        result = execute_tool("Write", {"file_path": path, "content": "hello"}, str(tmp_path))
        assert "Successfully wrote" in result
        assert Path(path).read_text() == "hello"

    def test_write_creates_dirs(self, tmp_path: Path):
        path = str(tmp_path / "sub" / "dir" / "file.txt")
        result = execute_tool("Write", {"file_path": path, "content": "nested"}, str(tmp_path))
        assert "Successfully wrote" in result

    def test_edit_file(self, tmp_path: Path):
        f = tmp_path / "test.txt"
        f.write_text("hello world")
        result = execute_tool(
            "Edit",
            {"file_path": str(f), "old_string": "world", "new_string": "earth"},
            str(tmp_path),
        )
        assert "Successfully edited" in result
        assert f.read_text() == "hello earth"

    def test_edit_not_found(self, tmp_path: Path):
        f = tmp_path / "test.txt"
        f.write_text("hello world")
        result = execute_tool(
            "Edit",
            {"file_path": str(f), "old_string": "xyz", "new_string": "abc"},
            str(tmp_path),
        )
        assert "Error" in result

    def test_edit_ambiguous(self, tmp_path: Path):
        f = tmp_path / "test.txt"
        f.write_text("aaa bbb aaa")
        result = execute_tool(
            "Edit",
            {"file_path": str(f), "old_string": "aaa", "new_string": "ccc"},
            str(tmp_path),
        )
        assert "appears 2 times" in result

    def test_edit_replace_all(self, tmp_path: Path):
        f = tmp_path / "test.txt"
        f.write_text("aaa bbb aaa")
        result = execute_tool(
            "Edit",
            {"file_path": str(f), "old_string": "aaa", "new_string": "ccc", "replace_all": True},
            str(tmp_path),
        )
        assert "Successfully edited" in result
        assert f.read_text() == "ccc bbb ccc"

    def test_glob(self, tmp_path: Path):
        (tmp_path / "a.py").write_text("")
        (tmp_path / "b.py").write_text("")
        (tmp_path / "c.txt").write_text("")
        result = execute_tool("Glob", {"pattern": "*.py"}, str(tmp_path))
        assert "a.py" in result
        assert "b.py" in result
        assert "c.txt" not in result

    def test_bash_allowed(self, tmp_path: Path):
        result = execute_tool("Bash", {"command": "echo hello"}, str(tmp_path), bash_patterns=[])
        assert "hello" in result

    def test_bash_restricted(self, tmp_path: Path):
        result = execute_tool(
            "Bash", {"command": "rm -rf /"}, str(tmp_path),
            bash_patterns=["git *", "ls *"],
        )
        assert "Error: Command not allowed" in result

    def test_bash_timeout(self, tmp_path: Path):
        result = execute_tool(
            "Bash", {"command": "sleep 10", "timeout": 1}, str(tmp_path), bash_patterns=[],
        )
        assert "timed out" in result

    def test_unknown_tool(self, tmp_path: Path):
        result = execute_tool("FakeTool", {}, str(tmp_path))
        assert "Unknown tool" in result
