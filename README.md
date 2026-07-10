# Can these 100 apps become agent toolkits?

An agent-run feasibility audit of 100 SaaS apps — the research Composio does before
building a toolkit (auth methods, credential gating, API surface, buildability),
done by a multi-agent pipeline instead of by hand, with measured accuracy.

**Live case study:** https://pra2107tham.github.io/app-toolkit-audit/ — findings,
patterns, agent architecture, and verification results on one page.
Machine-readable results: [`docs/results.json`](docs/results.json).

## What the agent does

A CrewAI flow, one crew per app, with per-app caching (every stage is resumable):

| Stage | What | Who |
|---|---|---|
| S0 | Composio toolkit-registry match, official MCP-registry lookup, homepage liveness | pure Python, no LLM |
| S1 | Docs Scout finds official docs/auth/pricing pages → API Analyst fetches them and fills the record; every field cites a fetched URL | `gpt-5-mini` + Composio `COMPOSIO_SEARCH` tools |
| S2 | Skeptic Verifier grades each field against its cited evidence page — pages are pre-fetched in Python and injected, the model runs tool-free and single-shot | `gpt-4o-mini` (different lineage than the researcher) |
| S3 | Only flagged fields are re-researched and merged; fixed apps get re-verified | `gpt-5-mini` + Composio tools |
| S4 | Eval layer: deterministic checks (schema, URL liveness) + LLM judge on a stratified sample + human spot-check CSV | `gpt-4.1-mini` (third lineage) + human |
| S5–S6 | Aggregation → `patterns.json`, `accuracy.json` → renders `docs/index.html` | pure Python |

Research tools are Composio's no-auth `COMPOSIO_SEARCH` toolkit (Tavily/web search +
URL fetch) through the official `composio_crewai` provider, with a plain-HTTP
fallback fetch tool for pages the crawler can't reach.

## Where humans were needed

Seeding the app list, API keys, three pipeline problems agents couldn't see for
themselves (gpt-5 models rejecting CrewAI's `stop` param; a too-loose MCP-registry
name matcher that called "storefront" a match for "Front"; a first verification
design that trusted the verifier to call fetch tools — it skipped them at batch
scale, flagged 70% of correct fields, and burned the OpenRouter budget, so
verification was redesigned to pre-fetch evidence deterministically and grade
tool-free), grading the human spot-check CSV, and final review of the page copy.
Research, verification, fixing, and scoring ran unattended.

## Reproduce

Rebuild analysis + site from committed artifacts (no keys needed):

```bash
uv sync
uv run python -m src.analyze
uv run python -m src.build_site
open docs/index.html
```

Re-run the full research pipeline (needs keys — copy `.env.example` to `.env` and
fill `COMPOSIO_API_KEY`, `OPENAI_API_KEY`, `OPENROUTER_API_KEY`):

```bash
uv run python -m src.flow --stage s0                    # deterministic enrichment
uv run python -m src.flow --stage s1 --concurrency 6    # pass-1 research (≈20 min)
uv run python -m src.flow --stage s2 --concurrency 6    # verification
uv run python -m src.flow --stage s3 --concurrency 4    # fix flagged fields
uv run python -m src.flow --stage reverify              # re-verify fixed apps
uv run python -m src.flow --stage results               # merge -> out/results.json
uv run python -m src.evals --deterministic --judge --accuracy
uv run python -m src.evals --emit-csv                   # human spot-check round
uv run python -m src.analyze && uv run python -m src.build_site
```

Any stage can be re-run safely; per-app JSON in `out/` is never recomputed unless
deleted. `--slugs a,b` / `--limit N` scope a run.

## Layout

```
data/apps.json        seed list (100 apps, 10 categories)
schema/               JSON Schemas generated from the Pydantic contracts
src/crews/            research / verify / fix / judge crew definitions
src/flow.py           orchestrator (stages, caching, concurrency)
src/enrich.py         S0 deterministic enrichment
src/evals.py          S4 eval layer -> accuracy.json
src/analyze.py        S5 aggregation -> patterns.json
src/build_site.py     S6 page renderer
out/                  committed pipeline artifacts (evidence trail)
docs/                 GitHub Pages root: index.html + results.json
```

## Honesty notes

- No paid app accounts were used; "gated behind payment/partnership, with
  evidence" is recorded as a finding, not papered over.
- Apps with no researchable public developer docs are marked
  `insufficient_public_docs` with the queries that were tried.
- Pass-1 vs final accuracy, the misses, and the human-check disagreements are
  all on the page and in `out/accuracy.json`.
