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

    def test_normative_email_authorities_preserve_relay_ownership(self) -> None:
        authority_paths = (
            "_docs/specs/README.md",
            "_docs/specs/open-decisions.md",
            "_docs/specs/01-platform-architecture.md",
            "_docs/specs/04-courses-and-cohorts.md",
            "_docs/specs/05-events-registration-email.md",
            "_docs/specs/06-studio-and-admin-api.md",
            "_docs/specs/07-security-privacy-operations.md",
            "_docs/specs/08-aws-development-terraform.md",
            "_docs/specs/09-migration-rollout-roadmap.md",
            "_docs/specs/10-verification-strategy.md",
            "_docs/architecture/app-boundaries.md",
        )
        authorities = {
            path: (BASE_DIR / path).read_text(encoding="utf-8") for path in authority_paths
        }
        normalized_authorities = {
            path: " ".join(document.split()) for path, document in authorities.items()
        }
        for path, document in authorities.items():
            with self.subTest(path=path):
                self.assertIn("Relay", document)
                former_environment_name = "sand" + "box"
                self.assertNotIn(
                    f"08-aws-{former_environment_name}-terraform.md",
                    document,
                )

        index = authorities["_docs/specs/README.md"]
        decisions = authorities["_docs/specs/open-decisions.md"]
        platform = authorities["_docs/specs/01-platform-architecture.md"]
        courses = authorities["_docs/specs/04-courses-and-cohorts.md"]
        events = authorities["_docs/specs/05-events-registration-email.md"]
        infrastructure = authorities["_docs/specs/08-aws-development-terraform.md"]
        boundaries = authorities["_docs/architecture/app-boundaries.md"]
        normalized_platform = normalized_authorities["_docs/specs/01-platform-architecture.md"]
        normalized_events = normalized_authorities["_docs/specs/05-events-registration-email.md"]
        normalized_rollout = normalized_authorities["_docs/specs/09-migration-rollout-roadmap.md"]
        normalized_boundaries = normalized_authorities["_docs/architecture/app-boundaries.md"]

        self.assertIn("Worker --> Relay", index)
        self.assertNotIn("Worker --> SES", index)
        self.assertIn("Email provider and semantics (resolved by #21)", decisions)
        self.assertIn("Decision still required in #22", decisions)
        self.assertIn("logical `EmailDelivery` intent", platform)
        self.assertIn("calls Relay only after commit", platform)
        self.assertIn("redacted status projection", normalized_platform)
        self.assertIn("logical `EmailDelivery` intents", boundaries)
        self.assertIn(
            "only after the business transaction commits",
            normalized_boundaries,
        )
        self.assertIn("Datamailer remains read-only", boundaries)
        self.assertIn("send-disabled, read-only", courses)
        self.assertIn("Datamailer receives no new website send", events)
        self.assertIn(
            "neither the website job nor Relay automatically resends it",
            normalized_events,
        )
        self.assertIn("no Amazon SES send action", infrastructure)
        self.assertIn("provider-event ingress", infrastructure)
        self.assertIn(
            "Rollback never silently restores Datamailer",
            normalized_rollout,
        )
        self.assertIn("Relay sender ID `courses`", decisions)
        self.assertIn("unresolved #22 purpose/sender", infrastructure)

        stale_claims = {
            "_docs/specs/README.md": (
                "Transactional email uses Amazon SES",
                "rare duplicate transactional email",
            ),
            "_docs/specs/open-decisions.md": (
                "unified durable outbox with direct Amazon SES",
                "whether Datamailer should remain the long-term delivery provider",
            ),
            "_docs/specs/01-platform-architecture.md": (
                "Amazon SES through a provider adapter",
                "Signed SES events update delivery state",
                "workers or SES are unavailable",
            ),
            "_docs/specs/05-events-registration-email.md": (
                "New transactional delivery uses the unified outbox and SES provider adapter",
                "## Amazon SES",
                "a rare duplicate is preferable",
            ),
            "_docs/specs/06-studio-and-admin-api.md": (
                "deliveries, attempts, SES events, suppression",
                "deliveries, attempts, provider events, suppression",
            ),
            "_docs/specs/07-security-privacy-operations.md": (
                "submitted to SES within 5 minutes",
                "Worker/SES failure",
            ),
            "_docs/specs/08-aws-development-terraform.md": (
                "Transactional SES region",
                "Task role has only required SES actions",
                "SES event ingress verifies provider signatures",
            ),
            "_docs/specs/09-migration-rollout-roadmap.md": (
                "delivery/attempt/event/suppression model and SES adapter",
                "SES " + "sand" + "box safeguards",
            ),
            "_docs/specs/10-verification-strategy.md": (
                "GitHub and SES webhook signature fixtures",
                "controlled SES delivery",
                "GitHub, search, worker, SES, OIDC",
            ),
        }
        for path, phrases in stale_claims.items():
            for phrase in phrases:
                with self.subTest(path=path, phrase=phrase):
                    self.assertNotIn(phrase, authorities[path])
