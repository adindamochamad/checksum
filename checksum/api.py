"""HTTP surface. One question in, a live verification out.

Judging happens weeks after submission, on a link, unattended. Two things follow
from that and both are load-bearing here:

  Setup happens once, at startup. Resolving the catalog, loading the policy and
  opening the MCP session cost seconds; paying that per request would make the
  first click look broken.

  Preset questions are cached after their first run. The free Gemini tier rate
  limits, and a judge who opens the demo at the same moment as another judge must
  not meet a 429. A cached verification is a real verification that already ran --
  its SQL and row counts are the ones that executed, not a canned script.
"""

from __future__ import annotations

import asyncio
import json
import os
import pathlib
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any, AsyncIterator

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse

from . import cache as warm_cache
from .agents import adjudicator_agent, naive_agent
from .compare import _guards_brief, _readings_brief, _say, receipts
from .policy import load_policy
from .query import tools_for
from .variants import Scope, all_variants, resolve_scope
from .verify import adjudicate, audit_foreign_sql, stream_variants
from .warehouse import OWN_WAREHOUSE, read_only_toolset

# Replay pacing. A cached run still reads as a verification happening rather than a
# wall of text landing at once. Set to 0 for screenshots and automated capture.
REPLAY_DELAY = float(os.environ.get("CHECKSUM_REPLAY_DELAY", "0.12"))

PRESETS = [
    "Which of our 2026 originals should we renew?",
    "What was our most watched original this year?",
    "Which original has the most engaged audience?",
]


@dataclass
class Engine:
    toolset: Any = None
    naive_toolset: Any = None
    tools: dict[str, Any] = field(default_factory=dict)
    policy: list = field(default_factory=list)
    scope: Scope = field(default_factory=Scope)
    variants: list = field(default_factory=list)
    cache: dict[str, list[dict]] = field(default_factory=dict)
    ready: bool = False


engine = Engine()


@asynccontextmanager
async def lifespan(app: FastAPI):
    engine.toolset = read_only_toolset(OWN_WAREHOUSE, timeout=120.0)
    engine.naive_toolset = read_only_toolset(OWN_WAREHOUSE, timeout=120.0)
    engine.tools = await tools_for(engine.toolset)
    engine.policy = await load_policy(engine.tools)
    engine.scope = await resolve_scope(engine.tools, Scope())
    engine.variants = all_variants(engine.scope, engine.policy)
    # Warming costs quota, so it must survive a restart.
    engine.cache = warm_cache.load()
    engine.ready = True
    try:
        yield
    finally:
        engine.ready = False
        for toolset in (engine.toolset, engine.naive_toolset):
            if toolset:
                await toolset.close()


app = FastAPI(title="Checksum", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


class Ask(BaseModel):
    question: str


@app.get("/api/health")
async def health() -> dict[str, Any]:
    return {
        "ready": engine.ready,
        "titles": len(engine.scope.titles),
        "definitions": len(engine.policy),
        "readings": len(engine.variants),
        "warehouse": OWN_WAREHOUSE.describe,
        "cached": sorted(engine.cache),
    }


@app.get("/api/presets")
async def presets() -> dict[str, Any]:
    return {"questions": PRESETS, "scope": engine.scope.label}


def _event(kind: str, **payload: Any) -> dict[str, str]:
    return {"event": "message", "data": json.dumps({"type": kind, **payload})}


async def _verify_stream(question: str) -> AsyncIterator[dict[str, str]]:
    """Emit the verification as it happens, then remember it if it is a preset."""
    collected: list[dict[str, str]] = []

    def emit(kind: str, **payload: Any) -> dict[str, str]:
        event = _event(kind, **payload)
        collected.append(event)
        return event

    yield emit(
        "scope",
        question=question,
        titles=len(engine.scope.titles),
        label=engine.scope.label,
        readings=len(engine.variants),
    )

    # Left panel: the standard build, answering the question as literally asked.
    #
    # Every Gemini call below is wrapped. The free tier allows twenty requests a day,
    # and a judge meeting a 429 must still see the verification -- which needs no
    # model at all. The readings, the verdict and the receipts are pure ClickHouse.
    started = time.monotonic()
    yield emit("naive_start")
    try:
        answer, calls = await _say(naive_agent(engine.naive_toolset), question)
    except Exception as exc:
        answer, calls = "", []
        yield emit("model_unavailable", where="standard_agent", reason=str(exc)[:200])
    naive_sql = [c["args"].get("query", "") for c in calls if c["tool"] == "run_query"]
    yield emit(
        "naive_done",
        answer=answer,
        queries=naive_sql,
        seconds=round(time.monotonic() - started, 2),
    )

    # Audit what it actually ran, not a stand-in for what it might have run. This is
    # what keeps the comparison from being a strawman: the SQL below executed.
    audit = audit_foreign_sql(naive_sql[-1] if naive_sql else "", engine.policy)
    yield emit(
        "naive_audit",
        sql=audit.sql,
        measure=audit.measure,
        in_policy=audit.in_policy,
        note=audit.note,
    )

    # Right panel: every defensible reading at once.
    started = time.monotonic()
    yield emit("verification_start", readings=len(engine.variants))
    runs = []
    async for run in stream_variants(
        engine.tools, engine.variants, names=engine.scope.titles
    ):
        runs.append(run)
        yield emit(
            "reading",
            key=run.variant.key,
            title=run.variant.title,
            category=run.variant.category,
            rationale=run.variant.rationale,
            ok=run.ok,
            leader=(run.ranking() or [""])[0],
            error=run.error[:200],
        )
    fanout_seconds = round(time.monotonic() - started, 2)

    verdict = adjudicate(runs, engine.policy)
    yield emit(
        "verdict",
        stable=verdict.stable,
        refused=verdict.refused,
        headline=verdict.headline,
        detail=verdict.detail,
        naive_leader=verdict.naive_leader,
        policy_leader=verdict.policy_leader,
        displacement=verdict.displacement,
        seconds=fanout_seconds,
    )

    adjudicator = adjudicator_agent(
        verdict_headline=verdict.headline,
        verdict_detail=verdict.detail,
        readings=_readings_brief(runs, engine.scope),
        guards=_guards_brief(runs),
    )
    try:
        prose, _ = await _say(adjudicator, question)
        yield emit("explanation", text=prose)
    except Exception as exc:
        # The verdict was never the model's to decide, so losing the model costs the
        # prose and nothing else. No explanation event here: the verdict block above
        # already carries this text, and emitting it again printed it twice.
        prose = f"{verdict.headline}\n\n{verdict.detail}"
        yield emit("model_unavailable", where="adjudicator", reason=str(exc)[:200])

    from .compare import Comparison, Side

    yield emit(
        "receipts",
        items=receipts(
            Comparison(
                question=question,
                naive=Side("Standard agent", answer),
                checksum=Side("Checksum", prose),
                verdict=verdict,
                runs=runs,
            )
        ),
    )
    # Store before the final yield, not after. An async generator stops at its last
    # yield -- the consumer never asks for another item, so nothing below it runs.
    # Written afterwards, this silently cached nothing and every judge paid the full
    # 35s live path.
    if question in PRESETS:
        engine.cache[question] = list(collected)
        try:
            warm_cache.save(engine.cache)
        except OSError:
            pass  # An unwritable disk must not fail a good verification.

    yield emit("done")


@app.post("/api/ask")
async def ask(body: Ask) -> EventSourceResponse:
    if not engine.ready:
        raise HTTPException(503, "Warehouse connection is still warming up.")

    question = body.question.strip()
    if not question:
        raise HTTPException(400, "Ask something.")

    cached = engine.cache.get(question)
    if cached:
        async def replay() -> AsyncIterator[dict[str, str]]:
            for event in cached:
                yield event
                # Paced so a cached run still reads as a verification happening,
                # rather than a wall of text appearing at once.
                if REPLAY_DELAY:
                    await asyncio.sleep(REPLAY_DELAY)

        return EventSourceResponse(replay())

    return EventSourceResponse(_verify_stream(question))


async def warm() -> None:
    """Run every preset once so the first judge never waits for a cold path."""
    for question in PRESETS:
        async for _ in _verify_stream(question):
            pass


# Mounted last: the API routes above must win, and everything else is the page.
_WEB = pathlib.Path(__file__).resolve().parent.parent / "web"
if _WEB.is_dir():
    app.mount("/", StaticFiles(directory=_WEB, html=True), name="web")
