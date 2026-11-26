"""Crawl legacy sitemaps against a target origin and classify 200/301/404 parity.

Usage:
    uv run --frozen python scripts/cutover_parity_crawl.py
    uv run --frozen python scripts/cutover_parity_crawl.py --json-out .tmp/parity-crawl.json
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import xml.etree.ElementTree as ET
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener, urlopen

DEFAULT_SITEMAPS = (
    "https://datatalks.club/sitemap.xml",
    "https://datatalks.club/podwiki/sitemap.xml",
)
DEFAULT_TARGET_ORIGIN = "https://prod.datatalks.club"
USER_AGENT = "dtc-cutover-parity-crawl/1.0"
REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})
SITEMAP_NS = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9"}

_STATIC_MISSING_ALIASES = frozenset(
    {
        "/courses.html",
        "/articles",
        "/articles/",
        "/slack/guidelines",
        "/slack/guidelines/",
        "/slack/guidelines.html",
        "/support",
        "/support/",
    }
)


class _NoRedirectHandler(HTTPRedirectHandler):
    def redirect_request(
        self,
        req: Request,
        fp: Any,
        code: int,
        msg: str,
        headers: Any,
        newurl: str,
    ) -> None:
        return None


_NO_REDIRECT_OPENER = build_opener(_NoRedirectHandler())


@dataclass(frozen=True)
class HopResult:
    url: str
    status: int | None
    location: str | None = None
    error: str | None = None


@dataclass(frozen=True)
class ProbeResult:
    path: str
    sources: tuple[str, ...]
    initial_status: int | None
    final_status: int | None
    final_url: str
    hops: tuple[HopResult, ...]
    classification: str
    explanation: str


def fetch_sitemap_urls(sitemap_url: str, *, timeout: float = 20.0) -> list[str]:
    """Fetch a sitemap (or sitemapindex) and return all <loc> URLs."""
    req = Request(sitemap_url, headers={"User-Agent": USER_AGENT})
    with urlopen(req, timeout=timeout) as resp:
        payload = resp.read()
    root = ET.fromstring(payload)
    tag = root.tag.rsplit("}", 1)[-1]
    if tag == "sitemapindex":
        urls: list[str] = []
        for loc in root.findall(".//sm:sitemap/sm:loc", SITEMAP_NS):
            if loc.text and loc.text.strip():
                urls.extend(fetch_sitemap_urls(loc.text.strip(), timeout=timeout))
        return urls
    return [loc.text.strip() for loc in root.findall(".//sm:url/sm:loc", SITEMAP_NS) if loc.text]


def collect_sitemap_paths(
    sitemap_urls: tuple[str, ...],
    *,
    timeout: float = 20.0,
) -> dict[str, tuple[str, ...]]:
    """Return ordered mapping of normalized path+query -> tuple of source sitemap URLs."""
    path_sources: dict[str, list[str]] = {}
    for sitemap_url in sitemap_urls:
        for loc in fetch_sitemap_urls(sitemap_url, timeout=timeout):
            parts = urlsplit(loc)
            path = parts.path or "/"
            if parts.query:
                path = f"{path}?{parts.query}"
            sources = path_sources.setdefault(path, [])
            if sitemap_url not in sources:
                sources.append(sitemap_url)
    return {path: tuple(sources) for path, sources in path_sources.items()}


def explain_404(path: str) -> tuple[str, str]:
    """Classify a 404 path as expected, explained (known audit item), or unexplained."""
    clean_path = path.split("?", 1)[0]
    if clean_path in {"/404.html", "/podwiki/404.html"}:
        return "404_expected", "Static 404 error page; not a content route."
    if re.match(r"^/podcast/(?!s[0-9]+/)[^?]+$", clean_path):
        return (
            "404_expected",
            "Pre-2026-11 podcast URL shape; episodes answer only at /podcast/sNN/eNN-<slug>.",
        )
    if clean_path == "/podwiki" or clean_path.startswith("/podwiki/"):
        return (
            "404_explained",
            "Legacy /podwiki/* route awaiting /podwiki/* -> /wiki/* redirect (audit §3 row 4).",
        )
    if clean_path in {"/people", "/people/", "/people.html"} or clean_path.startswith("/people/"):
        return (
            "404_explained",
            "People hub or unmigrated /people/*.html profile (audit §3 row 2).",
        )
    if (
        clean_path in _STATIC_MISSING_ALIASES
        or clean_path == "/tools"
        or clean_path.startswith("/tools/")
        or clean_path.startswith("/conferences/")
    ):
        return (
            "404_explained",
            "Missing legacy static 301 redirect (audit §3 row 3).",
        )
    if clean_path == "/feed.xml":
        return "404_explained", "Missing /feed.xml RSS feed (audit §3 row 5)."
    if clean_path == "/academy" or clean_path.startswith("/academy/"):
        return "404_explained", "Unresolved /academy/* routing (audit §3 row 9)."
    return "404_unexplained", "Unexpected 404 not covered by known migration exclusions."


def _single_hop(url: str, *, method: str, timeout: float) -> HopResult:
    req = Request(url, method=method, headers={"User-Agent": USER_AGENT})
    try:
        with _NO_REDIRECT_OPENER.open(req, timeout=timeout) as resp:
            return HopResult(url=url, status=resp.status, location=resp.headers.get("Location"))
    except HTTPError as err:
        return HopResult(url=url, status=err.code, location=err.headers.get("Location"))
    except URLError as err:
        return HopResult(url=url, status=None, error=str(err.reason))
    except Exception as err:  # pragma: no cover - defensive fallback
        return HopResult(url=url, status=None, error=f"{type(err).__name__}: {err}")


def classify_hops(path: str, hops: tuple[HopResult, ...], target_origin: str) -> tuple[str, str]:
    """Classify a probe's hop sequence and produce a concise explanation."""
    first = hops[0]
    if first.status is None:
        return "error", f"Request failed: {first.error}"
    if first.status == 200:
        return "200", "Direct 200 OK."
    if first.status == 404:
        return explain_404(path)
    if first.status >= 500:
        return "5xx", f"Server error HTTP {first.status}."
    if first.status in REDIRECT_STATUSES:
        last = hops[-1]
        redirect_hops = sum(1 for hop in hops if hop.status in REDIRECT_STATUSES)
        first_target = urljoin(first.url, first.location or "")
        target_display = (
            first_target.removeprefix(target_origin.rstrip("/"))
            if first_target.startswith(target_origin.rstrip("/"))
            else first_target
        )
        if first.status in {302, 307}:
            return (
                "302_redirect",
                f"Temporary HTTP {first.status} -> {target_display} (final {last.status}); "
                "permanent 301 required for SEO.",
            )
        if last.status == 200:
            if redirect_hops == 1:
                return "301_ok", f"One-hop {first.status} -> {target_display} (200 OK)."
            return (
                "301_chain",
                f"Multi-hop ({redirect_hops}) redirect starting {first.status} -> "
                f"{target_display} (final 200 OK).",
            )
        if last.status in REDIRECT_STATUSES:
            return (
                "301_broken",
                f"Redirect exceeded max hops or looped after {first.status} -> {target_display}.",
            )
        return (
            "301_broken",
            f"{first.status} -> {target_display} ended with HTTP {last.status or last.error}.",
        )
    return "other", f"Unexpected HTTP {first.status}."


def probe_url(
    path: str,
    sources: tuple[str, ...],
    *,
    target_origin: str,
    method: str = "GET",
    timeout: float = 15.0,
    max_hops: int = 5,
) -> ProbeResult:
    """Probe a single path against target_origin, tracking redirect hops."""
    base = target_origin.rstrip("/")
    current_url = f"{base}{path}"
    hops: list[HopResult] = []
    seen_urls: set[str] = set()

    for _ in range(max_hops + 1):
        if current_url in seen_urls:
            break
        seen_urls.add(current_url)
        hop = _single_hop(current_url, method=method, timeout=timeout)
        hops.append(hop)
        if hop.status not in REDIRECT_STATUSES or not hop.location:
            break
        next_url = urljoin(current_url, hop.location)
        # Only follow redirects on the target origin; external redirects terminate the probe.
        if not next_url.startswith(base):
            break
        current_url = next_url

    hop_tuple = tuple(hops)
    classification, explanation = classify_hops(path, hop_tuple, target_origin)
    return ProbeResult(
        path=path,
        sources=sources,
        initial_status=hop_tuple[0].status,
        final_status=hop_tuple[-1].status,
        final_url=hop_tuple[-1].url,
        hops=hop_tuple,
        classification=classification,
        explanation=explanation,
    )


def run_crawl(
    *,
    sitemaps: tuple[str, ...] = DEFAULT_SITEMAPS,
    target_origin: str = DEFAULT_TARGET_ORIGIN,
    concurrency: int = 12,
    timeout: float = 15.0,
    method: str = "GET",
    limit: int | None = None,
) -> list[ProbeResult]:
    """Collect URLs from sitemaps and probe them concurrently against target_origin."""
    path_sources = collect_sitemap_paths(sitemaps, timeout=timeout)
    items = list(path_sources.items())
    if limit is not None:
        items = items[:limit]

    results_by_path: dict[str, ProbeResult] = {}
    with ThreadPoolExecutor(max_workers=max(1, concurrency)) as executor:
        futures = {
            executor.submit(
                probe_url,
                path,
                sources,
                target_origin=target_origin,
                method=method,
                timeout=timeout,
            ): path
            for path, sources in items
        }
        for future in as_completed(futures):
            result = future.result()
            results_by_path[result.path] = result

    return [results_by_path[path] for path, _ in items]


def format_report(results: list[ProbeResult], *, target_origin: str) -> str:
    """Format a human-readable summary of parity-crawl results."""
    counts = Counter(item.classification for item in results)
    lines = [
        f"Parity crawl against {target_origin}: {len(results)} unique URLs probed",
        "Classifications:",
    ]
    for key in (
        "200",
        "301_ok",
        "301_chain",
        "301_broken",
        "302_redirect",
        "404_expected",
        "404_explained",
        "404_unexplained",
        "5xx",
        "error",
        "other",
    ):
        if counts[key]:
            lines.append(f"  {key}: {counts[key]}")

    for label, bucket in (
        ("Unexplained 404s", "404_unexplained"),
        ("Broken redirects", "301_broken"),
        ("Redirect chains", "301_chain"),
        ("Temporary 302 redirects", "302_redirect"),
        ("Server 5xx / transport errors", "5xx"),
    ):
        items = [item for item in results if item.classification == bucket]
        if items:
            lines.append(f"\n{label} ({len(items)}):")
            for item in items[:50]:
                lines.append(f"  {item.path} -> {item.explanation}")
            if len(items) > 50:
                lines.append(f"  ... and {len(items) - 50} more")

    explained = [item for item in results if item.classification == "404_explained"]
    if explained:
        by_reason = Counter(item.explanation for item in explained)
        lines.append(f"\nExplained 404s ({len(explained)}):")
        for reason, count in by_reason.most_common():
            lines.append(f"  {count}x {reason}")

    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target-origin", default=DEFAULT_TARGET_ORIGIN)
    parser.add_argument(
        "--sitemap",
        action="append",
        dest="sitemaps",
        help="Sitemap URL to crawl (repeatable; defaults to datatalks.club + podwiki sitemaps).",
    )
    parser.add_argument("--concurrency", type=int, default=12)
    parser.add_argument("--timeout", type=float, default=15.0)
    parser.add_argument("--method", choices=("GET", "HEAD"), default="GET")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--json-out", type=Path, default=None)
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Exit non-zero if unexplained 404s, broken redirects, 5xx, or errors occur.",
    )
    args = parser.parse_args(argv)

    sitemaps = tuple(args.sitemaps) if args.sitemaps else DEFAULT_SITEMAPS
    results = run_crawl(
        sitemaps=sitemaps,
        target_origin=args.target_origin,
        concurrency=args.concurrency,
        timeout=args.timeout,
        method=args.method,
        limit=args.limit,
    )
    print(format_report(results, target_origin=args.target_origin))

    if args.json_out is not None:
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "target_origin": args.target_origin,
            "sitemaps": list(sitemaps),
            "total": len(results),
            "counts": dict(Counter(item.classification for item in results)),
            "results": [asdict(item) for item in results],
        }
        args.json_out.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

    if args.strict:
        bad = {"404_unexplained", "301_broken", "5xx", "error"}
        if any(item.classification in bad for item in results):
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
