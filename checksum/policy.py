"""The studio's written definitions of what a metric means.

This table is what separates Checksum from an agent that merely double-checks
itself. Self-consistency proves an agent agrees with itself, and an agent can be
consistently wrong. Comparing against a policy the business wrote down is the only
way to catch an answer that is correct SQL and still the wrong number.

The definitions here are authored for the demo, standing in for a studio's internal
analytics handbook. Real ones exist and disagree with each other the same way:
Netflix redefined "views" in 2024, and Nielsen counts differently again.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .query import Result, run_sql


@dataclass(frozen=True)
class MetricDefinition:
    name: str
    sql_fragment: str
    threshold: str
    effective_from: str
    source_document: str
    is_renewal_basis: bool

    @property
    def is_authoritative(self) -> bool:
        """True for the definition the business actually makes decisions with."""
        return self.is_renewal_basis

    def describe(self) -> str:
        mark = "  [BASIS FOR RENEWAL DECISIONS]" if self.is_renewal_basis else ""
        return f"{self.name}: {self.threshold} (since {self.effective_from}){mark}"


async def load_policy(tools: dict[str, Any], table: str = "metric_policy") -> list[MetricDefinition]:
    result: Result = await run_sql(
        tools,
        f"""
        SELECT metric_name, definition_sql, threshold,
               toString(effective_from), source_document, is_renewal_basis
        FROM {table}
        ORDER BY is_renewal_basis DESC, metric_name
        """,
    )
    return [
        MetricDefinition(
            name=row[0],
            sql_fragment=row[1],
            threshold=row[2],
            effective_from=row[3],
            source_document=row[4],
            is_renewal_basis=bool(row[5]),
        )
        for row in result.rows
    ]


def authoritative(definitions: list[MetricDefinition]) -> MetricDefinition | None:
    """The definition the renewal policy binds to, if the studio has declared one."""
    for definition in definitions:
        if definition.is_renewal_basis:
            return definition
    return None


def policy_briefing(definitions: list[MetricDefinition]) -> str:
    """A compact rendering of the policy for an agent prompt."""
    lines = [d.describe() for d in definitions]
    basis = authoritative(definitions)
    if basis:
        lines.append(
            f"\nRenewal decisions are governed by '{basis.name}', "
            f"per {basis.source_document}."
        )
    return "\n".join(lines)
