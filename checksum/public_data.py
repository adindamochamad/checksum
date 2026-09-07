"""Checksum pointed at data this project did not create.

The studio warehouse has an obvious weakness as evidence: its telemetry is generated
here, and the disagreement it exposes was planted here. A verifier that only finds
faults its author installed has proved nothing.

So the same machinery is aimed at ClickHouse's public SQL Playground — real GitHub
event data, ~11 billion rows, untouched by this project. The question is the kind
anyone would ask:

    "Which repositories were most active in the second week of January 2024?"

Ranked by event count, the top entries are all abuse: `mdmaid69/reimagined-giggle`
posted 268,592 events in seven days from a single account. Ranked by distinct
contributors, the top entries are projects people have heard of -- subql,
copilot-gpt4-service, maybe -- and not one repository appears in both lists.

Nothing was planted. The instability is in GitHub's own data, and a text-to-SQL agent
asked this question returns the abuse list with total confidence.

There is no metric policy here, which changes what Checksum can honestly say. On the
studio warehouse it can name the definition the business is bound to. Here it can only
report that the readings disagree, and show the guard that explains why -- which is
the correct thing to say when nobody has written the definition down.
"""

from __future__ import annotations

from .variants import Scope, Variant

GITHUB_SCOPE = Scope(
    events_table="github.github_events",
    catalog_table="",
    filter_sql="created_at >= '2024-01-08' AND created_at < '2024-01-15'",
    label="GitHub, 8-14 January 2024",
    top_n=10,
)

QUESTION = "Which repositories were most active in the second week of January 2024?"

_WHERE = GITHUB_SCOPE.filter_sql
_FROM = f"FROM {GITHUB_SCOPE.events_table}\nWHERE {_WHERE}"

# Bots that announce themselves. Deliberately incomplete: the abuse accounts below are
# not named like bots at all, which is the point -- a name-based filter looks thorough
# and catches none of them.
_BOTLIKE = (
    "actor_login LIKE '%[bot]' OR actor_login LIKE '%-bot' OR actor_login LIKE 'bot-%'"
)


def _ranking(measure: str, order: str = "value") -> str:
    return f"""
SELECT repo_name AS title,
       {measure} AS value
{_FROM}
GROUP BY repo_name
ORDER BY {order} DESC
LIMIT {GITHUB_SCOPE.top_n}
""".strip()


def github_variants() -> list[Variant]:
    return [
        Variant(
            key="playback_start",  # keyed as the naive reading so adjudication lines up
            title="Every event",
            category="definition",
            rationale="Counts every event on the repository. The literal reading of "
                      "'most active', and what a text-to-SQL agent returns.",
            sql=_ranking("count()"),
        ),
        Variant(
            key="excluding_bots",
            title="Excluding named bots",
            category="definition",
            rationale="Filters accounts that identify themselves as bots. Looks "
                      "thorough; changes almost nothing here.",
            sql=_ranking(f"countIf(NOT ({_BOTLIKE}))"),
        ),
        Variant(
            key="pushes_only",
            title="Pushes only",
            category="definition",
            rationale="Code actually landing, rather than stars, forks and comments.",
            sql=_ranking("countIf(event_type = 'PushEvent')"),
        ),
        Variant(
            key="completion_90",  # keyed as the authoritative reading for adjudication
            title="Distinct contributors",
            category="dedup",
            rationale="How many different people touched it. One account acting a "
                      "million times is one contributor, not a million.",
            sql=_ranking("uniq(actor_login)"),
        ),
        Variant(
            key="distinct_pushers",
            title="Distinct people who pushed code",
            category="dedup",
            rationale="The strictest reading: distinct accounts that actually pushed.",
            sql=_ranking("uniqIf(actor_login, event_type = 'PushEvent')"),
        ),
        Variant(
            key="watchers",
            title="Stars gained",
            category="definition",
            rationale="Attention rather than activity. A different question wearing "
                      "the same words.",
            sql=_ranking("countIf(event_type = 'WatchEvent')"),
        ),
        Variant(
            key="guard_concentration",
            title="Events per contributor",
            category="guard",
            rationale="The ratio that exposes the rest. A repository real people use "
                      "sits near 1; a single account acting on repeat sits in the "
                      "hundreds of thousands.",
            ranks=False,
            # Measured across every repository, with no volume filter. Filtering to
            # high-volume repos first reported a median of ~22,000 events per
            # contributor and read as if that were normal for GitHub; it was the
            # median of a set that is mostly abuse. Unfiltered, the contrast is the
            # finding: 2 for the median repository, 43 at the 99th percentile, and
            # 268,592 for the one a literal reading calls "most active".
            sql=f"""
SELECT count()                                            AS repositories,
       round(median(events_per_actor), 2)                 AS median_repo,
       round(quantile(0.99)(events_per_actor), 1)         AS p99_repo,
       round(max(events_per_actor), 0)                    AS worst_ratio,
       argMax(repo_name, events_per_actor)                 AS worst_repo
FROM (
  SELECT repo_name,
         count() / uniq(actor_login) AS events_per_actor
  {_FROM}
  GROUP BY repo_name
)
""".strip(),
        ),
        Variant(
            key="guard_volume",
            title="Volume and window",
            category="guard",
            rationale="What was actually scanned, so the ranking above has a size.",
            ranks=False,
            sql=f"""
SELECT count()                          AS rows_scanned,
       uniq(repo_name)                  AS repositories,
       uniq(actor_login)                AS actors,
       toString(min(created_at))        AS first_event,
       toString(max(created_at))        AS last_event
{_FROM}
""".strip(),
        ),
    ]
