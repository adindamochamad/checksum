"""Which ClickHouse Checksum is pointed at, and with what authority.

Two things are deliberately separated here:

  WHERE     -- OWN_WAREHOUSE (the studio data) or PLAYGROUND (ClickHouse's public
               demo cluster). Checksum is not a tool for one dataset; being able to
               aim it at data its author never touched is the point.

  AUTHORITY -- read_only_toolset() cannot write. Every agent gets this one. An agent
               that audits numbers must not be able to change them, and that is
               enforced by the connection, not by asking the model nicely.
"""

from __future__ import annotations

import os
import pathlib
import sys
from dataclasses import dataclass, field

from google.adk.tools.mcp_tool import McpToolset, StdioConnectionParams
from mcp import StdioServerParameters

READ_TOOLS = ["run_query", "list_tables", "list_databases"]


def load_env(filename: str = ".env") -> None:
    """Minimal .env loader, so the project needs no extra dependency for it."""
    path = pathlib.Path(__file__).resolve().parent.parent / filename
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip())


@dataclass(frozen=True)
class Warehouse:
    """A ClickHouse target plus the tables Checksum expects to find there."""

    label: str
    describe: str
    events_table: str = ""
    catalog_table: str = ""
    policy_table: str = ""
    #: mcp-clickhouse caps a query at 30s by default. Billions of rows on a shared
    #: demo cluster need more, and a reading that times out is a reading that
    #: silently stops contributing to the verdict.
    query_timeout: int = 0
    _env: dict[str, str] = field(default_factory=dict)

    def env(self) -> dict[str, str]:
        extra = {"CLICKHOUSE_MCP_QUERY_TIMEOUT": str(self.query_timeout)} if self.query_timeout else {}
        if self._env:
            return {**self._env, **extra}
        load_env()
        return {
            **extra,
            "CLICKHOUSE_HOST": os.environ["CLICKHOUSE_HOST"],
            "CLICKHOUSE_PORT": os.environ.get("CLICKHOUSE_PORT", "8443"),
            "CLICKHOUSE_USER": os.environ.get("CLICKHOUSE_USER", "default"),
            "CLICKHOUSE_PASSWORD": os.environ.get("CLICKHOUSE_PASSWORD", ""),
            "CLICKHOUSE_SECURE": os.environ.get("CLICKHOUSE_SECURE", "true"),
        }


OWN_WAREHOUSE = Warehouse(
    label="studio",
    describe="Simulated studio warehouse: 500M playback events over a real IMDb catalog.",
    events_table="playback_events",
    catalog_table="catalog",
    policy_table="metric_policy",
)

PLAYGROUND = Warehouse(
    label="playground",
    describe="ClickHouse's public SQL Playground. Real datasets this project never touched.",
    query_timeout=150,
    _env={
        "CLICKHOUSE_HOST": "sql-clickhouse.clickhouse.com",
        "CLICKHOUSE_PORT": "8443",
        "CLICKHOUSE_USER": "demo",
        "CLICKHOUSE_PASSWORD": "",
        "CLICKHOUSE_SECURE": "true",
    },
)


def mcp_server_command() -> tuple[str, list[str]]:
    """How to launch the ClickHouse MCP server.

    It gets its own virtualenv, and it has to. mcp-clickhouse 0.6.0 depends on
    fastmcp 4.x, which requires mcp>=2; google-adk 2.8.0 requires mcp<2. The two
    cannot be installed together, and pip will happily break the agent side while
    reporting success -- the symptom is McpToolset vanishing from an import.

    They never meet, because stdio is a process boundary. That is what made the
    original `uv run --with mcp-clickhouse` work, and it is why the fix here is a
    second venv rather than one flat install.

    In production the server must already be installed: resolving it from PyPI at
    launch means uv has to exist, the host has to permit it, and PyPI has to be up
    at request time. Three ways to fail that a prebuilt venv simply removes.
    """
    for candidate in (
        pathlib.Path(__file__).resolve().parent.parent / ".venv-mcp" / "bin" / "mcp-clickhouse",
        pathlib.Path(sys.executable).parent / "mcp-clickhouse",
    ):
        if candidate.exists():
            return str(candidate), []
    return "uv", ["run", "--with", "mcp-clickhouse", "--python", "3.13", "mcp-clickhouse"]


def read_only_toolset(
    warehouse: Warehouse = OWN_WAREHOUSE, timeout: float = 60.0
) -> McpToolset:
    """The only connection an agent is ever handed. Write access is absent, not disabled."""
    command, args = mcp_server_command()
    return McpToolset(
        connection_params=StdioConnectionParams(
            server_params=StdioServerParameters(
                command=command,
                args=args,
                env={**warehouse.env(), "PATH": os.environ.get("PATH", "")},
            ),
            timeout=timeout,
        ),
        tool_filter=READ_TOOLS,
    )
