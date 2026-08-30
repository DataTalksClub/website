from django.test import SimpleTestCase

from events.slugs import MAX_EVENT_SLUG_LENGTH, event_title_slug


class EventTitleSlugTests(SimpleTestCase):
    def test_short_title_keeps_its_complete_title_slug(self) -> None:
        self.assertEqual(event_title_slug("Identity fixture"), "identity-fixture")

    def test_long_title_is_shortened_at_a_word_boundary(self) -> None:
        slug = event_title_slug(
            "How to Work with AI Coding Agents: Spec-Driven Development, "
            "Context and Loop Engineering, Workflows"
        )

        self.assertEqual(
            slug,
            "how-to-work-with-ai-coding-agents-spec-driven-development",
        )
        self.assertLessEqual(len(slug), MAX_EVENT_SLUG_LENGTH)

    def test_single_long_word_respects_the_hard_limit(self) -> None:
        slug = event_title_slug("A" * 100)

        self.assertEqual(slug, "a" * MAX_EVENT_SLUG_LENGTH)
