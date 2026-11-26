"""The site-wide breadcrumb rule (``_docs/design/design-system.md``, ``.breadcrumbs``).

Every page below a hub draws its trail with the one ``{% breadcrumbs %}`` tag,
ending with the page itself; hubs, the homepage and entrance pages draw none;
and no template offers an ad-hoc link back up the hierarchy beside the trail.
These are source contracts: the lists name templates, so a new page type has to
be placed in one of them on purpose.
"""

from __future__ import annotations

import re
from pathlib import Path

from django.conf import settings
from django.test import SimpleTestCase

ROOT = Path(settings.BASE_DIR)

#: Templates of pages below a hub.  Each calls ``{% breadcrumbs`` itself (or, for
#: a shared shell, fills the block its children override).
TRAILED = (
    # Blog, books, wiki, podcast, events, people — the reference trails.
    "templates/public/article_detail.html",
    "templates/public/book_detail.html",
    "templates/public/wiki_detail.html",
    "templates/public/wiki_search.html",
    "templates/public/wiki_graph.html",
    "templates/public/wiki_special.html",
    "templates/public/podcast_detail.html",
    "templates/public/event_detail.html",
    "templates/public/person_detail.html",
    # Docs and FAQ.
    "templates/review/docs_detail.html",
    "templates/review/faq_detail.html",
    # Courses: family, cohort, curriculum, homework, projects, leaderboard.
    "courses/templates/courses/course_family.html",
    "courses/templates/courses/course.html",
    "courses/templates/courses/module.html",
    "courses/templates/courses/unit.html",
    "courses/templates/courses/shared_module.html",
    "courses/templates/courses/shared_lesson.html",
    "courses/templates/courses/register.html",
    "courses/templates/courses/dashboard.html",
    "courses/templates/courses/enrollment.html",
    "courses/templates/courses/leaderboard.html",
    "courses/templates/courses/leaderboard_score_breakdown.html",
    "courses/templates/courses/leaderboard_complaint.html",
    "courses/templates/courses/wrapped.html",
    "courses/templates/courses/user_wrapped.html",
    "courses/templates/homework/homework.html",
    "courses/templates/homework/solution.html",
    "courses/templates/homework/stats.html",
    "courses/templates/projects/project.html",
    "courses/templates/projects/eval.html",
    "courses/templates/projects/eval_submit.html",
    "courses/templates/projects/criteria.html",
    "courses/templates/projects/results.html",
    "courses/templates/projects/stats.html",
    "courses/templates/projects/site_gallery.html",
    # Academy pages below the Academy home.
    "academy/templates/academy/article.html",
    "academy/templates/academy/articles.html",
    "academy/templates/academy/faq.html",
    "academy/templates/academy/guide.html",
    "academy/templates/academy/legal.html",
    "academy/templates/academy/materials.html",
    "academy/templates/academy/program.html",
    "academy/templates/academy/sessions.html",
    # Studio pages below the Studio home.
    "templates/studio/audit_list.html",
    "templates/studio/audit_detail.html",
    "templates/studio/certificates.html",
    "templates/studio/course_launch.html",
    "templates/studio/credentials.html",
    "templates/studio/event_identities.html",
    "templates/studio/event_identity_detail.html",
    "templates/studio/event_qna.html",
    "templates/studio/event_qna_cohost_created.html",
    "templates/studio/navigation.html",
    "templates/studio/settings.html",
    "templates/studio/sponsors.html",
    "templates/studio/sponsor_detail.html",
    "studio_courses/templates/studio_courses/base.html",
    "studio_courses/templates/studio_courses/campaign_form.html",
    "studio_courses/templates/studio_courses/campaign_registrations.html",
    "studio_courses/templates/studio_courses/cloudwatch_dashboard.html",
    "studio_courses/templates/studio_courses/course_admin.html",
    "studio_courses/templates/studio_courses/enrollment_edit.html",
    "studio_courses/templates/studio_courses/enrollments.html",
    "studio_courses/templates/studio_courses/homework_submission_edit.html",
    "studio_courses/templates/studio_courses/homework_submissions.html",
    "studio_courses/templates/studio_courses/leaderboard_complaints.html",
    "studio_courses/templates/studio_courses/project_submission_edit.html",
    "studio_courses/templates/studio_courses/project_submissions.html",
    "slack_access/templates/slack_access/review.html",
    "slack_access/templates/slack_access/report.html",
    "slack_access/templates/slack_access/moderation.html",
)

#: Hubs, the homepage and top-level pages: the masthead marks where you are.
#: Entrances (auth, confirmation and status steps, live Q&A screens) are steps
#: of a flow reached from a link or a message, not places under a hub.
UNTRAILED = (
    "templates/core/home.html",
    "templates/core/member_home.html",
    "templates/core/newsletter.html",
    "templates/core/sponsors.html",
    "templates/core/tour.html",
    "templates/public/collection_hub.html",
    "templates/public/events.html",
    "templates/public/podcast_hub.html",
    "templates/public/wiki_hub.html",
    "templates/review/docs_home.html",
    "templates/review/faq_home.html",
    "templates/review/slack.html",
    "templates/studio/home.html",
    "courses/templates/courses/course_list.html",
    "academy/templates/academy/home.html",
    "academy/templates/academy/confirm.html",
    "courses/templates/courses/registration_confirm.html",
    "courses/templates/courses/registration_resend_status.html",
    "courses/templates/courses/registration_fix_email_status.html",
    "accounts/templates/accounts/login.html",
    "accounts/templates/account/signup.html",
    "accounts/templates/account/password_reset.html",
    "accounts/templates/account/password_reset_done.html",
    "accounts/templates/account/password_reset_from_key.html",
    "accounts/templates/account/email_confirm.html",
    "accounts/templates/account/verification_sent.html",
    "course_platform_templates/accounts/account_settings.html",
    "slack_access/templates/slack_access/application.html",
    "slack_access/templates/slack_access/vouch.html",
    "slack_access/templates/slack_access/invite.html",
    "slack_access/templates/slack_access/redeem.html",
    "templates/events/qna/public.html",
    "templates/events/qna/host.html",
    "templates/events/qna/present.html",
)

#: Text that names an ad-hoc way up: "← all …", "← Back to …", or a link or
#: button whose own label starts "Back to".  ``aria_label=`` and ``aria-label=``
#: are deliberately not matched — they describe a control, they do not draw one.
UP_LINK = re.compile(
    r"(?:←|&larr;|&#8592;)\s*(?:all|back)\b"
    r"|<a\b[^>]*>\s*Back to\b"
    r"|(?<![\w-])label=\"Back to\b",
    re.IGNORECASE,
)

#: Matches that are not up-links, each with the reason it stays.
ALLOWED_UP_LINKS = {
    # In-page anchor to the FAQ's own table of contents, not a level up.
    ("templates/review/faq_detail.html", 'href="#faq-contents">Back to contents'),
    # Transactional status pages: the way back to the form to retry or fix the
    # address, reached from an emailed link; there is no hierarchy to trail.
    ("courses/templates/courses/registration_confirm.html", ">Back to registration"),
    ("courses/templates/courses/registration_resend_status.html", ">Back to registration"),
    ("courses/templates/courses/registration_fix_email_status.html", ">Back to registration"),
    # Auth flow: the next step of the reset flow is signing in again.
    ("accounts/templates/account/password_reset_done.html", ">Back to sign in"),
}

#: Sequence navigation that reads like a way back but is the previous step of a
#: reading order (the first lesson's "previous" is its module overview).  Pinned
#: here so it is a decision, not something the pattern happens to miss.
SEQUENCE_NAVIGATION = {
    "courses/templates/courses/shared_lesson.html": 'label="← Module overview"',
    "courses/templates/homework/homework.html": 'label="Module overview"',
}

_BREADCRUMBS_CALL = re.compile(r"\{%\s*breadcrumbs\b")


def _template_files() -> list[Path]:
    roots = [ROOT / "templates", ROOT / "course_platform_templates"]
    roots += sorted(path for path in ROOT.glob("*/templates") if path.is_dir())
    files: list[Path] = []
    for root in roots:
        files += sorted(root.rglob("*.html"))
    return files


def _calls_breadcrumbs(source: str) -> bool:
    # A call inside a {% comment %} block is prose about the tag, not a trail.
    stripped = re.sub(r"\{%\s*comment\s*%\}.*?\{%\s*endcomment\s*%\}", "", source, flags=re.S)
    return bool(_BREADCRUMBS_CALL.search(stripped))


class BreadcrumbRuleTests(SimpleTestCase):
    def test_every_page_below_a_hub_draws_the_trail(self) -> None:
        for name in TRAILED:
            with self.subTest(template=name):
                self.assertTrue(_calls_breadcrumbs((ROOT / name).read_text()))

    def test_hubs_home_and_entrances_draw_none(self) -> None:
        for name in UNTRAILED:
            with self.subTest(template=name):
                self.assertFalse(_calls_breadcrumbs((ROOT / name).read_text()))

    def test_the_two_lists_do_not_overlap(self) -> None:
        self.assertFalse(set(TRAILED) & set(UNTRAILED))

    def test_no_template_draws_an_ad_hoc_up_link(self) -> None:
        offenders = []
        for path in _template_files():
            name = str(path.relative_to(ROOT))
            source = path.read_text()
            for match in UP_LINK.finditer(source):
                start = source.rfind("\n", 0, match.start()) + 1
                line = source[start : source.find("\n", match.end())]
                allowed = {snippet for owner, snippet in ALLOWED_UP_LINKS if owner == name}
                if any(snippet in line for snippet in allowed):
                    continue
                offenders.append(f"{name}: {line.strip()}")
        self.assertEqual(offenders, [])

    def test_allowed_up_links_still_exist(self) -> None:
        # An allowance whose link is gone is stale; drop it rather than keep it.
        for name, snippet in ALLOWED_UP_LINKS:
            with self.subTest(template=name):
                self.assertIn(snippet, (ROOT / name).read_text())

    def test_sequence_navigation_keeps_its_module_overview_step(self) -> None:
        for name, snippet in SEQUENCE_NAVIGATION.items():
            with self.subTest(template=name):
                self.assertIn(snippet, (ROOT / name).read_text())

    def test_every_tag_call_ends_with_the_current_page(self) -> None:
        # A call either names `current=` or passes a built Trail (a single
        # variable, whose own `current` is the last crumb).  Shared shells whose
        # every child overrides the trail block are the one exception.
        shells = {
            "courses/templates/courses/_submission_page.html",
            "courses/templates/projects/_base.html",
        }
        call = re.compile(r"\{%\s*breadcrumbs\s+(.*?)%\}", re.S)
        for path in _template_files():
            name = str(path.relative_to(ROOT))
            if name in shells:
                continue
            source = re.sub(
                r"\{%\s*comment\s*%\}.*?\{%\s*endcomment\s*%\}", "", path.read_text(), flags=re.S
            )
            for match in call.finditer(source):
                arguments = match.group(1)
                without_keywords = re.sub(r'\w+=(?:"[^"]*"|\S+)', "", arguments)
                positional = re.findall(r'"[^"]*"|\S+', without_keywords)
                with self.subTest(template=name, call=arguments.strip()):
                    self.assertTrue(
                        "current=" in arguments or len(positional) == 1,
                        "a trail must end with the page itself",
                    )
