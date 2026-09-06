"""Two agents, one question.

The left-hand agent is what this stack produces by default: one LlmAgent, one MCP
tool, one query. It is not a strawman -- it answers the question as asked, and it
is what most agents built on Gemini plus a ClickHouse MCP server will return.

Checksum is a SequentialAgent:

    Planner (LlmAgent)      -- reads the question against the studio's written
                               metric policy and says which readings are in scope.
    ParallelAgent           -- one VariantAgent per reading, fanned out at once.
                               Each is a real MCP call against real ClickHouse.
    Adjudicator (LlmAgent)  -- explains a verdict it is not allowed to compute.

That last split is deliberate. The verdict -- stable, unstable, or refused -- is
decided by `verify.adjudicate`, in Python, from the numbers. The model's job is to
say it in English and cite the receipts. A tool whose entire pitch is "the model
sounded confident and was wrong" cannot hand the model the final call.
"""

from __future__ import annotations

from typing import Any, AsyncGenerator

from google.adk.agents import BaseAgent, LlmAgent, ParallelAgent, SequentialAgent
from google.adk.agents.invocation_context import InvocationContext
from google.adk.events import Event
from google.genai import types

from .guardrails import read_only_guard
from .policy import MetricDefinition, policy_briefing
from .query import QueryError, run_sql
from .variants import Scope, Variant

MODEL = "gemini-2.5-flash"

NAIVE_INSTRUCTION = """\
You are a data analyst agent with access to a ClickHouse warehouse.

Answer the user's question by querying the `playback_events` table, which has one
row per playback: title_id, user_id, session_id, started_at, watched_seconds,
runtime_seconds, device, country, is_autoplay. Title names are in `catalog`
(title_id, title, is_original).

Use the run_query tool. Report the ranking you find, with the numbers. Be concise
and confident.
"""

PLANNER_INSTRUCTION = """\
You decide how a question about studio analytics should be read before anyone
answers it.

The studio's written metric definitions:
{policy}

The question may look like it has one answer. Your job is to notice that the phrase
"views" (or "most watched", "top performing", "biggest") has more than one defensible
definition, and that the studio's renewal policy binds to a specific one.

Reply with two short paragraphs, no lists:
1. Which definitions could reasonably be used to answer this question.
2. Which definition the renewal policy requires, and what would go wrong if a
   different one were used by mistake.

Do not run any queries. Do not give a ranking. You are setting up the check, not
performing it.
"""

ADJUDICATOR_INSTRUCTION = """\
You report the outcome of a verification that has already been performed. The
verdict was computed from the numbers, not by you, and you may not change it.

Verdict: {verdict_headline}
Detail: {verdict_detail}

Readings that ran:
{readings}

Data-quality guards:
{guards}

Write at most 120 words for an executive who is about to make a renewal decision.
State what the standard reading says, what the policy-mandated reading says, and
whether those disagree. If they disagree, name the title that moves and say where
it moves to. Cite the policy document by name.

Do not soften the verdict. Do not add numbers that are not above. If the verdict is
a refusal, say plainly that the question cannot be answered from this data and why.
"""


def naive_agent(toolset: Any) -> LlmAgent:
    """The standard build: one agent, one tool, one query, full confidence."""
    return LlmAgent(
        name="standard_agent",
        model=MODEL,
        description="A conventional text-to-SQL agent over ClickHouse.",
        instruction=NAIVE_INSTRUCTION,
        tools=[toolset],
        before_tool_callback=read_only_guard,
    )


class VariantAgent(BaseAgent):
    """One reading of the question. Deterministic by design.

    The SQL is not model-generated. Each variant encodes a definition somebody
    wrote down, so the comparison between readings means something; if a model
    invented all eight, disagreement between them would only measure its variance.
    """

    model_config = {"arbitrary_types_allowed": True}

    variant: Variant
    tools: dict[str, Any]

    async def _run_async_impl(
        self, ctx: InvocationContext
    ) -> AsyncGenerator[Event, None]:
        state = ctx.session.state
        try:
            result = await run_sql(self.tools, self.variant.sql)
            state[f"reading:{self.variant.key}"] = {
                "columns": result.columns,
                "rows": result.rows,
                "sql": result.sql,
            }
            summary = f"{self.variant.title}: {result.row_count} rows"
        except QueryError as exc:
            state[f"reading:{self.variant.key}"] = {"error": str(exc)}
            summary = f"{self.variant.title}: failed"

        yield Event(
            author=self.name,
            content=types.Content(role="model", parts=[types.Part(text=summary)]),
        )


def verification_fanout(variants: list[Variant], tools: dict[str, Any]) -> ParallelAgent:
    """Every reading at once. This is the verification budget being spent."""
    return ParallelAgent(
        name="verification_fanout",
        description="Runs every defensible reading of the question concurrently.",
        sub_agents=[
            VariantAgent(
                name=f"reading_{v.key}",
                description=v.rationale,
                variant=v,
                tools=tools,
            )
            for v in variants
        ],
    )


def planner_agent(policy: list[MetricDefinition]) -> LlmAgent:
    return LlmAgent(
        name="planner",
        model=MODEL,
        description="Reads the question against the studio's written metric policy.",
        instruction=PLANNER_INSTRUCTION.format(policy=policy_briefing(policy)),
    )


def adjudicator_agent(
    verdict_headline: str, verdict_detail: str, readings: str, guards: str
) -> LlmAgent:
    return LlmAgent(
        name="adjudicator",
        model=MODEL,
        description="States the verdict and cites the receipts.",
        instruction=ADJUDICATOR_INSTRUCTION.format(
            verdict_headline=verdict_headline,
            verdict_detail=verdict_detail,
            readings=readings,
            guards=guards,
        ),
    )


def checksum_agent(
    variants: list[Variant],
    tools: dict[str, Any],
    policy: list[MetricDefinition],
) -> SequentialAgent:
    """Planner, then fan-out. Adjudication is assembled once the numbers exist."""
    return SequentialAgent(
        name="checksum",
        description="Verifies an analytics answer before anyone decides on it.",
        sub_agents=[planner_agent(policy), verification_fanout(variants, tools)],
    )


def scope_summary(scope: Scope) -> str:
    return f"{scope.label} ({len(scope.titles)} titles)"
