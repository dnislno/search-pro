"""Page fetcher, stdlib only. No browser rendering by design:
JS-heavy pages are labeled js-empty (never cited as verified) instead of faked.
Returns {"url", "status", "text"} with status in:
  ok | paywall | js-empty | error
"""
import html
import re
import urllib.error
import urllib.request
from html.parser import HTMLParser

UA = {"User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                     "AppleWebKit/537.36 (KHTML, like Gecko) "
                      "Chrome/126.0 Safari/537.36 search-pro/3.2.0")}
TIMEOUT = 25
MAX_BYTES = 2_000_000
MAX_CHARS = 20_000

PAYWALL_MARKS = (
    "paywall", "subscribe to continue", "subscription required",
    "sign in to continue", "log in to continue", "register to continue",
    "this content is for subscribers", "premium content",
    "cf-challenge", "just a moment",  # cloudflare challenge pages
)
JS_MARKS = ("__next_data__", 'id="root"', "window.__", "__nuxt", "ng-app")


class _Text(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts, self._skip = [], 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style", "noscript", "header", "footer", "nav"):
            self._skip += 1

    def handle_endtag(self, tag):
        if tag in ("script", "style", "noscript", "header", "footer", "nav"):
            self._skip = max(0, self._skip - 1)

    def handle_data(self, data):
        if not self._skip and data.strip():
            self.parts.append(data.strip())


def html_to_text(raw_html: str) -> str:
    p = _Text()
    p.feed(raw_html)
    text = html.unescape(" ".join(p.parts))
    return re.sub(r"\s+", " ", text).strip()


def fetch(url: str) -> dict:
    req = urllib.request.Request(url, headers={**UA, "Accept": "text/html,*/*"})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            ctype = (r.headers.get("Content-Type") or "").lower()
            if "html" not in ctype and "text" not in ctype:
                return _fallback(url, {"url": url, "status": "error",
                    "text": f"unsupported content-type: {ctype or 'unknown'}"})
            raw = r.read(MAX_BYTES).decode("utf-8", "ignore")
    except urllib.error.HTTPError as e:
        if e.code in (401, 403, 429):
            return _fallback(url, {"url": url, "status": "paywall",
                                   "text": f"http {e.code}"})
        return _fallback(url, {"url": url, "status": "error",
                               "text": f"http {e.code}"})
    except Exception as e:
        return _fallback(url, {"url": url, "status": "error",
                               "text": f"fetch failed: {e}"})

    low = raw.lower()
    if any(m in low for m in PAYWALL_MARKS):
        return _fallback(url, {"url": url, "status": "paywall",
                               "text": "paywall/challenge marker found"})
    text = html_to_text(raw)
    if len(text) < 300 and any(m in raw for m in JS_MARKS):
        rendered = _render_fallback(url)
        if rendered:
            return {"url": url, "status": "ok",
                    "text": rendered[:MAX_CHARS], "via": "render"}
        return _fallback(url, {"url": url, "status": "js-empty",
                               "text": "client-rendered shell, no readable text"})
    if not text.strip():
        return _fallback(url, {"url": url, "status": "error",
                               "text": "empty after parsing"})
    return {"url": url, "status": "ok", "text": text[:MAX_CHARS], "via": "direct"}


def _render_fallback(url):
    """v2.6 3.3: optional local renderer as last resort before js-empty.

    Only active with FETCH_RENDER=1 AND an installed renderer (crawl4ai).
    Zero-deps by default: missing lib -> None (previous behavior kept).
    Any renderer failure -> None (never faked, never raised)."""
    import os
    if os.environ.get("FETCH_RENDER", "0") != "1":
        return None
    try:
        from crawl4ai import AsyncWebCrawler
    except ImportError:
        return None
    try:
        import asyncio
        timeout = float(os.environ.get("FETCH_RENDER_TIMEOUT", "40"))

        async def _go():
            async with AsyncWebCrawler() as crawler:
                res = await crawler.arun(url=url)
                return (getattr(res, "markdown", "") or "").strip()

        text = asyncio.run(asyncio.wait_for(_go(), timeout))
        text = re.sub(r"\s+", " ", text or "").strip()
        return text if len(text) >= 300 else None
    except Exception:
        return None


def _fallback(url, first):
    """Mitigation chain for M2 (link-live), refs jina-ai/reader + Wikipedia API:
    1. *.wikipedia.org -> official REST API (no scraping, no key).
    2. anything else -> Jina Reader proxy (headless render, free, no key).
    Honors JINA_FALLBACK=0 to disable. Returns first failure if all miss."""
    import os
    import time
    import urllib.parse
    if os.environ.get("JINA_FALLBACK", "1") == "0":
        return first
    m = re.match(r"https?://([a-z-]+)\.wikipedia\.org/wiki/(.+)", url)
    if m:
        try:
            api = (f"https://{m.group(1)}.wikipedia.org/api/rest_v1/page/html/"
                   + urllib.parse.quote(m.group(2), safe="%/()"))
            req = urllib.request.Request(api, headers=UA)
            with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
                text = html_to_text(r.read(MAX_BYTES).decode("utf-8", "ignore"))
            if len(text.strip()) >= 300:
                return {"url": url, "status": "ok",
                        "text": text[:MAX_CHARS], "via": "wikipedia-api"}
        except Exception:
            pass
    try:
        time.sleep(float(os.environ.get("JINA_GAP", "3")))
        jh = dict(UA)
        if os.environ.get("JINA_API_KEY"):  # keyed: higher limits, no IP block
            jh["Authorization"] = "Bearer " + os.environ["JINA_API_KEY"]
        req = urllib.request.Request("https://r.jina.ai/" + url, headers=jh)
        with urllib.request.urlopen(req, timeout=45) as r:
            body = r.read(MAX_BYTES).decode("utf-8", "ignore")
        lines = [ln for ln in body.splitlines()
                 if not ln.startswith(("Title:", "URL Source:", "Published ",
                                       "Markdown Content:"))]
        text = re.sub(r"\s+", " ", "\n".join(lines)).strip()
        bad = ("warning: target", "403 forbidden", "invalid url", "404 not found")
        if len(text) >= 300 and not any(b in text.lower()[:500] for b in bad):
            return {"url": url, "status": "ok",
                    "text": text[:MAX_CHARS], "via": "jina-reader"}
    except Exception:
        pass
    return first
