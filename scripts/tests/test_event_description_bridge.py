from __future__ import annotations

import copy
import json
import tempfile
from pathlib import Path
from typing import Any
from unittest.mock import patch

from django.conf import settings
from django.test import SimpleTestCase

from scripts.projection_build import event_description_bridge as bridge_contract
from scripts import build_event_description_bridge as bridge_builder
from scripts.projection_build.event_description_bridge import (
    EventDescriptionBridgeError,
    load_event_description_bridge,
    validate_description_html,
    validate_projected_event,
)


def _rehash_bridge(bridge: dict[str, Any]) -> None:
    bridge["content_sha256"] = bridge_contract.canonical_json_sha256(
        {key: value for key, value in bridge.items() if key != "content_sha256"}
    )


def _add_rehashed_link(bridge: dict[str, Any], href: str) -> dict[str, Any]:
    entry = bridge["matches"][0]
    if href.startswith("https://datatalks.club/"):
        link = f'<a class="app-link" href="{href}">Tampered action</a>'
    else:
        link = (
            f'<a class="app-link" href="{href}" target="_blank" '
            'rel="noopener noreferrer">Tampered action'
            '<span class="sr-only"> (opens in a new tab)</span></a>'
        )
    entry["description_html"] += f'<p class="mt-4 leading-7">{link}</p>'
    entry["description_text"] = bridge_contract.description_plain_text(entry["description_html"])
    entry["entry_sha256"] = bridge_contract.canonical_json_sha256(
        {key: value for key, value in entry.items() if key != "entry_sha256"}
    )
    _rehash_bridge(bridge)
    return entry


class EventDescriptionBridgeArtifactTests(SimpleTestCase):
    def test_committed_bridge_is_schema_bound_complete_and_public_safe(self) -> None:
        bridge = load_event_description_bridge()
        schema = json.loads(bridge_contract.BRIDGE_SCHEMA_PATH.read_text(encoding="utf-8"))

        self.assertEqual(schema["$schema"], "https://json-schema.org/draft/2020-12/schema")
        decision_schema = schema["properties"]["link_review"]["properties"]["decision_counts"]
        self.assertFalse(decision_schema["additionalProperties"])
        self.assertEqual(
            set(decision_schema["required"]),
            set(bridge_contract.EXPECTED_LINK_DECISION_COUNTS),
        )
        self.assertEqual(
            {
                reason: contract["const"]
                for reason, contract in decision_schema["properties"].items()
            },
            dict(bridge_contract.EXPECTED_LINK_DECISION_COUNTS),
        )
        serialized = json.dumps(bridge, ensure_ascii=False).casefold()
        for forbidden in (
            "/home/",
            "luma.com",
            "lu.ma",
            "images.lumacdn.com",
            '"guests"',
            '"meeting_url"',
            '"registration_questions"',
        ):
            self.assertNotIn(forbidden, serialized)
        self.assertNotRegex(serialized, r"\bluma\b")

    def test_bridge_tampering_fails_with_bounded_errors(self) -> None:
        bridge = load_event_description_bridge()
        cases = []

        changed_html = copy.deepcopy(bridge)
        changed_html["matches"][0]["description_html"] += "<script>private-canary</script>"
        cases.append(changed_html)

        changed_entry = copy.deepcopy(bridge)
        changed_entry["matches"][0]["entry_sha256"] = "0" * 64
        cases.append(changed_entry)

        changed_gap = copy.deepcopy(bridge)
        changed_gap["gaps"].pop()
        cases.append(changed_gap)

        changed_source = copy.deepcopy(bridge)
        changed_source["source"]["safe_source_sha256"] = "0" * 64
        cases.append(changed_source)

        for candidate in cases:
            with self.subTest(keys=sorted(candidate)):
                with self.assertRaises(EventDescriptionBridgeError) as raised:
                    bridge_contract._validate_bridge(candidate)
                self.assertNotIn("private-canary", str(raised.exception))

    def test_entire_bridge_corpus_has_no_dangling_link_or_form_copy(self) -> None:
        bridge = load_event_description_bridge()

        for entry in bridge["matches"]:
            with self.subTest(event=entry["target"]["source_key"]):
                self.assertNotRegex(
                    entry["description_html"],
                    bridge_contract.DANGLING_ACTION_COPY,
                )
                self.assertNotRegex(
                    entry["description_text"],
                    bridge_contract.DANGLING_ACTION_COPY,
                )

    def test_fully_rehashed_bridge_rejects_dangling_action_copy(self) -> None:
        for dangling_copy in (
            "Use this link to submit them in advance.",
            "Be among the first who’ll see the course contents!form.",
        ):
            with self.subTest(dangling_copy=dangling_copy):
                bridge = copy.deepcopy(load_event_description_bridge())
                entry = bridge["matches"][0]
                entry["description_html"] += f'<p class="mt-4 leading-7">{dangling_copy}</p>'
                entry["description_text"] = bridge_contract.description_plain_text(
                    entry["description_html"]
                )
                entry["entry_sha256"] = bridge_contract.canonical_json_sha256(
                    {key: value for key, value in entry.items() if key != "entry_sha256"}
                )
                _rehash_bridge(bridge)

                with self.assertRaisesRegex(
                    EventDescriptionBridgeError,
                    "dangling action copy",
                ):
                    bridge_contract._validate_bridge(bridge)

    def test_fully_rehashed_bridge_rejects_unreviewed_and_action_links(self) -> None:
        bridge = load_event_description_bridge()
        cases = {
            "external registration": "https://example.com/register",
            "meeting join": "https://zoom.us/j/private-canary",
            "dead internal registration": ("https://datatalks.club/events/private-canary/register"),
            "unreviewed external host": "https://unreviewed.invalid/resource",
            "unreviewed literal on reviewed host": (
                "https://github.com/private-canary/not-reviewed"
            ),
        }

        for label, href in cases.items():
            with self.subTest(label=label):
                changed = copy.deepcopy(bridge)
                _add_rehashed_link(changed, href)
                with self.assertRaises(EventDescriptionBridgeError) as raised:
                    bridge_contract._validate_bridge(changed)
                self.assertNotIn("private-canary", str(raised.exception))

    def test_fully_rehashed_bridge_rejects_unknown_link_decision_reason(self) -> None:
        bridge = copy.deepcopy(load_event_description_bridge())
        count = bridge["link_review"]["decision_counts"].pop("external_resource_kept")
        bridge["link_review"]["decision_counts"]["unknown_reason_kind"] = count
        _rehash_bridge(bridge)

        with self.assertRaisesRegex(EventDescriptionBridgeError, "link review mismatch"):
            bridge_contract._validate_bridge(bridge)

    def test_exact_provider_identity_normalization_never_guesses(self) -> None:
        self.assertEqual(
            bridge_builder._normalized_provider_identity("https://lu.ma/ByteExact/"),
            "https://luma.com/ByteExact",
        )
        self.assertEqual(
            bridge_builder._normalized_provider_identity("http://luma.com:80/ByteExact"),
            "https://luma.com/ByteExact",
        )
        for value in (
            "https://luma.com/byteexact?query=1",
            "https://luma.com/byteexact#fragment",
            "https://user@luma.com/byteexact",
            "https://luma.com:444/byteexact",
            "https://luma.com/two/segments",
            "https://example.com/byteexact",
        ):
            with self.subTest(value=value):
                with self.assertRaisesRegex(bridge_builder.BridgeBuildError, "provider event"):
                    bridge_builder._normalized_provider_identity(value)

    def test_markdown_renderer_keeps_semantics_and_removes_actions(self) -> None:
        public_paths, fragments = bridge_builder._projection_routes_and_fragments()
        renderer = bridge_builder.DescriptionRenderer(public_paths, fragments)
        rendered, text = renderer.render(
            "# Overview\n\n"
            "A **strong** paragraph with `code` and "
            "[the community](http://datatalks.club/).\n\n"
            "> A useful quote.\n\n"
            "- [Repository](https://github.com/DataTalksClub/ai-dev-tools-zoomcamp)\n"
            "- [Register now](https://luma.com/private-canary)\n"
        )

        self.assertIn('<h2 class="mt-8 text-xl font-semibold">Overview</h2>', rendered)
        self.assertIn("<strong>strong</strong>", rendered)
        self.assertIn("<code>code</code>", rendered)
        self.assertIn('href="https://datatalks.club/"', rendered)
        self.assertIn('target="_blank" rel="noopener noreferrer"', rendered)
        self.assertIn("(opens in a new tab)", rendered)
        self.assertIn("A useful quote.", rendered)
        self.assertNotIn("private-canary", rendered)
        self.assertNotIn("Register now", rendered)
        self.assertNotIn("opens in a new tab", text)

    def test_markdown_renderer_removes_action_copy_and_tracking_queries(self) -> None:
        public_paths, fragments = bridge_builder._projection_routes_and_fragments()
        renderer = bridge_builder.DescriptionRenderer(public_paths, fragments)
        rendered, text = renderer.render(
            "The cohort starts soon. If you have not joined yet, you can "
            "[register here](https://courses.datatalks.club/register/course/?utm_source=luma).\n\n"
            "Register to join this live event! The course remains free.\n\n"
            "[Repository](https://github.com/DataTalksClub/ai-dev-tools-zoomcamp?utm_source=luma)\n\n"
            "[DataTalks.Club](https://datatalks.club/) is the place to talk about data. "
            "**[Join our Slack community](https://datatalks.club/slack.html)****!**\n\n"
            "Come prepared with your questions! Use "
            "[this link](https://luma.com/private-question-form) "
            "to submit them in advance.\n\n"
            "Be among the first who’ll see the course contents! "
            "[form](https://luma.com/private-course-form)."
        )

        self.assertIn("The cohort starts soon.", rendered)
        self.assertIn("The course remains free.", rendered)
        self.assertIn('href="https://github.com/DataTalksClub/ai-dev-tools-zoomcamp"', rendered)
        self.assertIn("DataTalks.Club", rendered)
        self.assertIn("Come prepared with your questions!", rendered)
        self.assertIn("Be among the first who’ll see the course contents!", rendered)
        self.assertNotIn("course contents!.", rendered)
        for forbidden in (
            "you can",
            "Register",
            "Slack community",
            "utm_source",
            "luma",
            "****",
            "Use this link",
            "form.",
        ):
            self.assertNotIn(forbidden, rendered)
            self.assertNotIn(forbidden, text)

    def test_markdown_renderer_rejects_unsafe_or_unsupported_content(self) -> None:
        public_paths, fragments = bridge_builder._projection_routes_and_fragments()
        for markdown in (
            "<script>private-canary</script>",
            "| unsafe | table |\n| --- | --- |\n| value | value |",
            "[unsafe](javascript:alert(1))",
            "[join](https://zoom.us/j/private-canary)",
            "[unreviewed](https://github.com/private-canary/not-reviewed)",
        ):
            with self.subTest(markdown=markdown):
                renderer = bridge_builder.DescriptionRenderer(public_paths, fragments)
                with self.assertRaises(bridge_builder.BridgeBuildError) as raised:
                    renderer.render(markdown)
                self.assertNotIn("private-canary", str(raised.exception))

    def test_source_pair_allowlist_excludes_guest_csv_and_private_event_values(self) -> None:
        scratch = Path(settings.BASE_DIR) / ".tmp"
        scratch.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=scratch) as directory:
            source_root = Path(directory)
            checkpoint_root = source_root / "_json"
            description_root = source_root / "descriptions"
            checkpoint_root.mkdir()
            description_root.mkdir()
            event: dict[str, object] = {
                field: None for field in bridge_builder.EXPECTED_EVENT_FIELDS
            }
            event.update(
                {
                    "url": "https://luma.com/public-source-key",
                    "name": "Synthetic public event",
                    "start_at": "2026-08-11T12:00:00Z",
                    "meeting_url": "https://zoom.us/j/private-meeting-canary",
                }
            )
            checkpoint = {
                "event": event,
                "fetched_at": "2026-08-11T12:00:00Z",
                "guests": [
                    {"email": f"private-guest-canary-{index}@example.invalid"}
                    for index in range(100)
                ],
                "schema_version": 1,
            }
            checkpoint_path = checkpoint_root / "pair.json"
            checkpoint_path.write_text(json.dumps(checkpoint), encoding="utf-8")
            markdown = "A public description."
            (description_root / "pair.md").write_text(markdown, encoding="utf-8")
            (source_root / "private.csv").write_text(
                "email\nprivate-csv-canary@example.invalid\n",
                encoding="utf-8",
            )
            with (
                patch.object(bridge_builder, "EXPECTED_PAIR_COUNT", 1),
                patch.object(bridge_builder, "EXPECTED_DESCRIPTION_BYTES", len(markdown.encode())),
                patch.object(
                    bridge_builder,
                    "_decode_selected_json_value",
                    wraps=bridge_builder._decode_selected_json_value,
                ) as decode_value,
                patch.object(
                    bridge_builder,
                    "_read_bounded",
                    wraps=bridge_builder._read_bounded,
                ) as read_bounded,
                patch.dict(
                    bridge_builder.os.environ,
                    {"ISSUE_131_PRIVATE_CANARY": "private-environment-canary"},
                ),
            ):
                records = bridge_builder._load_source_pairs(source_root)

        self.assertEqual(decode_value.call_count, 4)
        self.assertEqual(
            {call.args[0].suffix for call in read_bounded.call_args_list},
            {".json", ".md"},
        )

        serialized = json.dumps(records, ensure_ascii=False)
        for private_value in (
            "private-guest-canary",
            "private-csv-canary",
            "private-meeting-canary",
            "private-environment-canary",
            "pair.json",
            "private.csv",
        ):
            self.assertNotIn(private_value, serialized)

    def test_exporter_and_output_symlinks_fail_before_use(self) -> None:
        scratch = Path(settings.BASE_DIR) / ".tmp"
        scratch.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=scratch) as directory:
            root = Path(directory)
            exporter = root / "exporter"
            source = exporter / "luma-events"
            source.mkdir(parents=True)
            exporter_alias = root / "exporter-alias"
            exporter_alias.symlink_to(exporter, target_is_directory=True)
            with self.assertRaisesRegex(bridge_builder.BridgeBuildError, "filesystem boundary"):
                bridge_builder._validate_exporter(
                    exporter_alias,
                    exporter_alias / "luma-events",
                )

            real_output = root / "bridge-target.json"
            real_output.write_text("unchanged", encoding="utf-8")
            output_alias = root / "bridge.json"
            output_alias.symlink_to(real_output)
            with (
                patch.object(bridge_builder, "BRIDGE_PATH", output_alias),
                self.assertRaisesRegex(bridge_builder.BridgeBuildError, "output boundary"),
            ):
                bridge_builder._write_bridge(output_alias, {"candidate": True})
            self.assertEqual(real_output.read_text(encoding="utf-8"), "unchanged")

    def test_interrupted_bridge_write_preserves_prior_artifact(self) -> None:
        scratch = Path(settings.BASE_DIR) / ".tmp"
        scratch.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=scratch) as directory:
            output = Path(directory) / "bridge.json"
            output.write_text("prior artifact\n", encoding="utf-8")
            with (
                patch.object(bridge_builder, "BRIDGE_PATH", output),
                patch.object(bridge_builder.os, "replace", side_effect=OSError("synthetic")),
                self.assertRaisesRegex(bridge_builder.BridgeBuildError, "write failed"),
            ):
                bridge_builder._write_bridge(output, {"candidate": True})

            self.assertEqual(output.read_text(encoding="utf-8"), "prior artifact\n")
            self.assertEqual(list(output.parent.glob("*.building")), [])

    def test_stored_html_validator_rejects_provider_and_attribute_tampering(self) -> None:
        for fragment in (
            '<p class="mt-4 leading-7"><script>bad</script></p>',
            '<p class="mt-4 leading-7"><a href="https://luma.com/source">Bad</a></p>',
            '<p class="mt-4 leading-7"><a class="app-link" href="https://example.com">Bad</a></p>',
        ):
            with self.subTest(fragment=fragment):
                with self.assertRaises(EventDescriptionBridgeError):
                    validate_description_html(fragment)
