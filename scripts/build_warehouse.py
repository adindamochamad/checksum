"""Block 1 -- build the Checksum warehouse in ClickHouse Cloud.

Stages run independently so a failure never forces a full rebuild:

    python scripts/build_warehouse.py catalog
    python scripts/build_warehouse.py policy
    python scripts/build_warehouse.py telemetry
    python scripts/build_warehouse.py verify

Data honesty (stated in README and the demo video):
  - catalog  : REAL. IMDb non-commercial datasets.
  - telemetry: SIMULATED at studio scale. No public playback telemetry exists.
  - policy   : AUTHORED. Stands in for a studio's internal metric definitions.
"""

import asyncio
import json
import sys
import time

from ch import loader_toolset

MIN_VOTES = 50_000
TELEMETRY_ROWS = 500_000_000

# Calibrated on 2026-09-06, see docs/04-DATA.md.
# randCanonical(N) with DISTINCT dummy args -- identical calls get folded into one
# column by common subexpression elimination, which silently correlates the two
# populations and corrupts the completion metric.
WATCHED_SECONDS = """
    if(randCanonical(1) < bounce_rate,
       toUInt32(8 + 232 * pow(randCanonical(2), 1.4)),
       toUInt32(runtime_seconds * (0.45 + 0.55 * pow(randCanonical(2), 0.7))))
"""

STAGES: dict[str, list[tuple[str, str]]] = {
    "catalog": [
        ("drop", "DROP TABLE IF EXISTS catalog"),
        ("create", """
            CREATE TABLE catalog (
              title_id        String,
              title           String,
              title_type      LowCardinality(String),
              release_year    UInt16,
              runtime_seconds UInt32,
              imdb_rating     Float32,
              imdb_votes      UInt32,
              popularity_rank UInt32   COMMENT '1 = most popular; drives playback volume',
              bounce_rate     Float32  COMMENT 'share of playbacks that stop early',
              is_original     UInt8    COMMENT '1 = studio original, 0 = licensed library'
            ) ENGINE = MergeTree ORDER BY title_id
        """),
        ("load from IMDb", f"""
            INSERT INTO catalog
            SELECT tconst, primaryTitle, titleType, year, runtime_seconds,
                   averageRating, numVotes, popularity_rank + 12,
                   0.44 + 0.22 * (cityHash64(tconst) % 1000) / 1000 AS bounce_rate,
                   0 AS is_original
            FROM (
              SELECT tconst, primaryTitle, titleType, year,
                     runtime_min * 60 AS runtime_seconds,
                     averageRating, numVotes,
                     row_number() OVER (ORDER BY numVotes DESC) AS popularity_rank
              FROM (
                SELECT tconst, titleType, primaryTitle,
                       toUInt16OrNull(startYear) AS year,
                       toUInt16OrNull(runtimeMinutes) AS runtime_min
                FROM url('https://datasets.imdbws.com/title.basics.tsv.gz','TSVWithNames',
                  'tconst String, titleType String, primaryTitle String, originalTitle String,
                   isAdult String, startYear String, endYear String, runtimeMinutes String,
                   genres String')
                WHERE titleType IN ('movie','tvSeries','tvMiniSeries')
              ) AS b
              INNER JOIN (
                SELECT tconst, averageRating, numVotes
                FROM url('https://datasets.imdbws.com/title.ratings.tsv.gz','TSVWithNames',
                  'tconst String, averageRating Float32, numVotes UInt32')
              ) AS r USING (tconst)
              WHERE runtime_min BETWEEN 20 AND 240 AND year >= 1990
                AND numVotes >= {MIN_VOTES}
            )
        """),
        ("seed studio originals", """
            INSERT INTO catalog VALUES
             ('org001','Nightshade Parish','tvSeries',2024,3300,7.8,0,1,0.81,1),
             ('org002','The Long Quiet','tvSeries',2024,3180,8.4,0,2,0.47,1),
             ('org003','Harbour & Vine','tvSeries',2024,2820,7.6,0,3,0.55,1),
             ('org004','Ashfall County','tvSeries',2024,3420,8.1,0,4,0.51,1),
             ('org005','The Gilded Hour','tvSeries',2024,3600,7.4,0,5,0.63,1),
             ('org006','Salt & Stone','tvMiniSeries',2024,3000,8.6,0,6,0.44,1),
             ('org007','Reverb','tvSeries',2024,2700,7.1,0,7,0.68,1),
             ('org008','The Understudy','tvSeries',2024,3120,7.9,0,8,0.52,1),
             ('org009','Cold Harbor Lane','tvSeries',2024,3300,7.3,0,9,0.60,1),
             ('org010','Wildflower Season','tvMiniSeries',2024,3540,8.2,0,10,0.49,1),
             ('org011','The Cartographer','tvSeries',2024,2940,7.7,0,11,0.57,1),
             ('org012','Fathom','tvSeries',2024,3240,7.0,0,12,0.71,1)
        """),
    ],
    "policy": [
        ("drop", "DROP TABLE IF EXISTS metric_policy"),
        ("create", """
            CREATE TABLE metric_policy (
              metric_name     String,
              definition_sql  String,
              threshold       String,
              effective_from  Date,
              source_document String,
              is_renewal_basis UInt8
            ) ENGINE = MergeTree ORDER BY metric_name
        """),
        ("seed", """
            INSERT INTO metric_policy VALUES
             ('view_playback_start', 'count()', 'any playback start',
              '2019-01-01', 'Analytics Handbook s2.1 (legacy)', 0),
             ('view_2min', 'countIf(watched_seconds >= 120)', 'watched_seconds >= 120',
              '2022-04-01', 'Analytics Handbook s2.4', 0),
             ('view_completion_90', 'countIf(watched_seconds >= 0.9 * runtime_seconds)',
              'watched_seconds >= 0.9 * runtime_seconds',
              '2024-01-01', 'Renewal Policy s4.2 -- BASIS FOR RENEWAL DECISIONS', 1),
             ('unique_viewers', 'uniqExact(user_id)', 'distinct user_id',
              '2022-04-01', 'Analytics Handbook s3.1', 0),
             ('unique_sessions', 'uniqExact(session_id)', 'distinct session_id',
              '2022-04-01', 'Analytics Handbook s3.2', 0)
        """),
    ],
    "telemetry": [
        ("drop", "DROP TABLE IF EXISTS playback_events"),
        ("create", """
            CREATE TABLE playback_events (
              event_id        UUID,
              title_id        String,
              user_id         UInt64,
              session_id      UInt64,
              started_at      DateTime,
              watched_seconds UInt32,
              runtime_seconds UInt32,
              device          LowCardinality(String),
              country         LowCardinality(String),
              is_autoplay     UInt8
            ) ENGINE = MergeTree
            PARTITION BY toYYYYMM(started_at)
            ORDER BY (title_id, started_at)
        """),
    ],
}

# The catalog is only ~4.7k rows, so it is inlined as constant arrays and indexed by
# hash. Joining 500M generated rows against it instead exceeds the memory ceiling on a
# single-replica service (observed: 7.2 GiB, code 241). Batching keeps peak memory flat.
_TELEMETRY_BATCH = """
INSERT INTO playback_events
SELECT generateUUIDv4(),
       slate[idx].2,
       cityHash64(number, 'user') % 4000000,
       cityHash64(number, 'sess'),
       toDateTime('2026-03-01 00:00:00') + toIntervalSecond(number % 15897600),
       least(
         if(randCanonical(1) < slate[idx].4,
            toUInt32(8 + 232 * pow(randCanonical(2), 1.4)),
            toUInt32(slate[idx].3 * (0.45 + 0.55 * pow(randCanonical(2), 0.7)))),
         slate[idx].3),
       slate[idx].3,
       ['tv','mobile','web','tablet'][1 + (cityHash64(number,'dev') % 4)],
       ['US','GB','DE','ID','BR','IN','JP','FR'][1 + (cityHash64(number,'geo') % 8)],
       randCanonical(3) < 0.55
FROM (
  SELECT number,
         -- ONE array of tuples, explicitly sorted. Fetching title/runtime/bounce as three
         -- separate groupArray() calls silently misaligns them: ClickHouse drops the inner
         -- ORDER BY because an aggregate is assumed not to care about row order, so each
         -- array comes back in a different order and titles inherit another title's bounce
         -- rate. It fails with no error -- the only symptom was a title with bounce 0.81
         -- showing a *lower* early-stop rate than one with 0.55.
         (SELECT arraySort(t -> t.1,
                   groupArray((popularity_rank, title_id, runtime_seconds, bounce_rate)))
          FROM catalog) AS slate,
         -- Two populations, deliberately shaped differently. Originals (ranks 1-12) are the
         -- current slate, promoted together, so volumes are comparable. A steep power-law
         -- here gave the top title ~4x the runner-up's playbacks, which swamped the
         -- completion penalty and erased the metric-definition trap the demo turns on.
         -- The licensed library (ranks 13+) is a real long tail, so it stays steep.
         if(randCanonical(5) < {originals_share},
            1 + least(11, toUInt32(12 * pow(randCanonical(4), 1.3))),
            13 + least(length(slate) - 13,
                       toUInt32((length(slate) - 12) * pow(randCanonical(6), 3.0)))) AS idx
  FROM numbers({start}, {count})
)
"""

# Share of playbacks going to the current originals slate.
ORIGINALS_SHARE = 0.40

BATCH_ROWS = 25_000_000
_BATCHES = TELEMETRY_ROWS // BATCH_ROWS
for _i in range(_BATCHES):
    STAGES["telemetry"].append((
        f"generate {_i + 1}/{_BATCHES}",
        _TELEMETRY_BATCH.format(start=_i * BATCH_ROWS, count=BATCH_ROWS,
                                originals_share=ORIGINALS_SHARE),
    ))



def succeeded(text: str) -> bool:
    """A successful run_query returns JSON. Failures AND policy refusals -- such as
    "Destructive operations are not allowed" -- come back as plain prose. Checking
    for one known error prefix is not enough: a refusal would read as success and
    leave the warehouse half-built with no error anywhere.
    """
    try:
        json.loads(text)
        return True
    except (json.JSONDecodeError, TypeError):
        return False


async def run_stage(name: str) -> int:
    steps = STAGES[name]
    toolset = loader_toolset()
    tools = {t.name: t for t in await toolset.get_tools()}
    try:
        for label, sql in steps:
            started = time.monotonic()
            # Send SQL with newlines intact. Flattening it onto one line makes any
            # "--" comment swallow the entire rest of the query, silently truncating
            # it into something that still parses but means something else.
            result = await tools["run_query"].run_async(
                args={"query": sql.strip()}, tool_context=None)
            text = result["content"][0]["text"]
            took = time.monotonic() - started
            if not succeeded(text):
                print(f"FAIL  [{name}/{label}] after {took:.1f}s\n      {text[:400]}")
                return 1
            print(f"OK    [{name}/{label}] {took:.1f}s")
        return 0
    finally:
        await toolset.close()


async def verify() -> int:
    toolset = loader_toolset()
    tools = {t.name: t for t in await toolset.get_tools()}
    checks = {
        "catalog rows": "SELECT count() FROM catalog",
        "policy rows": "SELECT count() FROM metric_policy",
        "telemetry rows": "SELECT count() FROM playback_events",
    }
    for label, sql in checks.items():
        r = await tools["run_query"].run_async(args={"query": sql}, tool_context=None)
        print(f"  {label:<18} {r['content'][0]['text'][:120]}")
    await toolset.close()
    return 0


if __name__ == "__main__":
    stage = sys.argv[1] if len(sys.argv) > 1 else ""
    if stage == "verify":
        sys.exit(asyncio.run(verify()))
    if stage not in STAGES:
        print(f"usage: build_warehouse.py [{'|'.join(STAGES)}|verify]")
        sys.exit(2)
    sys.exit(asyncio.run(run_stage(stage)))
