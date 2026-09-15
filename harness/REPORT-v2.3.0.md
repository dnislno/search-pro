# Harness Report — v2.3.0 re-run (identical conditions to v2.0.0)

Date: 2026-09-15. Changes since v2.0.0: keyed Jina Reader fallback
(`JINA_API_KEY`), Wikipedia REST fast-path kept, corroboration gate wired,
polite gaps unchanged. Mode pro, extractive, same 30 queries, same provider.
Completion: 30/30, zero throttling failures (keyed quota held).

## Results vs v2.0.0 vs targets vs Haus

| Metric | v2.0.0 | v2.3.0 | Target | Haus | Verdict |
|---|---|---|---|---|---|
| M1 grep-pass | 1.000 (453/453) | **0.971** (645/664) | ≥0.85 | 0.659 | ✅ PASS |
| M2 link-live | 0.715 (163/228) | **0.963** (231/240) | ≥0.80 | 0.787 | ✅ PASS |
| M3 median verified | 15 (28/30 ≥6) | **23** (30/30 ≥6, min 10) | median ≥6 | — | ✅ PASS |

Fetch provenance (240 attempts): direct 144, jina-reader 76, wikipedia-api 11,
9 residual failures (true paywalls/login-walls). Without fallbacks M2 would be
144/240 = 0.600 — the chain, not luck, carries the gain.

## Notes (honest deltas)

- M1 dipped 1.000 → 0.971: Jina-rendered pages occasionally reorder/strip the
  exact sentence the composer quoted (19 claims demoted, all labeled, none served).
- M2 0.963 still trails the aspirational 0.98: residual is login-walled and
  subscription paywalls no free tool can open.
- Study re-run (3 prompts, Nex 2.5 Pro): P1 4→5, P2 5→7 (sourced refusal with
  mismatch reasoning), P3 6→6. No regressions. Pointer-demotion branch is
  unit-proven both directions but did not fire live (LLM-path claims carry no
  file attribution) — recorded as untested-live, not as proven.
