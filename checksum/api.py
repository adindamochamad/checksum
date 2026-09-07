"""HTTP surface. One question in, a live verification out.

Two datasets are served, and the second is the important one.

  studio  -- the simulated warehouse, where the disagreement was planted on purpose.
  github  -- ClickHouse's public SQL Playground, ~11 billion real GitHub events this
             project never touched. The instability there is GitHub's, not ours.

Judging happens weeks after submission, on a link, unattended. Two consequences run
through everything below:

  Setup happens once, at startup. Resolving catalogs and opening MCP sessions costs
  seconds; paying that per request would make the first click look broken.

  The free Gemini tier allows twenty requests per day. Preset runs are cached to disk,
  and both model calls are wrapped -- the verification itself needs no model at all, so
  a spent quota costs the prose and nothing else.
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
from . import public_data
from .agents import adjudicator_agent, naive_agent
from .compare import _guards_brief, _readings_brief, _say, receipts
from .policy import load_policy
from .query import tools_for
from .variants import Scope, Variant, all_variants, resolve_scope
from .verify import adjudicate, audit_foreign_sql, stream_variants
from .warehouse import OWN_WAREHOUSE, PLAYGROUND, Warehouse, read_only_toolset

REPLAY_DELAY = float(os.environ.get("CHECKSUM_REPLAY_DELAY", "0.12"))

STUDIO_QUESTIONS = [
    "Which of our 2026 originals should we renew?",
    "What was our most watched original this year?",
]


@dataclass
class Dataset:
    key: str
    label: str
    blurb: str
    warehouse: Warehouse
    questions: list[str]
    toolset: Any = None
    naive_toolset: Any = None
    tools: dict[str, Any] = field(default_factory=dict)
    policy: list = field(default_factory=list)
    scope: Scope = field(default_factory=Scope)
    variants: list[Variant] = field(default_factory=list)


@dataclass
class Engine:
    datasets: dict[str, Dataset] = field(default_factory=dict)
    cache: dict[str, list[dict]] = field(default_factory=dict)
    ready: bool = False

    def dataset_for(self, question: str, requested: str = "") -> Dataset:
        if requested and requested in self.datasets:
            return self.datasets[requested]
        for dataset in self.datasets.values():
            if question in dataset.questions:
                return dataset
        return self.datasets["studio"]


engine = Engine()


def _presets() -> list[dict[str, str]]:
    return [
        {"question": q, "dataset": d.key, "label": d.label}
        for d in engine.datasets.values()
        for q in d.questions
    ]


def _cache_key(dataset_key: str, question: str) -> str:
    return f"{dataset_key}::{question}"


@asynccontextmanager
async def lifespan(app: FastAPI):
    studio = Dataset(
        key="studio",
        label="Studio warehouse",
        blurb="500M simulated playback events over a real IMDb catalog. "
              "The disagreement here was planted on purpose.",
        warehouse=OWN_WAREHOUSE,
        questions=list(STUDIO_QUESTIONS),
    )
    studio.toolset = read_only_toolset(studio.warehouse, timeout=120.0)
    studio.naive_toolset = read_only_toolset(studio.warehouse, timeout=120.0)
    studio.tools = await tools_for(studio.toolset)
    studio.policy = await load_policy(studio.tools)
    studio.scope = await resolve_scope(studio.tools, Scope())
    studio.variants = all_variants(studio.scope, studio.policy)

    github = Dataset(
        key="github",
        label="GitHub, public data",
        blurb="~11 billion real GitHub events on ClickHouse's public Playground. "
              "This project never touched it, and there is no metric policy to appeal to.",
        warehouse=PLAYGROUND,
        questions=[public_data.QUESTION],
        scope=public_data.GITHUB_SCOPE,
        variants=public_data.github_variants(),
    )
    github.toolset = read_only_toolset(github.warehouse, timeout=180.0)
    github.naive_toolset = read_only_toolset(github.warehouse, timeout=180.0)
    github.tools = await tools_for(github.toolset)

    engine.datasets = {"studio": studio, "github": github}
    engine.cache = warm_cache.load()
    engine.ready = True
    try:
        yield
    finally:
        engine.ready = False
        for dataset in engine.datasets.values():
            for toolset in (dataset.toolset, dataset.naive_toolset):
                if toolset:
                    await toolset.close()


app = FastAPI(title="Checksum", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"]
)


class Ask(BaseModel):
    question: str
    dataset: str = ""


@app.get("/api/health")
async def health() -> dict[str, Any]:
    return {
        "ready": engine.ready,
        "datasets": [
            {
                "key": d.key,
                "label": d.label,
                "blurb": d.blurb,
                "readings": len(d.variants),
                "has_policy": bool(d.policy),
            }
            for d in engine.datasets.values()
        ],
        "cached": sorted(engine.cache),
    }


@app.get("/api/presets")
async def presets() -> dict[str, Any]:
    return {"presets": _presets()}


def _event(kind: str, **payload: Any) -> dict[str, str]:
    return {"event": "message", "data": json.dumps({"type": kind, **payload})}


async def _verify_stream(question: str, dataset: Dataset) -> AsyncIterator[dict[str, str]]:
    collected: list[dict[str, str]] = []

    def emit(kind: str, **payload: Any) -> dict[str, str]:
        event = _event(kind, **payload)
        collected.append(event)
        return event

    yield emit(
        "scope",
        question=question,
        dataset=dataset.key,
        dataset_label=dataset.label,
        blurb=dataset.blurb,
        has_policy=bool(dataset.policy),
        label=dataset.scope.label,
        readings=len(dataset.variants),
    )

    started = time.monotonic()
    yield emit("naive_start")
    try:
        answer, calls = await _say(naive_agent(dataset.naive_toolset), question)
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

    audit = audit_foreign_sql(naive_sql[-1] if naive_sql else "", dataset.policy)
    yield emit(
        "naive_audit",
        sql=audit.sql, measure=audit.measure, in_policy=audit.in_policy, note=audit.note,
    )

    started = time.monotonic()
    yield emit("verification_start", readings=len(dataset.variants))
    runs = []
    async for run in stream_variants(
        dataset.tools, dataset.variants, names=dataset.scope.titles
    ):
        runs.append(run)
        yield emit(
            "reading",
            key=run.variant.key, title=run.variant.title, category=run.variant.category,
            rationale=run.variant.rationale, ok=run.ok,
            leader=(run.ranking() or [""])[0], error=run.error[:200],
        )
    fanout_seconds = round(time.monotonic() - started, 2)

    verdict = adjudicate(runs, dataset.policy)
    yield emit(
        "verdict",
        stable=verdict.stable, refused=verdict.refused, headline=verdict.headline,
        detail=verdict.detail, naive_leader=verdict.naive_leader,
        policy_leader=verdict.policy_leader, displacement=verdict.displacement,
        seconds=fanout_seconds,
    )

    adjudicator = adjudicator_agent(
        verdict_headline=verdict.headline,
        verdict_detail=verdict.detail,
        readings=_readings_brief(runs, dataset.scope),
        guards=_guards_brief(runs),
    )
    try:
        prose, _ = await _say(adjudicator, question)
        yield emit("explanation", text=prose)
    except Exception as exc:
        # No explanation event: the verdict block already carries this text, and
        # emitting it again printed the same paragraph twice.
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

    # Stored before the final yield: an async generator stops at its last yield, so
    # anything written after it never runs.
    if question in dataset.questions:
        engine.cache[_cache_key(dataset.key, question)] = list(collected)
        try:
            warm_cache.save(engine.cache)
        except OSError:
            pass

    yield emit("done")


@app.post("/api/ask")
async def ask(body: Ask) -> EventSourceResponse:
    if not engine.ready:
        raise HTTPException(503, "Warehouse connection is still warming up.")

    question = body.question.strip()
    if not question:
        raise HTTPException(400, "Ask something.")

    dataset = engine.dataset_for(question, body.dataset.strip())
    cached = engine.cache.get(_cache_key(dataset.key, question))
    if cached:
        async def replay() -> AsyncIterator[dict[str, str]]:
            for event in cached:
                yield event
                if REPLAY_DELAY:
                    await asyncio.sleep(REPLAY_DELAY)

        return EventSourceResponse(replay())

    return EventSourceResponse(_verify_stream(question, dataset))


async def warm() -> None:
    """Run every preset once so the first judge never waits for a cold path."""
    for preset in _presets():
        dataset = engine.datasets[preset["dataset"]]
        async for _ in _verify_stream(preset["question"], dataset):
            pass


# Mounted last: the API routes above must win, and everything else is the page.
_WEB = pathlib.Path(__file__).resolve().parent.parent / "web"
if _WEB.is_dir():
    app.mount("/", StaticFiles(directory=_WEB, html=True), name="web")
