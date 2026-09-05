from __future__ import annotations

from unittest.mock import patch

from django.core.exceptions import ImproperlyConfigured
from django.template.loader import render_to_string
from django.test import SimpleTestCase
from django.utils.html import escape

from content.event_speakers import event_speaker_records
from content.person_chip import PersonChip


class EventSpeakerRecordTests(SimpleTestCase):
    def test_speaker_credit_joins_the_canonical_profile_blocks_without_mutating_the_credit(
        self,
    ) -> None:
        credit = {
            "key": "ada-example",
            "name": "Ada Example",
            "public_path": "/people/ada-example.html",
        }
        blocks = [
            {
                "kind": "paragraph",
                "text": "Builds safe systems and shares [a guide](https://example.com/guide).",
            }
        ]

        records = event_speaker_records(
            [credit],
            people_by_slug={"ada-example": {"blocks": blocks}},
            people_by_path={},
        )

        self.assertEqual(records[0]["key"], "ada-example")
        self.assertEqual(records[0]["bio_blocks"], tuple(blocks))
        self.assertNotIn("bio_blocks", credit)
        self.assertIsNot(records[0]["bio_blocks"][0], blocks[0])

    def test_missing_or_malformed_profile_bio_is_an_empty_rendering_state(self) -> None:
        records = event_speaker_records(
            [
                {
                    "key": "missing",
                    "name": "Missing Bio",
                    "public_path": "/people/missing.html",
                },
                {
                    "key": "malformed",
                    "name": "Malformed Bio",
                    "public_path": "/people/malformed.html",
                },
                {
                    "key": "invalid-block",
                    "name": "Invalid Block",
                    "public_path": "/people/invalid-block.html",
                },
            ],
            people_by_slug={
                "malformed": {"blocks": "not-a-block-list"},
                "invalid-block": {"blocks": [{"kind": [], "text": "ignored"}]},
            },
            people_by_path={},
        )

        self.assertEqual(records[0]["bio_blocks"], ())
        self.assertEqual(records[1]["bio_blocks"], ())
        self.assertEqual(records[2]["bio_blocks"], ())

    def test_speaker_credit_shape_is_checked(self) -> None:
        with self.assertRaisesRegex(ImproperlyConfigured, "speakers must be a list"):
            event_speaker_records(
                {"key": "ada-example"},
                people_by_slug={},
                people_by_path={},
            )
        with self.assertRaisesRegex(ImproperlyConfigured, "speaker must be a mapping"):
            event_speaker_records(
                ["ada-example"],
                people_by_slug={},
                people_by_path={},
            )


class EventSpeakerTemplateTests(SimpleTestCase):
    def _render(self, speaker: dict) -> str:
        with patch(
            "content.templatetags.people.resolve_person_chip",
            return_value=PersonChip(
                name=str(speaker["name"]),
                public_path=str(speaker.get("public_path", "")),
                image_path="",
                media_available=False,
            ),
        ):
            return render_to_string("public/_event_speaker.html", {"speaker": speaker})

    def test_bio_text_is_escaped_and_reviewed_markdown_links_remain_safe_links(self) -> None:
        body = self._render(
            {
                "name": "Ada <Speaker>",
                "public_path": "",
                "bio_blocks": (
                    {
                        "kind": "paragraph",
                        "text": (
                            "Uses <script>alert(1)</script> and "
                            "[a public guide](https://example.com/guide)."
                        ),
                    },
                ),
            }
        )

        self.assertNotIn("<script>", body)
        self.assertIn(escape("<script>alert(1)</script>"), body)
        self.assertIn('href="https://example.com/guide"', body)
        self.assertIn('target="_blank" rel="noopener noreferrer"', body)

    def test_empty_bio_has_a_clear_message_without_an_empty_prose_region(self) -> None:
        body = self._render(
            {
                "name": "Ada Example",
                "public_path": "",
                "bio_blocks": (),
            }
        )

        self.assertIn("No biography is available for this speaker.", body)
        self.assertNotIn('class="prose event-speaker-bio"', body)
