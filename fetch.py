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
                     "Chrome/126.0 Safari/537.36 search-pro/1.0.1")}
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
                return {"url": url, "status": "error",
                        "text": f"unsupported content-type: {ctype or 'unknown'}"}
            raw = r.read(MAX_BYTES).decode("utf-8", "ignore")
    except urllib.error.HTTPError as e:
        if e.code in (401, 403, 429):
            return {"url": url, "status": "paywall", "text": f"http {e.code}"}
        return {"url": url, "status": "error", "text": f"http {e.code}"}
    except Exception as e:
        return {"url": url, "status": "error", "text": f"fetch failed: {e}"}

    low = raw.lower()
    if any(m in low for m in PAYWALL_MARKS):
        return {"url": url, "status": "paywall", "text": "paywall/challenge marker found"}
    text = html_to_text(raw)
    if len(text) < 300 and any(m in raw for m in JS_MARKS):
        return {"url": url, "status": "js-empty",
                "text": "client-rendered shell, no readable text"}
    if not text.strip():
        return {"url": url, "status": "error", "text": "empty after parsing"}
    return {"url": url, "status": "ok", "text": text[:MAX_CHARS]}
