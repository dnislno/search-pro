"""Synthesis: extractive composer (offline, deterministic) + optional LLM pass.
Extractive output is built from verbatim source sentences, so the grep
verifier passes structurally — the honest offline default.
LLM pass (Anthropic/OpenAI via env keys) rewrites fluently; its claims are
then re-verified and anything unsupported is demoted to Unverified.
"""
import json
import os
import re
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


def llm_rewrite(query, evidence, provider="auto", model=None):
    """Rewrite extractive bullets fluently. Keeps [[n]](url) markers."""
    if provider == "auto":
        provider = ("anthropic" if os.environ.get("ANTHROPIC_API_KEY")
                    else "openai" if os.environ.get("OPENAI_API_KEY")
                    else "openrouter" if os.environ.get("OPENROUTER_API_KEY")
                    else None)
    if provider is None:
        raise RuntimeError(
            "No LLM key: set ANTHROPIC_API_KEY, OPENAI_API_KEY, or OPENROUTER_API_KEY")
    ev_txt = "\n".join(
        f"[{e['n']}] {e['title']} {e['url']}\n" + "\n".join(e["chunks"])
        for e in evidence)
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
        d = _post_json("https://openrouter.ai/api/v1/chat/completions",
            {"model": model, "max_tokens": 2000,
             "reasoning": {"effort": "high"},
             "messages": [
                {"role": "system", "content": sys},
                {"role": "user",
                 "content": f"Question: {query}\n\nEvidence:\n{ev_txt}"}]},
            {"Content-Type": "application/json",
             "Authorization": f"Bearer {os.environ['OPENROUTER_API_KEY']}",
             "HTTP-Referer": "https://github.com/dnislno/search-pro",
             "X-Title": "search-pro"})
        return d["choices"][0]["message"]["content"]
    d = _post_json("https://api.openai.com/v1/chat/completions",
        {"model": "gpt-5.2", "max_tokens": 1500, "messages": [
            {"role": "system", "content": sys},
            {"role": "user", "content": f"Question: {query}\n\nEvidence:\n{ev_txt}"}]},
        {"Content-Type": "application/json",
         "Authorization": f"Bearer {os.environ['OPENAI_API_KEY']}"})
    return d["choices"][0]["message"]["content"]
