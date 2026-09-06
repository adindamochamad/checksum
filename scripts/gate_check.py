"""Gate check: prove google-adk can drive the ClickHouse MCP server over stdio.

Phase 1 needs no API key -- it connects to the public ClickHouse SQL Playground
and prints the exact tool names the server exposes.
Phase 2 runs a real Gemini agent through those tools (needs GOOGLE_API_KEY).
"""

import asyncio
import os
import pathlib
import sys

from google.adk.tools.mcp_tool import McpToolset, StdioConnectionParams
from mcp import StdioServerParameters

# Public read-only demo cluster: https://clickhouse.com/docs/getting-started/playground
PLAYGROUND = {
    "CLICKHOUSE_HOST": "sql-clickhouse.clickhouse.com",
    "CLICKHOUSE_PORT": "8443",
    "CLICKHOUSE_USER": "demo",
    "CLICKHOUSE_PASSWORD": "",
    "CLICKHOUSE_SECURE": "true",
}


def load_env(path: str = ".env") -> None:
    """Minimal .env loader so the gate scripts need no extra dependency."""
    f = pathlib.Path(__file__).resolve().parent.parent / path
    if not f.exists():
        return
    for line in f.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip())


def build_toolset() -> McpToolset:
    env = {**PLAYGROUND, "PATH": os.environ.get("PATH", "")}
    return McpToolset(
        connection_params=StdioConnectionParams(
            server_params=StdioServerParameters(
                command="uv",
                args=[
                    "run", "--with", "mcp-clickhouse",
                    "--python", "3.13", "mcp-clickhouse",
                ],
                env=env,
            ),
            timeout=180.0,
        ),
    )


async def main() -> int:
    toolset = build_toolset()
    try:
        tools = await toolset.get_tools()
    except Exception as exc:
        print(f"FAIL  could not reach mcp-clickhouse: {type(exc).__name__}: {exc}")
        return 1

    if not tools:
        print("FAIL  server started but exposed zero tools")
        return 1

    print(f"PASS  mcp-clickhouse exposed {len(tools)} tools:\n")
    for tool in tools:
        desc = (getattr(tool, "description", "") or "").split("\n")[0][:88]
        print(f"  - {tool.name}\n      {desc}")

    await toolset.close()
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
