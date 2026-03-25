"""Integration test stubs for the full agent pipeline.

These tests require an ANTHROPIC_API_KEY and are skipped by default.
Run with: pytest tests/integration/ -m integration --run-integration
"""

import os

import pytest

# Skip all tests in this module unless --run-integration is passed
pytestmark = pytest.mark.skipif(
    not os.environ.get("ANTHROPIC_API_KEY"),
    reason="Integration tests require ANTHROPIC_API_KEY",
)


@pytest.mark.integration
async def test_coordinator_greeting():
    """Test that the coordinator responds to a greeting without spawning tasks."""
    from dev_agent.config import load_config
    from dev_agent.coordinator import handle_message

    config = load_config()
    replies = []

    await handle_message(
        chat_id="test",
        message="hello",
        config=config,
        on_reply=lambda t: replies.append(t),
    )

    assert len(replies) > 0
    # Should be a conversational reply, not spawn tasks
    assert any("hello" in r.lower() or "hey" in r.lower() or "team" in r.lower() for r in replies)


@pytest.mark.integration
async def test_architect_explores_repo():
    """Test that the architect agent can explore a repo and produce a spec."""
    from dev_agent.agents.architect import run_architect
    from dev_agent.config import load_config

    config = load_config()
    result = await run_architect(
        requirement="Explain the project structure",
        config=config,
        workspace_dir=str(config.workspace_dir),
    )

    assert result.success
    assert len(result.output) > 100
    assert result.usage.api_calls > 0
