"""Gate check phase 1.5: actually execute SQL through the MCP tool (no API key needed)."""

import asyncio
import sys

from gate_check import build_toolset


async def main() -> int:
    toolset = build_toolset()
    tools = {t.name: t for t in await toolset.get_tools()}

    print("=== full run_query description (matters for the guardrail) ===")
    print((tools["run_query"].description or "").strip()[:700])

    probes = [
        ("sanity", "SELECT 1 AS ok"),
        ("real data", "SELECT count() AS n FROM system.tables"),
        ("write must be refused", "CREATE TABLE checksum_should_fail (x Int32) ENGINE = Memory"),
    ]

    print("\n=== probes ===")
    failures = 0
    for label, sql in probes:
        try:
            result = await tools["run_query"].run_async(args={"query": sql}, tool_context=None)
            print(f"  [{label}] {sql[:52]}\n      -> {str(result)[:160]}")
        except Exception as exc:
            print(f"  [{label}] {sql[:52]}\n      -> raised {type(exc).__name__}: {str(exc)[:130]}")
            if label != "write must be refused":
                failures += 1

    await toolset.close()
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
