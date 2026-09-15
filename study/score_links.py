"""Link-liveness check for study scenario-B outputs (CIT dimension).
Extracts http(s) URLs from the three B transcripts and GETs each.
Writes study/linkcheck.json. stdlib only.
"""
import json
import os
import re
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/126.0"}
out = {}
for pid in ("P1", "P2", "P3"):
    path = os.path.join(HERE, f"scenario-B-{pid}.md")
    urls = sorted(set(re.findall(r"https?://[^\s\)\]]+", open(path, encoding="utf-8").read())))
    rows = []
    for u in urls:
        u = u.rstrip(".,")
        try:
            req = urllib.request.Request(u, headers=UA, method="GET")
            with urllib.request.urlopen(req, timeout=25) as r:
                body = r.read(200000)
                rows.append({"url": u, "http": r.status,
                             "live": r.status == 200 and len(body) > 500})
        except Exception as e:
            rows.append({"url": u, "http": None, "live": False,
                         "err": str(e)[:100]})
    out[pid] = rows
    live = sum(1 for r in rows if r["live"])
    print(f"{pid}: {live}/{len(rows)} live")
    for r in rows:
        if not r["live"]:
            print("   DEAD:", r["url"][:75], r.get("http"), r.get("err", ""))
json.dump(out, open(os.path.join(HERE, "linkcheck.json"), "w", encoding="utf-8"),
          ensure_ascii=False, indent=2)
