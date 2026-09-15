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


def pick_sentences(query, text, k=3):
    q = set(tokens(query))
    scored = []
    for s in SENT_SPLIT.split(text or ""):
        s = s.strip()
        if len(s) < 40:
            continue
        scored.append((len(q & set(tokens(s))), -len(s), s))
    scored.sort(reverse=True)
    return [s for _, _, s in scored[:k]]


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


def llm_rewrite(query, evidence, provider="auto", model=None,
               reasoning_effort=None):
    """Rewrite extractive bullets fluently. Keeps [[n]](url) markers."""
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
    sys = ("Rewrite the evidence as a concise cited answer in the user's language. "
           "Use ONLY the evidence. Keep every [[n]](url) marker attached to its claim. "
           "No new numbers, dates, or quotes beyond the evidence.")
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
