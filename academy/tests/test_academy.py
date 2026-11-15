from django.test import TestCase, override_settings


class AcademyPagesTests(TestCase):
    INDEXABLE_PAGES = (
        ("/academy/", "DataTalks.Club Academy | Live Full-Time Courses with Alexey Grigorev"),
        ("/academy/agentic-engineering/", "Agentic Engineering"),
        ("/academy/ai-engineering/", "AI Engineering"),
        ("/academy/sessions/", "Between jobs: the AI skills that get experienced developers hired"),
    )

    def test_indexable_pages_return_200_with_self_canonical_and_csp(self) -> None:
        for path, expected_text in self.INDEXABLE_PAGES:
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertEqual(response.status_code, 200)
                body = response.content.decode("utf-8")
                self.assertIn(expected_text, body)
                self.assertIn(
                    f'<link rel="canonical" href="https://datatalks.club{path}">',
                    body,
                )
                self.assertNotIn('<meta name="robots" content="noindex">', body)
                self.assertIn(
                    'data-api-url="https://augldvp3omxdxz3irqsmmxc25e0blpxx.lambda-url.eu-west-1.on.aws"',
                    body,
                )
                csp = response.headers["Content-Security-Policy"]
                self.assertIn(
                    "https://augldvp3omxdxz3irqsmmxc25e0blpxx.lambda-url.eu-west-1.on.aws",
                    csp,
                )
                self.assertIn("https://cloud.umami.is", csp)
                self.assertIn("https://api-gateway.umami.dev", csp)

    def test_noindex_pages_return_200(self) -> None:
        for path in ("/academy/sessions/materials/", "/academy/confirm/"):
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertEqual(response.status_code, 200)
                self.assertIn('<meta name="robots" content="noindex">', response.content.decode())

    @override_settings(
        ACADEMY_UMAMI_SCRIPT_URL="https://umami.example.com/script.js",
        ACADEMY_UMAMI_WEBSITE_ID="11111111-2222-3333-4444-555555555555",
    )
    def test_umami_script_and_csp_allow_configured_origin(self) -> None:
        response = self.client.get("/academy/")
        self.assertEqual(response.status_code, 200)
        body = response.content.decode("utf-8")
        self.assertIn(
            '<script defer src="https://umami.example.com/script.js" '
            'data-website-id="11111111-2222-3333-4444-555555555555"></script>',
            body,
        )
        self.assertIn("https://umami.example.com", response.headers["Content-Security-Policy"])

    def test_ics_downloads(self) -> None:
        for slug in ("hireable-with-ai", "session-1", "session-2", "session-3"):
            with self.subTest(slug=slug):
                response = self.client.get(f"/academy/sessions/{slug}.ics")
                self.assertEqual(response.status_code, 200)
                self.assertTrue(response.headers["Content-Type"].startswith("text/calendar"))
                self.assertIn(b"BEGIN:VCALENDAR", response.content)
        self.assertEqual(self.client.get("/academy/sessions/unknown.ics").status_code, 404)

    def test_redirects_and_query_preservation(self) -> None:
        self.assertRedirects(
            self.client.get("/academy"),
            "/academy/",
            status_code=301,
            fetch_redirect_response=False,
        )
        self.assertRedirects(
            self.client.get("/academy/layoff-proof/?utm_source=chatgpt"),
            "/academy/sessions/?utm_source=chatgpt",
            status_code=301,
            fetch_redirect_response=False,
        )
        self.assertRedirects(
            self.client.get("/academy/privacy/"),
            "/privacy",
            status_code=301,
            fetch_redirect_response=False,
        )
        self.assertRedirects(
            self.client.get("/academy/impressum/"),
            "/impressum",
            status_code=301,
            fetch_redirect_response=False,
        )

    def test_main_sitemap_includes_academy_pages(self) -> None:
        response = self.client.get("/sitemaps/main.xml")
        self.assertEqual(response.status_code, 200)
        body = response.content.decode("utf-8")
        for path, _ in self.INDEXABLE_PAGES:
            self.assertIn(f"<loc>https://datatalks.club{path}</loc>", body)
