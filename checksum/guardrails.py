"""The read-only boundary that actually holds.

The plan was for `tool_filter` to be the first guardrail layer, on the assumption
that mcp-clickhouse exposes a dedicated read-only query tool. It does not. There is
one query tool, `run_query`, and it serves reads and writes alike; the only thing
holding it back is an environment flag whose own description says:

    "That gate is a best-effort accident guard, not a security boundary."

On the public Playground a second layer exists, because the demo user lacks the
grants. On the project's own ClickHouse Cloud service the connecting user is admin,
so that layer is gone. This callback is what remains.

It runs before every tool call, so it is also where a refused query becomes a
message the agent can reason about rather than an opaque server error.
"""

from __future__ import annotations

import re
from typing import Any

# Statements that change state. Matched on word boundaries, after comments and
# string literals are stripped, so a title like "Total Recall" in a WHERE clause is
# not a violation.
#
# Deliberately absent: SYSTEM, SET and REPLACE. As leading commands they are already
# caught by the "must start with a read verb" rule below, and as substrings they
# appear in legitimate reads -- `FROM system.tables` is a pure read, `replace()` is
# a string function, and `SETTINGS` is not `SET`. Blocking them here would refuse
# valid queries, which trains an agent to fight its own guardrail.
FORBIDDEN = (
    "insert", "update", "delete", "drop", "truncate", "alter", "create",
    "attach", "detach", "rename", "grant", "revoke", "optimize", "kill",
)

_COMMENT = re.compile(r"--[^\n]*|/\*.*?\*/", re.DOTALL)
_STRING = re.compile(r"'(?:[^']|'')*'")


def _bare_sql(sql: str) -> str:
    """Strip comments and string literals so keyword matching sees only real syntax."""
    return _STRING.sub(" ", _COMMENT.sub(" ", sql)).lower()


def check_sql(sql: str) -> str | None:
    """Return a refusal reason, or None if the statement is a plain read."""
    bare = _bare_sql(sql).strip()
    if not bare:
        return "Empty query."

    # Reject stacked statements outright. A trailing semicolon is fine.
    if ";" in bare.rstrip().rstrip(";"):
        return (
            "Multiple statements in one call are not allowed. "
            "Send exactly one SELECT."
        )

    if not re.match(r"^\s*(select|with|explain|describe|show)\b", bare):
        return (
            f"Only read queries are permitted. This statement starts with "
            f"'{bare.split()[0]}'."
        )

    for word in FORBIDDEN:
        if re.search(rf"\b{word}\b", bare):
            return (
                f"'{word.upper()}' is not permitted. Checksum audits the warehouse; "
                f"it cannot modify it."
            )
    return None


def read_only_guard(tool: Any, args: dict[str, Any], tool_context: Any) -> dict | None:
    """ADK before_tool_callback. Returning a dict short-circuits the tool call."""
    if getattr(tool, "name", "") != "run_query":
        return None

    reason = check_sql(str(args.get("query", "")))
    if reason is None:
        return None

    # Returned in the tool-result position, so the agent reads it as a result and
    # can adjust, rather than seeing an exception it will likely just retry.
    return {"error": "refused_by_guardrail", "reason": reason}
