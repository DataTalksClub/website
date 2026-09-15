"""External destinations in course Markdown open in a new tab; internal ones don't.

Course bodies routinely link out to docs sites, GitHub repositories and other
tools.  A reader who follows one of those should not lose the lesson page in
the process, so ``render_markdown`` (``courses.registration``) tags every
external anchor with ``target="_blank" rel="noopener noreferrer"`` -- the
security-correct pairing (``target="_blank"`` alone is a reverse-tabnabbing
hole).  A link back to this site, or a relative path, is left exactly as
authored: it should navigate in place like every other internal link.
"""

from django.test import SimpleTestCase

from courses.registration import render_markdown


class ExternalMarkdownLinkTests(SimpleTestCase):
    def test_external_link_gets_a_new_tab_target_and_safe_rel(self):
        rendered = render_markdown("[docs](https://docs.example.com/guide)")

        self.assertIn(
            '<a href="https://docs.example.com/guide" target="_blank" '
            'rel="noopener noreferrer">docs</a>',
            rendered,
        )

    def test_absolute_link_back_to_this_site_is_untouched(self):
        rendered = render_markdown("[course](https://datatalks.club/courses/x)")

        self.assertIn('<a href="https://datatalks.club/courses/x">course</a>', rendered)
        self.assertNotIn("target=", rendered)

    def test_www_variant_of_this_site_counts_as_internal(self):
        rendered = render_markdown("[course](https://www.datatalks.club/courses/x)")

        self.assertNotIn("target=", rendered)

    def test_relative_link_is_untouched(self):
        rendered = render_markdown("[lesson](/courses/x/module/y/lesson)")

        self.assertIn('<a href="/courses/x/module/y/lesson">lesson</a>', rendered)
        self.assertNotIn("target=", rendered)

    def test_mailto_link_is_not_tagged_as_a_new_tab(self):
        rendered = render_markdown("[email](mailto:hello@example.com)")

        self.assertNotIn("target=", rendered)

    def test_raw_html_external_anchor_is_also_tagged(self):
        rendered = render_markdown(
            'Course code on <a href="https://github.com/DataTalksClub/thing">GitHub</a>.'
        )

        self.assertIn(
            '<a href="https://github.com/DataTalksClub/thing" target="_blank" '
            'rel="noopener noreferrer">GitHub</a>',
            rendered,
        )

    def test_existing_rel_on_a_raw_external_anchor_is_preserved_and_extended(self):
        rendered = render_markdown('See <a href="https://example.com" rel="nofollow">this</a>.')

        self.assertIn(
            '<a href="https://example.com" rel="nofollow noopener noreferrer" '
            'target="_blank">this</a>',
            rendered,
        )

    def test_external_url_shown_as_text_inside_a_code_fence_is_not_turned_into_a_link(self):
        rendered = render_markdown("```\nhttps://example.com\n```")

        self.assertNotIn("<a ", rendered)
        self.assertIn("https://example.com", rendered)

    def test_escaped_markup_inside_a_fenced_block_is_not_reparsed_as_a_tag(self):
        rendered = render_markdown(
            '```html\n<a href="https://example.com">not a real link</a>\n```'
        )

        self.assertNotIn('<a href="https://example.com">', rendered)
        self.assertIn("&lt;a href=", rendered)
