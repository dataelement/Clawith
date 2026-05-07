FROM python:3.12-slim

# Claude Code CLI and a couple of common dev tools
RUN apt-get update && apt-get install -y --no-install-recommends \
        git curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Claude Code CLI (npm-based)
RUN curl -fsSL https://deb.nodesource.com/setup_22.x | bash - \
    && apt-get update && apt-get install -y --no-install-recommends nodejs \
    && rm -rf /var/lib/apt/lists/* \
    && npm install -g @anthropic-ai/claude-code \
    && npm cache clean --force

WORKDIR /app
COPY pyproject.toml ./
COPY clawith_bridge ./clawith_bridge
RUN pip install --no-cache-dir .

# Non-root; matches Claude Code's assumption that ~/.claude belongs to the runner
RUN useradd -m -u 1000 bridge
USER bridge
WORKDIR /home/bridge
RUN mkdir -p /home/bridge/workspace

ENV CLAWITH_BRIDGE_SERVER="" \
    CLAWITH_BRIDGE_TOKEN=""

ENTRYPOINT ["clawith-bridge"]
