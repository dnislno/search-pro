# Search-Pro — Open-Source, Verifiable AI Search Engine (Perplexity Pro Alternative)

**Self-hosted AI search with cited answers you can actually verify.** Search-Pro is an open-source Perplexity alternative that retrieves live web results, re-ranks them deterministically, and refuses to publish any claim it cannot trace to a full-read source — fixing the citation-reliability gap documented in today's AI answer engines.

Keywords: AI search engine, Perplexity alternative, open-source Perplexity, self-hosted AI search, RAG pipeline, cited answers, verifiable AI, deep research agent, web-grounded LLM.

## Why teams switch from Perplexity Pro to Search-Pro

| What matters | Perplexity Pro (documented) | Search-Pro |
|---|---|---|
| Citation integrity | ~35% of numeric citations fail to contain the claimed figure (Haus Research, Sep 2026); ~37% citation error in independent audit (Tow Center, Columbia) | Deterministic grep verifier: numbers, dates, and quotes must appear verbatim in fetched page text, or the claim ships as `Unverified` |
| Model transparency | Silent model fallbacks reported by Pro users (Nov 2025) | Mandatory provenance footer on every answer: model, queries used, fetch timestamps |
| Quota stability | Deep Research quotas cut mid-contract (early 2026) | Self-hosted — your keys, your limits, no mid-contract downgrades |
| Ranking control | Black-box reranker | Auditable two-stage funnel: heuristic (zero-dependency) or local BGE cross-encoder (`--advanced`) |
| Privacy | Queries logged on vendor servers | Runs on your infrastructure; pairs with SearXNG/Ollama for fully private AI search |

Search-Pro does not claim a 200-billion-page private index or licensed premium datasets — it claims something narrower and checkable: **no source, no claim.**

## How it works

Inspired by Perplexity's Search-as-Code architecture, Perplexica (20k+ ⭐), Haystack (26k+ ⭐), and LlamaIndex (51k+ ⭐):

```
User → Muse Spark Intent IR → Planner (≤4 steps) → Parallel web search
→ Full-page fetch → Deterministic rerank → Grep verifier → Grounded synthesis
→ Cited answer + sources + conflicts + follow-ups + provenance
```

1. **Translate intent** (`intent-ir.schema.json`) — Muse Spark 1.3 resolves pronouns, classifies `best | pro | research`, and emits 4–12 rewritten queries (`site:`, exact-phrase, recency).
2. **Retrieve wide** — `scripts/rerank.py` dedups by normalized URL and scores candidates (Plan A heuristic, Plan B local BGE cross-encoder for finance/niche queries).
3. **Verify hard** — `scripts/verify-citations.py` requires every numeric/date/quote claim to match the fetched body text (Haus-style deterministic check).
4. **Synthesize honestly** — grounded-only generation with inline `[[n]](url)` citations and an `Unverified` section for anything unsupported (follow-ups + backfill loop on roadmap).

## Measured behavior (live harness, 30 queries, pro mode — see `harness/REPORT-v2.3.0.md`)

- Citation faithfulness: **645/664 claims verified (0.971)** vs 0.659 Haus baseline ✅
- Source accessibility: **231/240 fetches ok (0.963)** vs 0.787 Haus — ✅ via keyed
  reader fallback (direct alone would be 0.600); residual is login-walled paywalls
- Volume: **median 23 verified claims/answer, 30/30 ≥6** ✅
- Study re-run (3 adversarial prompts): no score regressions; sourced refusals
  where evidence is thin — full account in [STUDY-CASE.md](STUDY-CASE.md)
- Fixture-tested: true claim passes as `verified-full-read`, fabricated claim
  rejected as `unverified` (see `examples/`)

## Quickstart — one command, local

```powershell
# Offline demo (no keys, no network): full pipeline on fixtures
python search.py --dry-run

# Live: pick a provider (SearXNG = free/self-hosted, Exa = API key)
$env:SEARXNG_URL = "http://localhost:8888"   # or a public instance
python search.py --query "AI search market size 2026" --mode pro

$env:EXA_API_KEY = "exa-..."                 # alternative
python search.py --query "Perplexity vs ChatGPT accuracy" --mode best --provider exa

# Fluent rewrite via LLM (re-verified; default is offline extractive):
$env:ANTHROPIC_API_KEY = "sk-ant-..."        # or OPENAI_API_KEY
python search.py --query "..." --mode research --synth llm --advanced --out answer.md
```

## Module-level usage

```powershell
python scripts/rerank.py --input examples/candidates.json --query "AI search market size 2026" --top-k 8
python scripts/verify-citations.py --claims examples/claims.json --corpus examples/corpus
# Plan B for niche/finance queries (local, no new API key):
pip install "rerankers[transformers]"
python scripts/rerank.py --input candidates.json --query "..." --advanced
```

## Roadmap

`--advanced` rerank service · academic/YouTube/Reddit verticals (Perplexica-style) · scheduled deep-research runs · OpenAI-compatible `/search` API · paywall corroboration (`corroborate.py`, v2.2.0 experiment: figure-search + independence + grade).

MIT-licensed. Star it, self-host it, and hold every answer to its sources.

---

## Study case: raw model vs system (A/B benchmark)

Neutral, pre-registered benchmark report with frozen transcripts, independent
link re-checks, and an explicit statement of what is and isn't proven:
[STUDY-CASE.md](STUDY-CASE.md) (protocol + evidence in `study/`).
