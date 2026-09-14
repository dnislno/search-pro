#!/usr/bin/env python3
"""search-pro CLI — one command, local, full pipeline.
  python search.py --query "..." --mode pro --provider searxng
  python search.py --query "..." --mode best --provider exa --synth llm
  python search.py --dry-run            # offline, uses examples/ fixtures
Pipeline: plan -> fan-out search -> rerank -> fetch -> evidence ->
          synthesize -> verify -> cited markdown + provenance.
Exit codes: 0 ok (even with Unverified section), 2 config error, 1 failure.
"""
import argparse
import datetime
import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import providers
from fetch import fetch
import synthesize

BUDGETS = {
    "best": {"queries": 4, "per_query": 5, "fetch": 4},
    "pro": {"queries": 8, "per_query": 6, "fetch": 8},
    "research": {"queries": 12, "per_query": 8, "fetch": 10},
}


def plan_queries(query, mode):
    words = [w for w in query.split() if len(w) > 4]
    key = " ".join(words[:4]) or query
    variants = [
        query,
        f"{query} data statistics",
        f'"{key}"',
        f"{query} 2026",
        f"{query} study OR paper OR report",
        f"{query} site:go.id OR site:ac.id OR site:edu",
    ]
    return variants[:BUDGETS[mode]["queries"]]


def run(cmd):
    p = subprocess.run(cmd, capture_output=True, text=True)
    if p.returncode != 0:
        raise RuntimeError(f"{' '.join(cmd)} failed: {p.stderr.strip()[:500]}")
    return p.stdout


def main():
    ap = argparse.ArgumentParser(description="search-pro local CLI")
    ap.add_argument("--query", default="")
    ap.add_argument("--mode", choices=["best", "pro", "research"], default="pro")
    ap.add_argument("--provider", choices=["auto", "searxng", "exa"], default="auto")
    ap.add_argument("--searxng-url", default=os.environ.get("SEARXNG_URL"))
    ap.add_argument("--synth", choices=["auto", "extractive", "llm"], default="auto")
    ap.add_argument("--advanced", action="store_true", help="BGE cross-encoder rerank")
    ap.add_argument("--top-k", type=int, default=0, help="override fetch budget")
    ap.add_argument("--out", default="", help="write markdown answer to file")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    if not a.query and not a.dry_run:
        print("error: --query required (or use --dry-run)", file=sys.stderr)
        return 2
    b = BUDGETS[a.mode]
    fetch_n = a.top_k or b["fetch"]
    tmp = tempfile.mkdtemp(prefix="searchpro_")
    corpus = os.path.join(tmp, "corpus")
    os.makedirs(corpus, exist_ok=True)
    t0 = datetime.datetime.now(datetime.timezone.utc)

    try:
        # 1-2. plan + fan-out
        if a.dry_run:
            cands = json.load(open(os.path.join(HERE, "examples", "candidates.json"),
                                   encoding="utf-8"))
            queries = ["dry-run fixture"]
            fetched = {"examples/corpus/doc1.md": "ok"}
            shutil_corpus = os.path.join(HERE, "examples", "corpus", "doc1.md")
            open(os.path.join(corpus, "doc0.md"), "w", encoding="utf-8").write(
                open(shutil_corpus, encoding="utf-8").read())
        else:
            queries = plan_queries(a.query, a.mode)
            cands = []
            for q in queries:
                try:
                    cands += providers.search(q, provider=a.provider,
                                              limit=b["per_query"],
                                              searxng_url=a.searxng_url)
                except RuntimeError as e:
                    print(f"error: {e}", file=sys.stderr)
                    return 2

        # 3. rerank (existing tested script)
        cj = os.path.join(tmp, "candidates.json")
        json.dump(cands, open(cj, "w", encoding="utf-8"), ensure_ascii=False)
        cmd = [sys.executable, os.path.join(HERE, "scripts", "rerank.py"),
               "--input", cj, "--query", a.query or "dry-run",
               "--top-k", str(fetch_n)]
        if a.advanced:
            cmd.append("--advanced")
        ranked = json.loads(run(cmd))

        # 4. fetch
        if not a.dry_run:
            fetched = {}
            for i, c in enumerate(ranked):
                r = fetch(c["url"])
                fetched[c["url"]] = r["status"]
                if r["status"] == "ok":
                    open(os.path.join(corpus, f"doc{i}.md"), "w",
                         encoding="utf-8").write(f"# {c.get('title','')}\n{r['text']}")

        # 5. evidence chunks (query-relevant sentences, deterministic)
        files = sorted(f for f in os.listdir(corpus) if f.endswith(".md"))
        url_of = ({c["url"]: f"doc{i}.md" for i, c in enumerate(ranked)}
                  if not a.dry_run else
                  {"https://example.com/perplexity-guide": "doc0.md"})
        evidence, n = [], 0
        order = ranked if not a.dry_run else cands[:fetch_n]
        for c in order:
            fn = url_of.get(c["url"])
            if not fn or not os.path.exists(os.path.join(corpus, fn)):
                continue
            n += 1
            text = open(os.path.join(corpus, fn), encoding="utf-8").read()
            evidence.append({"n": n, "title": c.get("title", ""), "url": c["url"],
                             "file": fn,
                             "chunks": synthesize.pick_sentences(a.query or "dry-run", text)})

        # 6. synthesize
        method = a.synth
        if method == "auto":
            method = ("llm" if os.environ.get("ANTHROPIC_API_KEY")
                      or os.environ.get("OPENAI_API_KEY") else "extractive")
        if method == "llm":
            try:
                body = synthesize.llm_rewrite(a.query, evidence)
                bullets, claims = [body], []  # claims extracted below
                import re
                for m in re.finditer(r"[^.\n]*\d[^.\n]*\.", body):
                    claims.append({"id": f"c{len(claims)+1}", "text": m.group(0),
                                   "quote": " ".join(m.group(0).split()[:25]),
                                   "url": (evidence[0]["url"] if evidence else "")})
            except RuntimeError as e:
                print(f"error: {e}", file=sys.stderr)
                return 2
        else:
            bullets, claims = synthesize.extractive(a.query or "dry-run", evidence)

        # 7. verify (existing tested script)
        idmap = {cl["id"]: cl.pop("file", "") for cl in claims if "file" in cl}
        qj = os.path.join(tmp, "claims.json")
        json.dump(claims, open(qj, "w", encoding="utf-8"), ensure_ascii=False)
        vcmd = [sys.executable, os.path.join(HERE, "scripts", "verify-citations.py"),
                "--claims", qj, "--corpus", corpus]
        if idmap:
            mj = os.path.join(tmp, "map.json")
            json.dump(idmap, open(mj, "w", encoding="utf-8"))
            vcmd += ["--map", mj]
        verdict = json.loads(run(vcmd)) if claims else {
            "verified": [], "unverified": [], "stats": {"pass_rate": 0}}

        # 8. compose
        ok_ids = {v.get("text") for v in verdict["verified"]}
        lines = [f"## Answer — {a.query or 'dry-run'}", ""]
        lines += [bl for bl in bullets
                  if method == "llm" or any(bl.startswith(f"- {v['text']}")
                                            for v in verdict["verified"])] or bullets
        lines += ["", "## Sources", ""]
        for e in evidence:
            st = "read-full" if e["file"] in (os.listdir(corpus)) else "missing"
            lines.append(f"{e['n']}. [{e['title']}]({e['url']}) — {st}")
        if verdict["unverified"]:
            lines += ["", "## Unverified (not cited as fact)", ""]
            lines += [f"- {u['text']}" for u in verdict["unverified"]]
        if method == "extractive" and not a.dry_run:
            lines += ["", "_Extractive draft — set ANTHROPIC_API_KEY/OPENAI_API_KEY "
                      "or --synth llm for fluent rewrite (re-verified)._"]
        dt = (datetime.datetime.now(datetime.timezone.utc) - t0).total_seconds()
        lines += ["", "## Provenance",
                  f"model=search-pro/1.0.1+{method} mode={a.mode} "
                  f"provider={a.provider} queries={len(queries)} "
                  f"fetched={len(evidence)} verified={verdict['stats'].get('verified', len(verdict['verified']))}/{len(claims)} "
                  f"pass_rate={verdict['stats']['pass_rate']} elapsed={dt:.1f}s "
                  f"{t0.isoformat()}"]
        out = "\n".join(lines)
        print(out)
        if a.out:
            open(a.out, "w", encoding="utf-8").write(out)
            print(f"\nwrote {a.out}", file=sys.stderr)
        return 0
    except RuntimeError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
