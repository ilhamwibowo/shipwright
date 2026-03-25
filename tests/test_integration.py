"""Integration test stubs.

These tests would require a valid ANTHROPIC_API_KEY and are intended to be
run manually or in CI with appropriate credentials.
"""

import os

import pytest

# Skip all tests in this module if no API key is available
pytestmark = pytest.mark.skipif(
    not os.environ.get("ANTHROPIC_API_KEY"),
    reason="ANTHROPIC_API_KEY not set — skipping integration tests",
)


@pytest.mark.asyncio
async def test_coordinator_greeting():
    """Test that the coordinator handles a greeting without spawning tasks."""
    from dev_agent.config import load_config
    from dev_agent.coordinator import handle_message

    config = load_config()
    replies = []

    await handle_message(
        chat_id="test",
        message="hello",
        config=config,
        on_reply=lambda text: replies.append(text),
    )

    assert len(replies) >= 1
    # Should get a conversational reply, not spawn tasks
    assert any("hello" in r.lower() or "hey" in r.lower() or "team" in r.lower() for r in replies)


@pytest.mark.asyncio
async def test_architect_runs():
    """Test that the architect agent can analyze a small requirement."""
    from pathlib import Path
    from dev_agent.agents.architect import run_architect
    from dev_agent.config import load_config

    config = load_config()
    workspace = Path("/tmp/dev-agent-test-workspace")
    workspace.mkdir(exist_ok=True)

    result = await run_architect(
        requirement="Add a health check endpoint that returns 200 OK",
        config=config,
        workspace_dir=str(workspace),
    )

    assert result.output  # Should produce some output
    # The architect might or might not succeed depending on the repo
    # but it should at least run without crashing
