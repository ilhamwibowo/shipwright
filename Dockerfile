FROM python:3.12-slim-bookworm

# System deps for git, gh CLI, and Node.js (for Playwright MCP)
RUN apt-get update && apt-get install -y --no-install-recommends \
    git curl gpg && \
    # GitHub CLI
    curl -fsSL https://cli.github.com/packages/githubcli-archive-keyring.gpg \
      | gpg --dearmor -o /usr/share/keyrings/githubcli-archive-keyring.gpg && \
    echo "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/githubcli-archive-keyring.gpg] https://cli.github.com/packages stable main" \
      > /etc/apt/sources.list.d/github-cli.list && \
    apt-get update && apt-get install -y gh && \
    # Node.js (for Playwright MCP server)
    curl -fsSL https://deb.nodesource.com/setup_22.x | bash - && \
    apt-get install -y nodejs && \
    # Playwright browsers
    npx playwright install --with-deps chromium && \
    # Cleanup
    apt-get clean && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY pyproject.toml .
RUN pip install --no-cache-dir .

COPY dev_agent/ dev_agent/

# Install Playwright MCP server globally
RUN npm install -g @anthropic-ai/mcp-server-playwright

CMD ["dev-agent"]
