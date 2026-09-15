# Study Case: Raw Model vs search-pro System (A/B benchmark, pre-registered)

Status: controlled A/B run, 2026-09-15. Protocol pre-registered in `study/protocol.md`
BEFORE any scenario-B execution. Raw transcripts in `study/`. Reproducible via
commands below (web drift expected on re-run).

## Method in 30 seconds

- Arm A: Muse Spark 1.3 (free), parametric knowledge only, answers frozen first.
- Arm B: search-pro @ `14a4a72`, `search.py --mode pro --synth llm
  --llm-provider openrouter` (nex-agi/nex-n2.5-pro:free, reasoning high).
- 3 hard Indonesian prompts: P1 multi-hop numeric (Meta GDPR fines), P2 refusal
  test (MotoGP Mandalika 2026 winner+margin), P3 contested commercial (iPhone 17
  Pro 256GB launch vs marketplace price).
- Rubric 0–2 × 5 (CIT live links / NUM traceable numbers / REF calibration /
  CON conflict disclosure / DAT temporal anchoring), link liveness re-checked
  independently (`study/score_links.py` → `study/linkcheck.json`).
- NO Perplexity Pro arm was available: this study makes NO head-to-head parity
  claim. External reference points only: Haus Sep-2026 (65.9% numeric support),
  Tow Center (37% citation error).

## Scores

| Prompt | Arm A (raw) | Arm B (system) | Winner |
|---|---|---|---|
| P1 Meta fines | 4/10 (CIT0 NUM0 REF2 CON1 DAT1) | 4/10 (CIT2 NUM0 REF1 CON1 DAT0) | tie, opposite failures |
| P2 Mandalika | 6/6 applicable (REF2 CON2 DAT2) | 5/10 (CIT2 NUM0 REF1 CON1 DAT1) | A (clean refusal) |
| P3 iPhone price | 4/10 (CIT0 NUM0 REF2 CON1 DAT1) | 6/10 (CIT2 NUM0 REF2 CON1 DAT1) | B (refusal + live sources) |

CIT detail: all 18/18 cited B URLs resolve live (5+6+7). NUM detail: every B
numeric claim failed grep verification (0/2, 0/1, 0/1) and was demoted to
Unverified — zero fabrications served as fact.

## What actually happened (evidence, not adjectives)

- P1/B: retrieval pool off-topic (Sushi wiki, tariffs, TikTok fine). The LLM
  refused to total the fines (good) but misattributed €1.2B Meta to a TikTok
  article (bad) — the verifier caught it (0/2) and labeled it Unverified (gate
  works). Arm A stated ~10 correct-looking numbers with zero traceability.
- P2/B: named Pedro Acosta from an Instagram caption; verifier rejected it
  (0/1). The authoritative motogp.com page was fetched but unused for the claim.
  Arm A declined cleanly. Lesson: retrieval can hurt on refusal tasks when the
  pool carries social noise — v2.2.0's pointer-exclusion gate exists but is not
  yet wired into `search.py`.
- P3/B: pool fully off-topic (KPU blog, Arabic Wikipedia, IG reels); system
  refused all prices (REF 2). Arm A gave an Rp20–21jt estimate flagged uncertain.

## Defects found during the study (frozen, fix deferred)

1. Pro/research budgets (8/12 queries) exceed the 6 hardcoded query variants —
   only 6 ever run. Under-utilized fan-out.
2. Corroboration gate (`corroborate.py`) not yet integrated into the pipeline.

## Reading guide for investors

- PROVEN: failure-mode profile — the system fails LOUD (unverified labels, 0
  false facts served in 3 adversarial runs, 18/18 links live) where raw chatbots
  fail SILENT (fluent, plausible, uncited numbers). This property is diligenceable:
  re-run the commands, re-check the links.
- NOT PROVEN: parity with Perplexity Pro (no head-to-head arm), answer
  correctness on on-topic pools, or performance outside free-tier retrieval —
  the binding constraint in every run above was retrieval quality, not synthesis.
- NEXT (priced in effort, not adjectives): wire corroboration gate + fix query
  budget + upgrade retrieval (self-host SearXNG/Exa), then repeat this protocol.

## Re-run under v2.3.0 (2026-09-15, identical prompts/settings + keyed reader)

| Prompt | Before | After | Note |
|---|---|---|---|
| P1 Meta fines | 4/10 | **5/10** | pool now includes relevant Kontan article; composite claims still correctly rejected (0/2) |
| P2 Mandalika | 5/10 | **7/10** | sourced refusal with explicit mismatch reasoning (truncated title, San Marino vs Mandalika); no winner served |
| P3 iPhone price | 6/10 | **6/10** | clean refusal, itemized; no regression |

No score regressions. Explicit pointer-demotion branch remains unit-proven but
unobserved live (LLM-path claims lack file attribution) — tracked as open item,
not claimed as proven. Link re-check at scoring time: 21/22 live (one 403, one
redirect loop appeared after fetching — transient, recorded).

## Reproduce

`python search.py --query "<prompt>" --mode pro --synth llm --llm-provider
openrouter` (needs `SEARXNG_URL` + `OPENROUTER_API_KEY`);
`python study/score_links.py` re-checks citation liveness.
