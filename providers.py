"""Search providers, stdlib only. No new dependencies.
Supported:
  - searxng : free, no key. Needs SEARXNG_URL env or --searxng-url
              (self-hosted or public instance, e.g. your own SearXNG).
  - exa     : needs EXA_API_KEY env. Neural search + date filters.
Common result schema: {"title": str, "url": str, "snippet": str, "published": str|None}
Refs: ItzCrazyKns/Perplexica (SearXNG backend), firecrawl/fireplexity.
"""
import json
import os
import urllib.parse
import urllib.request

UA = {"User-Agent": "search-pro/2.5.0 (+local CLI)"}
TIMEOUT = 25

_ID_MARKERS = frozenset(
    "yang dan harga berapa jumlah daftar terbaru penduduk rupiah indonesia "
    "vs tren".split())


def guess_lang(query):
    """id if Indonesian markers present, else en. Sinks instance-locale bias
    (e.g. German-default instances answering 'Haus' with houses)."""
    toks = set(query.lower().split())
    return "id" if toks & _ID_MARKERS else "en"


def searxng_search(query, base_url, limit=5, time_range=None, category="general",
                   language="auto"):
    """GET {base}/search?q=..&format=json. base_url may be comma-separated:
    tries each in order (failover ring). Raises RuntimeError with setup hint."""
    bases = [b.strip().rstrip("/") for b in (base_url or "").split(",") if b.strip()]
    if not bases:
        raise RuntimeError(
            "SearXNG not configured. Set SEARXNG_URL env or pass --searxng-url "
            "(comma-separated allowed for failover), "
            "e.g. SEARXNG_URL=http://localhost:8888"
        )
    if language == "auto":
        language = guess_lang(query)
    params = {"q": query, "format": "json", "categories": category,
              "language": language}
    if time_range:  # SearXNG: day | week | month | year
        params["time_range"] = time_range
    errs = []
    for base in bases:
        url = base + "/search?" + urllib.parse.urlencode(params)
        req = urllib.request.Request(url, headers=UA)
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
                data = json.loads(r.read().decode("utf-8", "ignore"))
        except Exception as e:
            errs.append(f"{base}: {e}")
            continue
        out = []
        for it in (data.get("results") or [])[:limit]:
            if it.get("url"):
                out.append({
                    "title": it.get("title") or "",
                    "url": it.get("url"),
                    "snippet": it.get("content") or "",
                    "published": it.get("publishedDate"),
                })
        return out
    raise RuntimeError("All SearXNG backends failed — " + " | ".join(errs)[:400])


def exa_search(query, api_key=None, limit=5, start_date=None):
    """POST api.exa.ai/search. Raises RuntimeError with setup hint."""
    api_key = api_key or os.environ.get("EXA_API_KEY")
    if not api_key:
        raise RuntimeError(
            "Exa not configured. Set EXA_API_KEY env with your key from exa.ai"
        )
    payload = {"query": query, "numResults": limit, "type": "auto"}
    if start_date:
        payload["startPublishedDate"] = start_date
    req = urllib.request.Request(
        "https://api.exa.ai/search",
        data=json.dumps(payload).encode("utf-8"),
        headers={**UA, "Content-Type": "application/json", "x-api-key": api_key},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            data = json.loads(r.read().decode("utf-8", "ignore"))
    except Exception as e:
        raise RuntimeError(f"Exa request failed: {e}")
    out = []
    for it in (data.get("results") or [])[:limit]:
        if it.get("url"):
            out.append({
                "title": it.get("title") or "",
                "url": it.get("url"),
                "snippet": it.get("text") or it.get("snippet") or "",
                "published": it.get("publishedDate"),
            })
    return out


def search(query, provider="auto", limit=5, searxng_url=None, **kw):
    """Dispatch. provider auto = exa if EXA_API_KEY else searxng."""
    if provider == "auto":
        provider = "exa" if os.environ.get("EXA_API_KEY") else "searxng"
    if provider == "exa":
        return exa_search(query, limit=limit, **kw)
    if provider == "searxng":
        return searxng_search(
            query, searxng_url or os.environ.get("SEARXNG_URL"), limit=limit, **kw
        )
    raise RuntimeError(f"Unknown provider: {provider} (use searxng|exa)")
