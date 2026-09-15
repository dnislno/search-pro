#!/usr/bin/env python3
"""Live harness: runs search.py per query, aggregates 3 metrics vs Haus baselines.
  python harness/run.py --searxng-url https://sx.xo.st [--ids F01 F02] [--mode pro]
Metrics: M1 grep pass (target >=85%, Haus 65.9%) | M2 link live (target >=98%,
Haus readable 78.7%) | M3 verified>=6 per answer (target median >=6).
Writes harness/results/<id>.json + harness/report.json. Polite 3s gap between queries.
"""
import argparse
import datetime
import json
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--searxng-url", default=os.environ.get("SEARXNG_URL", ""))
    ap.add_argument("--mode", default="pro")
    ap.add_argument("--ids", nargs="*", default=None)
    ap.add_argument("--gap", type=float, default=3.0)
    a = ap.parse_args()

    queries = json.load(open(os.path.join(HERE, "queries.json"), encoding="utf-8"))
    if a.ids:
        queries = [q for q in queries if q["id"] in a.ids]
    resdir = os.path.join(HERE, "results")
    os.makedirs(resdir, exist_ok=True)

    rows = []
    for i, q in enumerate(queries):
        out = os.path.join(resdir, f"{q['id']}.json")
        cmd = [sys.executable, os.path.join(ROOT, "search.py"),
               "--query", q["q"], "--mode", a.mode, "--provider", "searxng",
               "--searxng-url", a.searxng_url, "--synth", "extractive",
               "--run-json", out]
        t0 = time.time()
        env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
        p = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                           env=env, timeout=300)
        dt = round(time.time() - t0, 1)
        row = {"id": q["id"], "cat": q["cat"], "exit": p.returncode,
               "elapsed_s": dt}
        if p.returncode == 0 and os.path.exists(out):
            s = json.load(open(out, encoding="utf-8"))
            v = s.get("verdict", {})
            row.update({
                "verified": v.get("verified", 0),
                "unverified": v.get("unverified", 0),
                "pass_rate": v.get("pass_rate", 0),
                "fetched_ok": sum(1 for f in s.get("fetched", [])
                                  if f.get("status") == "ok"),
                "fetched_n": len(s.get("fetched", [])),
            })
        else:
            row["stderr"] = (p.stderr or "")[:2000]
        rows.append(row)
        print(f"{q['id']} exit={row['exit']} "
              f"v={row.get('verified', '-')}/{row.get('verified', 0) + row.get('unverified', 0) if 'verified' in row else '-'} "
              f"ok={row.get('fetched_ok', '-')}/{row.get('fetched_n', '-')} {dt}s",
              flush=True)
        if i < len(queries) - 1:
            time.sleep(a.gap)

    done = [r for r in rows if r["exit"] == 0 and "verified" in r]
    tv = sum(r["verified"] for r in done)
    tu = sum(r["unverified"] for r in done)
    fok = sum(r["fetched_ok"] for r in done)
    fn = sum(r["fetched_n"] for r in done)
    med = sorted(r["verified"] for r in done)
    report = {
        "ts": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "mode": a.mode, "n": len(queries), "completed": len(done),
        "M1_grep_pass": round(tv / max(1, tv + tu), 3),
        "M2_link_live": round(fok / max(1, fn), 3),
        "M3_median_verified": med[len(med) // 2] if med else 0,
        "M3_frac_ge6": round(sum(1 for r in done if r["verified"] >= 6)
                             / max(1, len(done)), 3),
        "baselines": {"haus_grep": 0.659, "haus_readable": 0.787},
        "targets": {"M1": 0.85, "M2": 0.98, "M3_median": 6},
        "rows": rows,
    }
    json.dump(report, open(os.path.join(HERE, "report.json"), "w",
                           encoding="utf-8"), ensure_ascii=False, indent=2)
    print(json.dumps({k: report[k] for k in
                      ("completed", "M1_grep_pass", "M2_link_live",
                       "M3_median_verified", "M3_frac_ge6")}, indent=2))


if __name__ == "__main__":
    sys.exit(main())
