# Checksum

**The query ran fine. The number was wrong.**

Checksum verifies a studio analytics answer before anyone decides on it. For one
question it runs nine different readings against ClickHouse at once — competing metric
definitions, competing deduplication, a rolling window, and two data-quality guards —
and reports whether the answer survives all of them.

When the ranking changes depending on which definition you pick, that *is* the finding.

Built for the [Agentic Cinema Hackathon](https://agentic-cinema.devpost.com/),
**ClickHouse partner track**.

---

## The problem

> What happens when the SQL is perfectly correct, but the number is wrong by the
> industry's definition — and someone renews a $200M series on it?

This is not a bug. The query runs. There is no error. The output is clean and the agent
is confident. What is wrong is the *definition*.

In streaming, a "view" is counted several incompatible ways: a playback start, two
minutes watched, or 90% completion. Netflix changed its own definition in 2024 and broke
historical comparability across the industry. Nielsen counts differently again.

A text-to-SQL agent answers with whichever definition is easiest to express in SQL — not
the one the studio's renewal policy actually binds to. The result is a confident,
plausible, wrong ranking that nothing flags, because nothing failed.

---

## What it does

Ask both agents the same question. The left panel is a conventional agent: Gemini plus
the ClickHouse MCP server, one query, one answer. It is not a strawman — it is what this
stack produces by default.

The right panel runs nine readings and adjudicates them.

**On the demo question, the two disagree.** *Nightshade Parish* leads on playback
starts and sits at **rank 9** under the completion-based definition the renewal policy
requires, because **81%** of its playbacks stop inside four minutes. That is autoplay,
not audience.

### Three things that make this more than a second opinion

**It audits the other agent's real SQL.** Checksum reads the query the standard agent
actually executed and classifies its measure against the policy table. In testing, the
standard agent invented `sum(watched_seconds)` — a metric that appears in no handbook —
and ranked on it confidently. Checksum reports: *"The number is real; the definition was
invented at query time."*

**The verdict is not the model's to decide.** Stable, unstable or refused is computed in
Python from the numbers (`checksum/verify.py`). Gemini writes it in English and cites the
receipts. A tool whose entire premise is "the model sounded confident and was wrong"
cannot hand the model the final call.

**It refuses.** When too few readings survive, or a column the question depends on is
unusable, Checksum says the question cannot be answered from this data and names why. A
confident answer built on absent data is the failure this project exists to prevent.

---

## Architecture

```
SequentialAgent  (google-adk)
│
├─ Planner (LlmAgent)      reads the question against the studio's written metric policy
│
├─ ParallelAgent           nine readings, fanned out concurrently.
│   │                      Each sub-agent is a custom BaseAgent making a real MCP call.
│   ├─ any playback start                      ← what a standard agent reaches for
│   ├─ watched ≥ 2 minutes
│   ├─ watched ≥ 90% of runtime                ← what the renewal policy binds to
│   ├─ distinct viewers  /  distinct sessions
│   ├─ distinct viewers who finished
│   ├─ last 90 days only
│   └─ volume + null rates  /  impossible rows ← guards
│
└─ Adjudicator (LlmAgent)  states a verdict it is not allowed to compute
```

**Every reading is a separate call to the ClickHouse MCP server at runtime.** The SQL is
not model-generated: each reading encodes a definition someone wrote down. If a model
invented all nine, disagreement between them would measure its variance rather than the
ambiguity of the question.

The agents cannot write. `read_only_toolset()` has no write authority at all, and a
`before_tool_callback` rejects anything that is not a plain read. An agent that audits
the warehouse should be structurally incapable of changing it.

---

## Stack

| Layer | Technology |
|---|---|
| Model | Gemini 2.5 Flash via Google AI Studio |
| Orchestration | Google Agent Development Kit (`google-adk`) — `SequentialAgent`, `ParallelAgent`, custom `BaseAgent`, `before_tool_callback` |
| Data access | Official ClickHouse MCP server (`mcp-clickhouse`) over stdio |
| Warehouse | ClickHouse Cloud, `ap-southeast-1` |
| Backend | FastAPI + Server-Sent Events |
| Frontend | Vanilla JS, no build step |

---

## The data

**Catalog is real.** 4,699 titles from the
[IMDb non-commercial datasets](https://developer.imdb.com/non-commercial-datasets/) —
films and series since 1990 with at least 50,000 votes. This is the studio's licensed
library.

**The twelve studio originals are invented.** A studio does not renew *The Shawshank
Redemption*, and planting anomalous telemetry on a real, beloved title would not survive
a moment's scrutiny.

**Playback telemetry is simulated at studio scale.** 500,000,000 events, generated
inside ClickHouse — no streaming platform publishes real playback telemetry, so no public
dataset of this kind exists. Sessions are drawn from a two-population mixture: autoplay
bounces and genuine viewers. The metric-definition divergence Checksum detects is present
in the generated data by design, and the per-title bounce rate is the lever that puts it
there.

**The metric policy is authored**, standing in for a studio's internal analytics
handbook. Real ones exist and disagree with each other the same way.

Saying this plainly matters. The point of Checksum is that numbers should arrive with
their provenance attached.

---

## Findings and learnings

Everything below was measured, and several of these reversed a decision.

**"Eight readings in the time others run one" was false.** The original pitch assumed
fan-out would be nearly free. Measured against a real serial baseline on a
single-replica service, the speedup was 1.2×–2.1× across runs — concurrency cannot
invent cores. The claim changed to match the measurement rather than the other way
round. What is true is that each reading is cheap enough to afford nine of them: nine
readings over 500M rows complete in about eight seconds, and the slowest single reading
is a little over three.

**Three modelling bugs failed silently.** ClickHouse folds identical `randCanonical()`
calls into one column, so the branch selector and the branch value shared a draw.
Separate `groupArray()` calls came back in different orders — an inner `ORDER BY` is
dropped under an aggregate — so titles inherited each other's bounce rates. Flattening
SQL onto one line let `--` comments swallow the rest of the query. None raised an error.
The only symptom of the second was a title with a high bounce rate showing a *lower*
early-stop rate than one with a low bounce rate.

**A passing test can sit on top of a completely broken path.** The HTTP gate passed
sixteen checks while the page rendered nothing: `sse-starlette` terminates lines with
CRLF, `httpx.aiter_lines()` normalises that, and the browser's fetch reader does not.
Splitting on `\n\n` matched nothing, so the browser received all twenty events and
discarded them. That is why there is now a browser-level gate.

**The standard agent has to be allowed to be good.** An early version of the demo
question said "2024 originals" while the telemetry began in 2026. The standard agent
noticed and refused to answer — and every other check still passed while the contrast the
whole submission rests on had quietly disappeared. There is now a gate asserting it
answers confidently rather than hedging.

**`mcp-clickhouse` and `google-adk` cannot share an environment.** The former needs
`mcp>=2` through fastmcp 4.x; the latter needs `mcp<2`. Installing both breaks the agent
side silently — `McpToolset` disappears from an import. They never need to meet, because
stdio is a process boundary, so the MCP server gets its own virtualenv.

**Free-tier quota is a design constraint, not an operational detail.** Google AI Studio
allows twenty requests per day per model. Judging runs for two weeks on a public link.
So the verification uses no model at all — readings, verdict and receipts are pure
ClickHouse — both Gemini calls are wrapped, and preset runs are cached to disk. With the
quota fully spent, the page still verifies and says why the prose is missing. This was
tested against a genuinely exhausted quota.

---

## Running it

Two virtualenvs, for the reason given above.

```bash
git clone <repo-url> && cd checksum

# 1. Agent environment
uv venv --python 3.13 .venv
uv pip install --python .venv/bin/python -r requirements.lock

# 2. ClickHouse MCP server, isolated
uv venv --python 3.13 .venv-mcp
uv pip install --python .venv-mcp/bin/python -r requirements-mcp-server.lock

# 3. Credentials
cp .env.example .env        # GOOGLE_API_KEY + your ClickHouse Cloud service

# 4. Build the warehouse (~20 minutes, mostly generating 500M rows)
cd scripts
../.venv/bin/python build_warehouse.py catalog
../.venv/bin/python build_warehouse.py policy
../.venv/bin/python build_warehouse.py telemetry

# 5. Run
cd .. && .venv/bin/python -m uvicorn checksum.api:app --port 8000
```

`pip` cannot resolve this dependency graph — it exits with `resolution-too-deep`. Use
`uv`.

### Docker

```bash
docker build -t checksum .
docker run --env-file .env -p 8080:8080 checksum
```

### Quality gates

Nothing in this project advanced on an unproven claim. Seventy-six checks across five
gates, including a browser-level gate that drives the real page:

```bash
.venv/bin/python scripts/qa.py foundation   # guardrails, policy, swappable warehouse
.venv/bin/python scripts/qa.py variants     # nine readings, timing budget, the trap
.venv/bin/python scripts/qa.py agents       # both agents, end to end
.venv/bin/python scripts/qa.py api          # SSE stream, caching
.venv/bin/python scripts/qa.py web          # the actual page, in Chromium
```

---

## Links

- Live demo: _(pending deploy)_
- Demo video: _(pending)_
- Devpost submission: _(pending)_

## License

MIT — see [LICENSE](LICENSE).
