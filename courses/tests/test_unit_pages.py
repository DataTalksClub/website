import uuid
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import resolve, reverse
from django.utils import timezone

from courses.models import Cohort, Course, CurriculumFormat, Homework, Module, Unit
from courses.services.unit_read_state import set_unit_read_state
from courses.templatetags.curriculum_titles import unit_display_title


class PublicUnitPageTests(TestCase):
    def setUp(self):
        self.course_family = Course.objects.create(
            slug="llm-zoomcamp",
            title="LLM Zoomcamp",
        )
        self.cohort = Cohort.objects.create(
            course=self.course_family,
            slug="llm-zoomcamp-spring-2026",
            identifier="spring-2026",
            year=2026,
            title="LLM Zoomcamp Spring 2026",
            description="A module-format cohort.",
            curriculum_format=CurriculumFormat.MODULES,
            github_repo_url="https://github.com/DataTalksClub/llm-zoomcamp.git",
        )
        self.homework = Homework.objects.create(
            course=self.cohort,
            slug="agentic-rag-homework",
            title="Agentic RAG Homework",
            due_date=timezone.now() + timedelta(days=7),
        )
        self.module = Module.objects.create(
            cohort=self.cohort,
            position=10,
            slug="01-agentic-rag",
            title="Agentic RAG",
            terminal_homework=self.homework,
        )
        self.first_unit = Unit.objects.create(
            module=self.module,
            position=10,
            slug="01-intro",
            title="Introduction",
            source_content_id=uuid.uuid4(),
            source_path="cohorts/2026/01-agentic-rag/lessons/01-intro.md",
            source_commit_sha="a" * 40,
            source_checksum="b" * 64,
            content_markdown=(
                "## Welcome\n\nBuild an **agent** with `Python`.\n\n"
                '```python\nprint("hello, agent")\n```'
            ),
        )
        self.middle_unit = Unit.objects.create(
            module=self.module,
            position=20,
            slug="02-environment",
            title="Environment",
            content_markdown="Configure the environment.",
        )
        self.final_unit = Unit.objects.create(
            module=self.module,
            position=30,
            slug="03-evaluation",
            title="Evaluation",
            content_markdown="Evaluate the system.",
        )

    def unit_url(self, unit, **overrides):
        kwargs = {
            "course_slug": self.course_family.slug,
            "cohort_identifier": self.cohort.identifier,
            "module_slug": self.module.slug,
            "unit_slug": unit.slug,
        }
        kwargs.update(overrides)
        return reverse("unit", kwargs=kwargs)

    def test_renders_markdown_and_course_hierarchy(self):
        url = self.unit_url(self.first_unit)

        response = self.client.get(url)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(resolve(url).url_name, "unit")
        self.assertEqual(response.context["unit"], self.first_unit)
        self.assertContains(response, "<h2>Welcome</h2>", html=True)
        self.assertContains(response, "<strong>agent</strong>", html=True)
        self.assertContains(response, "<code>Python</code>", html=True)
        self.assertContains(
            response,
            '<pre><code>print("hello, agent")\n</code></pre>',
            html=True,
        )
        self.assertContains(response, 'src="/static/core/code_blocks.js"')
        self.assertContains(response, self.course_family.title)
        self.assertContains(response, self.cohort.title)
        self.assertContains(response, self.module.title)
        self.assertContains(response, 'class="module-sidebar module-rail"')
        self.assertContains(response, self.middle_unit.title)
        self.assertContains(response, 'aria-current="page"')
        self.assertContains(
            response,
            f'<link rel="canonical" href="https://datatalks.club{url}">',
            html=True,
        )
        self.assertContains(response, 'class="band band-lavender content-page-content')
        self.assertContains(response, 'class="module-layout shell-breakout"')
        body = response.content.decode()
        self.assertLess(
            body.index('class="module-main unit-main"'),
            body.index('class="module-sidebar'),
        )
        self.assertEqual(body.count("<h1"), 1)

    def test_middle_unit_links_to_previous_and_next_units_with_buttons(self):
        response = self.client.get(self.unit_url(self.middle_unit))

        self.assertContains(response, self.unit_url(self.first_unit))
        self.assertContains(response, self.unit_url(self.final_unit))
        self.assertContains(response, "← Introduction")
        self.assertContains(response, "Evaluation →")
        self.assertNotContains(response, "Continue to homework")

    def test_prev_and_next_share_one_variant_and_carry_normalised_labels(self):
        """Both directions match the homework page, and neither repeats an ordinal.

        The rail's disc numbers the lesson, so a button saying "1.2 ML vs
        Rule-Based Systems →" numbered it a second time inside a control.
        """

        self.first_unit.title = "1.1 Introduction"
        self.first_unit.save(update_fields=["title"])
        self.final_unit.title = "1.3 Evaluation"
        self.final_unit.save(update_fields=["title"])

        body = self.client.get(self.unit_url(self.middle_unit)).content.decode()
        start = body.index('class="unit-navigation"')
        navigation = body[start : body.index("</nav>", start)]

        self.assertIn("← Introduction", navigation)
        self.assertIn("Evaluation →", navigation)
        self.assertNotIn("1.1 ", navigation)
        self.assertNotIn("1.3 ", navigation)
        self.assertNotIn("cta-subtle", navigation)
        self.assertEqual(navigation.count("cta-secondary"), 2)

    def test_the_rail_numbers_each_lesson_once(self):
        self.middle_unit.title = "1.2 Environment"
        self.middle_unit.save(update_fields=["title"])

        body = self.client.get(self.unit_url(self.first_unit)).content.decode()
        start = body.index('class="module-sidebar module-rail"')
        rail = body[start : body.index("</aside>", start)]

        self.assertIn(">Environment</a>", rail)
        self.assertNotIn("1.2 Environment", rail)

    def test_declared_lesson_video_is_embedded_and_leading_title_is_removed(self):
        """The video is a persisted lesson field, not a line fished out of the body.

        The repository declares it in the lesson frontmatter, so it never
        appears in the Markdown the page renders.
        """

        self.first_unit.content_markdown = "# Introduction\n\nThe lesson body."
        self.first_unit.video_url = "https://www.youtube.com/watch?v=abc123&list=PL3"
        self.first_unit.save(update_fields=["content_markdown", "video_url"])

        response = self.client.get(self.unit_url(self.first_unit))

        self.assertContains(response, 'src="https://www.youtube.com/embed/abc123"')
        self.assertContains(response, 'title="Introduction video lesson"')
        self.assertContains(response, 'referrerpolicy="strict-origin-when-cross-origin"')
        self.assertContains(response, "The lesson body.")
        self.assertNotContains(response, "<h1>Introduction</h1>", html=True)

    def test_unit_without_a_declared_video_renders_no_player(self):
        response = self.client.get(self.unit_url(self.first_unit))

        self.assertEqual(response.context["video_embed_url"], "")
        self.assertNotContains(response, "<iframe")

    def test_non_youtube_video_url_is_never_framed(self):
        self.first_unit.video_url = "https://videos.example.invalid/watch?v=abc123"
        self.first_unit.save(update_fields=["video_url"])

        response = self.client.get(self.unit_url(self.first_unit))

        self.assertEqual(response.context["video_embed_url"], "")
        self.assertNotContains(response, "videos.example.invalid")

    def test_declared_code_files_resolve_to_upstream_repository_links(self):
        self.first_unit.code_sources = [
            {
                "label": "notebook.ipynb",
                "source_path": "cohorts/2026/01-agentic-rag/code/notebook.ipynb",
            }
        ]
        self.first_unit.save(update_fields=["code_sources"])

        response = self.client.get(self.unit_url(self.first_unit))

        self.assertEqual(
            [(link.label, link.url) for link in response.context["unit_code_links"]],
            [
                (
                    "notebook.ipynb",
                    "https://github.com/DataTalksClub/llm-zoomcamp/blob/main/"
                    "cohorts/2026/01-agentic-rag/code/notebook.ipynb",
                )
            ],
        )

    def test_declared_code_files_are_dropped_without_a_known_repository(self):
        self.cohort.github_repo_url = ""
        self.cohort.save(update_fields=["github_repo_url"])
        self.first_unit.code_sources = [
            {"label": "notebook.ipynb", "source_path": "code/notebook.ipynb"}
        ]
        self.first_unit.save(update_fields=["code_sources"])

        response = self.client.get(self.unit_url(self.first_unit))

        self.assertEqual(response.context["unit_code_links"], ())

    def test_renders_edit_on_github_link_from_cohort_repository_and_source_path(self):
        response = self.client.get(self.unit_url(self.first_unit))

        edit_url = (
            "https://github.com/DataTalksClub/llm-zoomcamp/edit/main/"
            "cohorts/2026/01-agentic-rag/lessons/01-intro.md"
        )
        self.assertContains(response, "Edit this page on GitHub")
        self.assertContains(
            response,
            edit_url,
        )
        self.assertContains(response, 'target="_blank"')

    def test_edit_link_falls_back_to_course_family_repository(self):
        self.cohort.github_repo_url = ""
        self.cohort.save(update_fields=["github_repo_url"])
        self.course_family.github_repo_url = "https://github.com/example/llm-zoomcamp/"
        self.course_family.save(update_fields=["github_repo_url"])

        response = self.client.get(self.unit_url(self.first_unit))

        self.assertContains(
            response,
            "https://github.com/example/llm-zoomcamp/edit/main/"
            "cohorts/2026/01-agentic-rag/lessons/01-intro.md",
        )

    def test_does_not_render_edit_link_without_repository_or_source_path(self):
        self.cohort.github_repo_url = ""
        self.cohort.save(update_fields=["github_repo_url"])
        self.first_unit.source_content_id = None
        self.first_unit.source_path = None
        self.first_unit.source_commit_sha = None
        self.first_unit.source_checksum = None
        self.first_unit.save(
            update_fields=[
                "source_content_id",
                "source_path",
                "source_commit_sha",
                "source_checksum",
            ]
        )

        response = self.client.get(self.unit_url(self.first_unit))

        self.assertNotContains(response, "Edit this page on GitHub")

    def test_the_read_toggle_is_the_one_control_at_the_foot_of_the_lesson(self):
        """One toggle, at the end of the lesson, not one under every rail row."""

        user = get_user_model().objects.create_user(username="learner")
        self.client.force_login(user)
        unit_url = self.unit_url(self.first_unit)

        body = self.client.get(unit_url).content.decode()
        footer_start = body.index('<div class="unit-footer">')
        footer = body[footer_start : body.index("</nav>", footer_start)]
        rail_start = body.index('class="module-sidebar module-rail"')
        rail = body[rail_start : body.index("</aside>", rail_start)]

        self.assertEqual(body.count("Mark as read"), 1)
        self.assertIn("Mark as read", footer)
        self.assertIn('name="is_read" value="1"', footer)
        self.assertIn(f'name="next" value="{unit_url}"', footer)
        self.assertNotIn("<form", rail)
        # The footer sits after the article and before the prev/next row.
        self.assertLess(body.index("</article>"), footer_start)
        self.assertLess(footer_start, body.index('class="unit-navigation"'))

        set_unit_read_state(
            user=user,
            module=self.module,
            unit=self.first_unit,
            is_read=True,
        )
        body = self.client.get(unit_url).content.decode()
        self.assertIn("Mark as unread", body)
        self.assertNotIn("Mark as read<", body)

    def test_signed_out_readers_get_no_read_toggle(self):
        body = self.client.get(self.unit_url(self.first_unit)).content.decode()

        self.assertNotIn("Mark as read", body)
        self.assertNotIn("<form", body.split('<div class="unit-footer">', 1)[1])

    def test_the_edit_link_is_a_colophon_after_the_lesson(self):
        """An editor affordance is information about the page, not part of it."""

        body = self.client.get(self.unit_url(self.first_unit)).content.decode()

        self.assertLess(body.index("</article>"), body.index("Edit this page on GitHub"))
        self.assertNotIn("unit-edit-link", body)

    def test_marking_a_lesson_read_returns_to_that_lesson(self):
        user = get_user_model().objects.create_user(username="reader")
        self.client.force_login(user)
        unit_url = self.unit_url(self.middle_unit)
        read_state_url = reverse(
            "unit_read_state",
            kwargs={
                "course_slug": self.course_family.slug,
                "cohort_identifier": self.cohort.identifier,
                "module_slug": self.module.slug,
                "unit_slug": self.middle_unit.slug,
            },
        )

        response = self.client.post(read_state_url, {"is_read": "1", "next": unit_url})

        self.assertRedirects(response, unit_url, fetch_redirect_response=False)

    def test_an_off_site_return_path_is_refused(self):
        user = get_user_model().objects.create_user(username="reader")
        self.client.force_login(user)
        read_state_url = reverse(
            "unit_read_state",
            kwargs={
                "course_slug": self.course_family.slug,
                "cohort_identifier": self.cohort.identifier,
                "module_slug": self.module.slug,
                "unit_slug": self.middle_unit.slug,
            },
        )
        module_url = reverse(
            "module",
            kwargs={
                "course_slug": self.course_family.slug,
                "cohort_identifier": self.cohort.identifier,
                "module_slug": self.module.slug,
            },
        )
        hostile_paths = (
            "https://evil.example/steal",
            "//evil.example/steal",
            "javascript:alert(1)",
        )

        for hostile in hostile_paths:
            with self.subTest(next=hostile):
                response = self.client.post(read_state_url, {"is_read": "1", "next": hostile})
                self.assertRedirects(response, module_url, fetch_redirect_response=False)

    def test_the_rail_states_sign_in_once_and_returns_to_this_lesson(self):
        """The rail said sign-in twice; the masthead already says it globally.

        What survives is the one contextual line, because it explains the read
        marks that are visibly absent here -- and its `next` used to be the
        module page, so signing in from a lesson dropped the reader back on the
        index they had already left.
        """

        unit_url = self.unit_url(self.middle_unit)
        body = self.client.get(unit_url).content.decode()
        rail_start = body.index('class="module-sidebar module-rail"')
        rail = body[rail_start : body.index("</aside>", rail_start)]

        self.assertEqual(rail.count("Sign in"), 1)
        self.assertIn(f'href="/accounts/login/?next={unit_url}"', rail)
        self.assertNotIn("Sign in to keep track of what you have read.", rail)
        # Reading is not gated, so the local prompt is a link, not a button.
        self.assertIn('class="band-link"', rail)
        self.assertNotIn("cta-secondary", rail)

    def test_final_unit_links_to_homework_with_a_button(self):
        response = self.client.get(self.unit_url(self.final_unit))
        homework_url = reverse(
            "homework",
            kwargs={
                "course_slug": self.course_family.slug,
                "cohort_year": self.cohort.identifier,
                "homework_slug": self.homework.slug,
            },
        )

        self.assertContains(response, homework_url)
        self.assertContains(response, "Continue to homework →")

    def test_breadcrumb_stops_at_the_module_and_names_the_edition_once(self):
        """The trail is ancestors only, and the cohort crumb is not the course again.

        "Courses / LLM Zoomcamp / spring-2026 / Agentic RAG", then the lesson as
        the h1.  The old trail said "LLM Zoomcamp Spring 2026" one crumb after
        "LLM Zoomcamp" and then repeated the lesson title as a fifth crumb, which
        is what pushed it onto a second row.
        """

        body = self.client.get(self.unit_url(self.first_unit)).content.decode()
        trail = body.split('<nav class="breadcrumbs"', 1)[1].split("</nav>", 1)[0]

        self.assertIn(">Courses</a>", trail)
        self.assertIn(f">{self.course_family.title}</a>", trail)
        self.assertIn(f">{self.cohort.identifier}</a>", trail)
        self.assertIn(f">{self.module.title}</a>", trail)
        # The edition crumb is the identifier, never the course name repeated.
        self.assertNotIn(self.cohort.title, trail)
        # The lesson is the heading below, so it is not also a crumb.
        self.assertNotIn(self.first_unit.title, trail)
        self.assertNotIn('aria-current="page"', trail)
        self.assertEqual(trail.count("<li"), 4)

    def test_the_unit_heading_stands_alone_without_a_module_subtitle(self):
        """The module line under the h1 was the crumb above it, said twice."""

        body = self.client.get(self.unit_url(self.first_unit)).content.decode()
        hero = body.split('class="unit-hero-inner"', 1)[1].split("</div>", 1)[0]

        self.assertIn(f'<h1 id="unit-heading">{self.first_unit.title}</h1>', hero)
        self.assertNotIn("unit-module", body)

    def test_arbitrary_cohort_identifier_is_the_route_identity(self):
        url = self.unit_url(self.first_unit)

        self.assertEqual(
            url,
            "/courses/llm-zoomcamp/spring-2026/modules/01-agentic-rag/01-intro",
        )
        self.assertEqual(self.client.get(url).status_code, 200)

    def unit_article(self, unit):
        body = self.client.get(self.unit_url(unit)).content.decode()
        return body[
            body.index('<article class="prose prose-reading unit-content">') : body.index(
                "</article>"
            )
        ]

    def test_sanitizes_active_content_from_markdown(self):
        self.first_unit.content_markdown = (
            "Safe text.\n\n"
            "<script>alert('xss')</script>\n\n"
            '<img src="x" onerror="alert(1)">\n\n'
            '<iframe src="https://evil.example/frame"></iframe>\n\n'
            '<a href="javascript:alert(1)" onclick="alert(2)">raw</a>\n\n'
            "[unsafe](javascript:alert(1))"
        )
        self.first_unit.save(update_fields=["content_markdown"])

        article = self.unit_article(self.first_unit)

        self.assertIn("Safe text.", article)
        self.assertNotIn("<script", article)
        self.assertNotIn("<iframe", article)
        self.assertNotIn("onerror", article)
        self.assertNotIn("onclick", article)
        self.assertNotIn('href="javascript:', article)
        self.assertIn("&lt;script&gt;", article)
        self.assertIn("&lt;iframe", article)
        # The one surviving element is the image, and only because its
        # repository-relative source was resolved to a public upstream URL.
        self.assertIn(
            '<img src="https://raw.githubusercontent.com/DataTalksClub/llm-zoomcamp'
            '/main/cohorts/2026/01-agentic-rag/lessons/x"',
            article,
        )

    def test_renders_raw_html_blocks_written_by_course_authors(self):
        self.first_unit.content_markdown = (
            '<a href="https://www.youtube.com/watch?v=Crm_5n4mvmg">'
            '<img src="images/thumbnail-1-01.jpg"></a>\n\n'
            "<table>\n<tr>\n<td>Warning</td>\n"
            "<td>The notes are written by the community.<br>Send a fix.</td>\n"
            "</tr>\n</table>\n"
        )
        self.first_unit.save(update_fields=["content_markdown"])

        article = self.unit_article(self.first_unit)

        self.assertIn('<a href="https://www.youtube.com/watch?v=Crm_5n4mvmg">', article)
        self.assertIn(
            '<img src="https://raw.githubusercontent.com/DataTalksClub/llm-zoomcamp'
            '/main/cohorts/2026/01-agentic-rag/lessons/images/thumbnail-1-01.jpg"',
            article,
        )
        self.assertIn("<table>", article)
        self.assertIn("<td>Warning</td>", article)
        self.assertIn("<br>", article)
        self.assertNotIn("&lt;table&gt;", article)
        self.assertNotIn("&lt;img", article)

    def test_untitled_upstream_image_borrows_the_unit_title_for_its_description(self):
        self.first_unit.content_markdown = '<img src="images/diagram.png">'
        self.first_unit.save(update_fields=["content_markdown"])

        self.assertIn('alt="Introduction"', self.unit_article(self.first_unit))

    def test_unresolvable_image_is_dropped_rather_than_rendered_broken(self):
        self.cohort.github_repo_url = ""
        self.cohort.save(update_fields=["github_repo_url"])
        self.first_unit.content_markdown = (
            '<img src="images/diagram.png" alt="Diagram">\n\n![Chart](images/chart.png)'
        )
        self.first_unit.save(update_fields=["content_markdown"])

        article = self.unit_article(self.first_unit)

        self.assertNotIn("<img", article)
        self.assertNotIn("images/diagram.png", article)
        self.assertNotIn("images/chart.png", article)
        self.assertIn("Chart", article)

    def test_absolute_upstream_image_sources_are_left_alone(self):
        self.first_unit.content_markdown = (
            '<img src="https://github.com/user-attachments/assets/abc" alt="Shared">'
        )
        self.first_unit.save(update_fields=["content_markdown"])

        self.assertIn(
            '<img src="https://github.com/user-attachments/assets/abc" alt="Shared">',
            self.unit_article(self.first_unit),
        )

    def test_returns_404_for_legacy_or_mismatched_ownership(self):
        legacy_cohort = Cohort.objects.create(
            course=self.course_family,
            slug="llm-zoomcamp-legacy",
            identifier="legacy",
            year=2025,
            title="Legacy cohort",
            description="Legacy curriculum.",
            curriculum_format=CurriculumFormat.LEGACY,
        )
        legacy_homework = Homework.objects.create(
            course=legacy_cohort,
            slug="legacy-homework",
            title="Legacy Homework",
            due_date=timezone.now() + timedelta(days=7),
        )
        legacy_module = Module.objects.create(
            cohort=legacy_cohort,
            position=10,
            slug="legacy-module",
            title="Legacy Module",
            terminal_homework=legacy_homework,
        )
        legacy_unit = Unit.objects.create(
            module=legacy_module,
            position=10,
            slug="legacy-unit",
            title="Legacy Unit",
        )
        other_family = Course.objects.create(
            slug="other-course",
            title="Other Course",
        )
        Cohort.objects.create(
            course=other_family,
            slug="other-course-spring-2026",
            identifier=self.cohort.identifier,
            year=2026,
            title="Other Course Spring 2026",
            description="Another module-format cohort.",
            curriculum_format=CurriculumFormat.MODULES,
        )
        other_homework = Homework.objects.create(
            course=self.cohort,
            slug="other-homework",
            title="Other Homework",
            due_date=timezone.now() + timedelta(days=7),
        )
        other_module = Module.objects.create(
            cohort=self.cohort,
            position=20,
            slug="other-module",
            title="Other Module",
            terminal_homework=other_homework,
        )
        other_unit = Unit.objects.create(
            module=other_module,
            position=10,
            slug="other-unit",
            title="Other Unit",
        )

        cases = (
            self.unit_url(
                legacy_unit,
                cohort_identifier=legacy_cohort.identifier,
                module_slug=legacy_module.slug,
            ),
            self.unit_url(self.first_unit, course_slug=other_family.slug),
            self.unit_url(other_unit, module_slug=self.module.slug),
            self.unit_url(self.first_unit, module_slug="missing-module"),
            self.unit_url(self.first_unit, unit_slug="missing-unit"),
        )
        for url in cases:
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 404)

    @override_settings(NOINDEX=False)
    def test_uses_public_course_page_index_and_cache_policy(self):
        response = self.client.get(self.unit_url(self.first_unit))

        self.assertNotIn("X-Robots-Tag", response.headers)
        cache_control = response.headers.get("Cache-Control", "")
        self.assertNotIn("private", cache_control)
        self.assertNotIn("no-store", cache_control)


class UnitDisplayTitleTests(TestCase):
    """Upstream titles number themselves raggedly; the UI numbers them once."""

    def test_strips_the_ordinal_the_surrounding_chrome_already_states(self):
        cases = {
            "1.1 Introduction to Machine Learning": "Introduction to Machine Learning",
            "1.10 Summary": "Summary",
            "2.3.1 Nested Section": "Nested Section",
            "1.1. Punctuated Section": "Punctuated Section",
            "3) Bracketed Ordinal": "Bracketed Ordinal",
            "4. Dotted Ordinal": "Dotted Ordinal",
        }
        for raw, expected in cases.items():
            with self.subTest(title=raw):
                self.assertEqual(unit_display_title(raw), expected)

    def test_leaves_a_title_whose_number_is_part_of_its_name(self):
        """A bare leading integer is not evidence of an ordinal.

        "10 Minutes to Pandas" opens with a number that belongs to the lesson's
        name; deleting it would produce a wrong title rather than a quieter one.
        """

        cases = (
            "Setting up the Environment",
            "10 Minutes to Pandas",
            "3 Ways to Evaluate a Model",
            "Introduction",
        )
        for raw in cases:
            with self.subTest(title=raw):
                self.assertEqual(unit_display_title(raw), raw)

    def test_a_title_that_is_only_an_ordinal_keeps_it(self):
        self.assertEqual(unit_display_title("1.1 "), "1.1 ")
        self.assertEqual(unit_display_title(None), "")
