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

VERSION = "3.0.0"

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


class FatalConfigError(RuntimeError):
    """Provider/LLM setup failures -> exit code 2 (not 1)."""


# v2.5 2.1: agentic-light loop defaults per mode (overridable via
# --max-loops / SEARCH_MAX_LOOPS; 0 disables re-search).
DEFAULT_LOOPS = {"best": 0, "pro": 1, "research": 2}
MIN_VERIFIED_STOP = 8
MAX_UNVERIFIED_RATIO_STOP = 0.25


def fanout_search(queries, provider, searxng_url, per_query, gap):
    cands = []
    for i, q in enumerate(queries):
        try:
            cands += providers.search(q, provider=provider, limit=per_query,
                                      searxng_url=searxng_url)
        except RuntimeError as e:
            print(f"error: {e}", file=sys.stderr)
            raise FatalConfigError(str(e)) from e
        if gap and i < len(queries) - 1:
            __import__("time").sleep(gap)
    return cands


def rerank_stage(cands, query, fetch_n, use_advanced, tmp, tag):
    cj = os.path.join(tmp, f"candidates-{tag}.json")
    json.dump(cands, open(cj, "w", encoding="utf-8"), ensure_ascii=False)
    cmd = [sys.executable, os.path.join(HERE, "scripts", "rerank.py"),
           "--input", cj, "--query", query, "--top-k", str(fetch_n)]
    if use_advanced:
        cmd.append("--advanced")
    ranked = json.loads(run(cmd))
    method = ranked[0].get("_method", "?") if ranked else "?"
    return ranked, method


def fetch_stage(ranked, corpus, start_idx, seen_urls, fetched, vias,
                url_to_file, url_to_title):
    """Fetch unseen URLs into corpus/doc{idx}.md. Returns (next_idx, n_new)."""
    idx, n_new = start_idx, 0
    for c in ranked:
        u = c.get("url", "")
        if not u:
            continue
        url_to_title.setdefault(u, c.get("title", ""))
        if u in seen_urls:
            continue
        seen_urls.add(u)
        r = fetch(u)
        fetched[u] = r["status"]
        vias[u] = r.get("via", "direct")
        if r["status"] == "ok":
            fn = f"doc{idx}.md"
            open(os.path.join(corpus, fn), "w",
                 encoding="utf-8").write(f"# {c.get('title', '')}\n{r['text']}")
            url_to_file[u] = fn
            idx += 1
            n_new += 1
    return idx, n_new


def build_evidence(url_to_file, url_to_title, corpus, query):
    evidence, n = [], 0
    for u, fn in sorted(url_to_file.items(), key=lambda kv: kv[1]):
        path = os.path.join(corpus, fn)
        if not os.path.exists(path):
            continue
        n += 1
        text = open(path, encoding="utf-8").read()
        evidence.append({"n": n, "title": url_to_title.get(u, ""), "url": u,
                         "file": fn,
                         "chunks": synthesize.pick_sentences(query, text)})
    return evidence


def synthesize_stage(query, evidence, method, a):
    if method == "llm":
        try:
            body = synthesize.llm_rewrite(
                query, evidence, provider=a.llm_provider,
                model=a.llm_model or None,
                reasoning_effort=a.reasoning_effort or None,
                style=("structured" if a.mode == "research" else "default"))
            claims = synthesize.attribute_claims(body, evidence)
            log(f"synthesized via llm: {len(claims)} attributed claims")
            return [body], claims
        except RuntimeError as e:
            print(f"error: {e}", file=sys.stderr)
            raise FatalConfigError(str(e)) from e
    return synthesize.extractive(query, evidence)


def verify_stage(claims, corpus, tmp, tag):
    for cl in claims:  # stash attribution before the verifier pops "file"
        if "file" in cl:
            cl["_file"] = cl.get("file", "")
    idmap = {cl["id"]: cl.pop("file", "") for cl in claims if "file" in cl}
    qj = os.path.join(tmp, f"claims-{tag}.json")
    json.dump(claims, open(qj, "w", encoding="utf-8"), ensure_ascii=False)
    vcmd = [sys.executable, os.path.join(HERE, "scripts", "verify-citations.py"),
            "--claims", qj, "--corpus", corpus]
    if idmap:
        mj = os.path.join(tmp, f"map-{tag}.json")
        json.dump(idmap, open(mj, "w", encoding="utf-8"))
        vcmd += ["--map", mj]
    verdict = json.loads(run(vcmd)) if claims else {
        "verified": [], "unverified": [], "stats": {"pass_rate": 0}}
    return verdict


def apply_gate(verdict, claims, evidence, corpus):
    """Pointer-without-backing demotion. Returns (verdict, n_demoted)."""
    try:
        from corroborate import (origin_of as _org, body_has_figure as _bhf,
                                 norm_fig as _nf, extract_figures as _xf)
    except ImportError:
        return verdict, 0
    import re as _re
    n_demoted = 0
    if not verdict.get("verified"):
        return verdict, 0
    f2u = {e["file"]: e["url"] for e in evidence}
    bodies = {fn: open(os.path.join(corpus, fn), encoding="utf-8").read()
              for fn in os.listdir(corpus) if fn.endswith(".md")}
    claim_file = {cl["id"]: cl.get("_file", "") for cl in claims}
    keep, drop = [], []
    for v in verdict["verified"]:
        own_fn = claim_file.get(v["id"], "")
        if _org(f2u.get(own_fn, ""), "").startswith("pointer:"):
            # v2.6.1: bare years are not backing. A year (2026) appearing in
            # another body says nothing about the pointer's figure (2,5 detik),
            # so year norms are excluded before the independence check.
            figs = {_nf(m) for m in _xf(v.get("text", ""))
                    if not _re.fullmatch(r"(19|20)\d{2}", _nf(m))} - {""}
            backed = any(
                not _org(f2u.get(fn, ""), "").startswith("pointer:")
                and any(_bhf(txt, x) for x in figs)
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
    return verdict, n_demoted


def generate_followups(query, verdict, intent, seen_queries, limit=4):
    """Rule-based follow-up queries for loop>0 (v2.5 2.1)."""
    year = str(datetime.datetime.now().year)
    cands = []
    unverified = verdict.get("unverified", []) if verdict else []
    if any("pointer" in str(u.get("gate", "")) for u in unverified):
        cands.append(f"{query} official source OR government OR report")
    if intent.get("is_numeric"):
        cands.append(f"{query} statistics OR data OR angka resmi")
    if intent.get("is_compare"):
        cands.append(f"{query} vs comparison review terbaru")
    cands.append(f"{query} {year} latest update")
    cands.append(f"{query} official OR laporan OR announcement")
    out = []
    for q in cands:
        if q.lower() not in seen_queries:
            seen_queries.add(q.lower())
            out.append(q)
        if len(out) >= limit:
            break
    return out


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
    ap.add_argument("--max-loops", type=int, default=None,
                    help="agentic re-search loops (default per mode: best 0, "
                         "pro 1, research 2; 0 disables; also SEARCH_MAX_LOOPS)")
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
    if a.max_loops is not None:
        max_loops = max(0, a.max_loops)
    elif (os.environ.get("SEARCH_MAX_LOOPS", "") or "").strip() != "":
        try:
            max_loops = max(0, int(os.environ["SEARCH_MAX_LOOPS"]))
        except ValueError:
            max_loops = DEFAULT_LOOPS[a.mode]
    else:
        max_loops = DEFAULT_LOOPS[a.mode]
    if a.dry_run:
        max_loops = 0
    # v2.4 1.3: BGE cross-encoder is default on pro/research (falls back
    # to heuristic inside rerank.py when `rerankers` isn't installed).
    use_advanced = (a.advanced or a.mode in ("pro", "research")) \
        and not a.no_advanced
    method = a.synth
    if method == "auto":
        method = ("llm" if os.environ.get("ANTHROPIC_API_KEY")
                  or os.environ.get("OPENAI_API_KEY")
                  or os.environ.get("OPENROUTER_API_KEY") else "extractive")
    tmp = tempfile.mkdtemp(prefix="searchpro_")
    corpus = os.path.join(tmp, "corpus")
    os.makedirs(corpus, exist_ok=True)
    t0 = datetime.datetime.now(datetime.timezone.utc)
    intent = detect_intent(a.query or "")

    try:
        seen_urls, seen_queries = set(), set()
        url_to_file, url_to_title = {}, {}
        fetched, vias = {}, {}
        doc_idx = 0
        expand_fn = None
        if a.expand_queries or os.environ.get("SEARCH_QUERY_EXPANSION") == "1":
            def expand_fn(q, n, _a=a):
                return synthesize.llm_expand_queries(
                    q, n, provider=_a.llm_provider,
                    model=_a.llm_model or None)
        if a.dry_run:
            cands = json.load(open(os.path.join(HERE, "examples", "candidates.json"),
                                   encoding="utf-8"))
            queries = ["dry-run fixture"]
            fetched = {"examples/corpus/doc1.md": "ok"}
            shutil_corpus = os.path.join(HERE, "examples", "corpus", "doc1.md")
            open(os.path.join(corpus, "doc0.md"), "w", encoding="utf-8").write(
                open(shutil_corpus, encoding="utf-8").read())
            url_to_file = {"https://example.com/perplexity-guide": "doc0.md"}
            url_to_title = {"https://example.com/perplexity-guide":
                            cands[0].get("title", "")}
            seen_urls.add("https://example.com/perplexity-guide")

        bullets, claims, evidence = [], [], []
        verdict = {"verified": [], "unverified": [],
                   "stats": {"pass_rate": 0}}
        n_demoted, rerank_method = 0, "?"
        followups_all, loops_used = [], 0
        # v2.6.2: run-json accumulates across loops (queries, candidates,
        # fetched) instead of reflecting only the last loop.
        all_queries, n_candidates_total = [], 0
        gap = float(os.environ.get("SEARCH_GAP", "1.0"))

        for loop in range(max_loops + 1):
            loops_used = loop + 1
            if a.dry_run:
                pass  # fixture queries/cands already staged above
            elif loop == 0:
                queries = plan_queries(a.query, a.mode,
                                       llm_expand_fn=expand_fn)
                seen_queries.update(q.lower() for q in queries)
                log(f"planned {len(queries)} queries (mode={a.mode})")
                cands = fanout_search(
                    queries, a.provider, a.searxng_url, b["per_query"], gap)
                log(f"fan-out done: {len(cands)} candidates")
            else:
                queries = generate_followups(a.query, verdict, intent,
                                             seen_queries)
                if not queries:
                    log("loop %d: no new follow-ups, stop" % loop)
                    loops_used = loop
                    break
                followups_all += queries
                log(f"loop {loop}: follow-up queries: {queries}")
                cands = fanout_search(
                    queries, a.provider, a.searxng_url, b["per_query"], gap)
            all_queries += queries
            n_candidates_total += len(cands)

            # 3. rerank
            if a.dry_run:
                ranked, rerank_method = cands[:fetch_n], "fixture"
            else:
                ranked, rerank_method = rerank_stage(
                    [c for c in cands if c.get("url") not in seen_urls] or cands,
                    a.query, fetch_n, use_advanced, tmp, f"l{loop}")
            log(f"loop {loop}: reranked {len(ranked)} via {rerank_method}")

            # 4. fetch (unseen only)
            if not a.dry_run:
                doc_idx, n_new = fetch_stage(
                    ranked, corpus, doc_idx, seen_urls, fetched, vias,
                    url_to_file, url_to_title)
                log(f"loop {loop}: fetched {n_new} new ok "
                    f"({len(url_to_file)} docs total)")
                if loop > 0 and n_new == 0:
                    log(f"loop {loop}: nothing new, stop")
                    loops_used = loop
                    break

            # 5. evidence (rebuilt over merged corpus each loop)
            if a.dry_run:
                url_of = {"https://example.com/perplexity-guide": "doc0.md"}
                evidence, n = [], 0
                for c in cands[:fetch_n]:
                    fn = url_of.get(c["url"])
                    if not fn or not os.path.exists(os.path.join(corpus, fn)):
                        continue
                    n += 1
                    text = open(os.path.join(corpus, fn),
                                encoding="utf-8").read()
                    evidence.append({"n": n, "title": c.get("title", ""),
                                     "url": c["url"], "file": fn,
                                     "chunks": synthesize.pick_sentences(
                                         "dry-run", text)})
            else:
                evidence = build_evidence(url_to_file, url_to_title,
                                          corpus, a.query)

            # 6-7. synthesize + verify + gate
            bullets, claims = synthesize_stage(a.query or "dry-run",
                                               evidence, method, a)
            verdict = verify_stage(claims, corpus, tmp, f"l{loop}")
            verdict, n_demoted = apply_gate(verdict, claims, evidence, corpus)
            if n_demoted:
                log(f"corroboration gate demoted {n_demoted} claim(s)")
            n_ver = len(verdict.get("verified", []))
            n_unv = len(verdict.get("unverified", []))
            ratio = n_unv / max(1, n_ver + n_unv)
            log(f"loop {loop}: verified={n_ver} unverified={n_unv} "
                f"ratio={ratio:.2f}")
            if ((n_ver >= MIN_VERIFIED_STOP
                    and ratio < MAX_UNVERIFIED_RATIO_STOP)
                    or loop == max_loops):
                break

        # 7c. conflicts across verified claims (v2.5 2.3)
        conflicts = synthesize.detect_conflicts(verdict.get("verified", []))
        if conflicts:
            log(f"conflicting reports: {len(conflicts)} unit(s)")

        # 7d. source authority & diversity (v2.6 3.2)
        try:
            from corroborate import diversity_report as _div
            diversity = _div([e.get("url", "") for e in evidence])
        except ImportError:
            diversity = {"n_sources": len(evidence), "n_publishers": 0,
                         "authority": {}, "authority_mean": 0.0,
                         "pointer_ratio": 0.0, "top_publisher_share": 0.0}
        log(f"diversity: {diversity['n_publishers']} publishers, "
            f"authority_mean={diversity['authority_mean']}, "
            f"top_share={diversity['top_publisher_share']}")

        # 8. compose
        lines = [f"## Answer — {a.query or 'dry-run'}", ""]
        lines += [bl for bl in bullets
                  if method == "llm" or any(bl.startswith(f"- {v['text']}")
                                            for v in verdict["verified"])] or bullets
        lines += ["", "## Sources", ""]
        for e in evidence:
            st = "read-full" if e["file"] in (os.listdir(corpus)) else "missing"
            lines.append(f"{e['n']}. [{e['title']}]({e['url']}) — {st}")
        if conflicts:
            lines += ["", "## Conflicting reports", ""]
            for cf in conflicts:
                vals = "; ".join(
                    f"{v['value']} — {v['text'][:140]}" for v in cf["values"])
                lines.append(f"- unit `{cf['unit']}`: {vals}")
        if verdict["unverified"]:
            lines += ["", "## Unverified (not cited as fact)", ""]
            lines += [f"- {u['text']}" for u in verdict["unverified"]]
        if method == "extractive" and not a.dry_run:
            lines += ["", "_Extractive draft — set ANTHROPIC_API_KEY / OPENAI_API_KEY / "
                      "OPENROUTER_API_KEY or --synth llm for fluent rewrite (re-verified)._"]
        dt = (datetime.datetime.now(datetime.timezone.utc) - t0).total_seconds()
        lines += ["", "## Provenance",
                  f"model=search-pro/{VERSION}+{method} mode={a.mode} "
                  f"provider={a.provider} queries={len(all_queries)} "
                  f"fetched={len(evidence)} verified={verdict['stats'].get('verified', len(verdict['verified']))}/{len(claims)} "
                  f"demoted={n_demoted} rerank={rerank_method} loops={loops_used} "
                  f"auth={diversity['authority'].get('primary', 0)}/"
                  f"{diversity['authority'].get('secondary', 0)}/"
                  f"{diversity['authority'].get('derivative', 0)} "
                  f"pubs={diversity['n_publishers']} "
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
                "queries_used": all_queries,
                "n_candidates": n_candidates_total,
                "rerank_method": rerank_method, "demoted": n_demoted,
                "loops_used": loops_used, "followups": followups_all,
                "n_conflicts": len(conflicts), "diversity": diversity,
                "ranked": [{"url": c["url"], "score": c.get("_score"),
                            "method": c.get("_method")} for c in ranked],
                # v2.6.2: every fetched URL across all loops, in fetch order
                # (was: last loop's ranking only).
                "fetched": ([{"url": u, "status": fetched.get(u),
                               "via": vias.get(u, "direct")}
                              for u in (list(url_to_file)
                                        + [u for u in fetched
                                           if u not in url_to_file])]
                             if not a.dry_run else
                             [{"url": "fixture", "status": v, "via": "direct"}
                              for v in fetched.values()]),
                "n_evidence": len(evidence),
                "verdict": verdict["stats"],
                "elapsed_s": round(dt, 1), "ts": t0.isoformat(),
            }
            json.dump(summary, open(a.run_json, "w", encoding="utf-8"),
                      ensure_ascii=False, indent=2)
        return 0
    except FatalConfigError:
        return 2
    except RuntimeError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
