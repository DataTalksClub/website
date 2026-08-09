from pathlib import Path

from django.conf import settings
from django.test import TestCase
from django.utils.html import strip_tags

from api.openapi.spec import build_openapi_spec

ROOT = Path(__file__).resolve().parents[2]
LOCAL_VERSION = "local-development-build-version-not-configured"


class VersionSurfaceTests(TestCase):
    def test_unified_public_footer_renders_readable_version(self) -> None:
        response = self.client.get("/")

        self.assertEqual(response.status_code, 200)
        rendered_text = " ".join(strip_tags(response.content.decode()).split())
        self.assertIn(f"Version {LOCAL_VERSION}", rendered_text)

    def test_all_shell_sources_use_only_the_readable_version(self) -> None:
        for relative_path in (
            "templates/base.html",
            "templates/studio/base.html",
            "course_platform_templates/base.html",
        ):
            with self.subTest(template=relative_path):
                source = (ROOT / relative_path).read_text()
                self.assertIn("Version", source)
                self.assertIn("{{ VERSION }}", source)
                self.assertNotIn("SOURCE_SHA", source)
                self.assertNotIn("IMAGE_DIGEST", source)
                self.assertNotIn("{{ app_version }}", source)

    def test_adopted_openapi_uses_the_canonical_release_identity_contract(self) -> None:
        spec = build_openapi_spec()
        health = spec["components"]["schemas"]["Health"]

        self.assertEqual(spec["info"]["version"], settings.VERSION)
        self.assertEqual(
            health,
            {
                "type": "object",
                "additionalProperties": False,
                "required": ["status", "version", "source_sha", "image_digest"],
                "properties": {
                    "status": {"type": "string"},
                    "version": {"type": "string"},
                    "source_sha": {"type": ["string", "null"]},
                    "image_digest": {"type": ["string", "null"]},
                },
            },
        )
