from __future__ import annotations

import re

from django.test import TestCase


class AdminLoginCSPTemplateTests(TestCase):
    def test_login_uses_non_alpine_unfold_surface(self) -> None:
        response = self.client.get("/admin/login/?next=/admin/")
        content = response.content.decode()
        scripts = re.findall(r'<script[^>]+src="([^"]+)"', content)

        self.assertEqual(response.status_code, 200)
        self.assertIn("<form", content)
        self.assertIn('id="login-form"', content)
        self.assertIn("/static/unfold/js/htmx/htmx.js", scripts)
        self.assertIn("/static/unfold/js/chart/chart.js", scripts)
        self.assertFalse(any("/static/unfold/js/alpine/" in script for script in scripts))
        self.assertNotIn("/static/unfold/js/app.js", scripts)

        for marker in (
            "Unfold 0.103",
            "Available shortcuts",
            "Open command tool",
            "Toggle sidebar",
            "modal-overlay",
            "command-results",
        ):
            self.assertNotIn(marker, content)

        policy = response.headers["Content-Security-Policy"]
        self.assertNotIn("'unsafe-eval'", policy)
