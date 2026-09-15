"""Pipeline tests: gate year-loophole (v2.6.1a), conflict anchors (v2.6.1b),
plus v2.4/v2.5 regression guards. Offline, stdlib only. Run: pytest tests/."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import synthesize as Z


def test_gate_year_only_pointer_demotes():
    """A pointer claim carrying ONLY a year figure must demote: the year in
    other bodies is not independent backing (v2.6.1a)."""
    import tempfile
    import search as S

    tmp = tempfile.mkdtemp(prefix="gatetest_")
    corp = os.path.join(tmp, "corpus")
    os.makedirs(corp, exist_ok=True)
    open(os.path.join(corp, "doc0.md"), "w", encoding="utf-8").write(
        "Balapan MotoGP Mandalika 2026 digelar Oktober 2026 di Sirkuit Mandalika.")
    open(os.path.join(corp, "doc1.md"), "w", encoding="utf-8").write(
        "Kabar viral balapan Mandalika 2026 katanya sudah ada pemenangnya.")
    evidence = [
        {"n": 1, "title": "A", "url": "https://motogp.com/x", "file": "doc0.md", "chunks": []},
        {"n": 2, "title": "B", "url": "https://www.instagram.com/p/x", "file": "doc1.md", "chunks": []},
    ]
    claims = [{"id": "c1", "text": "Kabar viral balapan Mandalika 2026 katanya sudah ada pemenangnya.",
               "quote": "Kabar viral", "url": "https://www.instagram.com/p/x",
               "_file": "doc1.md"}]
    verdict = {"verified": [{"id": "c1", "text": claims[0]["text"]}],
               "unverified": [], "stats": {}}
    verdict, n = S.apply_gate(verdict, claims, evidence, corp)
    assert n == 1 and verdict["verified"] == [], (n, verdict)


def test_gate_substantive_figure_still_verifies():
    """A pointer claim whose figure IS backed verbatim elsewhere survives."""
    import tempfile
    import search as S

    tmp = tempfile.mkdtemp(prefix="gatetest_")
    corp = os.path.join(tmp, "corpus")
    os.makedirs(corp, exist_ok=True)
    open(os.path.join(corp, "doc0.md"), "w", encoding="utf-8").write(
        "Harga resmi iPhone adalah 28999000 rupiah menurut Apple Indonesia.")
    open(os.path.join(corp, "doc1.md"), "w", encoding="utf-8").write(
        "Viral iPhone cuma 28999000 rupiah, sikat guys.")
    evidence = [
        {"n": 1, "title": "A", "url": "https://apple.com/x", "file": "doc0.md", "chunks": []},
        {"n": 2, "title": "B", "url": "https://www.instagram.com/p/x", "file": "doc1.md", "chunks": []},
    ]
    claims = [{"id": "c1", "text": "Viral iPhone cuma 28999000 rupiah, sikat guys.",
               "quote": "Viral", "url": "https://www.instagram.com/p/x",
               "_file": "doc1.md"}]
    verdict = {"verified": [{"id": "c1", "text": claims[0]["text"]}],
               "unverified": [], "stats": {}}
    verdict, n = S.apply_gate(verdict, claims, evidence, corp)
    assert n == 0 and len(verdict["verified"]) == 1, (n, verdict)


def test_conflict_shared_anchor_groups():
    vc = [{"text": "Total denda mencapai 1,2 miliar euro menurut GDPR Meta."},
          {"text": "Versi lain menyebut akumulasi denda GDPR Meta 1,3 miliar euro."}]
    cf = Z.detect_conflicts(vc)
    assert len(cf) == 1 and cf[0]["unit"] == "miliar", cf
    assert {v["value"] for v in cf[0]["values"]} == {"12", "13"}


def test_conflict_unrelated_context_stays_separate():
    vc = [{"text": "Nilai tukar rupiah tercatat 16200 rupiah per dolar."},
          {"text": "Harga resmi iPhone adalah 28999000 rupiah saat peluncuran."}]
    assert Z.detect_conflicts(vc) == []


def test_conflict_years_and_identical_ignored():
    assert Z.detect_conflicts([{"text": "Rapat tahun 2026 membahas agenda 2026."}]) == []
    assert Z.detect_conflicts([{"text": "Naik 5 persen."},
                               {"text": "Juga naik 5 persen."}]) == []


def test_plan_budgets_hold():
    import search as S

    for mode, n in (("best", 4), ("pro", 8), ("research", 12)):
        for q in ("berapa harga beras terbaru vs 2024", "quantum computer", "x"):
            qs = S.plan_queries(q, mode)
            assert len(qs) == n, (mode, q, len(qs))
            assert len({x.lower() for x in qs}) == n


def test_attribution_marker_and_file():
    ev = [{"n": 1, "title": "A", "url": "https://a.test/1", "file": "doc0.md",
           "chunks": ["x"]},
          {"n": 2, "title": "B", "url": "https://b.test/2", "file": "doc1.md",
           "chunks": ["x"]}]
    cl = Z.attribute_claims(
        "Angka 42 muncul di sini [[2]](https://b.test/2).", ev)
    assert cl and cl[0]["file"] == "doc1.md" and cl[0]["url"] == "https://b.test/2"


def test_pick_prefers_figures():
    text = ("Kalimat generik tanpa angka yang cukup panjang untuk lolos filter. "
            "Harga beras naik 5 persen menjadi 15000 rupiah per kilogram pada 2026 menurut data.")
    top = Z.pick_sentences("berapa harga beras 2026", text, k=1)
    assert any("15000" in s for s in top), top


def test_run_json_accumulates_across_loops():
    """v2.6.2: queries_used / n_candidates / fetched cover ALL loops."""
    import io
    import json
    import tempfile
    import contextlib
    import search as S

    out_json = os.path.join(tempfile.mkdtemp(prefix="looptest_"), "r.json")
    sys.argv = ["search.py", "--query", "harga beras 2026", "--mode", "best",
                "--synth", "extractive", "--max-loops", "1",
                "--run-json", out_json]
    os.environ["SEARCH_GAP"] = "0"
    S.providers.search = lambda q, provider="auto", limit=5, searxng_url=None, **kw: [
        {"title": f"D {abs(hash(q)) % 10000}-{i}",
         "url": f"https://t.test/{abs(hash(q)) % 10000}-{i}",
         "snippet": "x", "published": None} for i in range(limit)]
    S.fetch = lambda url: {
        "url": url, "status": "ok",
        "text": "Harga beras tercatat sebesar 15000 rupiah per kilogram pada 2026 menurut data resmi yang dirilis kemarin.",
        "via": "direct"}
    with contextlib.redirect_stdout(io.StringIO()):
        assert S.main() == 0
    d = json.load(open(out_json, encoding="utf-8"))
    assert d["loops_used"] == 2, d["loops_used"]
    assert len(d["queries_used"]) == 4 + len(d["followups"]) > 4, d["queries_used"]
    assert d["n_candidates"] == sum(
        5 for _ in d["queries_used"]), d["n_candidates"]
    fetched_urls = {f["url"] for f in d["fetched"]}
    assert len(d["fetched"]) == d["n_evidence"] == 8, d["fetched"]
    assert fetched_urls, "fetched list must cover all loops, not the last one"
    assert all(f["status"] == "ok" for f in d["fetched"])
    del os.environ["SEARCH_GAP"]
