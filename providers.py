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

UA = {"User-Agent": "search-pro/1.0.1 (+local CLI)"}
TIMEOUT = 25


def searxng_search(query, base_url, limit=5, time_range=None, category="general"):
    """GET {base}/search?q=..&format=json. Raises RuntimeError with setup hint."""
    if not base_url:
        raise RuntimeError(
            "SearXNG not configured. Set SEARXNG_URL env or pass --searxng-url, "
            "e.g. SEARXNG_URL=http://localhost:8888"
        )
    params = {"q": query, "format": "json", "categories": category}
    if time_range:  # SearXNG: day | week | month | year
        params["time_range"] = time_range
    url = base_url.rstrip("/") + "/search?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers=UA)
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            data = json.loads(r.read().decode("utf-8", "ignore"))
    except Exception as e:
        raise RuntimeError(f"SearXNG request failed ({base_url}): {e}")
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
