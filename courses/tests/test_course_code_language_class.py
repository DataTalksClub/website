"""The course sanitizer keeps a fence's language marker and nothing else.

Course bodies come from public GitHub repositories, so the allowlist in
``courses.registration`` is a security boundary rather than a formality.  The
browser's code runtime reads ``class="language-<name>"`` off the ``code``
element to decide how to colour a sample; before this contract existed the
class was stripped with everything else and every course sample rendered as
plain, unhighlighted text with no error anywhere.  These cases pin the exact
width of the exception: one attribute, on one tag, holding one language token.
"""

from django.test import SimpleTestCase

from courses.registration import ALLOWED_MARKDOWN_ATTRIBUTES, render_markdown


class CourseCodeLanguageClassTests(SimpleTestCase):
    def test_a_fenced_block_keeps_its_language_marker(self):
        rendered = render_markdown("```python\nimport os\n```")

        self.assertIn('<pre><code class="language-python">import os\n</code></pre>', rendered)

    def test_a_bare_fence_stays_a_plain_code_block(self):
        rendered = render_markdown("```\nmake test\n```")

        self.assertIn("<pre><code>make test\n</code></pre>", rendered)

    def test_the_widening_reaches_only_the_class_attribute_on_code(self):
        self.assertEqual(ALLOWED_MARKDOWN_ATTRIBUTES["code"], ["class"])
        self.assertNotIn("class", ALLOWED_MARKDOWN_ATTRIBUTES.get("pre", ()))
        self.assertNotIn("class", ALLOWED_MARKDOWN_ATTRIBUTES.get("span", ()))
        self.assertNotIn("span", ALLOWED_MARKDOWN_ATTRIBUTES)

        rendered = render_markdown(
            '<pre class="prose-scroll"><code id="x" onclick="alert(1)" '
            'class="language-python">import os</code></pre>'
        )

        self.assertIn('<code class="language-python">', rendered)
        self.assertNotIn("prose-scroll", rendered)
        self.assertNotIn("onclick", rendered)
        self.assertNotIn('id="x"', rendered)

    def test_a_class_that_is_not_a_single_language_token_is_dropped(self):
        for value in (
            "prose-scroll",
            "language-python prose-scroll",
            "impersonation-banner",
            "",
            "language-",
            'language-python" onmouseover="alert(1)',
        ):
            with self.subTest(value=value):
                rendered = render_markdown(f'<code class="{value}">x</code>')

                self.assertIn("<code", rendered)
                self.assertNotIn(f'class="{value}"', rendered)
                self.assertNotIn("onmouseover", rendered)

    def test_inline_code_written_by_markdown_carries_no_class(self):
        rendered = render_markdown("Use `uv` for this.")

        self.assertIn("<code>uv</code>", rendered)

    def test_a_mermaid_fence_is_still_a_diagram_rather_than_a_code_block(self):
        rendered = render_markdown("```mermaid\nflowchart LR\n  A --> B\n```")

        self.assertIn('<div class="mermaid">', rendered)
        self.assertNotIn("language-mermaid", rendered)
