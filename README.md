# Checksum

**The query ran fine. The number was wrong.**

Checksum is a multi-agent system that verifies studio analytics before anyone makes a
decision on them. For a single question it deliberately runs 6–8 different queries in
parallel against ClickHouse — different metric definitions, different deduplication,
different time windows — and reports whether the answer holds up across all of them.

When the ranking changes depending on which definition you use, that *is* the finding.

Built for the [Agentic Cinema Hackathon](https://agentic-cinema.devpost.com/) —
**ClickHouse partner track**.

---

## The problem

> What happens when the SQL is perfectly correct, but the number is wrong by the
> industry's definition — and someone renews a $200M series on it?

This is not a bug. The query runs. There is no error. The output is clean and the agent
is confident. What's wrong is the *definition*.

In streaming, a "view" is counted several incompatible ways: a playback start, two
minutes watched, or 90% completion. Netflix changed its own definition in 2024 and broke
historical comparability across the industry. Nielsen counts differently again. Does
"opening weekend" include preview screenings?

A naive text-to-SQL agent answers with whichever definition is easiest to express in SQL —
not the one the studio's renewal policy actually uses. The result is a confident,
plausible, wrong ranking that nothing flags, because nothing failed.

---

## How it works

<!-- TODO: ganti dengan diagram/screenshot asli setelah agent jalan -->

```
SequentialAgent
├─ Planner        parse the question, load the metric definitions from the policy table
├─ ParallelAgent  6–8 verification sub-agents run concurrently
│                 · view = playback start
│                 · view = ≥ 2 minutes
│                 · view = ≥ 90% completion   (renewal policy definition)
│                 · dedup per user / per session
│                 · calendar vs rolling window
│                 · row-count, null-rate and cardinality guards
└─ Adjudicator    compare, score stability, emit the verdict with receipts —
                  or refuse to answer
```

ClickHouse's speed is the mechanism, not decoration: it turns query latency into a
**verification budget**. Eight readings cost about what one costs elsewhere. On a
row-store this approach is not viable.

Every query Checksum runs is shown to the user. The SQL is the receipt.

---

## Refusing to answer

When the data genuinely cannot support a question, Checksum says so and names the missing
columns instead of producing a number. A confident answer built on absent data is the
failure this project exists to prevent.

---

## Stack

| Layer | Technology |
|---|---|
| Model | Gemini (Google AI Studio) |
| Orchestration | Google Agent Development Kit (`google-adk`) |
| Data access | Official ClickHouse MCP server (`mcp-clickhouse`) |
| Warehouse | ClickHouse Cloud |
| Backend | FastAPI + Server-Sent Events |
| Frontend | React |

---

## Data disclosure

**Catalog data is real.** Titles, ratings and credits come from the
[IMDb non-commercial datasets](https://developer.imdb.com/non-commercial-datasets/).

**Playback telemetry is simulated at studio scale**, generated inside ClickHouse with
`generateRandom()`. No streaming platform publishes real playback telemetry, so no public
dataset of this kind exists. The distribution is shaped to reflect a realistic
autoplay-heavy session mix, and the metric-definition divergence this project detects is
present in the generated data by design.

Stating this plainly matters: the point of Checksum is that numbers should come with
their provenance attached.

---

## Running locally

<!-- TODO: lengkapi setelah GATE lolos -->

```bash
git clone <repo-url> && cd checksum
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env    # fill in your keys
```

Requires a Google AI Studio API key and a ClickHouse Cloud service.

---

## Links

- Live demo: <!-- TODO -->
- Demo video: <!-- TODO -->
- Devpost submission: <!-- TODO -->

## License

MIT — see [LICENSE](LICENSE).
