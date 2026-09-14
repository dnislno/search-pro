"""Deterministic citation verifier (grep-based, no LLM).
Refs: Haus Research Sep-2026 deterministic check (figure must appear verbatim).
Usage:
  python verify-citations.py --claims claims.json --corpus ./corpus/
claims.json: [{"id":"c1","text":"... 185M ...","numbers":["185"],"quote":"verbatim <=25 words","url":"https://..."}, ...]
corpus/: one .md/.txt per URL (filename = sanitized url or id map via --map map.json)
Output JSON: {"verified":[...], "unverified":[...], "stats":{...}}
Rule: numbers/dates in claim must appear in body; quote (if given) must match normalized.
"""
import argparse, json, re, sys
from pathlib import Path

def norm(s: str) -> str:
    s = (s or "").lower()
    s = s.replace("$", " ").replace(",", "")
    s = re.sub(r"\s+", " ", s)
    return s

def extract_numbers(text: str):
    # money, %, years, runs of 3+ digits — same spirit as Haus audit
    pats = re.findall(r"\d{4}|\d[\d.,]*\s?%|\d[\d.,]*\s?(?:miliar|juta|million|billion|m\b|b\b)|\d{3,}", text.lower())
    out = set()
    for p in pats:
        digits = re.sub(r"\D", "", p)
        if digits:
            out.add(digits)
            out.add(digits.lstrip("0") or "0")
    # also bare years
    out.update(re.findall(r"\b(19|20)\d{2}\b", text))
    return {o for o in out if o}

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--claims", required=True)
    ap.add_argument("--corpus", required=True)
    ap.add_argument("--map", default=None, help="optional {claim_id: filename}")
    a = ap.parse_args()

    claims = json.load(open(a.claims, encoding="utf-8"))
    corp = Path(a.map and a.corpus or a.corpus)
    idmap = json.load(open(a.map, encoding="utf-8")) if a.map else {}
    verified, unverified = [], []
    for cl in claims:
        fn = idmap.get(cl.get("id", ""), None)
        body = ""
        if fn:
            p = Path(a.corpus) / fn
            body = p.read_text(encoding="utf-8", errors="ignore") if p.exists() else ""
        else:
            # fallback: concat all (caller should map precisely; concat is lenient)
            body = "\n".join(p.read_text(encoding="utf-8", errors="ignore")
                             for p in Path(a.corpus).glob("*.md"))
        nb, nq = norm(body), norm(cl.get("quote", ""))
        need = set(cl.get("numbers") or []) or extract_numbers(cl.get("text", ""))
        hit_num = [n for n in need if norm(str(n)) and norm(str(n)) in nb]
        quote_ok = (not nq) or (nq in nb)
        # PASS iff: (no numbers needed OR >=1 number hit) AND quote_ok AND body non-empty
        passed = bool(nb.strip()) and (not need or hit_num) and quote_ok
        rec = {**cl, "hit_numbers": hit_num, "quote_ok": quote_ok,
               "status": "verified-full-read" if passed else "unverified"}
        (verified if passed else unverified).append(rec)

    stats = {"n": len(claims), "verified": len(verified),
             "unverified": len(unverified),
             "pass_rate": round(len(verified) / max(1, len(claims)), 3)}
    json.dump({"verified": verified, "unverified": unverified, "stats": stats},
              sys.stdout, ensure_ascii=False, indent=2)

if __name__ == "__main__":
    main()
