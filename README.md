# search-pro — The Verifiable Answer Engine

> Perplexity Pro proved answers with citations are the future. `search-pro` makes them **verifiable** — every number grep-checked, every claim traceable, every failure labeled.

## Why this wins

AI search is broken: 34.7% of citations in the wild don't contain the number they claim (Haus Research, Sep 2026). `search-pro` flips the stack — **no source, no claim**. Snippets don't count. Paywalls don't count. Only full-read, verbatim-matched evidence ships.

- ⚡ **3 modes, 1 skill** — `best` (~30s), `pro` (2–4 min), `research` (deep dive)
- 🔬 **Deterministic verifier** — grep-based gate, not vibes. Fiction gets rejected (see `examples/`)
- 🏹 **SaC-lite engine** — parallel fan-out (4–12 queries) + deterministic dedup/rerank, inspired by Perplexity Search-as-Code, Perplexica (20k⭐), Haystack (26k⭐), LlamaIndex (51k⭐)
- 🧠 **Muse Spark 1.3 native** — 1M context planner + synthesizer, clarifying questions, provenance footer on every answer
- 📈 **Audited yield model** — typical `pro` run: ~10 verified claims (target ≥6 ✅); worst-case finance/niche auto-escalates to BGE cross-encoder (Plan B: 3.0 → 7.2 ✅)

## How it hits Perplexity Pro parity

```
User → Muse Spark Intent IR → Planner (≤4 steps) → Parallel search (Exa)
→ Fetch top URLs → Deterministic rerank → Grep verifier → Grounded synthesis
→ Cited answer + sources + conflicts + follow-ups + provenance
```

1. **Translate intent** (`intent-ir.schema.json`) — pronouns resolved, queries rewritten (`site:`, `"exact"`, recency).
2. **Retrieve wide** — `scripts/rerank.py` dedups + scores (heuristic default, `--advanced` BGE cross-encoder).
3. **Verify hard** — `scripts/verify-citations.py` requires numbers/dates/quotes to appear verbatim in fetched body.
4. **Synthesize honest** — verified claims cited `[[n]](url)`; the rest goes to `Unverified`, conflicts shown side-by-side.

## Quickstart

```powershell
python scripts/rerank.py --input examples/candidates.json --query "Perplexity 1.5B May 2026" --top-k 8
python scripts/verify-citations.py --claims examples/claims.json --corpus examples/corpus
# Plan B (niche/finance): pip install "rerankers[transformers]"
python scripts/rerank.py --input c.json --query "..." --advanced
```

Built for builders who ship truth, not theater. Star it, fork it, break it — the verifier dares you to.
