from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any, ClassVar

from django.template.loader import render_to_string
from django.test import SimpleTestCase

from content.podcast_content import episode_view
from content.podcast_resources import (
    EXTERNAL_RESOURCE_REL,
    EXTERNAL_RESOURCE_TARGET,
    PodcastResourceError,
    normalize_podcast_resource,
    normalize_podcast_resources,
)

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]


class PodcastShowNotesContractTests(SimpleTestCase):
    records: ClassVar[list[dict[str, Any]]]

    @classmethod
    def setUpClass(cls) -> None:
        super().setUpClass()
        cls.records = json.loads(
            (REPOSITORY_ROOT / "content/public_projection/podcasts.json").read_text(
                encoding="utf-8"
            )
        )

    def render_episode(self, record: dict) -> str:
        episode = episode_view(record, resource_podcast_records=self.records)
        return render_to_string(
            "public/podcast_detail.html",
            {
                "seo_title": record["title"],
                "seo_description": record["description"],
                "canonical_url": "https://datatalks.club" + record["public_path"],
                "episode": episode,
                "record": record,
                "previous_episode": None,
                "next_episode": None,
                "related_episodes": (),
                "episode_graph": None,
                "structured_data": "",
                "og_type": "article",
                "og_image_url": "",
                "published_time": "",
                "DARK_MODE": False,
            },
        )

    def test_all_checked_episodes_normalize_source_resources_with_safe_metadata(self) -> None:
        normalized = [
            normalize_podcast_resources(
                record.get("resources"),
                records=self.records,
                strict=False,
            )
            for record in self.records
        ]
        rows = [resource for episode in normalized for resource in episode]

        self.assertEqual(len(self.records), 203)
        self.assertEqual(sum(bool(episode) for episode in normalized), 174)
        self.assertEqual(
            sum((len(record.get("resources") or []) for record in self.records), 0),
            544,
        )
        self.assertEqual(len(rows), 544)
        self.assertEqual(sum(resource.is_external for resource in rows), 541)
        self.assertEqual(sum(not resource.is_external for resource in rows), 3)
        self.assertEqual(sum(not episode for episode in normalized), 29)
        self.assertFalse(
            any(
                resource.url.startswith("/podcast/") and ".html" in resource.url
                for resource in rows
            )
        )

        for record in self.records:
            for resource in record.get("resources") or []:
                self.assertEqual(
                    set(resource),
                    {"title", "url", "is_external", "target", "rel"},
                )

        for resource in rows:
            data = resource.as_dict()
            self.assertEqual(
                set(data),
                {"title", "url", "is_external", "target", "rel"},
            )
            if resource.is_external:
                self.assertTrue(resource.url.startswith("https://"))
                self.assertEqual(resource.target, EXTERNAL_RESOURCE_TARGET)
                self.assertEqual(resource.rel, EXTERNAL_RESOURCE_REL)
            else:
                self.assertRegex(resource.url, r"^/podcast/s[0-9]+e[0-9]+/[a-z0-9][a-z0-9_-]*$")
                self.assertEqual(resource.target, "")
                self.assertEqual(resource.rel, "")

    def test_structured_resources_keep_transcript_and_timestamp_data_separate(self) -> None:
        record = next(record for record in self.records if record["slug"] == "building-data-team")

        self.assertEqual(record["public_path"], "/podcast/building-data-team.html")
        self.assertEqual(len(record["resources"]), 14)
        self.assertEqual(
            record["resources"][0],
            {
                "title": "Extreme Programming Explained by Kent Beck (1999)",
                "url": (
                    "https://www.amazon.com/Extreme-Programming-Explained-Embrace-Change/"
                    "dp/0321278658"
                ),
                "is_external": True,
                "target": "_blank",
                "rel": "noopener noreferrer",
            },
        )
        self.assertEqual(
            record["transcript_provenance"]["source_path"],
            "podcasts/s01/e03-transcript.yaml",
        )
        self.assertEqual(record["transcript"][2]["time"], "2:06")
        self.assertEqual(record["transcript"][2]["sec"], 126)

    def test_normalization_is_idempotent_and_does_not_mutate_provenance(self) -> None:
        record = next(
            record
            for record in self.records
            if record["slug"]
            == "data-freelancing-career-strategy-market-demand-and-client-acquisition"
        )
        original = copy.deepcopy(record)
        first = normalize_podcast_resources(record["resources"], records=self.records)
        second = normalize_podcast_resources(
            [resource.as_dict() for resource in first],
            records=self.records,
        )

        self.assertEqual(first, second)
        self.assertEqual(record, original)
        self.assertEqual(record["provenance"]["source_key"], record["slug"])

        episode = episode_view(record, resource_podcast_records=self.records)
        self.assertEqual(episode.links, episode.resources)

    def test_projected_resource_metadata_must_agree_with_the_destination(self) -> None:
        with self.assertRaisesRegex(PodcastResourceError, "metadata disagrees"):
            normalize_podcast_resource(
                {
                    "title": "Website",
                    "url": "https://example.com/",
                    "is_external": False,
                    "target": "",
                    "rel": "",
                }
            )

        with self.assertRaisesRegex(PodcastResourceError, "metadata is incomplete"):
            normalize_podcast_resource(
                {
                    "title": "Website",
                    "url": "https://example.com/",
                    "is_external": True,
                }
            )

    def test_historical_internal_episode_paths_resolve_to_hierarchical_targets(self) -> None:
        cases = (
            (
                "/podcast/s16e09-become-data-freelancer.html",
                "/podcast/s16e09/becoming-data-freelancer",
            ),
            (
                "/podcast/s11e03-from-data-science-to-dataops.html",
                "/podcast/s11e03/dataops-and-gitops-best-practices-for-data-teams",
            ),
            (
                "/podcast/s07e02-recruiting-data-professionals.html",
                "/podcast/s07e02/hiring-data-scientists-and-analysts",
            ),
        )
        for source_url, expected_url in cases:
            with self.subTest(source_url=source_url):
                resource = normalize_podcast_resource(
                    {"title": "Related episode", "url": source_url},
                    records=self.records,
                )
                self.assertEqual(resource.url, expected_url)
                self.assertFalse(resource.is_external)
                self.assertNotIn(".html", resource.url)

        http_internal = normalize_podcast_resource(
            {
                "title": "Related episode",
                "url": "http://www.datatalks.club/podcast/s16e09-become-data-freelancer.html",
            },
            records=self.records,
        )
        self.assertEqual(http_internal.url, "/podcast/s16e09/becoming-data-freelancer")
        self.assertFalse(http_internal.is_external)

    def test_labels_keep_source_meaning_while_normalizing_platform_spelling(self) -> None:
        self.assertEqual(
            normalize_podcast_resource(
                {"title": "Linkedin", "url": "https://www.linkedin.com/"}
            ).label,
            "LinkedIn",
        )
        self.assertEqual(
            normalize_podcast_resource(
                {"title": "Github repo", "url": "https://github.com/example/repo"}
            ).title,
            "GitHub repo",
        )

    def test_invalid_or_unknown_links_are_omitted_only_in_tolerant_mode(self) -> None:
        values = [
            {"title": "Unsafe", "url": "javascript:alert(1)"},
            {"title": "Unknown", "url": "/podcast/not-in-the-catalog.html"},
            {
                "title": "Query-bearing internal",
                "url": "/podcast/s16e09-become-data-freelancer.html?from=notes",
            },
            {"title": "Missing URL"},
        ]
        self.assertEqual(
            normalize_podcast_resources(values, records=self.records, strict=False),
            (),
        )
        for value in values:
            with self.subTest(value=value):
                with self.assertRaises(PodcastResourceError):
                    normalize_podcast_resource(value, records=self.records)

    def test_show_notes_render_safe_external_and_internal_link_metadata(self) -> None:
        external_record = next(
            record
            for record in self.records
            if record["slug"] == "s24e06-how-to-build-ai-that-actually-ships-in-production"
        )
        external_body = self.render_episode(external_record)
        external_start = external_body.index('href="https://alexkimds.github.io/"')
        external_link = external_body[external_start : external_body.index("</a>", external_start)]
        self.assertIn('target="_blank"', external_link)
        self.assertIn('rel="noopener noreferrer"', external_link)
        self.assertIn("Website", external_link)
        self.assertIn("opens in a new tab", external_link)

        internal_record = next(
            record
            for record in self.records
            if record["slug"]
            == "data-freelancing-career-strategy-market-demand-and-client-acquisition"
        )
        internal_body = self.render_episode(internal_record)
        internal_start = internal_body.index('href="/podcast/s16e09/becoming-data-freelancer"')
        internal_link = internal_body[internal_start : internal_body.index("</a>", internal_start)]
        self.assertNotIn("target=", internal_link)
        self.assertNotIn("rel=", internal_link)
        self.assertNotIn("opens in a new tab", internal_link)
        self.assertNotIn(".html", internal_link)

    def test_show_notes_section_has_an_empty_state_when_the_source_has_no_resources(self) -> None:
        record = next(
            record
            for record in self.records
            if record["slug"] == "ab-testing-and-product-experimentation"
        )
        body = self.render_episode(record)

        self.assertIn('id="show-notes-heading"', body)
        self.assertIn("No show notes are available for this episode.", body)
        self.assertNotIn('<ul class="episode-resource-list"', body)
