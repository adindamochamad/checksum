"""One question, sent to both agents, returned as one comparable object.

This is what the split-screen renders and what the evaluation harness scores, so
both sides are collected the same way: what was asked, what SQL actually ran, what
came back, and how long it took. The receipts are not a debug view. They are the
product -- an assertion that two agents disagree is worth nothing without the two
queries sitting underneath it.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from google.adk.runners import InMemoryRunner
from google.genai import types

from .agents import adjudicator_agent, checksum_agent, naive_agent
from .policy import MetricDefinition
from .query import Result
from .variants import Scope, Variant
from .verify import Verdict, VariantRun, adjudicate

APP = "checksum"


async def _say(agent: Any, prompt: str, *, user: str = "demo") -> tuple[str, list[dict]]:
    """Run an ADK agent to completion. Returns its final text and every tool call."""
    runner = InMemoryRunner(agent=agent, app_name=APP)
    session = await runner.session_service.create_session(app_name=APP, user_id=user)
    calls: list[dict] = []
    final = ""
    async for event in runner.run_async(
        user_id=user,
        session_id=session.id,
        new_message=types.Content(role="user", parts=[types.Part(text=prompt)]),
    ):
        for call in event.get_function_calls() or []:
            calls.append({"tool": call.name, "args": dict(call.args or {})})
        if event.is_final_response() and event.content and event.content.parts:
            final = "".join(p.text or "" for p in event.content.parts)
    return final.strip(), calls


@dataclass
class Side:
    label: str
    answer: str
    queries: list[str] = field(default_factory=list)
    seconds: float = 0.0
    leader: str = ""


@dataclass
class Comparison:
    question: str
    naive: Side
    checksum: Side
    verdict: Verdict
    runs: list[VariantRun] = field(default_factory=list)

    @property
    def agents_disagree(self) -> bool:
        return bool(
            self.naive.leader
            and self.checksum.leader
            and self.naive.leader != self.checksum.leader
        )


def _readings_brief(runs: list[VariantRun], scope: Scope) -> str:
    lines = []
    for run in runs:
        if not run.ok or not run.variant.ranks:
            continue
        top = run.ranking()[:3]
        value = run.result.rows[0][1] if run.result.rows else "?"
        lines.append(
            f"- {run.variant.title}: 1st {top[0]} ({value:,}) "
            f"| then {', '.join(top[1:]) if len(top) > 1 else '-'}"
        )
    return "\n".join(lines) or "- none"


def _guards_brief(runs: list[VariantRun]) -> str:
    lines = []
    for run in runs:
        if run.ok and not run.variant.ranks and run.result.rows:
            row = dict(zip(run.result.columns, run.result.rows[0]))
            lines.append(f"- {run.variant.title}: {row}")
    return "\n".join(lines) or "- none"


async def compare(
    question: str,
    tools: dict[str, Any],
    naive_toolset: Any,
    variants: list[Variant],
    policy: list[MetricDefinition],
    scope: Scope,
) -> Comparison:
    """Ask both agents the same thing and return the two answers side by side."""

    started = time.monotonic()
    naive_answer, naive_calls = await _say(naive_agent(naive_toolset), question)
    naive_side = Side(
        label="Standard agent",
        answer=naive_answer,
        queries=[c["args"].get("query", "") for c in naive_calls if c["tool"] == "run_query"],
        seconds=time.monotonic() - started,
    )

    started = time.monotonic()
    # Planner and fan-out run as one ADK SequentialAgent; the readings land in state.
    await _say(checksum_agent(variants, tools, policy), question)

    from .verify import run_variants  # local import keeps the module graph flat

    runs = await run_variants(tools, variants, names=scope.titles)
    verdict = adjudicate(runs, policy)

    adjudicator = adjudicator_agent(
        verdict_headline=verdict.headline,
        verdict_detail=verdict.detail,
        readings=_readings_brief(runs, scope),
        guards=_guards_brief(runs),
    )
    prose, _ = await _say(adjudicator, question)

    checksum_side = Side(
        label="Checksum",
        answer=prose,
        queries=[r.variant.sql for r in runs if r.ok],
        seconds=time.monotonic() - started,
        leader=verdict.policy_leader,
    )
    naive_side.leader = verdict.naive_leader

    return Comparison(
        question=question,
        naive=naive_side,
        checksum=checksum_side,
        verdict=verdict,
        runs=runs,
    )


def receipts(comparison: Comparison) -> list[dict[str, Any]]:
    """Flat, renderable evidence: every reading, its SQL, and what it returned."""
    out: list[dict[str, Any]] = []
    for run in comparison.runs:
        entry: dict[str, Any] = {
            "key": run.variant.key,
            "title": run.variant.title,
            "category": run.variant.category,
            "rationale": run.variant.rationale,
            "policy_source": run.variant.policy_source,
            "sql": run.variant.sql,
            "ok": run.ok,
        }
        if run.ok:
            result: Result = run.result
            entry["columns"] = result.columns
            entry["rows"] = result.rows[:12]
            entry["row_count"] = result.row_count
            if run.variant.ranks:
                entry["ranking"] = run.ranking()
        else:
            entry["error"] = run.error
        out.append(entry)
    return out
