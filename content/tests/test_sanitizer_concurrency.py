from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from threading import local

from django.test import Client, TestCase, override_settings

from content.services import sanitize_rendered_html

_SANITIZER_CORPUS = (
    (
        "faq",
        '<h2 id="answer">Answer</h2><p>See '
        '<a href="/faq/data-engineering-zoomcamp.html#answer" rel="nofollow">the FAQ</a>.'
        "</p><pre><code>docker compose up</code></pre>",
        '<h2 id="answer">Answer</h2><p>See '
        '<a href="/faq/data-engineering-zoomcamp.html#answer" rel="nofollow">the FAQ</a>.'
        "</p><pre><code>docker compose up</code></pre>",
    ),
    (
        "docs",
        '<h1 id="activities">Activities</h1><table><thead><tr><th scope="col">Name</th>'
        '</tr></thead><tbody><tr><td colspan="1">Workshop</td></tr></tbody></table>'
        '<a href="https://datatalks.club/events">events</a>',
        '<h1 id="activities">Activities</h1><table><thead><tr><th scope="col">Name</th>'
        '</tr></thead><tbody><tr><td colspan="1">Workshop</td></tr></tbody></table>'
        '<a href="https://datatalks.club/events">events</a>',
    ),
    (
        "article",
        '<p class="lead">Read our <strong>article</strong>.</p><figure><img '
        'src="/images/posts/guide.png" alt="Guide" loading="lazy"><figcaption>Guide</figcaption>'
        "</figure>",
        '<p class="lead">Read our <strong>article</strong>.</p><figure><img '
        'src="/images/posts/guide.png" alt="Guide" loading="lazy"><figcaption>Guide</figcaption>'
        "</figure>",
    ),
    (
        "security-script",
        '<script>alert(1)</script><p onclick="evil()">bad '
        '<a href="javascript:alert(1)">link</a></p><!-- hidden -->',
        "alert(1)<p>bad <a>link</a></p>",
    ),
    (
        "security-image-protocols",
        '<img src="https://evil.invalid/x"><img src="//evil.invalid/x">'
        '<img src="data:image/svg+xml,x"><img src="java&#x73;cript:alert(1)">'
        "<svg/onload=alert(1)>",
        "<img><img><img><img>",
    ),
)

_PUBLIC_ROUTE_CASES = (
    ("/faq/data-engineering-zoomcamp.html", "Data Engineering Zoomcamp"),
    ("/faq/mlops-zoomcamp.html", "MLOps Zoomcamp"),
    ("/faq/llm-zoomcamp.html", "LLM Zoomcamp"),
    ("/docs/activities/", "Activities"),
    ("/blog/ai-dev-tools-zoomcamp.html", "AI Dev Tools Zoomcamp"),
)


class SanitizerConcurrencyTests(TestCase):
    def test_security_corpus_outputs_remain_exact(self) -> None:
        for content_kind, rendered_html, expected in _SANITIZER_CORPUS:
            with self.subTest(content_kind=content_kind):
                self.assertEqual(sanitize_rendered_html(content_kind, rendered_html), expected)

    def test_sanitizer_is_deterministic_under_bounded_concurrency(self) -> None:
        cases = tuple(
            (content_kind, rendered_html)
            for _ in range(8)
            for content_kind, rendered_html, _ in _SANITIZER_CORPUS
        )
        expected = {
            (content_kind, rendered_html): sanitized
            for content_kind, rendered_html, sanitized in _SANITIZER_CORPUS
        }

        with ThreadPoolExecutor(max_workers=6) as executor:
            actual = list(
                executor.map(
                    lambda case: sanitize_rendered_html(*case),
                    cases,
                )
            )

        self.assertEqual(actual, [expected[case] for case in cases])

    @override_settings(DEBUG=True)
    def test_representative_public_routes_are_stable_under_concurrency(self) -> None:
        serial_client = Client()
        golden: dict[str, bytes] = {}
        for path, marker in _PUBLIC_ROUTE_CASES:
            with self.subTest(serial_path=path):
                response = serial_client.get(path)
                self.assertEqual(response.status_code, 200)
                self.assertIn(marker.encode(), response.content)
                self.assertIn(
                    f'href="https://datatalks.club{path}"'.encode(),
                    response.content,
                )
                golden[path] = response.content

        requests = tuple(path for _ in range(6) for path, _ in _PUBLIC_ROUTE_CASES)
        clients_by_worker = local()

        def fetch(path: str) -> tuple[str, int, bytes]:
            client = getattr(clients_by_worker, "client", None)
            if client is None:
                client = Client()
                clients_by_worker.client = client
            response = client.get(path)
            return path, response.status_code, response.content

        with ThreadPoolExecutor(max_workers=6) as executor:
            results = list(executor.map(fetch, requests))

        self.assertEqual(len(results), len(requests))
        for path, status_code, body in results:
            with self.subTest(concurrent_path=path):
                self.assertEqual(status_code, 200)
                self.assertEqual(body, golden[path])
