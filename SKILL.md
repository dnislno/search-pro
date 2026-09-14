# search-pro — SaC-lite answer engine (Perplexity Pro parity)

> Scope: factual/comparative/research QA with inline citations.
> Non-goals: own web index, premium PitchBook/Statista, video/gen, browser login-wall.

## Workflow (must follow in order)

1. **Intent IR** — Rewrite user prompt to `intent-ir.schema.json`. One topic per thread, max 5 follow-ups then re-brief. If ambiguous set `need_clarify=true` and ask before searching.
2. **Plan** — Max 4 steps DAG. `best`: 1 step / 4 queries. `pro`: 2-3 steps / 8 queries. `research`: 3-4 steps / 12 queries.
3. **Fan-out** — One parallel retrieval per sub-query via `websearch` (5-10 results each). Rewrite variants: `site:`, `"exact phrase"`, `-noise`, `filetype:pdf`, recency filter.
4. **Fetch** — `webfetch` top unique URLs only: best=4, pro=8, research=10. Save markdown to `/tmp`. Mark `paywall/js-empty/snippet-only` — never cite as verified.
5. **Process (deterministic)** — Run `scripts/rerank.py` then `scripts/verify-citations.py`. No LLM for dedup/filter/score.
6. **Verify gate** — Numeric/date/quote claim passes ONLY if verbatim (<=25 words) found via grep in fetched body. Target: >=6 verified claims from >=5 full-read sources. If yield <5, one backfill round with new queries. Else emit `Unverified` section.
7. **Synthesize** — Muse Spark max reasoning, grounded-only: every numbered claim `[[n]](url)`. Sections: Answer, Sources (with read-status), Conflicts, Unverified, Follow-ups x3, Provenance (model+queries+fetch time).

## Hard rules

- No source, no claim. Snippet != source. Paywall/403 != source.
- Distinguish `verified-full-read` vs `snippet-only` vs `paywall`.
- Conflicts: show both numbers with both citations, never average.
- Provenance footer mandatory (anti silent-downgrade).
- Advanced rerank: `--advanced` uses BGE cross-encoder (Plan B) for finance/niche; default heuristic (Plan A).

## Scripts

- `scripts/rerank.py --input candidates.json [--advanced]` — dedup + heuristic or BGE cross-encoder, 50→5-10.
- `scripts/verify-citations.py --claims claims.json --corpus corpus_dir/` — grep verifier, emits verified/unverified.
- `intent-ir.schema.json` — input contract from Muse Spark translator.
