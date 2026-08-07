from pathlib import Path

from django.apps import apps
from django.test import SimpleTestCase

from website.settings.base import BASE_DIR


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

    def test_app_dependency_direction_is_documented(self) -> None:
        document = (BASE_DIR / "_docs/architecture/app-boundaries.md").read_text()
        for app_name in (
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
        ):
            self.assertIn(f"`{app_name}`", document)

    def test_process_documents_have_no_stale_source_repository_configuration(self) -> None:
        paths = [BASE_DIR / "AGENTS.md", BASE_DIR / "_docs/PROCESS.md"]
        paths.extend((BASE_DIR / ".claude/agents").glob("*.md"))
        combined = "\n".join(Path(path).read_text() for path in paths)
        self.assertNotIn("AI-Shipping-Labs/website", combined)
        self.assertNotIn("aishippinglabs.com", combined)
        self.assertIn("Closes #N", combined)
        self.assertIn(".tmp/", combined)
