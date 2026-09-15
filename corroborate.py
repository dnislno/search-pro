#!/usr/bin/env python3
"""Corroboration experiment (v2.2.0): figure-search + independence + grade.

Corrects the flawed 'similar titles + social quoter' proposal:
  - searches the FIGURE (number + entity), never title similarity;
  - counts INDEPENDENT origins (publisher grouping, same-wire collapse,
    social/forums as pointers only — never pillars);
  - grades evidence instead of synthesizing confirmation:
    confirmed | consistent-with | unverified | conflicted (ClaimReview-ish).
Precedents: ClaimReview schema, SHEG lateral reading, NATO Admiralty
two-axis grading, RAGChecker/SAFE per-claim support, Perplexica cross-checks.
stdlib only.
"""
import argparse
import json
import os
import re
import sys
import urllib.parse

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import providers
from fetch import fetch, html_to_text  # noqa: F401 (re-export for tests)

WIRE_DOMAINS = ("reuters.com", "apnews.com", "afp.com", "bloomberg.com",
                "associated press")
WIRE_MARKS = ("according to reuters", "according to the associated press",
              "according to bloomberg", "according to afp", "reuters reports",
              "ap reports", "bloomberg reports")
SOCIAL = ("x.com", "twitter.com", "facebook.com", "instagram.com",
          "tiktok.com", "youtube.com", "reddit.com", "linkedin.com",
          "threads.net", "bsky.app")
DERIV = SOCIAL + ("blogspot", "wordpress.com", "medium.com", "msn.com",
                  "yahoo.com", "aol.com", "tumblr.com")
PRIMARY_ENDS = (".gov", ".go.id", ".edu", ".ac.id", ".mil", ".int",
                ".sch.id", ".or.id")
# v2.6 3.2: high-authority non-government publishers count as primary.
PRIMARY_HOSTS = ("arxiv.org", "nature.com", "science.org", "ieee.org",
                 "acm.org", "who.int", "worldbank.org", "imf.org")


def norm_fig(fig):
    """Digits-only so 1.5 ~ 1,5 ~ $1.5M at digit level (lenient, documented)."""
    return re.sub(r"\D", "", fig or "")


def extract_figures(text, limit=6):
    pats = re.findall(
        r"\$\s?\d[\d.,]*\s?(?:million|billion|m\b|b\b)?"
        r"|\d[\d.,]*\s?(?:%|percent|million|billion|miliar|juta|km|mAh|kg|triliun|"
        r"second(?:s)?|detik|minute(?:s)?|menit|hour(?:s)?|jam|point(?:s)?|poin|"
        r"vote(?:s)?|suara|goal(?:s)?|gol|match(?:es)?)"
        r"|\b\d{3,}(?:[\d.,]*\d)?\b", text or "", flags=re.I)
    out, seen = [], set()
    for p in pats:
        n = norm_fig(p)
        if n and n not in seen and len(n) <= 12:
            seen.add(n)
            out.append(p.strip())
        if len(out) >= limit:
            break
    return out


def host_of(url):
    try:
        h = urllib.parse.urlparse(url).hostname or ""
        return h.lower()[4:] if h.lower().startswith("www.") else h.lower()
    except Exception:
        return ""


def body_has_figure(text, target_norm, limit=500):
    """Token-boundary match: '30' must occur as its own number, not inside
    '300'/'30,000'/dates. Whole-body digit-substring matching is banned
    (it turned day-numbers into 'verbatim support')."""
    if not target_norm:
        return False
    return any(norm_fig(m) == target_norm
               for m in extract_figures(text or "", limit=limit))


def _dom_match(h, d):
    d = d.rstrip(".")
    return h == d or h.endswith("." + d)


def origin_of(url, text):
    """Publisher origin with same-wire collapse. Social -> pointer/* (excluded)."""
    h = host_of(url)
    if any(_dom_match(h, d) for d in DERIV):
        return "pointer:" + h
    low = (text or "").lower()[:3000]
    for w in WIRE_DOMAINS:
        if w in h:
            return "wire:" + w
    for i, mark in enumerate(WIRE_MARKS):
        if mark in low:
            return "wire:" + WIRE_DOMAINS[i % len(WIRE_DOMAINS)]
    return "pub:" + h


def reliability(url):
    h = host_of(url)
    if h.endswith(PRIMARY_ENDS) or h in PRIMARY_HOSTS:
        return "primary"
    if origin_of(url, "").startswith("pointer:"):
        return "derivative"
    return "secondary"


AUTHORITY_POINTS = {"primary": 2, "secondary": 1, "derivative": 0}


def authority_score(url):
    """v2.6 3.2: numeric authority weight for a source URL."""
    return AUTHORITY_POINTS[reliability(url)]


def diversity_report(urls):
    """v2.6 3.2: source diversity summary over a list of URLs.

    Groups by publisher origin (same-wire collapse inherited from
    origin_of); pointers counted separately. Returns dict with counts,
    per-tier authority split, distinct publishers, and the largest
    single-publisher share (echo-chamber indicator).
    """
    urls = [u for u in (urls or []) if u]
    origins, tiers = {}, {"primary": 0, "secondary": 0, "derivative": 0}
    for u in urls:
        org = origin_of(u, "")
        origins[org] = origins.get(org, 0) + 1
        tiers[reliability(u)] += 1
    n = len(urls)
    top_share = round(max(origins.values()) / max(1, n), 3) if origins else 0.0
    return {"n_sources": n, "n_publishers": len(origins),
            "authority": tiers,
            "authority_mean": round(sum(authority_score(u) for u in urls)
                                    / max(1, n), 3),
            "pointer_ratio": round(sum(1 for u in urls
                                       if origin_of(u, "").startswith("pointer:"))
                                   / max(1, n), 3),
            "top_publisher_share": top_share}


def figure_queries(figure, entity):
    ent = " ".join(entity.split()[:6])
    return [f'"{figure}" {ent}', f"{ent} {figure}"]


def corroborate(figure, entity, search_fn, fetch_fn, per_query=6):
    """Returns ClaimReview-ish verdict dict."""
    target = norm_fig(figure)
    ent_toks = {t.lower() for t in re.findall(r"[a-z]{4,}", entity)}
    origins, pointers, seen_urls = {}, [], set()
    for q in figure_queries(figure, entity):
        for c in search_fn(q, per_query):
            u = c.get("url", "")
            if not u or u in seen_urls:
                continue
            seen_urls.add(u)
            f = fetch_fn(u)
            body, snip = f.get("text", ""), c.get("snippet", "")
            in_body = body_has_figure(body, target) and f.get("status") == "ok"
            in_snip = body_has_figure(snip, target)
            org = origin_of(u, body or snip)
            if org.startswith("pointer:"):
                pointers.append({"url": u, "role": "pointer (excluded)"})
                continue
            sup = ("verbatim" if in_body and f.get("status") == "ok"
                   else "snippet" if in_snip else "none")
            slot = origins.setdefault(org, {"reliability": reliability(u),
                                            "support": "none", "urls": [],
                                            "rival_figures": []})
            slot["urls"].append(u)
            if sup == "verbatim" or (sup == "snippet" and slot["support"] == "none"):
                slot["support"] = sup
            if in_body:  # contradiction scan: other figures beside entity words.
                # A rival counts only with corroboration of its own: the same
                # rival figure must recur in >=2 independent verbatim origins.
                # Bare years are never rivals. Single roundup prices ($6 vs $30
                # for different vendors) are context, not contradiction.
                for s in re.split(r"(?<=[.!?])\s+", body):
                    toks = set(re.findall(r"[a-z]{4,}", s.lower()))
                    if len(toks & ent_toks) >= 2:
                        for m in extract_figures(s, limit=4):
                            n = norm_fig(m)
                            if n != target and not re.fullmatch(r"(19|20)\d{2}", n) \
                                    and len(slot["rival_figures"]) < 6:
                                slot["rival_figures"].append(n)
    verb = [o for o, v in origins.items() if v["support"] == "verbatim"]
    snip = [o for o, v in origins.items() if v["support"] == "snippet"]
    rival_votes = {}
    for o in verb:
        for n in set(origins[o]["rival_figures"]):
            rival_votes[n] = rival_votes.get(n, 0) + 1
    rivals = [n for n, c in rival_votes.items() if c >= 2]
    conflict = bool(rivals)
    if len(verb) >= 2 and not conflict:
        grade = "confirmed"
    elif conflict and (len(verb) >= 2 or (len(verb) == 1 and snip)):
        grade = "conflicted"
    elif len(verb) == 1 and snip:
        grade = "consistent-with"
    elif len(snip) >= 2:
        grade = "consistent-with"
    else:
        grade = "unverified"
    return {"claim": {"figure": figure, "entity": entity},
            "grade": grade, "conflict": conflict, "rival_figures": rivals,
            "verbatim_origins": verb, "snippet_origins": snip,
            "origins": origins, "pointers_excluded": pointers}


def main():
    ap = argparse.ArgumentParser(description="corroborate a paywalled figure")
    ap.add_argument("--figure", required=True)
    ap.add_argument("--entity", required=True)
    ap.add_argument("--provider", default="auto")
    ap.add_argument("--searxng-url", default=os.environ.get("SEARXNG_URL", ""))
    ap.add_argument("--per-query", type=int, default=6)
    a = ap.parse_args()

    def search_fn(q, lim):
        return providers.search(q, provider=a.provider, limit=lim,
                                searxng_url=a.searxng_url or None)

    v = corroborate(a.figure, a.entity, search_fn, fetch, a.per_query)
    print(f"claim: {a.figure} :: {a.entity}\nGRADE: {v['grade']}")
    print(f"verbatim origins ({len(v['verbatim_origins'])}):")
    for o in v["verbatim_origins"]:
        print(f"  - {o} [{v['origins'][o]['reliability']}]")
    print(f"snippet origins ({len(v['snippet_origins'])}):")
    for o in v["snippet_origins"]:
        print(f"  - {o} [{v['origins'][o]['reliability']}]")
    print(f"pointers excluded: {len(v['pointers_excluded'])}")
    json.dump(v, open("corroborate-out.json", "w", encoding="utf-8"),
              ensure_ascii=False, indent=2)
    print("wrote corroborate-out.json")


if __name__ == "__main__":
    sys.exit(main())
