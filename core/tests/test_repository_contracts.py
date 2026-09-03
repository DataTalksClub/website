import re

from django.apps import apps
from django.test import SimpleTestCase

from website.settings.base import BASE_DIR

#: Every ``{% static ... %}`` tag, with whatever it was handed.
STATIC_TAG = re.compile(r"\{%\s*static\s+(?P<argument>.+?)\s*%\}", re.DOTALL)
TEMPLATE_ROOTS = ("templates", "core", "courses", "content", "events", "accounts", "studio")


class RepositoryContractTests(SimpleTestCase):
    def test_all_architecture_apps_are_installed(self) -> None:
        expected = {
            "core",
            "accounts",
            "content",
            "content_sync",
            "courses",
            "events",
            "email_app",
            "studio",
            "api",
            "jobs",
        }
        self.assertTrue(expected.issubset({config.name for config in apps.get_app_configs()}))

    def test_no_template_resolves_a_runtime_value_through_static(self) -> None:
        """``{% static %}`` may only be handed a literal.

        A literal is checked by ``collectstatic``: if the file is not there, the
        build fails.  Anything else -- a model field, a queryset value, a view
        context entry -- can change *after* the manifest was built, and under
        ``CompressedManifestStaticFilesStorage`` an unknown reference raises
        rather than 404s.  A tag inside a loop then abandons the whole render
        and answers 500, so one bad row costs a page rather than an image.

        The safe shape is to resolve the value where the failure can be caught
        and turned into an empty string, and hand the template the result;
        ``courses.models.Testimonial.portrait_url`` is the worked example.
        """

        offenders = []
        for root in TEMPLATE_ROOTS:
            for path in sorted((BASE_DIR / root).rglob("*.html")):
                text = path.read_text(encoding="utf-8")
                for match in STATIC_TAG.finditer(text):
                    argument = match.group("argument").split(" as ")[0].strip()
                    if not argument.startswith(("'", '"')):
                        line = text.count("\n", 0, match.start()) + 1
                        offenders.append(
                            f"{path.relative_to(BASE_DIR)}:{line}: {{% static {argument} %}}"
                        )
        self.assertEqual(offenders, [])

    def test_playwright_artifact_redaction_bypass_is_test_harness_only(self) -> None:
        harness = (BASE_DIR / "conftest.py").read_text(encoding="utf-8")
        middleware = (BASE_DIR / "core/middleware.py").read_text(encoding="utf-8")

        self.assertIn("bypass_csp=True", harness)
        self.assertIn("test-harness", harness)
        self.assertIn("def strict_csp_page", harness)
        strict_fixture = harness.split("def strict_csp_page", 1)[1].split("@pytest.fixture", 1)[0]
        self.assertNotIn("bypass_csp=True", strict_fixture)
        self.assertNotIn("'unsafe-eval'", middleware)
