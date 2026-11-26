"""The Book of the Week Q&A composition: who wrote each message, and its paragraphs."""

from __future__ import annotations

from django.test import SimpleTestCase

from content.book_content import book_threads


class BookThreadTests(SimpleTestCase):
    def test_author_replies_carry_the_author_credit_and_follow_ups_do_not(self) -> None:
        author = {"key": "andrewjones", "name": "Andrew Jones", "public_path": "/people/aj.html"}
        archive = [
            {
                "name": "Joe",
                "text": "First line.\n\nSecond line.",
                "replies": [
                    {"name": "andrew  jones", "text": "An answer."},
                    {"name": "Joe", "text": "Thanks!"},
                ],
            },
            {"name": "Ann", "text": "Unanswered?", "replies": []},
        ]
        first, second = book_threads(archive, [author])
        self.assertEqual(first.anchor, "question-1")
        self.assertEqual(first.question, ("First line.", "Second line."))
        self.assertIs(first.replies[0].author, author)
        self.assertIsNone(first.replies[1].author)
        self.assertTrue(first.answered_by_author)
        self.assertEqual(second.anchor, "question-2")
        self.assertFalse(second.answered_by_author)


class SlackEmphasisTests(SimpleTestCase):
    def test_slack_emphasis_marks_words_but_never_addresses_or_identifiers(self) -> None:
        from core.templatetags.public import public_text, slack_emphasis

        def render(text: str) -> str:
            return str(slack_emphasis(public_text(text)))

        self.assertEqual(render("the _autonomy_ word"), "the <em>autonomy</em> word")
        self.assertEqual(render("snake_case_name"), "snake_case_name")
        self.assertEqual(
            render("https://x.example/a_b_c?thread_ts=1"), "https://x.example/a_b_c?thread_ts=1"
        )
        # Markup inside the marks stays escaped text.
        self.assertEqual(render("_<b>_"), "<em>&lt;b&gt;</em>")


class BookThreadMergeTests(SimpleTestCase):
    def test_consecutive_messages_by_one_writer_read_as_one_reply(self) -> None:
        author = {"key": "a", "name": "Angelica", "public_path": ""}
        archive = [
            {
                "name": "Angelica",
                "text": "Hello everyone!",
                "replies": [
                    {"name": "Ifra", "text": "Question?"},
                    {"name": "Angelica", "text": "Part one."},
                    {"name": "Angelica", "text": "Part two."},
                    {"name": "Ifra", "text": "Thanks!"},
                ],
            }
        ]
        (thread,) = book_threads(archive, [author])
        self.assertIs(thread.asker_author, author)
        self.assertEqual([reply.name for reply in thread.replies], ["Ifra", "Angelica", "Ifra"])
        self.assertEqual(thread.replies[1].paragraphs, ("Part one.", "Part two."))
        self.assertIs(thread.replies[1].author, author)
