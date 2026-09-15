"""Plan A heuristic rerank + dedup (deterministic) with Plan B BGE flag.
Refs: Haystack explicit ranker stage, LlamaIndex retrieve-100/rerank-10,
      AnswerDotAI/rerankers + BAAI/bge-reranker-base.
Usage:
  python rerank.py --input candidates.json [--advanced] [--top-k 8]
Input JSON: [{"title":..,"url":..,"snippet":..,"published":..}, ...]
Output JSON to stdout: ranked top-k with _score and _method.
"""
import argparse, json, re, sys
from collections import OrderedDict
from urllib.parse import urlparse

TRUSTED = (".go.id", ".ac.id", ".edu", "arxiv.org", "nature.com", "ieee.org",
           "wikipedia.org", "github.com", "docs.python.org")
BLOCKED_NOISE = ("pinterest.", "shopee.", "tokopedia.", "spam", "judi", "slot,")

def normalize_url(u: str) -> str:
    u = (u or "").strip().rstrip("/").lower()
    u = re.sub(r"^https?://(www\.)?", "", u)
    u = re.sub(r"[?#].*$", "", u)
    return u

def tokens(s: str):
    return re.findall(r"[a-z0-9]{3,}", (s or "").lower())

def heuristic_score(query: str, c: dict) -> float:
    q = set(tokens(query))
    text = f"{c.get('title','')} {c.get('snippet','')}"
    t = tokens(text)
    overlap = len(q & set(t)) / max(1, len(q))
    host = urlparse("https://" + normalize_url(c.get("url", ""))).hostname or ""
    trust = 1.0 if host.endswith(TRUSTED) else 0.0
    noise = -1.0 if any(b in (c.get("url","").lower()) for b in BLOCKED_NOISE) else 0.0
    fresh = 0.3 if re.search(r"202[5-9]|2026", text) else 0.0
    length_pen = -0.2 if len(text) < 80 else 0.0
    return round(3.0 * overlap + 2.0 * trust + fresh + noise + length_pen, 4)

def bge_scores(query: str, cands):
    """Plan B: local cross-encoder. Lazy import so Plan A has zero deps."""
    try:
        from rerankers import Reranker
    except ImportError:
        print("WARN: 'rerankers' not installed, fallback to heuristic. pip install 'rerankers[transformers]'", file=sys.stderr)
        return None
    ranker = Reranker("cross-encoder", verbose=False)
    docs = [f"{c.get('title','')} {c.get('snippet','')}"[:1000] for c in cands]
    ranked = ranker.rank(query=query, docs=docs)
    out = [None] * len(cands)
    for r in ranked.results:
        out[r.doc_id] = round(float(r.score), 4)
    return out

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", required=True)
    ap.add_argument("--query", default="")
    ap.add_argument("--top-k", type=int, default=8)
    ap.add_argument("--advanced", action="store_true", help="Plan B BGE cross-encoder")
    a = ap.parse_args()

    cands = json.load(open(a.input, encoding="utf-8"))
    # dedup by normalized URL, keep first
    seen = OrderedDict()
    for c in cands:
        k = normalize_url(c.get("url", ""))
        if k and k not in seen:
            seen[k] = c
    uniq = list(seen.values())

    method = "heuristic-v1"
    if a.advanced:
        s = bge_scores(a.query, uniq)
        if s is not None:
            for c, sc in zip(uniq, s):
                c["_score"] = sc
            method = "bge-cross-encoder"
        else:
            for c in uniq:
                c["_score"] = heuristic_score(a.query, c)
    else:
        for c in uniq:
            c["_score"] = heuristic_score(a.query, c)

    uniq.sort(key=lambda c: c.get("_score", 0), reverse=True)
    for c in uniq[:a.top_k]:
        c["_method"] = method
    try:
        json.dump(uniq[:a.top_k], sys.stdout, ensure_ascii=False, indent=2)
    except UnicodeEncodeError:  # e.g. Windows cp1252 console/pipe
        json.dump(uniq[:a.top_k], sys.stdout, ensure_ascii=True, indent=2)

if __name__ == "__main__":
    main()
