"""Search providers, stdlib only. No new dependencies.
Supported:
  - searxng    : free, no key. Needs SEARXNG_URL env or --searxng-url
                 (self-hosted or public instance, e.g. your own SearXNG).
  - exa        : needs EXA_API_KEY env. Neural search + date filters.
  - langsearch : needs LANGSEARCH_API_KEY env. Broad-discovery bulk
                 retrieval (count up to 10/call, long summary) — v3.3.0
                 phase-1 for hybrid.
  - parallel   : Parallel Search MCP, free anonymous tier (lower limits)
                 or PARALLEL_API_KEY. Micro-iteration / granular
                 verification with excerpt evidence — v3.2.0, v3.3.0
                 phase-2 for hybrid.
  - hybrid     : loop 0 = langsearch broad discovery, loops >=1 = parallel
                 micro iteration (handled in search.py fan-out).
Common result schema: {"title": str, "url": str, "snippet": str,
                       "published": str|None, "text": str (optional),
                       "via": str (optional)}
Refs: ItzCrazyKns/Perplexica (SearXNG backend), firecrawl/fireplexity.
"""
import json
import os
import urllib.parse
import urllib.request

UA = {"User-Agent": "search-pro/3.3.0 (+local CLI)"}
TIMEOUT = 25

LANGSEARCH_URL = "https://api.langsearch.com/v1/web-search"
MCP_URL = "https://search.parallel.ai/mcp"

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
    """Dispatch. provider auto = langsearch if LANGSEARCH_API_KEY else
    exa if EXA_API_KEY else searxng (broad-discovery default, v3.3.0).

    provider=parallel fans out a single query as one MCP web_search call
    (objective=query). provider=hybrid is resolved in search.py fan-out
    (loop 0 langsearch, loops >=1 parallel), not here.
    """
    if provider == "auto":
        if os.environ.get("LANGSEARCH_API_KEY"):
            provider = "langsearch"
        else:
            provider = "exa" if os.environ.get("EXA_API_KEY") else "searxng"
    if provider == "exa":
        return exa_search(query, limit=limit, **kw)
    if provider == "searxng":
        return searxng_search(
            query, searxng_url or os.environ.get("SEARXNG_URL"), limit=limit, **kw
        )
    if provider == "langsearch":
        return langsearch_search(query, limit=limit, **kw)
    if provider == "parallel":
        return parallel_search(query, [query], limit=limit, **kw)
    if provider == "hybrid":
        raise RuntimeError(
            "provider=hybrid needs loop context — use search.py fan-out "
            "(loop 0 langsearch broad, loops >=1 parallel micro)"
        )
    raise RuntimeError(
        f"Unknown provider: {provider} (use searxng|exa|langsearch|parallel|hybrid)"
    )


def map_langsearch_results(data, limit=10):
    """Pure mapper: LangSearch JSON -> common schema. Unit-testable offline.

    Accepts either the full envelope {"code":..,"data":{"webPages":{"value":..}}}
    or the inner {"webPages":..} or {"value":..} for robustness.
    """
    if isinstance(data, dict) and isinstance(data.get("data"), dict):
        data = data["data"]
    if isinstance(data, dict) and isinstance(data.get("webPages"), dict):
        vals = data["webPages"].get("value") or []
    elif isinstance(data, dict) and isinstance(data.get("value"), list):
        vals = data["value"]
    elif isinstance(data, list):
        vals = data
    else:
        vals = []
    out = []
    for it in vals[:limit]:
        url = (it.get("url") or "").strip()
        if not url:
            continue
        summary = (it.get("summary") or "").strip()
        snippet = (it.get("snippet") or "").strip()
        # Bulk-retrieval value is the long summary; keep snippet short for
        # rerank display but preserve full summary in "text" for scoring.
        out.append({
            "title": (it.get("name") or it.get("title") or "")[:140],
            "url": url,
            "snippet": (summary or snippet)[:600],
            "published": it.get("datePublished"),
            "text": summary,
            "via": "langsearch",
        })
    return out


def langsearch_search(query, api_key=None, limit=10, freshness="noLimit",
                      summary=True):
    """POST api.langsearch.com/v1/web-search. Broad-discovery bulk retrieval.

    count is clamped to 1..10 (API limit). Raises RuntimeError with setup hint.
    Ref: https://docs.langsearch.com (Free Web Search API).
    """
    api_key = api_key or os.environ.get("LANGSEARCH_API_KEY")
    if not api_key:
        raise RuntimeError(
            "LangSearch not configured. Set LANGSEARCH_API_KEY env with your key "
            "from langsearch.com/dashboard > API Key Management"
        )
    try:
        count = max(1, min(10, int(limit or 10)))
    except (TypeError, ValueError):
        count = 10
    payload = {"query": query, "freshness": freshness or "noLimit",
               "summary": bool(summary), "count": count}
    req = urllib.request.Request(
        LANGSEARCH_URL,
        data=json.dumps(payload).encode("utf-8"),
        headers={**UA, "Content-Type": "application/json",
                 "Authorization": "Bearer " + api_key},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            data = json.loads(r.read().decode("utf-8", "ignore"))
    except Exception as e:
        raise RuntimeError(f"LangSearch request failed: {e}")
    if isinstance(data, dict) and data.get("code") not in (None, 200):
        raise RuntimeError(
            f"LangSearch API error code={data.get('code')}: "
            f"{str(data.get('msg') or data.get('message') or '')[:200]}")
    return map_langsearch_results(data, limit=count)


def parse_mcp_body(body):
    """Pure parser: Streamable HTTP returns plain JSON OR SSE data: lines."""
    t = (body or "").strip()
    if not t:
        raise RuntimeError("mcp: empty response")
    if t[0] == "{":
        return json.loads(t)
    datas = [ln[5:].strip() for ln in t.splitlines()
             if ln.startswith("data:")]
    datas = [s for s in datas if s and s != "[DONE]"]
    if not datas:
        raise RuntimeError("mcp: no data payload")
    return json.loads(datas[-1])


def map_parallel_results(parsed, limit=10):
    """Pure mapper: parsed web_search JSON {results:[...]} -> common schema."""
    out = []
    results = []
    if isinstance(parsed, dict):
        results = (((parsed.get("result") or {}).get("results"))
                   or parsed.get("results") or [])
    for x in (results or [])[:limit]:
        url = (x.get("url") or "").strip()
        if not url:
            continue
        text = " ".join(x.get("excerpts") or []).strip()
        text = " ".join(text.split())
        out.append({
            "title": (x.get("title") or "")[:140],
            "url": url,
            "snippet": text[:600],
            "published": x.get("publish_date"),
            "text": text,
            "via": "parallel",
        })
    return out


def _mcp_call(payload, session="", api_key=None):
    headers = {"Content-Type": "application/json",
               "Accept": "application/json, text/event-stream"}
    if api_key:
        headers["Authorization"] = "Bearer " + api_key
    if session:
        headers["Mcp-Session-Id"] = session
    req = urllib.request.Request(
        MCP_URL, data=json.dumps(payload).encode("utf-8"),
        headers={**UA, **headers}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=90) as r:
            sid = r.headers.get("Mcp-Session-Id") or session or ""
            body = r.read().decode("utf-8", "ignore")
            if r.status >= 400:
                raise RuntimeError(f"mcp http {r.status}")
            return parse_mcp_body(body), sid
    except RuntimeError:
        raise
    except Exception as e:
        raise RuntimeError(f"Parallel MCP request failed: {e}")


def _mcp_ensure_session(state, api_key=None):
    if state.get("sid"):
        return state["sid"]
    data, sid = _mcp_call(
        {"jsonrpc": "2.0", "id": 1, "method": "initialize",
         "params": {"protocolVersion": "2025-03-26", "capabilities": {},
                    "clientInfo": {"name": "search-pro-cli",
                                   "version": "3.3.0"}}}, "", api_key)
    state["sid"] = sid
    _mcp_call({"jsonrpc": "2.0", "method": "notifications/initialized",
               "params": {}}, sid, api_key)
    return sid


def parallel_search(objective, queries, api_key=None, limit=10,
                    session_state=None, parallel_session=None):
    """One MCP web_search call carrying up to 3 queries (Parallel guidance).

    Excerpts are verbatim source text -> snippet/text. Anonymous tier works
    (lower limits); PARALLEL_API_KEY lifts quota. session_state dict is reused
    across calls in one run for rate-limit stability.
    """
    import random
    api_key = api_key or os.environ.get("PARALLEL_API_KEY")
    state = session_state if session_state is not None else {}
    if parallel_session is None:
        parallel_session = state.get("parallel_session") or (
            "sp-%s%s" % (random.randrange(16 ** 8, 16 ** 16),
                         random.randrange(16 ** 8, 16 ** 16)))
        state["parallel_session"] = parallel_session
    sid = _mcp_ensure_session(state, api_key)
    import time
    args = {"objective": str(objective or "")[:500],
            "search_queries": list(queries or [])[:3],
            "session_id": parallel_session}
    data, _ = _mcp_call(
        {"jsonrpc": "2.0", "id": int(time.time() * 1000) % 100000,
         "method": "tools/call",
         "params": {"name": "web_search", "arguments": args}}, sid, api_key)
    blocks = (((data.get("result") or {}).get("content")) or [])
    if data.get("result", {}).get("isError"):
        raise RuntimeError(
            "parallel: " + " ".join(b.get("text", "") for b in blocks)[:300])
    joined = "\n".join(b.get("text", "") for b in blocks)
    try:
        parsed = json.loads(joined)
    except Exception:
        raise RuntimeError("parallel: unexpected response")
    return map_parallel_results(parsed, limit=limit)
