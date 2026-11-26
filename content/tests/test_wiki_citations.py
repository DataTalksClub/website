"""A wiki page draws its ``[[cite:...]]`` sources as numbered references."""

from __future__ import annotations

from unittest import mock

from django.template.loader import render_to_string
from django.test import SimpleTestCase

from content import projection_helpers as builder
from content import wiki_content

STACK = "/podcast/s23/e01-skill-stack"
ROLE = "/podcast/s23/e05-inside-role"
PODCASTS = (
    {"public_path": STACK, "title": "AI Engineering Skill Stack: Agents and LLMOps"},
    {"public_path": ROLE, "title": "Inside the AI Engineer Role"},
)


def _cite(markdown: str) -> dict:
    return {"kind": "paragraph", "text": "", "markdown": markdown}


class WikiCitationTests(SimpleTestCase):
    def setUp(self) -> None:
        patcher = mock.patch.object(wiki_content.catalogue, "podcasts", return_value=PODCASTS)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_the_sync_writes_a_citation_as_a_footnote_link_outside_the_plain_text(self) -> None:
        def href(token: str) -> str:
            return builder._wiki_token_href(
                token, {}, {"s23e01": STACK}, {}, {"nasser": "/people/nasser"}
            )[1]

        blocks = builder._body_blocks(
            "Ship product first.\n[[cite:s23e01=>Skill Stack]]\n[[person:nasser=>Nasser]] "
            "agrees.[[cite:unknown=>Gone]]",
            preserve_links=True,
            wiki_href=href,
        )

        self.assertEqual(
            blocks,
            [
                {
                    "kind": "paragraph",
                    "text": "Ship product first. Nasser agrees.",
                    "markdown": f"Ship product first. [^Skill Stack]({STACK}) "
                    "[Nasser](/people/nasser) agrees.",
                }
            ],
        )

    def test_references_number_in_reading_order_and_reuse_a_repeated_source(self) -> None:
        citations = wiki_content.wiki_citations(
            [
                {"kind": "heading", "level": 2, "id": "a", "text": "A"},
                _cite(f"Product first. [^Skill Stack]({STACK}) [Nasser](/people/n) agrees."),
                _cite(f"Model calls.[^Inside the AI Engineer Role]({ROLE})Next sentence."),
            ],
            [_cite(f"Again [^Skill Stack]({STACK}).")],
        )

        self.assertEqual(
            [(source.number, source.title, source.url) for source in citations.sources],
            [
                (1, "AI Engineering Skill Stack: Agents and LLMOps", STACK),
                (2, "Inside the AI Engineer Role", ROLE),
            ],
        )
        self.assertNotIn("html", citations.body[0])
        first, second = (block["html"] for block in citations.body[1:])
        self.assertIn(
            'Product first.\u2060<sup class="wiki-cite" id="cite-ref-1"><a href="#cite-note-1" '
            'title="AI Engineering Skill Stack: Agents and LLMOps" '
            'aria-label="Source 1: AI Engineering Skill Stack: Agents and LLMOps">[1]</a></sup> '
            '<a class="app-link" href="/people/n">Nasser</a> agrees.',
            first,
        )
        self.assertIn('Model calls.\u2060<sup class="wiki-cite" id="cite-ref-2">', second)
        self.assertIn("[2]</a></sup> Next sentence.", second)
        # A repeat reuses the number; trailing punctuation moves in front of it.
        self.assertIn(
            'Again.\u2060<sup class="wiki-cite" id="cite-ref-1-2"><a href="#cite-note-1"',
            citations.closing[0]["html"],
        )

    def test_a_stop_after_a_reference_is_not_doubled(self) -> None:
        citations = wiki_content.wiki_citations(
            [_cite(f"Proof. [^Role]({ROLE}). [^Stack]({STACK}) Then more.")]
        )
        html = citations.body[0]["html"]

        self.assertIn('Proof.\u2060<sup class="wiki-cite" id="cite-ref-1">', html)
        self.assertIn('[1]</a></sup>\u2060<sup class="wiki-cite" id="cite-ref-2">', html)
        self.assertIn("[2]</a></sup> Then more.", html)

    def test_a_page_without_citations_is_untouched(self) -> None:
        blocks = [{"kind": "paragraph", "text": "Plain", "markdown": "[Link](/wiki/x)"}]
        citations = wiki_content.wiki_citations(blocks)

        self.assertEqual(citations.body, tuple(blocks))
        self.assertEqual(citations.sources, ())

    def test_prose_body_draws_the_rendered_references(self) -> None:
        citations = wiki_content.wiki_citations([_cite(f"Text [^Stack]({STACK})")])
        rendered = render_to_string("public/_prose_body.html", {"blocks": citations.body})

        self.assertIn('Text\u2060<sup class="wiki-cite" id="cite-ref-1">', rendered)
        self.assertNotIn("[^", rendered)
