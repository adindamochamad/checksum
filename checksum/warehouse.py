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
    _env: dict[str, str] = field(default_factory=dict)

    def env(self) -> dict[str, str]:
        if self._env:
            return dict(self._env)
        load_env()
        return {
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
    _env={
        "CLICKHOUSE_HOST": "sql-clickhouse.clickhouse.com",
        "CLICKHOUSE_PORT": "8443",
        "CLICKHOUSE_USER": "demo",
        "CLICKHOUSE_PASSWORD": "",
        "CLICKHOUSE_SECURE": "true",
    },
)


def read_only_toolset(
    warehouse: Warehouse = OWN_WAREHOUSE, timeout: float = 60.0
) -> McpToolset:
    """The only connection an agent is ever handed. Write access is absent, not disabled."""
    return McpToolset(
        connection_params=StdioConnectionParams(
            server_params=StdioServerParameters(
                command="uv",
                args=["run", "--with", "mcp-clickhouse", "--python", "3.13", "mcp-clickhouse"],
                env={**warehouse.env(), "PATH": os.environ.get("PATH", "")},
            ),
            timeout=timeout,
        ),
        tool_filter=READ_TOOLS,
    )
