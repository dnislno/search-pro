"""v3.3.0 hybrid tests: LangSearch broad discovery + Parallel micro iteration.
Offline, stdlib only. Run: pytest tests/test_langsearch_hybrid.py -q"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import providers as P
import search as S


def test_map_langsearch_envelope():
    payload = {"code": 200, "data": {"webPages": {"value": [
        {"name": "A Title", "url": "https://a.test/1",
         "snippet": "short", "summary": "long summary text here",
         "datePublished": "2026-01-01"},
        {"name": "No URL", "url": "", "snippet": "x", "summary": "y"},
        {"name": "B", "url": "https://b.test/2", "snippet": "s2",
         "summary": "", "datePublished": None},
    ]}}}
    out = P.map_langsearch_results(payload, limit=10)
    assert len(out) == 2, out
    assert out[0]["via"] == "langsearch" and out[0]["url"] == "https://a.test/1"
    assert out[0]["snippet"].startswith("long summary"), out[0]
    assert out[0]["text"] == "long summary text here"
    assert out[1]["snippet"] == "s2"  # summary empty -> snippet fallback


def test_map_langsearch_inner_shapes():
    inner = {"webPages": {"value": [
        {"name": "T", "url": "https://t.test/", "snippet": "s", "summary": "big"}]}}
    assert len(P.map_langsearch_results(inner)) == 1
    assert len(P.map_langsearch_results(
        {"value": [{"name": "T", "url": "https://t.test/"}]})) == 1
    assert P.map_langsearch_results({}) == []


def test_langsearch_requires_key():
    old = os.environ.pop("LANGSEARCH_API_KEY", None)
    try:
        try:
            P.langsearch_search("hello", limit=2)
        except RuntimeError as e:
            assert "LANGSEARCH_API_KEY" in str(e), e
        else:
            raise AssertionError("must raise without key")
    finally:
        if old is not None:
            os.environ["LANGSEARCH_API_KEY"] = old


def test_langsearch_search_mocked():
    import urllib.request as U

    payload = {"code": 200, "data": {"webPages": {"value": [
        {"name": f"T{i}", "url": f"https://m.test/{i}",
         "snippet": f"s{i}", "summary": f"summary body {i} " * 10,
         "datePublished": None} for i in range(3)]}}}

    seen = {}

    class FakeResp:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def read(self): return json.dumps(payload).encode()

    def fake_urlopen(req, timeout=None):
        seen["auth"] = req.headers.get("Authorization", "")
        seen["body"] = json.loads(req.data.decode())
        return FakeResp()

    real = U.urlopen
    U.urlopen = fake_urlopen
    try:
        out = P.langsearch_search("bulk query", api_key="sk-test", limit=5)
    finally:
        U.urlopen = real
    assert len(out) == 3, out
    assert seen["auth"] == "Bearer sk-test"
    assert seen["body"]["count"] == 5 and seen["body"]["summary"] is True
    assert all(o["via"] == "langsearch" for o in out)


def test_parse_mcp_plain_and_sse():
    plain = json.dumps({"result": {"content": [{"text": "{}"}]}})
    assert P.parse_mcp_body(plain)["result"]["content"][0]["text"] == "{}"
    sse = 'event: message\ndata: {"result":{"ok":1}}\n\ndata: [DONE]\n'
    assert P.parse_mcp_body(sse) == {"result": {"ok": 1}}
    try:
        P.parse_mcp_body("   ")
    except RuntimeError:
        pass
    else:
        raise AssertionError("empty must raise")


def test_map_parallel_results():
    parsed = {"result": {"content": [{"text": json.dumps({"results": [
        {"url": "https://p.test/1", "title": "PT",
         "publish_date": "2026-02-02",
         "excerpts": ["verbatim excerpt one", "second line"]},
        {"url": "", "title": "skip"},
    ]})}]}}
    inner = json.loads(parsed["result"]["content"][0]["text"])
    out = P.map_parallel_results(inner, limit=10)
    assert len(out) == 1 and out[0]["via"] == "parallel", out
    assert "verbatim excerpt one" in out[0]["snippet"]


def test_fanout_hybrid_two_phases():
    calls = []

    def fake_lang(q, limit=10, **kw):
        calls.append(("langsearch", q, limit))
        return [{"title": "L", "url": f"https://l.test/{q[:5]}-{limit}",
                 "snippet": "s", "published": None, "via": "langsearch"}]

    def fake_par(obj, qs, limit=10, session_state=None, **kw):
        calls.append(("parallel", tuple(qs), limit))
        return [{"title": "P", "url": f"https://p.test/{len(qs)}",
                 "snippet": "s", "published": None, "via": "parallel"}]

    rl, rp = P.langsearch_search, P.parallel_search
    P.langsearch_search = fake_lang
    P.parallel_search = fake_par
    # route providers.search -> our fakes for this test
    rs = P.search

    def fake_search(q, provider="auto", limit=5, searxng_url=None, **kw):
        if provider == "langsearch":
            return fake_lang(q, limit=limit)
        raise AssertionError(provider)

    P.search = fake_search
    try:
        c0 = S.fanout_search(["q1", "q2"], "hybrid", None, 6, 0,
                             objective="obj", loop=0, mcp_state={})
        assert any(c[0] == "langsearch" for c in calls), calls
        # broad discovery bulks count=10 per query
        assert all(c[2] == 10 for c in calls if c[0] == "langsearch"), calls
        calls.clear()
        c1 = S.fanout_search(["f1", "f2", "f3", "f4"], "hybrid", None, 6, 0,
                             objective="obj", loop=1, mcp_state={})
        assert any(c[0] == "parallel" for c in calls), calls
        # micro iteration batches 3/query-call -> 4 queries = 2 calls
        assert sum(1 for c in calls if c[0] == "parallel") == 2, calls
        assert c0 and c1
    finally:
        P.langsearch_search, P.parallel_search, P.search = rl, rp, rs


def test_search_auto_prefers_langsearch():
    old = os.environ.get("LANGSEARCH_API_KEY")
    os.environ["LANGSEARCH_API_KEY"] = "sk-test"
    rl = P.langsearch_search
    P.langsearch_search = lambda q, limit=5, **kw: [{"url": "https://l.test/"}]
    try:
        out = P.search("q", provider="auto", limit=5)
        assert out[0]["url"] == "https://l.test/"
    finally:
        P.langsearch_search = rl
        if old is None:
            os.environ.pop("LANGSEARCH_API_KEY", None)
        else:
            os.environ["LANGSEARCH_API_KEY"] = old
