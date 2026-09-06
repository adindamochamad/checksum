"""Shared ClickHouse MCP connection helpers.

Two roles, deliberately separate:

  loader_toolset()  -- write access ON.  Used once, by the Block 1 data loader.
  agent_toolset()   -- write access OFF. Used by every agent that touches the
                       warehouse. Checksum audits numbers; it must never be able
                       to change them.

Keeping these apart is a design decision, not a convenience: the auditing agent
is structurally incapable of writing to the warehouse it audits.
"""

import os
import pathlib

from google.adk.tools.mcp_tool import McpToolset, StdioConnectionParams
from mcp import StdioServerParameters

READ_ONLY_TOOLS = ["run_query", "list_tables", "list_databases"]


def load_env(filename: str = ".env") -> None:
    """Minimal .env loader so no extra dependency is needed."""
    path = pathlib.Path(__file__).resolve().parent.parent / filename
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip())


def _connection_env(*, writable: bool) -> dict[str, str]:
    load_env()
    env = {
        "CLICKHOUSE_HOST": os.environ["CLICKHOUSE_HOST"],
        "CLICKHOUSE_PORT": os.environ.get("CLICKHOUSE_PORT", "8443"),
        "CLICKHOUSE_USER": os.environ.get("CLICKHOUSE_USER", "default"),
        "CLICKHOUSE_PASSWORD": os.environ.get("CLICKHOUSE_PASSWORD", ""),
        "CLICKHOUSE_SECURE": os.environ.get("CLICKHOUSE_SECURE", "true"),
        "PATH": os.environ.get("PATH", ""),
    }
    if writable:
        env["CLICKHOUSE_ALLOW_WRITE_ACCESS"] = "true"
        # Rebuilding a stage must be able to drop what it replaces.
        env["CLICKHOUSE_ALLOW_DROP"] = "true"
        # mcp-clickhouse caps queries at 30s by default -- far too short for bulk
        # generation. Raised for the loader only: an agent that hangs for ten
        # minutes is a bug, so agent_toolset() keeps the default.
        env["CLICKHOUSE_MCP_QUERY_TIMEOUT"] = "600"
        env["CLICKHOUSE_SEND_RECEIVE_TIMEOUT"] = "600"
    return env


def _toolset(*, writable: bool, timeout: float) -> McpToolset:
    return McpToolset(
        connection_params=StdioConnectionParams(
            server_params=StdioServerParameters(
                command="uv",
                args=["run", "--with", "mcp-clickhouse", "--python", "3.13", "mcp-clickhouse"],
                env=_connection_env(writable=writable),
            ),
            timeout=timeout,
        ),
        tool_filter=READ_ONLY_TOOLS,
    )


def loader_toolset(timeout: float = 900.0) -> McpToolset:
    """Write-enabled. Block 1 only -- never hand this to an agent."""
    return _toolset(writable=True, timeout=timeout)


def agent_toolset(timeout: float = 180.0) -> McpToolset:
    """Read-only. This is what agents get."""
    return _toolset(writable=False, timeout=timeout)
