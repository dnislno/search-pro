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

VERSION = "2.4.0"

BUDGETS = {
    "best": {"queries": 4, "per_query": 5, "fetch": 4},
    "pro": {"queries": 8, "per_query": 6, "fetch": 8},
    "research": {"queries": 12, "per_query": 8, "fetch": 10},
}

_ID_MARKERS = frozenset(
    "yang dan harga berapa jumlah daftar terbaru penduduk rupiah indonesia "
    "vs tren".split())


def log(msg):
    print(f"[search-pro] {msg}", file=sys.stderr)


def detect_intent(query):
    """Rule-based intent flags driving query planning (v2.4 1.1)."""
    import re as _re
    q = (query or "").lower()
    return {
        "is_compare": bool(_re.search(
            r"\b(vs|versus|bandingkan|dibanding|perbandingan|compar\w*)\b", q)),
        "is_numeric": bool(_re.search(
            r"\b(berapa|harga|jumlah|total|ukuran|size|price|cost|fine|denda|"
            r"statistic|data|angka|persen|percent)\b", q)),
        "is_fresh": bool(_re.search(
            r"\b(terbaru|latest|update|sekarang|current|202[5-9])\b", q)),
        "is_academic": bool(_re.search(
            r"\b(paper|studi|penelitian|jurnal|research|study|thesis)\b", q)),
        "lang": "id" if set(q.split()) & _ID_MARKERS else "en",
    }


def plan_queries(query, mode, llm_expand_fn=None):
    """Build up to BUDGETS[mode]['queries'] variants (v2.4 1.1).

    Rule-based core always fills the budget (zero-deps); llm_expand_fn, when
    given, supplies extra smart variants for remaining slots.
    """
    import re as _re
    budget = BUDGETS[mode]["queries"]
    intent = detect_intent(query)
    year = str(datetime.datetime.now().year)
    variants = [query]
    important = _re.findall(r"[A-Za-z0-9]{4,}", query or "")
    important = [w for w in important if len(w) > 4][:5]
    if important:
        variants.append(f'"{" ".join(important[:3])}"')
    variants.append(f"{query} {year}")
    variants.append(f"{query} {int(year) - 1}")
    if important:
        variants.append(f'"{" ".join(important[:3])}" {year}')
    if intent["is_numeric"]:
        variants.append(f"{query} data OR statistics OR angka OR jumlah")
        variants.append(f"{query} official OR report OR laporan")
    else:
        variants.append(f"{query} statistics OR data")
    if intent["is_compare"]:
        variants.append(f"{query} comparison OR vs OR perbedaan")
    if intent["is_academic"]:
        variants.append(f"{query} study OR paper OR research OR jurnal")
    if intent["is_fresh"]:
        variants.append(f"{query} after:{int(year) - 1}")
    if intent["lang"] == "id":
        variants.append(f"{query} site:go.id OR site:ac.id OR site:edu")
        variants.append(f"{query} site:go.id")
        variants.append(f"{query} site:bps.go.id OR site:kemenkeu.go.id OR site:bi.go.id")
    else:
        variants.append(f"{query} site:gov OR site:edu OR site:org")
        variants.append(f"{query} site:gov")
        variants.append(f"{query} official report")
    variants.append(f"{query} terbaru OR latest OR update")
    # Generic fillers so the budget is always honored zero-deps (LLM
    # expansion only replaces/augments these when explicitly enabled).
    variants.append(f"{query} filetype:pdf OR pdf")
    variants.append(f"{query} explained OR analysis OR dibahas")
    variants.append(f"{query} news OR berita OR kabar")
    seen, clean = set(), []
    for v in variants:
        k = v.lower().strip()
        if k not in seen:
            seen.add(k)
            clean.append(v)
    # Last-resort padding: guarantees the budget for any non-empty query.
    for suffix in ("overview", "faq", "wiki", "history OR sejarah",
                   "definition OR definisi"):
        if len(clean) >= budget:
            break
        cand = f"{query} {suffix}"
        if cand.lower() not in seen:
            seen.add(cand.lower())
            clean.append(cand)
    if llm_expand_fn is not None and len(clean) < budget:
        try:
            for e in llm_expand_fn(query, budget - len(clean)) or []:
                if e.lower().strip() not in seen:
                    seen.add(e.lower().strip())
                    clean.append(e)
        except Exception as e:
            log(f"query expansion skipped: {e}")
    return clean[:budget]


def run(cmd):
    env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
    p = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                       env=env)
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
    ap.add_argument("--llm-provider", choices=["auto", "anthropic", "openai", "openrouter"],
                    default="auto", help="LLM backend for --synth llm")
    ap.add_argument("--llm-model", default="",
                    help="override model id (default: OPENROUTER_MODEL or nex-agi/nex-n2.5-pro:free)")
    ap.add_argument("--reasoning-effort", default="",
                    help="opt-in OpenRouter reasoning effort "
                         "(max|xhigh|high|medium|low|minimal; default: off). "
                         "Also via OPENROUTER_REASONING_EFFORT env")
    ap.add_argument("--advanced", action="store_true", help="BGE cross-encoder rerank")
    ap.add_argument("--no-advanced", action="store_true",
                    help="force heuristic rerank (overrides auto-BGE on pro/research)")
    ap.add_argument("--expand-queries", action="store_true",
                    help="opt-in LLM query expansion for leftover budget slots "
                         "(also via SEARCH_QUERY_EXPANSION=1; needs LLM key)")
    ap.add_argument("--top-k", type=int, default=0, help="override fetch budget")
    ap.add_argument("--out", default="", help="write markdown answer to file")
    ap.add_argument("--run-json", default="",
                    help="write machine-readable run summary (for harness) to file")
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
            expand_fn = None
            if a.expand_queries or os.environ.get("SEARCH_QUERY_EXPANSION") == "1":
                def expand_fn(q, n, _a=a):
                    return synthesize.llm_expand_queries(
                        q, n, provider=_a.llm_provider,
                        model=_a.llm_model or None)
            queries = plan_queries(a.query, a.mode, llm_expand_fn=expand_fn)
            log(f"planned {len(queries)} queries (mode={a.mode})")
            cands, vias = [], {}
            gap = float(os.environ.get("SEARCH_GAP", "1.0"))
            for i, q in enumerate(queries):
                try:
                    cands += providers.search(q, provider=a.provider,
                                              limit=b["per_query"],
                                              searxng_url=a.searxng_url)
                except RuntimeError as e:
                    print(f"error: {e}", file=sys.stderr)
                    return 2
                if gap and i < len(queries) - 1:
                    __import__("time").sleep(gap)
            log(f"fan-out done: {len(cands)} candidates")

        # 3. rerank (existing tested script)
        cj = os.path.join(tmp, "candidates.json")
        json.dump(cands, open(cj, "w", encoding="utf-8"), ensure_ascii=False)
        # v2.4 1.3: BGE cross-encoder is default on pro/research (falls back
        # to heuristic inside rerank.py when `rerankers` isn't installed).
        use_advanced = (a.advanced or a.mode in ("pro", "research")) \
            and not a.no_advanced
        cmd = [sys.executable, os.path.join(HERE, "scripts", "rerank.py"),
               "--input", cj, "--query", a.query or "dry-run",
               "--top-k", str(fetch_n)]
        if use_advanced:
            cmd.append("--advanced")
        ranked = json.loads(run(cmd))
        rerank_method = (ranked[0].get("_method", "?") if ranked else "?")
        log(f"reranked: {len(ranked)} kept via {rerank_method}")

        # 4. fetch
        if not a.dry_run:
            fetched = {}
            for i, c in enumerate(ranked):
                r = fetch(c["url"])
                fetched[c["url"]] = r["status"]
                vias[c["url"]] = r.get("via", "direct")
                if r["status"] == "ok":
                    open(os.path.join(corpus, f"doc{i}.md"), "w",
                         encoding="utf-8").write(f"# {c.get('title','')}\n{r['text']}")
            nok = sum(1 for v in fetched.values() if v == "ok")
            log(f"fetched: {nok}/{len(ranked)} ok")

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
                      or os.environ.get("OPENAI_API_KEY")
                      or os.environ.get("OPENROUTER_API_KEY") else "extractive")
        if method == "llm":
            try:
                body = synthesize.llm_rewrite(
                    a.query, evidence, provider=a.llm_provider,
                    model=a.llm_model or None,
                    reasoning_effort=a.reasoning_effort or None)
                # v2.4 1.2: marker-aware attribution so every claim carries
                # file+url and the corroboration gate below can fire.
                claims = synthesize.attribute_claims(body, evidence)
                bullets = [body]
                log(f"synthesized via llm: {len(claims)} attributed claims")
            except RuntimeError as e:
                print(f"error: {e}", file=sys.stderr)
                return 2
        else:
            bullets, claims = synthesize.extractive(a.query or "dry-run", evidence)

        # 7. verify (existing tested script)
        claim_file = {cl["id"]: cl.get("file", "") for cl in claims}
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

        # 7b. corroboration gate (wired from v2.2.0 experiment): claims whose
        # only backing is a pointer source (social/forums) need an independent
        # verbatim backing in a non-pointer file, else demoted. Fixes study P2.
        # v2.4 1.2: LLM-path claims now carry file attribution, so this fires.
        n_demoted = 0
        try:
            from corroborate import (origin_of as _org,
                                     body_has_figure as _bhf,
                                     norm_fig as _nf,
                                     extract_figures as _xf)
        except ImportError:
            _org = None
        if _org is not None and verdict.get("verified"):
            f2u = {e["file"]: e["url"] for e in evidence}
            bodies = {fn: open(os.path.join(corpus, fn), encoding="utf-8").read()
                      for fn in os.listdir(corpus) if fn.endswith(".md")}
            keep, drop = [], []
            for v in verdict["verified"]:
                own_fn = claim_file.get(v["id"], "")
                if _org(f2u.get(own_fn, ""), "").startswith("pointer:"):
                    figs = {_nf(m) for m in _xf(v.get("text", ""))} - {""}
                    backed = any(
                        not _org(f2u.get(fn, ""), "").startswith("pointer:")
                        and any(_bhf(txt, n) for n in figs)
                        for fn, txt in bodies.items() if fn != own_fn)
                    if not backed:
                        v["gate"] = "demoted:pointer-without-backing"
                        drop.append(v)
                        continue
                keep.append(v)
            verdict["verified"] = keep
            verdict["unverified"] = drop + verdict.get("unverified", [])
            n_demoted = len(drop)
            verdict["stats"] = {"n": len(claims), "verified": len(keep),
                                "unverified": len(verdict["unverified"]),
                                "pass_rate": round(len(keep) / max(1, len(claims)), 3)}
        if n_demoted:
            log(f"corroboration gate demoted {n_demoted} pointer-backed claim(s)")

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
            lines += ["", "_Extractive draft — set ANTHROPIC_API_KEY / OPENAI_API_KEY / "
                      "OPENROUTER_API_KEY or --synth llm for fluent rewrite (re-verified)._"]
        dt = (datetime.datetime.now(datetime.timezone.utc) - t0).total_seconds()
        lines += ["", "## Provenance",
                  f"model=search-pro/{VERSION}+{method} mode={a.mode} "
                  f"provider={a.provider} queries={len(queries)} "
                  f"fetched={len(evidence)} verified={verdict['stats'].get('verified', len(verdict['verified']))}/{len(claims)} "
                  f"demoted={n_demoted} rerank={rerank_method} "
                  f"pass_rate={verdict['stats']['pass_rate']} elapsed={dt:.1f}s "
                  f"{t0.isoformat()}"]
        out = "\n".join(lines)
        try:
            print(out)
        except UnicodeEncodeError:  # Windows cp1252 console
            sys.stdout.buffer.write((out + "\n").encode("utf-8", "replace"))
        if a.out:
            open(a.out, "w", encoding="utf-8").write(out)
            print(f"\nwrote {a.out}", file=sys.stderr)
        if a.run_json:
            summary = {
                "query": a.query or "dry-run", "mode": a.mode,
                "provider": a.provider, "synth": method,
                "version": VERSION,
                "queries_used": queries, "n_candidates": len(cands),
                "rerank_method": rerank_method, "demoted": n_demoted,
                "ranked": [{"url": c["url"], "score": c.get("_score"),
                            "method": c.get("_method")} for c in ranked],
                "fetched": ([{"url": c["url"], "status": fetched.get(c["url"]),
                               "via": vias.get(c["url"], "direct")}
                              for c in ranked] if not a.dry_run else
                             [{"url": "fixture", "status": v, "via": "direct"}
                              for v in fetched.values()]),
                "n_evidence": len(evidence),
                "verdict": verdict["stats"],
                "elapsed_s": round(dt, 1), "ts": t0.isoformat(),
            }
            json.dump(summary, open(a.run_json, "w", encoding="utf-8"),
                      ensure_ascii=False, indent=2)
        return 0
    except RuntimeError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
