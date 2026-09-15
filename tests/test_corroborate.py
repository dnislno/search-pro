"""Unit tests: wire-collapse, pointer-exclusion, tiers, token-boundary.
Run: python tests/test_corroborate.py (stdlib only, no network)."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import corroborate as C


def check(name, cond):
    print(("PASS " if cond else "FAIL ") + name)
    if not cond:
        raise SystemExit(1)


check("social-is-pointer", C.origin_of("https://x.com/u/s/1", "...") == "pointer:x.com")
check("reddit-is-pointer",
      C.origin_of("https://www.reddit.com/r/x", "...").startswith("pointer:"))
check("wire-collapse", C.origin_of("https://news.example.org/a", "According to Reuters, x") == "wire:reuters.com")
check("same-publisher", C.origin_of("https://a.com/1", "...") == C.origin_of("https://www.a.com/2", "...") == "pub:a.com")
check("tier-primary", C.reliability("https://bps.go.id/x") == "primary")
check("tier-derivative", C.reliability("https://x.com/y") == "derivative")
check("tier-secondary", C.reliability("https://vendor.com/z") == "secondary")
check("no-substring-false-positive",
      C.body_has_figure("on August 30 the price was 300 dollars", "30") is False)
check("true-figure-match",
      C.body_has_figure("costs $30 per month per user", "30") is True)
check("norm-figure", C.norm_fig("$1.5M") == "15" and C.norm_fig("1,5") == "15")
print("UNIT-OK: all 10 pass")
