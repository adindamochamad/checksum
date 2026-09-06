"""Verify the ClickHouse Cloud credentials in .env before Block 1 starts.

    .venv/bin/python scripts/check_cloud.py
"""

import asyncio
import os
import sys

from google.adk.tools.mcp_tool import McpToolset, StdioConnectionParams
from mcp import StdioServerParameters

from gate_check import load_env

REQUIRED = ["CLICKHOUSE_HOST", "CLICKHOUSE_PORT", "CLICKHOUSE_USER", "CLICKHOUSE_PASSWORD"]


async def main() -> int:
    load_env()

    host = os.environ.get("CLICKHOUSE_HOST", "")
    missing = [k for k in REQUIRED if not os.environ.get(k)]
    if "CLICKHOUSE_PASSWORD" in missing and os.environ.get("CLICKHOUSE_USER"):
        missing.remove("CLICKHOUSE_PASSWORD")  # empty password is legal
    if missing:
        print(f"BLOCKED  belum diisi di .env: {', '.join(missing)}")
        return 2
    if host.startswith("http") or ":" in host:
        print(f"FAIL  CLICKHOUSE_HOST harus hostname polos, bukan URL.\n"
              f"      sekarang: {host}\n"
              f"      benar   : xxxxx.region.provider.clickhouse.cloud")
        return 1

    toolset = McpToolset(
        connection_params=StdioConnectionParams(
            server_params=StdioServerParameters(
                command="uv",
                args=["run", "--with", "mcp-clickhouse", "--python", "3.13", "mcp-clickhouse"],
                env={k: os.environ.get(k, "") for k in
                     REQUIRED + ["CLICKHOUSE_SECURE"]} | {"PATH": os.environ.get("PATH", "")},
            ),
            timeout=180.0,
        ),
    )

    try:
        tools = {t.name: t for t in await toolset.get_tools()}
        checks = [
            ("versi & write access", "SELECT version() AS v, currentUser() AS u"),
            ("bisa CREATE?", "CREATE TABLE IF NOT EXISTS checksum_write_probe (x UInt8) ENGINE = Memory"),
            ("bersihkan probe", "DROP TABLE IF EXISTS checksum_write_probe"),
        ]
        for label, sql in checks:
            r = await tools["run_query"].run_async(args={"query": sql}, tool_context=None)
            txt = r["content"][0]["text"]
            ok = not txt.startswith("Query execution failed")
            print(f"{'OK  ' if ok else 'FAIL'} {label}: {txt[:150]}")
            if not ok and label == "bisa CREATE?":
                print("      -> Blok 1 butuh CREATE. Set CLICKHOUSE_ALLOW_WRITE_ACCESS=true"
                      " di env MCP server saat generate data.")
    except Exception as exc:
        print(f"FAIL  tidak bisa konek: {type(exc).__name__}: {str(exc)[:220]}")
        return 1

    await toolset.close()
    print("\nPASS  ClickHouse Cloud terjangkau. Blok 1 siap jalan.")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
