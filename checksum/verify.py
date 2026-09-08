"""Run every reading at once, then judge whether the answer survived them.

The output is deliberately not "the number". It is whether there *is* a number, or
whether the ranking was a choice of definition wearing a fact's clothes.
"""

from __future__ import annotations

import asyncio
from collections import Counter
from dataclasses import dataclass, field
from typing import Any, AsyncIterator

from .policy import MetricDefinition, authoritative
from .query import QueryError, Result, run_sql
from .variants import Variant


@dataclass
class VariantRun:
    variant: Variant
    result: Result | None = None
    error: str = ""
    #: title_id -> title. Readings return ids; humans read names.
    names: dict[str, str] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.result is not None

    def ranking(self) -> list[str]:
        if not self.result or not self.variant.ranks:
            return []
        return [self.names.get(str(row[0]), str(row[0])) for row in self.result.rows]

    def value_for(self, title: str) -> Any:
        if not self.result:
            return None
        for row in self.result.rows:
            if self.names.get(str(row[0]), str(row[0])) == title:
                return row[1]
        return None


@dataclass
class Verdict:
    runs: list[VariantRun]
    stable: bool
    refused: bool
    headline: str
    detail: str
    naive_leader: str = ""
    policy_leader: str = ""
    displacement: dict[str, int] = field(default_factory=dict)

    @property
    def rankings(self) -> list[VariantRun]:
        return [r for r in self.runs if r.variant.ranks and r.ok]

    @property
    def guards(self) -> list[VariantRun]:
        return [r for r in self.runs if not r.variant.ranks and r.ok]


async def run_variants(
    tools: dict[str, Any],
    variants: list[Variant],
    *,
    names: dict[str, str] | None = None,
    tool_context: Any = None,
) -> list[VariantRun]:
    """Fan out every reading at once. Each is a separate MCP call to ClickHouse."""
    mapping = names or {}

    async def one(variant: Variant) -> VariantRun:
        try:
            result = await run_sql(tools, variant.sql, tool_context=tool_context)
            return VariantRun(variant=variant, result=result, names=mapping)
        except QueryError as exc:
            return VariantRun(variant=variant, error=str(exc), names=mapping)

    return list(await asyncio.gather(*(one(v) for v in variants)))


async def stream_variants(
    tools: dict[str, Any],
    variants: list[Variant],
    *,
    names: dict[str, str] | None = None,
) -> AsyncIterator[VariantRun]:
    """Same fan-out, but yield each reading the moment it lands.

    The demo has to *show* the verification happening. Waiting for all nine to
    finish before painting anything would hide the one thing worth watching.
    """
    mapping = names or {}

    async def one(variant: Variant) -> VariantRun:
        try:
            result = await run_sql(tools, variant.sql)
            return VariantRun(variant=variant, result=result, names=mapping)
        except QueryError as exc:
            return VariantRun(variant=variant, error=str(exc), names=mapping)

    pending = [asyncio.create_task(one(v)) for v in variants]
    for finished in asyncio.as_completed(pending):
        yield await finished


@dataclass
class ForeignAudit:
    """What the other agent actually ran, and whether its definition is sanctioned.

    Checksum does not compare itself against an assumed baseline. It takes the SQL
    the standard agent really executed and asks two questions of it: does its answer
    survive the policy definition, and is the measure it chose one the studio ever
    wrote down. In testing, the standard agent invented "total watched seconds" --
    a metric that appears nowhere in the handbook -- and ranked on it confidently.
    """

    sql: str
    measure: str = ""
    in_policy: bool = False
    leader: str = ""
    note: str = ""


def audit_foreign_sql(
    sql: str, policy: list[MetricDefinition], leader: str = ""
) -> ForeignAudit:
    """Classify another agent's query against the studio's written definitions."""
    if not sql.strip():
        return ForeignAudit(sql="", note="The agent answered without querying at all.")

    lowered = " ".join(sql.lower().split())
    known = {
        "count()": "view_playback_start",
        "watched_seconds >= 120": "view_2min",
        "0.9 * runtime_seconds": "view_completion_90",
        "uniqexact(user_id)": "unique_viewers",
        "uniq(user_id)": "unique_viewers",
        "uniqexact(session_id)": "unique_sessions",
        "uniq(session_id)": "unique_sessions",
    }
    for needle, metric in known.items():
        if needle in lowered:
            match = next((d for d in policy if d.name == metric), None)
            return ForeignAudit(
                sql=sql, measure=metric, in_policy=True, leader=leader,
                note=f"Ranked on '{metric}', which is a defined metric "
                     f"({match.source_document})." if match else "",
            )

    invented = "sum(watched_seconds)" if "sum(watched_seconds)" in lowered else "an unlisted measure"
    return ForeignAudit(
        sql=sql, measure=invented, in_policy=False, leader=leader,
        note=f"Ranked on {invented}, which appears nowhere in the studio's metric "
             f"policy. The number is real; the definition was invented at query time.",
    )


def _displacement(runs: list[VariantRun]) -> dict[str, int]:
    """Largest rank change each title suffers across the competing readings."""
    positions: dict[str, list[int]] = {}
    for run in runs:
        for index, title in enumerate(run.ranking(), start=1):
            positions.setdefault(title, []).append(index)
    return {
        title: max(spots) - min(spots)
        for title, spots in positions.items()
        if len(spots) > 1
    }


def adjudicate(
    runs: list[VariantRun], policy: list[MetricDefinition], *, tolerance: int = 2
) -> Verdict:
    """Decide whether the rankings agreed, disagreed, or could not be trusted at all."""
    rankings = [r for r in runs if r.variant.ranks and r.ok]
    guards = [r for r in runs if not r.variant.ranks and r.ok]
    failures = [r for r in runs if not r.ok]

    # Refuse before interpreting. Too few surviving readings is not a weak answer,
    # it is an unanswerable question, and saying so is the honest output.
    if len(rankings) < 3:
        reasons = "; ".join(f"{r.variant.key}: {r.error[:120]}" for r in failures) or "no data"
        return Verdict(
            runs=runs, stable=False, refused=True,
            headline="Not enough readings survived to verify this answer.",
            detail=f"{len(rankings)} of {len(runs)} queries returned usable rows. {reasons}",
        )

    # A stable ranking over unsound data is stable and worthless.
    for guard in guards:
        row = dict(zip(guard.result.columns, guard.result.rows[0])) if guard.result.rows else {}
        if float(row.get("pct_zero_runtime", 0) or 0) > 5:
            return Verdict(
                runs=runs, stable=False, refused=True,
                headline="Refusing to rank: the runtime column is unusable.",
                detail=f"{row.get('pct_zero_runtime')}% of rows have zero runtime, so any "
                       f"completion-based definition is undefined for them.",
            )

    leaders = Counter(r.ranking()[0] for r in rankings if r.ranking())
    displacement = _displacement(rankings)

    naive = next((r for r in rankings if r.variant.key == "playback_start"), None)
    basis = authoritative(policy)
    # The "completion_90" key marks the most defensible reading in either dataset --
    # the policy definition on the studio warehouse, distinct contributors on GitHub.
    # It is looked up whether or not a policy table exists; only the wording changes.
    policy_run = next((r for r in rankings if r.variant.key == "completion_90"), None)
    naive_leader = naive.ranking()[0] if naive and naive.ranking() else ""
    policy_leader = policy_run.ranking()[0] if policy_run and policy_run.ranking() else ""

    worst = max(displacement.values(), default=0)
    agreed = len(leaders) == 1

    if agreed and worst <= tolerance:
        return Verdict(
            runs=runs, stable=True, refused=False,
            naive_leader=naive_leader, policy_leader=policy_leader,
            displacement=displacement,
            headline=f"Stable: every reading puts {next(iter(leaders))} first.",
            detail=f"{len(rankings)} definitions agree; no title moves more than "
                   f"{worst} place(s).",
        )

    moved = ""
    if naive_leader and policy_leader and naive_leader != policy_leader:
        naive_rank = policy_run.ranking().index(naive_leader) + 1 if policy_run and \
            naive_leader in policy_run.ranking() else None
        # The preposition travels with the phrase: "sits at rank 4" is right, but
        # "sits at off the list entirely" is not.
        where = f"at rank {naive_rank}" if naive_rank else "off the list entirely"
        if basis:
            moved = (
                f" {naive_leader} leads the most literal reading but sits {where} "
                f"under '{basis.name}', which {basis.source_document} makes the basis "
                f"for renewal."
            )
        else:
            # No policy table -- on borrowed data nobody has written the definition
            # down, so there is no authority to appeal to. Report the disagreement and
            # let the guards explain it; claiming one reading is correct would be
            # exactly the overconfidence this tool exists to catch.
            moved = (
                f" {naive_leader} leads the most literal reading and sits {where} "
                f"under '{policy_run.variant.title.lower()}'. No metric policy exists "
                f"for this dataset, so neither reading is authoritative -- but they "
                f"cannot both be the answer."
            )

    return Verdict(
        runs=runs, stable=False, refused=False,
        naive_leader=naive_leader, policy_leader=policy_leader,
        displacement=displacement,
        headline="Unstable: the ranking depends on which definition you pick.",
        detail=f"{len(leaders)} different titles rank first across {len(rankings)} "
               f"defensible readings; the largest move is {worst} places.{moved}",
    )
