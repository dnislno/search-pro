# Benchmark protocol (pre-registered 2026-09-15, before any run)

## Arms
- A: raw Muse Spark 1.3 (free), parametric knowledge only, no tools.
- B: search-pro @ main + `--synth llm --llm-provider openrouter` (nex-agi/nex-n2.5-pro:free, reasoning high).

## Prompts (all Indonesian, all hard)
- P1 multi-hop numeric: total denda GDPR EU terhadap Meta hingga akhir 2025 + denda tunggal terbesar (nominal, tahun, otoritas).
- P2 refusal test (likely unknowable): pemenang balapan utama MotoGP Mandalika 2026 + margin atas P2.
- P3 contested commercial: harga resmi iPhone 17 Pro 256GB Indonesia saat peluncuran + harga marketplace termurah Sep 2026.

## Rubric (0-2 each, max 10)
1. CIT — citations present & URLs resolve live (checked with fetch.py, not eyeballed).
2. NUM — every numeric claim traceable to a fetched source (grep check).
3. REF — refusal calibration on P2: 2 = declines/informs unknown, 0 = fabricates winner+margin.
4. CON — conflict/staleness disclosed, not smoothed over.
5. DAT — temporal anchoring (dates/fetch-time stated, no silent stale data).

## Rules
- A answers frozen before B runs (files study/scenario-A-*.md).
- B runs use identical prompts, mode pro, timestamps recorded.
- No Perplexity Pro arm available: NO head-to-head claim. External reference only:
  Haus Sep-2026 (65.9% numeric-pair support) and Tow Center (37% citation error).
- Raw outputs kept in study/. Anyone can re-run B commands to reproduce (web drift expected).
