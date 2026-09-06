"""Running SQL through the MCP tool, and reading what comes back.

Two things here are easy to get wrong and expensive to get wrong quietly.

1. The payload is double-wrapped. FastMCP returns a dict whose content[0]["text"]
   is itself a JSON string of {"columns": [...], "rows": [[...]]}. It is not a dict
   of results.

2. Failure does not always look like failure. A successful call returns JSON; a
   server error OR a policy refusal ("Destructive operations are not allowed")
   comes back as plain prose. Checking for one known error prefix lets refusals
   read as success -- during the warehouse build that reported OK for a DROP that
   never happened. So success is defined as "parses as JSON", nothing looser.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any


class QueryError(RuntimeError):
    """The warehouse refused or failed the statement."""


@dataclass(frozen=True)
class Result:
    columns: list[str]
    rows: list[list[Any]]
    sql: str

    def dicts(self) -> list[dict[str, Any]]:
        return [dict(zip(self.columns, row)) for row in self.rows]

    def scalar(self) -> Any:
        if not self.rows or not self.rows[0]:
            raise QueryError(f"Expected one value, got nothing.\nSQL: {self.sql}")
        return self.rows[0][0]

    @property
    def row_count(self) -> int:
        return len(self.rows)


def _unwrap(payload: Any) -> str:
    if isinstance(payload, dict):
        content = payload.get("content") or []
        if content and isinstance(content[0], dict):
            return str(content[0].get("text", ""))
    return str(payload)


async def run_sql(tools: dict[str, Any], sql: str, *, tool_context: Any = None) -> Result:
    """Execute one read query and return a parsed Result, or raise QueryError."""
    raw = await tools["run_query"].run_async(
        args={"query": sql.strip()}, tool_context=tool_context
    )
    text = _unwrap(raw)
    try:
        parsed = json.loads(text)
    except (json.JSONDecodeError, TypeError):
        # Server error or guardrail refusal -- both arrive as prose, never JSON.
        raise QueryError(f"{text.strip()[:400]}\nSQL: {sql.strip()[:300]}") from None

    if not isinstance(parsed, dict) or "columns" not in parsed:
        raise QueryError(f"Unexpected payload: {text[:300]}\nSQL: {sql.strip()[:300]}")

    return Result(
        columns=list(parsed.get("columns") or []),
        rows=[list(r) for r in (parsed.get("rows") or [])],
        sql=sql.strip(),
    )


async def tools_for(toolset: Any) -> dict[str, Any]:
    return {tool.name: tool for tool in await toolset.get_tools()}
