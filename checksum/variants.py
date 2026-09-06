"""The verification budget: one question, asked many defensible ways.

Two kinds of variant:

  RankingVariant -- the same question under a different, defensible reading. Not
                    wrong readings; each is a definition somebody in the industry
                    actually uses. If they disagree, the answer was never a fact,
                    it was a choice of definition.

  GuardVariant   -- facts about the data itself. A stable ranking over a column
                    that is 40% null is stable and worthless.

Shape of the SQL matters as much as its meaning. Each reading filters on title_id,
the leading column of the table's ORDER BY, so ClickHouse skips granules instead of
scanning; aggregates to twelve rows; and never joins the catalog at all -- titles
are resolved once, up front, and mapped back in Python. Joining 500M event rows to
the catalog *before* filtering cost 11.8s per reading against 4.7s this way, and
starved the fan-out of the cores it needs to overlap.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Any, Literal

from .policy import MetricDefinition
from .query import run_sql

Category = Literal["definition", "dedup", "window", "guard"]


@dataclass(frozen=True)
class Scope:
    """What slice of the warehouse a question is about, with its titles resolved."""

    events_table: str = "playback_events"
    catalog_table: str = "catalog"
    filter_sql: str = "is_original = 1"
    label: str = "2026 originals"
    top_n: int = 12
    titles: dict[str, str] = field(default_factory=dict)

    @property
    def id_list(self) -> str:
        return ",".join(f"'{tid}'" for tid in self.titles)

    def name_of(self, title_id: str) -> str:
        return self.titles.get(title_id, title_id)


async def resolve_scope(tools: dict[str, Any], scope: Scope) -> Scope:
    """Look up the titles in scope once, so no reading has to join for a name."""
    result = await run_sql(
        tools,
        f"SELECT title_id, title FROM {scope.catalog_table} WHERE {scope.filter_sql}",
    )
    return replace(scope, titles={str(r[0]): str(r[1]) for r in result.rows})


@dataclass(frozen=True)
class Variant:
    key: str
    title: str
    category: Category
    rationale: str
    sql: str
    ranks: bool = True
    policy_source: str = ""
    caveat: str = ""


def _ranking_sql(scope: Scope, measure: str, extra_where: str = "") -> str:
    where = f"title_id IN ({scope.id_list})"
    if extra_where:
        where += f"\n  AND {extra_where}"
    return f"""
SELECT title_id,
       {measure} AS value
FROM {scope.events_table}
WHERE {where}
GROUP BY title_id
ORDER BY value DESC
LIMIT {scope.top_n}
""".strip()


def ranking_variants(scope: Scope, policy: list[MetricDefinition]) -> list[Variant]:
    by_name = {d.name: d for d in policy}

    def source(name: str) -> str:
        definition = by_name.get(name)
        return definition.source_document if definition else ""

    return [
        Variant(
            key="playback_start",
            title="Any playback start",
            category="definition",
            rationale="Counts every start. The easiest definition to express in SQL, "
                      "and the one a standard agent reaches for.",
            sql=_ranking_sql(scope, "count()"),
            policy_source=source("view_playback_start"),
        ),
        Variant(
            key="watched_2min",
            title="Watched at least 2 minutes",
            category="definition",
            rationale="Filters out instant bounces. A common industry floor.",
            sql=_ranking_sql(scope, "countIf(watched_seconds >= 120)"),
            policy_source=source("view_2min"),
        ),
        Variant(
            key="completion_90",
            title="Watched at least 90% of runtime",
            category="definition",
            rationale="The completion-based reading, and the one the renewal policy "
                      "binds to.",
            sql=_ranking_sql(scope, "countIf(watched_seconds >= 0.9 * runtime_seconds)"),
            policy_source=source("view_completion_90"),
        ),
        Variant(
            key="unique_viewers",
            title="Distinct viewers",
            category="dedup",
            rationale="One person rewatching six times is one viewer, not six views.",
            sql=_ranking_sql(scope, "uniq(user_id)"),
            policy_source=source("unique_viewers"),
            caveat="Distinct counts use ClickHouse's HyperLogLog estimator (~0.5% "
                   "error). Exact counting cost 2.8s against 1.0s here, and the "
                   "disagreement being reported spans eight rank positions -- an "
                   "order of magnitude above the estimator's error.",
        ),
        Variant(
            key="unique_sessions",
            title="Distinct sessions",
            category="dedup",
            rationale="Deduplicates by session rather than by person, which is what "
                      "most dashboards actually do.",
            sql=_ranking_sql(scope, "uniq(session_id)"),
            policy_source=source("unique_sessions"),
            caveat="Approximate distinct, as above.",
        ),
        Variant(
            key="completion_by_viewer",
            title="Distinct viewers who finished",
            category="dedup",
            rationale="The policy definition combined with viewer dedup -- the "
                      "strictest defensible reading of the question.",
            sql=_ranking_sql(
                scope, "uniqIf(user_id, watched_seconds >= 0.9 * runtime_seconds)"
            ),
            caveat="Approximate distinct, as above.",
        ),
        Variant(
            key="recent_window",
            title="Last 90 days only",
            category="window",
            rationale="A rolling window instead of all history. Launch spikes fade, "
                      "and rankings move when they do.",
            sql=_ranking_sql(
                scope,
                "count()",
                extra_where=(
                    f"started_at >= (SELECT max(started_at) - INTERVAL 90 DAY "
                    f"FROM {scope.events_table})"
                ),
            ),
        ),
    ]


def guard_variants(scope: Scope) -> list[Variant]:
    """Facts about the data, so a stable answer is not mistaken for a sound one."""
    return [
        Variant(
            key="guard_volume",
            title="Volume and null rates",
            category="guard",
            rationale="A ranking is only as trustworthy as the columns holding it up.",
            ranks=False,
            sql=f"""
SELECT count()                                                  AS rows_scanned,
       uniqExact(title_id)                                      AS titles,  -- 12 values; exact is free here
       round(100 * countIf(watched_seconds = 0) / count(), 3)    AS pct_zero_watched,
       round(100 * countIf(runtime_seconds = 0) / count(), 3)    AS pct_zero_runtime,
       toString(min(started_at))                                 AS first_event,
       toString(max(started_at))                                 AS last_event
FROM {scope.events_table}
WHERE title_id IN ({scope.id_list})
""".strip(),
        ),
        Variant(
            key="guard_sanity",
            title="Impossible rows",
            category="guard",
            rationale="Watch time above runtime would mean the column measures "
                      "something other than what its name claims.",
            ranks=False,
            sql=f"""
SELECT countIf(watched_seconds > runtime_seconds)                AS over_runtime,
       round(100 * countIf(watched_seconds > runtime_seconds)
             / count(), 3)                                       AS pct_over_runtime,
       round(avg(watched_seconds / nullIf(runtime_seconds, 0)), 4) AS avg_completion
FROM {scope.events_table}
WHERE title_id IN ({scope.id_list})
""".strip(),
        ),
    ]


def all_variants(scope: Scope, policy: list[MetricDefinition]) -> list[Variant]:
    return ranking_variants(scope, policy) + guard_variants(scope)


def naive_sql(scope: Scope) -> str:
    """What a standard text-to-SQL agent produces: the easiest reading, once.

    Not a strawman. It is a correct answer to the question as literally asked, and
    it is what most agents built on this stack will return.
    """
    return _ranking_sql(scope, "count()")
