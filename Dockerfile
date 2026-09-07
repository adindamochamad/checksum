# Checksum
#
# Two virtualenvs, deliberately. mcp-clickhouse depends on fastmcp 4.x, which needs
# mcp>=2; google-adk needs mcp<2. They cannot be installed together -- and they never
# have to be, because the MCP server is a separate process behind stdio. Installing
# them flat breaks the agent side quietly: McpToolset disappears from an import.
#
# Both are installed at build time. Resolving mcp-clickhouse from PyPI at launch
# would mean uv must exist, the host must let it spawn, and PyPI must be reachable
# when a judge clicks the link.

FROM python:3.13-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# uv, not pip. pip's resolver gives up on this graph with resolution-too-deep;
# uv solves it in seconds. Both environments install from locks, so the image gets
# the exact versions the quality gates ran against.
COPY --from=ghcr.io/astral-sh/uv:0.11.21 /uv /usr/local/bin/uv

# Agent environment: Gemini via ADK, mcp pinned below 2.
COPY requirements.lock ./
RUN uv venv /opt/venv --python 3.13 \
 && uv pip install --python /opt/venv/bin/python -r requirements.lock

# MCP server environment: isolated, so its mcp>=2 never meets the agent's mcp<2.
COPY requirements-mcp-server.lock ./
RUN uv venv /opt/venv-mcp --python 3.13 \
 && uv pip install --python /opt/venv-mcp/bin/python -r requirements-mcp-server.lock

COPY checksum ./checksum
COPY web ./web

# warehouse.py looks for .venv-mcp next to the package before falling back to uv.
RUN ln -s /opt/venv-mcp /app/.venv-mcp \
 && mkdir -p /app/data

# Non-root: the app only ever reads from ClickHouse, so it needs nothing more.
RUN useradd --create-home --uid 10001 checksum \
 && chown -R checksum:checksum /app
USER checksum

ENV PATH="/opt/venv/bin:${PATH}" \
    PORT=8080

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=10s --start-period=90s --retries=3 \
  CMD python -c "import urllib.request,os,sys; \
sys.exit(0 if b'\"ready\":true' in urllib.request.urlopen(f'http://127.0.0.1:{os.environ.get(\"PORT\",8080)}/api/health', timeout=8).read() else 1)"

CMD ["sh", "-c", "exec uvicorn checksum.api:app --host 0.0.0.0 --port ${PORT}"]
