"""Tool definitions and executors for the agentic loop.

Each agent gets a restricted set of tools. Tool schemas are passed to the
Anthropic messages API, and executors run locally when Claude calls a tool.
"""

from __future__ import annotations

import fnmatch
import logging
import os
import re
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Tool JSON schemas (Anthropic tool_use format)
# ---------------------------------------------------------------------------

TOOL_SCHEMAS: dict[str, dict] = {
    "Read": {
        "name": "Read",
        "description": (
            "Read a file from the filesystem. Returns contents with line numbers."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "file_path": {
                    "type": "string",
                    "description": "Absolute path to the file to read",
                },
                "offset": {
                    "type": "integer",
                    "description": "Line number to start reading from (1-based)",
                },
                "limit": {
                    "type": "integer",
                    "description": "Max lines to read (default 2000)",
                },
            },
            "required": ["file_path"],
        },
    },
    "Write": {
        "name": "Write",
        "description": "Write content to a file. Creates parent directories if needed.",
        "input_schema": {
            "type": "object",
            "properties": {
                "file_path": {
                    "type": "string",
                    "description": "Absolute path to the file to write",
                },
                "content": {
                    "type": "string",
                    "description": "The content to write",
                },
            },
            "required": ["file_path", "content"],
        },
    },
    "Edit": {
        "name": "Edit",
        "description": (
            "Replace exact text in a file. old_string must be unique unless "
            "replace_all is true."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "file_path": {
                    "type": "string",
                    "description": "Absolute path to the file to modify",
                },
                "old_string": {
                    "type": "string",
                    "description": "The exact text to find",
                },
                "new_string": {
                    "type": "string",
                    "description": "The replacement text",
                },
                "replace_all": {
                    "type": "boolean",
                    "description": "Replace all occurrences (default false)",
                },
            },
            "required": ["file_path", "old_string", "new_string"],
        },
    },
    "Glob": {
        "name": "Glob",
        "description": "Find files matching a glob pattern. Returns file paths.",
        "input_schema": {
            "type": "object",
            "properties": {
                "pattern": {
                    "type": "string",
                    "description": "Glob pattern (e.g. '**/*.py')",
                },
                "path": {
                    "type": "string",
                    "description": "Directory to search in (default: working directory)",
                },
            },
            "required": ["pattern"],
        },
    },
    "Grep": {
        "name": "Grep",
        "description": "Search file contents with regex. Returns matching files or lines.",
        "input_schema": {
            "type": "object",
            "properties": {
                "pattern": {
                    "type": "string",
                    "description": "Regex pattern to search for",
                },
                "path": {
                    "type": "string",
                    "description": "File or directory to search (default: working directory)",
                },
                "glob": {
                    "type": "string",
                    "description": "Glob filter for files (e.g. '*.py')",
                },
                "include_content": {
                    "type": "boolean",
                    "description": "Return matching lines with context (default false)",
                },
            },
            "required": ["pattern"],
        },
    },
    "Bash": {
        "name": "Bash",
        "description": "Execute a shell command. Returns stdout and stderr.",
        "input_schema": {
            "type": "object",
            "properties": {
                "command": {
                    "type": "string",
                    "description": "The shell command to execute",
                },
                "timeout": {
                    "type": "integer",
                    "description": "Timeout in seconds (default 120)",
                },
            },
            "required": ["command"],
        },
    },
}


# ---------------------------------------------------------------------------
# Allowed-tool parsing
# ---------------------------------------------------------------------------

def parse_allowed_tools(allowed_tools: list[str]) -> tuple[set[str], list[str]]:
    """Parse an allowed_tools list into (tool_names, bash_patterns).

    Examples:
        ["Read", "Write", "Bash(git *)", "Bash(ls *)"]
        -> ({"Read", "Write", "Bash"}, ["git *", "ls *"])

        ["Read", "Bash"]
        -> ({"Read", "Bash"}, [])  # empty patterns = all commands allowed
    """
    tool_names: set[str] = set()
    bash_patterns: list[str] = []

    for spec in allowed_tools:
        if spec.startswith("Bash(") and spec.endswith(")"):
            tool_names.add("Bash")
            pattern = spec[5:-1]
            bash_patterns.append(pattern)
        elif spec.startswith("mcp__"):
            continue
        else:
            tool_names.add(spec)

    return tool_names, bash_patterns


def get_tool_schemas(allowed_tools: list[str]) -> list[dict]:
    """Return Anthropic API tool schemas for the allowed tools."""
    tool_names, _ = parse_allowed_tools(allowed_tools)
    return [TOOL_SCHEMAS[name] for name in tool_names if name in TOOL_SCHEMAS]


def check_bash_allowed(command: str, bash_patterns: list[str]) -> bool:
    """Check if a bash command matches at least one allowed pattern."""
    if not bash_patterns:
        return True
    cmd = command.strip()
    return any(fnmatch.fnmatch(cmd, p) for p in bash_patterns)


# ---------------------------------------------------------------------------
# Tool executors
# ---------------------------------------------------------------------------

def execute_tool(
    name: str,
    params: dict,
    cwd: str,
    bash_patterns: list[str] | None = None,
) -> str:
    """Execute a tool and return the result as a string."""
    try:
        if name == "Read":
            return _exec_read(params, cwd)
        elif name == "Write":
            return _exec_write(params, cwd)
        elif name == "Edit":
            return _exec_edit(params, cwd)
        elif name == "Glob":
            return _exec_glob(params, cwd)
        elif name == "Grep":
            return _exec_grep(params, cwd)
        elif name == "Bash":
            return _exec_bash(params, cwd, bash_patterns or [])
        else:
            return f"Error: Unknown tool '{name}'"
    except Exception as exc:
        logger.warning("Tool %s failed: %s", name, exc)
        return f"Error executing {name}: {exc}"


def _resolve_path(file_path: str, cwd: str) -> str:
    if not os.path.isabs(file_path):
        file_path = os.path.join(cwd, file_path)
    return file_path


def _exec_read(params: dict, cwd: str) -> str:
    file_path = _resolve_path(params["file_path"], cwd)
    if not os.path.exists(file_path):
        return f"Error: File not found: {file_path}"
    if os.path.isdir(file_path):
        return f"Error: '{file_path}' is a directory."

    offset = params.get("offset", 1)
    limit = params.get("limit", 2000)

    try:
        with open(file_path, "r", errors="replace") as f:
            lines = f.readlines()
    except Exception as exc:
        return f"Error reading file: {exc}"

    start = max(0, offset - 1)
    end = start + limit
    selected = lines[start:end]

    numbered = [f"{i:>6}\t{line.rstrip()}" for i, line in enumerate(selected, start=start + 1)]
    result = "\n".join(numbered)
    if end < len(lines):
        result += f"\n\n... ({len(lines) - end} more lines)"
    return result


def _exec_write(params: dict, cwd: str) -> str:
    file_path = _resolve_path(params["file_path"], cwd)
    content = params["content"]
    try:
        os.makedirs(os.path.dirname(file_path), exist_ok=True)
        with open(file_path, "w") as f:
            f.write(content)
        return f"Successfully wrote {len(content)} bytes to {file_path}"
    except Exception as exc:
        return f"Error writing file: {exc}"


def _exec_edit(params: dict, cwd: str) -> str:
    file_path = _resolve_path(params["file_path"], cwd)
    if not os.path.exists(file_path):
        return f"Error: File not found: {file_path}"

    old_string = params["old_string"]
    new_string = params["new_string"]
    replace_all = params.get("replace_all", False)

    try:
        with open(file_path, "r") as f:
            content = f.read()
    except Exception as exc:
        return f"Error reading file: {exc}"

    if old_string not in content:
        return f"Error: old_string not found in {file_path}"

    if not replace_all:
        count = content.count(old_string)
        if count > 1:
            return (
                f"Error: old_string appears {count} times in {file_path}. "
                "Provide more context or set replace_all=true."
            )
        content = content.replace(old_string, new_string, 1)
    else:
        content = content.replace(old_string, new_string)

    try:
        with open(file_path, "w") as f:
            f.write(content)
        return f"Successfully edited {file_path}"
    except Exception as exc:
        return f"Error writing file: {exc}"


def _exec_glob(params: dict, cwd: str) -> str:
    import glob as glob_mod

    pattern = params["pattern"]
    search_path = _resolve_path(params.get("path", cwd), cwd)
    full_pattern = os.path.join(search_path, pattern)

    skip_dirs = {"__pycache__", ".git", "node_modules", ".venv", ".tox"}

    matches = sorted(glob_mod.glob(full_pattern, recursive=True))
    matches = [
        m for m in matches
        if not any(s in m.split(os.sep) for s in skip_dirs)
    ]

    if not matches:
        return "No files matched the pattern."
    if len(matches) > 200:
        return "\n".join(matches[:200]) + f"\n\n... and {len(matches) - 200} more files"
    return "\n".join(matches)


def _exec_grep(params: dict, cwd: str) -> str:
    pattern = params["pattern"]
    search_path = _resolve_path(params.get("path", cwd), cwd)
    include_content = params.get("include_content", False)
    glob_filter = params.get("glob")

    # Try ripgrep first, fall back to pure Python
    rg_cmd = ["rg", "--no-heading"]
    if not include_content:
        rg_cmd.append("--files-with-matches")
    else:
        rg_cmd.extend(["-n", "-C", "2"])
    if glob_filter:
        rg_cmd.extend(["--glob", glob_filter])
    rg_cmd.extend(["--max-count", "50", pattern, search_path])

    try:
        result = subprocess.run(
            rg_cmd, capture_output=True, text=True, timeout=30, cwd=cwd,
        )
        output = result.stdout.strip()
        if output:
            lines = output.split("\n")
            if len(lines) > 100:
                output = "\n".join(lines[:100]) + f"\n\n... and {len(lines) - 100} more"
            return output
    except FileNotFoundError:
        pass  # rg not available, fall through
    except subprocess.TimeoutExpired:
        return "Error: Search timed out after 30 seconds."

    # Pure-Python fallback
    return _exec_grep_python(pattern, search_path, include_content, glob_filter)


def _exec_grep_python(
    pattern: str, search_path: str, include_content: bool, glob_filter: str | None
) -> str:
    import glob as glob_mod

    try:
        regex = re.compile(pattern)
    except re.error as exc:
        return f"Error: Invalid regex: {exc}"

    if os.path.isfile(search_path):
        files = [search_path]
    else:
        if glob_filter:
            files = glob_mod.glob(os.path.join(search_path, "**", glob_filter), recursive=True)
        else:
            files = glob_mod.glob(os.path.join(search_path, "**", "*"), recursive=True)
        files = [f for f in files if os.path.isfile(f)]

    skip_dirs = {"__pycache__", ".git", "node_modules"}
    matches = []
    for fpath in files[:500]:
        if any(s in fpath.split(os.sep) for s in skip_dirs):
            continue
        try:
            with open(fpath, "r", errors="replace") as f:
                for i, line in enumerate(f, 1):
                    if regex.search(line):
                        if include_content:
                            matches.append(f"{fpath}:{i}:{line.rstrip()}")
                        else:
                            matches.append(fpath)
                            break
                        if len(matches) > 100:
                            break
        except Exception:
            continue
        if len(matches) > 100:
            break

    return "\n".join(matches) if matches else "No matches found."


def _exec_bash(params: dict, cwd: str, bash_patterns: list[str] | None = None) -> str:
    command = params["command"]
    timeout = params.get("timeout", 120)

    if bash_patterns is not None and not check_bash_allowed(command, bash_patterns):
        return (
            f"Error: Command not allowed. Permitted patterns: "
            f"{', '.join(bash_patterns)}"
        )

    try:
        result = subprocess.run(
            command,
            shell=True,
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=cwd,
            env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},
        )
        output_parts = []
        if result.stdout.strip():
            output_parts.append(result.stdout.strip())
        if result.stderr.strip():
            output_parts.append(f"STDERR:\n{result.stderr.strip()}")
        if result.returncode != 0:
            output_parts.append(f"Exit code: {result.returncode}")

        output = "\n".join(output_parts) if output_parts else "(no output)"
        if len(output) > 50000:
            output = output[:50000] + "\n\n... (output truncated)"
        return output
    except subprocess.TimeoutExpired:
        return f"Error: Command timed out after {timeout} seconds."
    except Exception as exc:
        return f"Error executing command: {exc}"
