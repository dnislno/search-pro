"""Synthesis: extractive composer (offline, deterministic) + optional LLM pass.
Extractive output is built from verbatim source sentences, so the grep
verifier passes structurally — the honest offline default.
LLM pass (Anthropic/OpenAI via env keys) rewrites fluently; its claims are
then re-verified and anything unsupported is demoted to Unverified.
"""
import json
import os
import re
import urllib.error
import urllib.request

SENT_SPLIT = re.compile(r"(?<=[.!?])\s+")


def tokens(s):
    return re.findall(r"[a-z0-9]{3,}", (s or "").lower())


def pick_sentences(query, text, k=5):
    """Query-relevant sentences (v2.5 2.2): token overlap + figure/number
    bonus + generic penalty. Returns up to k (default 5, was 3)."""
    q = set(tokens(query))
    q_figs = {re.sub(r"\D", "", m)
              for m in re.findall(r"\d[\d.,]*", query or "")} - {""}
    scored = []
    for s in SENT_SPLIT.split(text or ""):
        s = s.strip()
        if len(s) < 40 or len(s) > 600:
            continue
        t = tokens(s)
        overlap = len(q & set(t)) / max(1, len(q))
        sent_figs = {re.sub(r"\D", "", m)
                     for m in re.findall(r"\d[\d.,]*", s)} - {""}
        fig_bonus = 0.4 if (sent_figs & q_figs) else 0.0
        has_number = 0.15 if re.search(r"\d", s) else 0.0
        generic_pen = -0.3 if len(set(t)) < 6 else 0.0
        score = 2.5 * overlap + fig_bonus + has_number + generic_pen
        scored.append((score, -len(s), s))
    scored.sort(reverse=True)
    return [s for _, _, s in scored[:k]]


_CONFLICT_RE = re.compile(
    r"(\d[\d.,]*)\s*(%|percent|persen|million|billion|miliar|juta|triliun|"
    r"kg|km|mAh|rupiah|\brp\b|usd|\$|votes?|suara|points?|poin|seats?|kursi)",
    flags=re.I)

# v2.6.1: month names never serve as topic anchors.
_MONTHS = frozenset(
    "januari februari maret april mei juni juli agustus september oktober "
    "november desember jan feb mar apr may jun jul aug sep oct nov dec "
    "january february march april june july august october".split())


def _anchors(text):
    """Proper-noun topic anchors: capitalized tokens excluding the sentence
    opener and month names. Used to keep same-unit figures from unrelated
    contexts (kurs vs harga) out of one conflict group."""
    toks = re.findall(r"[A-Za-z][A-Za-z0-9.]*", text or "")
    out = set()
    for i, t in enumerate(toks):
        if i == 0 or len(t) < 2:
            continue
        # Starts-uppercase (Meta, Jakarta) or camelCase brand (iPhone, eBay).
        if not (t[0].isupper() or any(c.isupper() for c in t[1:])):
            continue
        low = t.lower().rstrip(".")
        if low in _MONTHS:
            continue
        out.add(low)
    return out


def detect_conflicts(verified_claims, limit=5):
    """Group verified claims by unit; a unit with >=2 distinct normalized
    values is a conflict (v2.5 2.3). v2.6.1: values join one group only when
    they share a topic anchor (or both sides are anchorless) -- rival figures
    about different things (exchange rate vs phone price) stay separate.
    Returns [{unit, values:[{value, text}]}]. stdlib only, heuristic by
    design -- reviewers see both numbers."""
    groups = {}
    for cl in verified_claims or []:
        text = cl.get("text", "")
        anchors = _anchors(text)
        for num, unit in _CONFLICT_RE.findall(text):
            norm = re.sub(r"\D", "", num)
            if not norm or re.fullmatch(r"(19|20)\d{2}", norm):
                continue  # bare years are never rivals
            key = unit.lower().replace("$", "usd")
            slot = groups.setdefault(key, {})
            prev = slot.get(norm)
            if prev is None:
                slot[norm] = (text, anchors)
            else:
                slot[norm] = (prev[0], prev[1] | anchors)
    out = []
    for unit, vals in groups.items():
        items = list(vals.items())
        rivals = []
        for i, (norm_i, (text_i, anch_i)) in enumerate(items):
            for norm_j, (text_j, anch_j) in items[i + 1:]:
                if norm_i == norm_j:
                    continue
                if (anch_i & anch_j) or (not anch_i and not anch_j):
                    if norm_i not in [v for v, _ in rivals]:
                        rivals.append((norm_i, text_i))
                    if norm_j not in [v for v, _ in rivals]:
                        rivals.append((norm_j, text_j))
        if rivals:
            out.append({"unit": unit,
                        "values": [{"value": v, "text": t}
                                   for v, t in rivals[:3]]})
        if len(out) >= limit:
            break
    return out


def extractive(query, evidence):
    """evidence: [{"n", "title", "url", "chunks":[sent,...]}, ...]
    Returns (bullets, claims) where claims mirror verifier input schema."""
    bullets, claims = [], []
    for ev in evidence:
        for s in ev["chunks"]:
            bullets.append(f"- {s} [[{ev['n']}]]({ev['url']})")
            claims.append({
                "id": f"c{len(claims) + 1}",
                "text": s,
                "quote": " ".join(s.split()[:25]),
                "url": ev["url"],
                "file": ev["file"],
            })
    return bullets, claims


def _post_json(url, payload, headers, timeout=60):
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode("utf-8"),
        headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8", "ignore"))


OPENROUTER_DEFAULT_MODEL = "nex-agi/nex-n2.5-pro:free"
# Cap evidence chars per LLM call so huge inputs (e.g. pasted docs) don't blow
# the free-tier context / trigger gateway failover retries (which surface as
# `reasoning encrypted_content was not issued to this caller`).
OPENROUTER_MAX_EVIDENCE_CHARS = int(
    os.environ.get("OPENROUTER_MAX_EVIDENCE_CHARS", "12000"))
OPENROUTER_MAX_QUERY_CHARS = int(
    os.environ.get("OPENROUTER_MAX_QUERY_CHARS", "2000"))


def _truncate(s, limit):
    s = s or ""
    return s if len(s) <= limit else s[:limit] + "\n…[truncated]"


def _openrouter_reasoning_cfg(explicit=None):
    """Return a `reasoning` dict or None (omit field).

    Default is to OMIT reasoning: safest across models/providers and avoids
    the encrypted-reasoning replay path entirely. Opt in via
    OPENROUTER_REASONING_EFFORT=high|medium|low|minimal|max|xhigh or the
    `reasoning_effort` arg. Value "none"/"off"/"false" also omits.
    """
    raw = explicit if explicit is not None else os.environ.get(
        "OPENROUTER_REASONING_EFFORT", "")
    val = str(raw or "").strip().lower()
    if val in ("", "none", "off", "false", "0"):
        return None
    if val not in ("max", "xhigh", "high", "medium", "low", "minimal"):
        raise RuntimeError(
            f"Invalid reasoning effort {raw!r}: use "
            "max|xhigh|high|medium|low|minimal (or unset to disable)")
    return {"effort": val}


def _upstream_message(exc):
    """Extract the provider's JSON error message from HTTPError/URLError."""
    try:
        body = exc.read().decode("utf-8", "ignore") if hasattr(exc, "read") else ""
    except Exception:
        body = ""
    if not body:
        return str(exc)[:300]
    try:
        d = json.loads(body)
        err = d.get("error", d)
        if isinstance(err, dict):
            return str(err.get("message", body))[:500]
        return str(err)[:500]
    except Exception:
        return body[:500]


def _is_reasoning_error(msg):
    m = (msg or "").lower()
    return any(k in m for k in (
        "encrypted_content", "reasoning_content", "reasoning",
        "was not issued", "must be passed back", "invalid_request_error"))


MARKER_RE = re.compile(r"\[\[(\d+)\]\]\([^)]*\)|\[(\d+)\]\([^)]*\)")
_MD_LINK_RE = re.compile(r"!?\[(?:\[[^\]]*\]|[^\]]*)\]\([^)]*\)")


def _best_evidence(text, evidence):
    """Fallback attribution: token overlap + figure match bonus."""
    if not evidence:
        return None
    q = set(tokens(text))
    q_figs = {re.sub(r"\D", "", m)
              for m in re.findall(r"\d[\d.,]*", text or "")} - {""}
    best, best_sc = None, -1.0
    for ev in evidence:
        doc = " ".join(ev.get("chunks", []))
        t = set(tokens(doc))
        overlap = len(q & t) / max(1, len(q))
        d_figs = {re.sub(r"\D", "", m)
                  for m in re.findall(r"\d[\d.,]*", doc)} - {""}
        sc = overlap + (1.0 if q_figs & d_figs else 0.0)
        if sc > best_sc:
            best, best_sc = ev, sc
    return best


def attribute_claims(body, evidence):
    """Map LLM rewrite sentences back to evidence files.

    Marker-aware: a sentence carrying [[n]](url) is attributed to evidence n
    (file+url), so the corroboration gate in search.py can fire. Sentences
    without markers fall back to overlap attribution. Only sentences with
    digits become claims (numbers/dates/quotes are what the verifier checks).
    """
    by_n = {e.get("n"): e for e in (evidence or [])}
    claims = []
    for s in SENT_SPLIT.split(body or ""):
        s = s.strip()
        if not s or not re.search(r"\d", s):
            continue
        markers = [int(a or b) for a, b in MARKER_RE.findall(s)]
        clean = _MD_LINK_RE.sub("", s).strip()
        if not clean:
            continue
        ev = next((by_n[m] for m in markers if m in by_n), None)
        if ev is None:
            ev = _best_evidence(clean, evidence)
        if ev is None:
            continue
        claims.append({"id": f"c{len(claims) + 1}", "text": clean,
                       "quote": " ".join(clean.split()[:25]),
                       "url": ev.get("url", ""),
                       "file": ev.get("file", "")})
    return claims


def llm_expand_queries(query, n=4, provider="auto", model=None):
    """Generate up to n extra search-query variants. Opt-in only (v2.4 1.1):
    called only when the caller enables query expansion AND an LLM key exists.
    Returns a list of plain query strings (may be shorter than n)."""
    if provider == "auto":
        provider = ("anthropic" if os.environ.get("ANTHROPIC_API_KEY")
                    else "openai" if os.environ.get("OPENAI_API_KEY")
                    else "openrouter" if os.environ.get("OPENROUTER_API_KEY")
                    else None)
    if provider is None:
        raise RuntimeError("No LLM key for query expansion")
    q = _truncate(query, 500)
    prompt = (f"Generate exactly {n} diverse web search queries that would "
              f"help answer: {q}\nRules: one query per line, no numbering, "
              f"no quotes around the whole line, no explanations.")
    lines = []
    if provider == "anthropic":
        d = _post_json("https://api.anthropic.com/v1/messages",
            {"model": "claude-sonnet-4-6", "max_tokens": 500,
             "messages": [{"role": "user", "content": prompt}]},
            {"Content-Type": "application/json",
             "x-api-key": os.environ["ANTHROPIC_API_KEY"],
             "anthropic-version": "2023-06-01"})
        txt = "".join(b.get("text", "") for b in d.get("content", []))
        lines = txt.splitlines()
    else:
        if provider == "openrouter":
            url, model = ("https://openrouter.ai/api/v1/chat/completions",
                          model or os.environ.get(
                              "OPENROUTER_MODEL", OPENROUTER_DEFAULT_MODEL))
            headers = {"Content-Type": "application/json",
                       "Authorization": f"Bearer {os.environ['OPENROUTER_API_KEY']}",
                       "HTTP-Referer": "https://github.com/dnislno/search-pro",
                       "X-Title": "search-pro"}
            payload = {"model": model, "max_tokens": 500,
                       "messages": [{"role": "user", "content": prompt}]}
        else:
            url, headers = ("https://api.openai.com/v1/chat/completions",
                            {"Content-Type": "application/json",
                             "Authorization": f"Bearer {os.environ['OPENAI_API_KEY']}"})
            payload = {"model": "gpt-5.2", "max_tokens": 500,
                       "messages": [{"role": "user", "content": prompt}]}
        try:
            d = _post_json(url, payload, headers)
            txt = d["choices"][0]["message"].get("content") or ""
        except Exception as e:
            raise RuntimeError(
                f"Query expansion failed: {_upstream_message(e)}") from e
        lines = (txt or "").splitlines()
    out, seen = [], set()
    for ln in lines:
        ln = re.sub(r"^[\s\d\-.*]+", "", ln).strip().strip('"').strip()
        if ln and ln.lower() not in seen:
            seen.add(ln.lower())
            out.append(ln)
    return out[:n]


STRUCTURED_SYS = (
    "Rewrite the evidence as a cited answer in the user's language, using "
    "ONLY the evidence. Keep every [[n]](url) marker attached to its claim. "
    "No new numbers, dates, or quotes beyond the evidence. "
    "Structure the answer with these sections: "
    "1) Key findings (2-5 bullets). 2) Numbers with sources (each number "
    "keeps its marker). 3) Conflicts / uncertainties (rival figures side by "
    "side, never averaged). 4) What is still unknown (max 3 bullets).")

DEFAULT_SYS = ("Rewrite the evidence as a concise cited answer in the user's language. "
               "Use ONLY the evidence. Keep every [[n]](url) marker attached to its claim. "
               "No new numbers, dates, or quotes beyond the evidence.")


def llm_rewrite(query, evidence, provider="auto", model=None,
               reasoning_effort=None, style=None):
    """Rewrite extractive bullets fluently. Keeps [[n]](url) markers.

    style: None/"default" (concise) or "structured" (v2.6 3.4: Key
    findings / Numbers / Conflicts / Unknowns — used for research mode).
    """
    if provider == "auto":
        provider = ("anthropic" if os.environ.get("ANTHROPIC_API_KEY")
                    else "openai" if os.environ.get("OPENAI_API_KEY")
                    else "openrouter" if os.environ.get("OPENROUTER_API_KEY")
                    else None)
    if provider is None:
        raise RuntimeError(
            "No LLM key: set ANTHROPIC_API_KEY, OPENAI_API_KEY, or OPENROUTER_API_KEY")
    query = _truncate(query, int(os.environ.get(
        "OPENROUTER_MAX_QUERY_CHARS", str(OPENROUTER_MAX_QUERY_CHARS))))
    ev_txt = _truncate("\n".join(
        f"[{e['n']}] {e['title']} {e['url']}\n" + "\n".join(e["chunks"])
        for e in evidence), int(os.environ.get(
            "OPENROUTER_MAX_EVIDENCE_CHARS",
            str(OPENROUTER_MAX_EVIDENCE_CHARS))))
    sys = STRUCTURED_SYS if style == "structured" else DEFAULT_SYS
    if provider == "anthropic":
        d = _post_json("https://api.anthropic.com/v1/messages",
            {"model": "claude-sonnet-4-6", "max_tokens": 1500,
             "system": sys, "messages": [{"role": "user",
              "content": f"Question: {query}\n\nEvidence:\n{ev_txt}"}]},
            {"Content-Type": "application/json",
             "x-api-key": os.environ["ANTHROPIC_API_KEY"],
             "anthropic-version": "2023-06-01"})
        return "".join(b.get("text", "") for b in d.get("content", []))
    if provider == "openrouter":
        model = model or os.environ.get("OPENROUTER_MODEL",
                                        OPENROUTER_DEFAULT_MODEL)
        max_tokens = int(os.environ.get("OPENROUTER_MAX_TOKENS", "2000"))
        base = {"model": model, "max_tokens": max_tokens,
                "messages": [
                 {"role": "system", "content": sys},
                 {"role": "user",
                  "content": f"Question: {query}\n\nEvidence:\n{ev_txt}"}]}
        headers = {"Content-Type": "application/json",
                   "Authorization": f"Bearer {os.environ['OPENROUTER_API_KEY']}",
                   "HTTP-Referer": "https://github.com/dnislno/search-pro",
                   "X-Title": "search-pro"}
        # Reasoning is opt-in (env OPENROUTER_REASONING_EFFORT or arg).
        # Hardcoding effort=high forces the encrypted-reasoning path on the
        # gateway; on free-tier failover that replays across providers and
        # fails with `encrypted_content was not issued to this caller`.
        payload = dict(base)
        try:
            rcfg = _openrouter_reasoning_cfg(reasoning_effort)
        except RuntimeError as e:
            raise e
        if rcfg is not None:
            payload["reasoning"] = rcfg
        try:
            d = _post_json("https://openrouter.ai/api/v1/chat/completions",
                           payload, headers)
        except (urllib.error.HTTPError, urllib.error.URLError,
                RuntimeError, KeyError) as e:
            msg = _upstream_message(e)
            if rcfg is not None and _is_reasoning_error(msg):
                # Self-heal: retry once WITHOUT the reasoning field.
                try:
                    d = _post_json(
                        "https://openrouter.ai/api/v1/chat/completions",
                        base, headers)
                except (urllib.error.HTTPError, urllib.error.URLError) as e2:
                    raise RuntimeError(
                        f"OpenRouter request failed (retry w/o reasoning): "
                        f"{_upstream_message(e2)}") from e2
            else:
                raise RuntimeError(f"OpenRouter request failed: {msg}") from e
        try:
            content = d["choices"][0]["message"].get("content") or ""
        except (KeyError, IndexError, AttributeError) as e:
            raise RuntimeError(
                f"OpenRouter bad response shape: {str(d)[:300]}") from e
        if not content.strip():
            raise RuntimeError(
                "OpenRouter returned empty content "
                "(reasoning-only/redacted response?)")
        return content
    d = _post_json("https://api.openai.com/v1/chat/completions",
        {"model": "gpt-5.2", "max_tokens": 1500, "messages": [
            {"role": "system", "content": sys},
            {"role": "user", "content": f"Question: {query}\n\nEvidence:\n{ev_txt}"}]},
        {"Content-Type": "application/json",
         "Authorization": f"Bearer {os.environ['OPENAI_API_KEY']}"})
    return d["choices"][0]["message"]["content"]
