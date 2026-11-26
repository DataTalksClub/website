from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

from scripts import cutover_parity_crawl as crawl


def test_explain_404_distinguishes_expected_explained_and_unexplained() -> None:
    assert crawl.explain_404("/404.html")[0] == "404_expected"
    assert crawl.explain_404("/podwiki/404.html")[0] == "404_expected"
    assert crawl.explain_404("/podcast/genai-pilots")[0] == "404_expected"
    assert crawl.explain_404("/podcast/some-episode.html")[0] == "404_expected"
    assert crawl.explain_404("/podcast/s18e09/some-episode")[0] == "404_expected"
    assert crawl.explain_404("/podwiki/some-page/")[0] == "404_explained"
    assert crawl.explain_404("/people")[0] == "404_explained"
    assert crawl.explain_404("/courses.html")[0] == "404_explained"
    assert crawl.explain_404("/feed.xml")[0] == "404_explained"
    assert crawl.explain_404("/academy/sessions/")[0] == "404_explained"
    assert crawl.explain_404("/brand-new-unknown-page")[0] == "404_unexplained"


def test_classify_hops_handles_200_301_chain_broken_and_302() -> None:
    origin = "https://prod.datatalks.club"
    assert (
        crawl.classify_hops("/blog", (crawl.HopResult(f"{origin}/blog", 200),), origin)[0] == "200"
    )
    assert (
        crawl.classify_hops(
            "/blog/",
            (
                crawl.HopResult(f"{origin}/blog/", 301, location="/blog"),
                crawl.HopResult(f"{origin}/blog", 200),
            ),
            origin,
        )[0]
        == "301_ok"
    )
    assert (
        crawl.classify_hops(
            "/old",
            (
                crawl.HopResult(f"{origin}/old", 301, location="/mid"),
                crawl.HopResult(f"{origin}/mid", 301, location="/new"),
                crawl.HopResult(f"{origin}/new", 200),
            ),
            origin,
        )[0]
        == "301_chain"
    )
    assert (
        crawl.classify_hops(
            "/broken",
            (
                crawl.HopResult(f"{origin}/broken", 301, location="/missing"),
                crawl.HopResult(f"{origin}/missing", 404),
            ),
            origin,
        )[0]
        == "301_broken"
    )
    assert (
        crawl.classify_hops(
            "/temp",
            (
                crawl.HopResult(f"{origin}/temp", 302, location="/blog"),
                crawl.HopResult(f"{origin}/blog", 200),
            ),
            origin,
        )[0]
        == "302_redirect"
    )


def test_collect_sitemap_paths_deduplicates_and_preserves_sources() -> None:
    sitemap_a = (
        b'<?xml version="1.0" encoding="UTF-8"?>'
        b'<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
        b"<url><loc>https://datatalks.club/</loc></url>"
        b"<url><loc>https://datatalks.club/blog/</loc></url>"
        b"</urlset>"
    )
    sitemap_b = (
        b'<?xml version="1.0" encoding="UTF-8"?>'
        b'<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
        b"<url><loc>https://datatalks.club/podwiki/</loc></url>"
        b"<url><loc>https://datatalks.club/</loc></url>"
        b"</urlset>"
    )

    class _FakeResp:
        def __init__(self, data: bytes) -> None:
            self._data = data

        def read(self) -> bytes:
            return self._data

        def __enter__(self) -> _FakeResp:
            return self

        def __exit__(self, *args: object) -> None:
            return None

    def fake_urlopen(req: object, timeout: float = 20.0) -> _FakeResp:
        url = getattr(req, "full_url", str(req))
        if "podwiki" in url:
            return _FakeResp(sitemap_b)
        return _FakeResp(sitemap_a)

    with patch.object(crawl, "urlopen", side_effect=fake_urlopen):
        paths = crawl.collect_sitemap_paths(
            ("https://datatalks.club/sitemap.xml", "https://datatalks.club/podwiki/sitemap.xml")
        )

    assert list(paths.keys()) == ["/", "/blog/", "/podwiki/"]
    assert paths["/"] == (
        "https://datatalks.club/sitemap.xml",
        "https://datatalks.club/podwiki/sitemap.xml",
    )


def test_main_writes_json_and_respects_strict_flag(tmp_path: Path) -> None:
    out_file = tmp_path / "crawl.json"
    fake_paths = {"/": ("https://datatalks.club/sitemap.xml",), "/missing": ("s",)}

    def fake_single_hop(url: str, *, method: str, timeout: float) -> crawl.HopResult:
        if url.endswith("/missing"):
            return crawl.HopResult(url=url, status=404)
        return crawl.HopResult(url=url, status=200)

    with (
        patch.object(crawl, "collect_sitemap_paths", return_value=fake_paths),
        patch.object(crawl, "_single_hop", side_effect=fake_single_hop),
    ):
        rc_non_strict = crawl.main(["--json-out", str(out_file)])
        rc_strict = crawl.main(["--strict"])

    assert rc_non_strict == 0
    assert rc_strict == 1
    payload = json.loads(out_file.read_text(encoding="utf-8"))
    assert payload["total"] == 2
    assert payload["counts"] == {"200": 1, "404_unexplained": 1}
