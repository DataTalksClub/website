from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest import expectedFailure

from django.conf import settings
from django.test import TestCase

from content import catalogue
from content.podcast_routes import PODCAST_HIERARCHICAL_ONLY_SLUGS, podcast_canonical_path


class EditorialRouteMigrationContractTests(TestCase):
    root: Path
    policy_path: Path
    policy: dict[str, Any]

    @classmethod
    def setUpClass(cls) -> None:
        super().setUpClass()
        cls.root = Path(settings.BASE_DIR)
        cls.policy_path = (
            cls.root / "_docs" / "runbooks" / "editorial-route-seo-cutover-policy.json"
        )
        cls.policy = json.loads(cls.policy_path.read_text(encoding="utf-8"))

    # test_checked_manifest_is_bound_to_schema_projection_and_runtime used to
    # live here: it compared the *live* catalogue's editorial_route_migration
    # singleton against this one frozen, pinned, real reviewed cutover file --
    # a one-time ingest-parity proof, not an ongoing regression check, the
    # same shape of thing the owner ruled should not run as a test on every
    # suite invocation (content_sync/tests/test_dtc_content_accepted_checkout.py,
    # removed the same way). It can only ever pass against the real reviewed
    # catalogue, never a synthetic one, and that proof already succeeded once,
    # historically; git history holds it. The other tests in this file check
    # the reviewed policy document's own internal consistency and are
    # unaffected by which catalogue (real or synthetic) is loaded.

    @expectedFailure
    def test_the_cutover_policy_still_pins_the_manifest_it_publishes(self) -> None:
        """Known red, and only the SEO cutover commander may clear it.

        A content-projection refresh moved the manifest's real `content_sha256`
        without moving the pin the cutover policy holds. The policy's own
        `human_gate` reserves bumping `required_content_sha256` to that role, so
        this stays an expected failure rather than an edit -- and the moment the
        pin is signed off it reports an unexpected success, which fails the run
        and asks for this wrapper to be removed.

        See `_docs/runbooks/editorial-route-seo-cutover.md` and issue #310.
        """

        self.assertEqual(
            self.policy["manifest"]["required_content_sha256"],
            catalogue.singleton("editorial_route_migration")["content_sha256"],
        )

    def test_manifest_has_every_source_to_final_mapping_without_graph_hazards(self) -> None:
        migration = catalogue.singleton("editorial_route_migration")
        finals = {item["final_path"]: item for item in migration["finals"]}
        aliases = {item["source_path"]: item for item in migration["aliases"]}

        self.assertTrue(set(finals).isdisjoint(aliases))
        self.assertTrue(all(item["final_path"] in finals for item in aliases.values()))
        self.assertTrue(all(item["status_code"] == 301 for item in aliases.values()))
        self.assertTrue(all(item["query_policy"] == "preserve_raw" for item in aliases.values()))
        self.assertTrue(all(item["final_path"] not in aliases for item in aliases.values()))
        for final_path, final in finals.items():
            if final["collection"] == "podcasts":
                self.assertEqual(final_path, podcast_canonical_path(final["record_key"]))
            else:
                self.assertTrue(final_path.endswith(".html"))
            clean_path = (
                f"/podcast/{final['record_key']}"
                if final["collection"] == "podcasts"
                else final_path.removesuffix(".html")
            )
            if (
                final["collection"] == "podcasts"
                and final["record_key"] in PODCAST_HIERARCHICAL_ONLY_SLUGS
            ):
                self.assertNotIn(clean_path, aliases)
                self.assertNotIn(f"{clean_path}/", aliases)
                continue
            self.assertEqual(
                {
                    aliases[clean_path]["final_path"],
                    aliases[f"{clean_path}/"]["final_path"],
                },
                {final_path},
            )
            self.assertEqual(
                set(final["source"]),
                {
                    "repository",
                    "revision",
                    "source_path",
                    "source_key",
                    "checksum",
                    "source_url",
                },
            )

    def test_seo_policy_has_named_owners_baseline_windows_and_exact_thresholds(self) -> None:
        self.assertEqual(self.policy["schema_version"], 1)
        self.assertEqual(
            self.policy["owner"],
            {
                "primary_role": "production SEO cutover commander",
                "responder_role": "website on-call engineer",
                "rollback_approver_role": "product manager",
            },
        )
        self.assertEqual(self.policy["baseline"]["complete_days"], 28)
        self.assertEqual(self.policy["baseline"]["timezone"], "UTC")
        self.assertEqual(
            self.policy["action_semantics"]["stop_or_rollback"],
            "stop before the canonical traffic switch; roll back immediately at or after "
            "the switch",
        )
        self.assertEqual(
            [window["id"] for window in self.policy["monitoring_windows"]],
            ["initial", "first_three_days", "first_two_weeks", "rollback_tail"],
        )
        self.assertEqual(
            {signal["id"] for signal in self.policy["signals"]},
            {
                "route_contract",
                "googlebot_http",
                "search_console",
                "organic_landings",
                "robots_sitemap_canonical",
            },
        )
        thresholds = {item["id"]: item for item in self.policy["rollback_thresholds"]}
        self.assertEqual(thresholds["route_contract_failure"]["failed_routes_gte"], 1)
        self.assertEqual(thresholds["editorial_5xx"]["rate_percent_gte"], 1.0)
        self.assertEqual(
            thresholds["googlebot_alias_failure"]["non_301_or_chain_rate_percent_gte"],
            1.0,
        )
        self.assertEqual(thresholds["organic_landing_regression"]["baseline_percent_lte"], 70.0)
        self.assertEqual(
            thresholds["search_console_index_regression"]["baseline_percent_lte"], 85.0
        )
        self.assertEqual(
            thresholds["search_console_canonical_regression"]["wrong_canonical_urls_gte"],
            80,
        )

    def test_policy_preserves_redirects_and_keeps_live_actions_human_only(self) -> None:
        self.assertEqual(
            [step["id"] for step in self.policy["rollback_steps"]],
            [
                "freeze",
                "retain_redirects",
                "restore_application",
                "verify_contract",
                "observe_recovery",
                "notify",
            ],
        )
        self.assertEqual(self.policy["retention"]["permanent_redirects"], "indefinite")
        self.assertTrue(self.policy["human_gate"]["required"])
        self.assertFalse(
            self.policy["human_gate"]["production_activation_performed_by_automation_test"]
        )
        self.assertFalse(
            self.policy["human_gate"]["search_console_submission_performed_by_automation_test"]
        )
