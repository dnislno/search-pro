# Harness Report — v2.0.0 live measurement (30 queries, pro mode, extractive)

Date: 2026-09-15. Provider: public SearXNG `sx.xo.st` (free, no key) + stdlib fetcher.
Method: `harness/run.py` over `harness/queries.json` (10 factual / 10 fresh / 5 compare / 5 academic).
Completion: 26/30 first pass; 30/30 after 2 retry rounds (instance throttled: SSL EOF, 502, timeouts).

## Results vs targets vs Haus baselines

| Metric | Measured | Target | Haus baseline | Verdict |
|---|---|---|---|---|
| M1 grep-pass (verified/total claims) | 453/453 = **1.000** | ≥0.85 | 0.659 | ✅ PASS (+34pp) |
| M2 link-live (fetch ok/attempted) | 163/228 = **0.715** | ≥0.98 | 0.787 | ❌ FAIL (−7pp) |
| M3 verified claims/answer | median **15**, 28/30 ≥6 (0.933), min 3 | median ≥6 | — | ✅ PASS |

Per-category verified: factual 158, fresh 123, compare 91, academic 81. Zero unverified claims in extractive mode.

## Failure analysis (M2)

Non-ok fetches (65/228) break down as: true paywalls/login-walls, bot-block 403s
(incl. Wikipedia via datacenter IP), JS-walled apps (ESPN/SofaScore — no renderer
in stdlib fetcher), and instance-side errors during throttling. This is the
free-tier retrieval gap vs Perplexity's own index + Firecrawl rendering — not a
synthesis bug: every served claim remained verbatim-traceable (M1 = 1.0).

## Known metric blind spot

M1 measures faithfulness (claim text appears in cited page), not relevance or
correctness. Off-locale pools (DE/FR pages for ID queries — the public instance
ignores `language`) still score 1.0 when the composer quotes them faithfully.
Relevance grading is open work (see roadmap: cross-encoder relevance gate).

## Conclusion

Parity claim: **NOT YET**. 2/3 targets pass; the binding constraint is source
accessibility on free retrieval (M2), plus unimplemented output contract items
(follow-ups x3, backfill loop, research report format). Recommended path: self-hosted
SearXNG (proper locales/engines) or Exa key + BGE relevance gate, then re-run harness.
Raw per-query runs: `harness/results/*.json` (local, reproducible via run.py).
