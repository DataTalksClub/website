from datetime import timedelta

from django.contrib import admin
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from courses.models import (
    Course,
    CourseRegistration,
    DeliveryMode,
    Enrollment,
    Homework,
    ProjectSubmission,
    RegistrationCampaign,
    SharedCurriculum,
    SharedModule,
    User,
)
from test_support.course_catalog import make_cohort


class CourseFamilyLandingTests(TestCase):
    def setUp(self):
        self.family = Course.objects.create(
            slug="practical-course",
            title="Practical Course",
            starting_point="You have a dataset and a question.",
            prerequisites="You can write Python and use the command line.",
            progression=[
                {
                    "heading": "I have a dataset and a question",
                    "description": "I want to turn raw information into a useful answer.",
                },
                {
                    "heading": "I build and evaluate a model",
                    "description": "I prepare data, train a model, and test its predictions.",
                },
                {
                    "heading": "I publish a prediction service",
                    "description": "I deploy a working project that other people can use.",
                },
            ],
            outcome="Build and publish a useful prediction service.",
            github_repo_url="https://github.com/example/practical-course",
        )
        self.cohort = make_cohort(self.family, 2026, project_count=1)
        self.project = self.cohort.project_set.get()
        self.project.title = "Attempt 1"
        self.project.save(update_fields=["title"])
        self.curriculum = SharedCurriculum.objects.create(course=self.family)
        for position, title in enumerate(
            ["Prepare the data", "Train a model", "Evaluate predictions", "Deploy the service"]
        ):
            SharedModule.objects.create(
                curriculum=self.curriculum,
                position=position,
                slug=f"skill-{position}",
                title=title,
                summary=f"Learn how to {title.lower()} in a working project.",
            )
        self.campaign = RegistrationCampaign.objects.create(
            slug="practical-course",
            title=self.family.title,
            current_course=self.cohort,
        )
        self.url = reverse("course_family", args=[self.family.slug])

    def add_submission(self, cohort=None, *, volunteer=False):
        cohort = cohort or self.cohort
        user = User.objects.create_user(username=f"learner-{cohort.pk}")
        enrollment = Enrollment.objects.create(student=user, course=cohort)
        return ProjectSubmission.objects.create(
            project=cohort.project_set.first(),
            student=user,
            enrollment=enrollment,
            github_link="https://github.com/example/learner-project",
            volunteer_review_only=volunteer,
        )

    def test_registration_opens_the_page_and_real_evidence_closes_it(self):
        self.add_submission()

        response = self.client.get(self.url)
        body = response.content.decode()

        self.assertContains(response, self.family.prerequisites)
        self.assertContains(response, self.family.outcome)
        # The prerequisites now answer "is this a good fit" as their own
        # opening-group section, ahead of the journey, which in turn still
        # establishes fit and value before the page asks the visitor to
        # choose a route.
        self.assertLess(
            body.index(self.family.prerequisites),
            body.index('id="transformation-heading"'),
        )
        self.assertLess(
            body.index('id="transformation-heading"'),
            body.index('id="register-heading"'),
        )
        self.assertLess(
            body.index('id="register-heading"'),
            body.index("Explore all learner projects"),
        )
        self.assertContains(response, reverse("family_projects", args=[self.family.slug]))
        self.assertContains(response, reverse("registration_campaign", args=[self.campaign.slug]))
        self.assertNotContains(response, "What people build")
        self.assertNotContains(response, 'class="family-built-grid"')

    def test_transformation_uses_three_repository_authored_scenes(self):
        response = self.client.get(self.url)

        transformation = response.context["family_transformation"]
        self.assertEqual(transformation, self.family.progression)
        self.assertContains(response, 'class="card journey-card"', count=3)
        for step in self.family.progression:
            self.assertContains(response, step["heading"])
            self.assertContains(response, step["description"])
        section = self.journey_section(response)
        self.assertNotIn("Attempt 1", section)
        # The stages break out to the shell's full width, on the page's own
        # lavender ground: the cards are too narrow to read at the reading
        # column, and the section introduces no band of its own.
        self.assertContains(
            response, 'class="family-transformation shell-breakout"', count=1
        )

    def journey_section(self, response) -> str:
        """The rendered three-stage section, markup only."""

        body = response.content.decode()
        start = body.index('class="family-transformation shell-breakout"')
        return body[start : body.index("</section>", start)]

    def hero_section(self, response) -> str:
        """The rendered hero, markup only (up to the illustration)."""

        body = response.content.decode()
        start = body.index('class="family-hero-inner"')
        end = body.index('class="family-hero-art', start)
        return body[start:end]

    def test_transformation_uses_neutral_start_and_shared_pipeline_art(self):
        self.family.slug = "de-zoomcamp"
        self.family.save(update_fields=["slug"])

        response = self.client.get(reverse("course_family", args=[self.family.slug]))
        section = self.journey_section(response)

        self.assertContains(response, "course-journey-start.")
        self.assertContains(response, "course-journey-start-dark.")
        # The family's own scene is the hero's, drawn once: stage 2 used to
        # repeat it a few hundred pixels below, which read as a template
        # accident rather than a story.
        self.assertContains(response, "course-de-zoomcamp.", count=1)
        self.assertNotIn("course-de-zoomcamp.", section)
        self.assertIn("home-step-2.", section)
        self.assertIn("home-step-3.", section)
        self.assertContains(response, 'class="family-hero-art family-course-art"')

    def test_journey_cards_carry_no_proof_block(self):
        # Owner feedback (2026-09): the dashed-divider "proof" block that
        # used to close each card was too busy and mostly redundant with
        # sections the page already draws further down (syllabus, built);
        # the cards now end on their own description, the same clean shape
        # as the home page's climb cards.
        self.add_submission()

        response = self.client.get(self.url)
        section = self.journey_section(response)

        self.assertNotIn('class="journey-proof"', section)
        self.assertNotIn("Good fit if", section)
        self.assertNotIn(self.family.prerequisites, section)
        self.assertNotIn(response.context["syllabus_fact"], section)
        self.assertNotIn('href="#syllabus-heading"', section)
        self.assertNotIn("Peer-reviewed", section)
        self.assertNotIn('href="#built-heading"', section)
        # Every card ends on its own <p> description; nothing follows it.
        self.assertEqual(section.count("</p>\n              </article>"), 3)

    def test_prerequisites_and_weekly_commitment_are_their_own_opening_sections(self):
        self.family.weekly_commitment = "Free. Plan for about 5-15 hours a week."
        self.family.save(update_fields=["weekly_commitment"])

        response = self.client.get(self.url)
        body = response.content.decode()

        opening_group = body[
            body.index('class="family-opening-group"') : body.index(
                'id="transformation-heading"'
            )
        ]
        self.assertIn('id="fit-heading"', opening_group)
        self.assertIn("Good fit if", opening_group)
        self.assertIn(self.family.prerequisites, opening_group)
        self.assertIn('id="commitment-heading"', opening_group)
        self.assertIn("Time and cost", opening_group)
        self.assertIn(self.family.weekly_commitment, opening_group)
        # Not a journey-card fact any more, and not in the hero either.
        self.assertNotIn("Good fit if", self.journey_section(response))
        self.assertNotIn("Time and cost", self.journey_section(response))
        self.assertNotIn(self.family.weekly_commitment, self.hero_section(response))

    def test_unpublished_retired_and_other_course_skills_are_not_promoted(self):
        modules = list(self.curriculum.modules.order_by("position"))
        modules[0].published = False
        modules[0].save(update_fields=["published"])
        modules[1].retired_at = timezone.now()
        modules[1].save(update_fields=["retired_at"])
        other = Course.objects.create(slug="other", title="Other")
        other_curriculum = SharedCurriculum.objects.create(course=other)
        SharedModule.objects.create(
            curriculum=other_curriculum, position=0, slug="other-skill", title="Other skill"
        )

        response = self.client.get(self.url)

        self.assertNotContains(response, "Prepare the data")
        self.assertNotContains(response, "Train a model")
        self.assertNotContains(response, "Other skill")
        self.assertContains(response, "Evaluate predictions")

    def test_learner_proof_requires_a_real_public_nonvolunteer_submission(self):
        self.add_submission(volunteer=True)
        hidden = make_cohort(self.family, 2027, visible=False, project_count=1)
        self.add_submission(hidden)
        other = Course.objects.create(slug="other", title="Other")
        self.add_submission(make_cohort(other, 2026, project_count=1))

        response = self.client.get(self.url)

        self.assertNotContains(response, "Explore learner projects")
        self.assertNotContains(response, "Put it into practice")

    def test_project_brief_uses_the_stored_instructions_destination(self):
        self.project.instructions_url = "https://github.com/example/course/blob/main/project.md"
        self.project.save(update_fields=["instructions_url"])

        response = self.client.get(self.url)

        self.assertContains(response, self.project.instructions_url)
        self.assertContains(response, "Read the project brief")
        self.assertNotContains(response, "Explore learner projects")

    def test_empty_family_omits_unsupported_narrative_and_proof(self):
        family = Course.objects.create(slug="empty", title="Empty course")
        response = self.client.get(reverse("course_family", args=[family.slug]))

        self.assertEqual(response.status_code, 200)
        for marker in (
            "Your starting point",
            'id="register-heading"',
            'id="certificate-heading"',
            'id="stories-heading"',
            'id="transformation-heading"',
        ):
            with self.subTest(marker=marker):
                self.assertNotContains(response, marker)

    def test_authored_prerequisites_and_outcome_are_escaped(self):
        self.family.prerequisites = "<script>before()</script>"
        self.family.outcome = "<script>after()</script>"
        self.family.save(update_fields=["prerequisites", "outcome"])
        response = self.client.get(self.url)

        self.assertContains(response, "&lt;script&gt;before()&lt;/script&gt;")
        self.assertContains(response, "&lt;script&gt;after()&lt;/script&gt;")
        self.assertNotContains(response, "<script>before()")

    def test_course_description_preserves_learning_notes_and_escapes_markup(self):
        self.family.description = (
            "A practical course.\n\nThis course is educational; results are not guaranteed."
            "\n\n<script>unsafe()</script>"
        )
        self.family.save(update_fields=["description"])
        response = self.client.get(self.url)
        body = response.content.decode()

        # Owner feedback (2026-09): the learning notes used to sit behind a
        # collapsible mid-page, then briefly under the hero lede (too much
        # text stacked in the cream hero band above the CTAs); they now
        # render as plain prose of their own in the body, still not a
        # disclosure, and not inside the hero any more.
        self.assertNotContains(response, "<details class=\"family-overview\"")
        self.assertNotContains(response, "About this course and learning notes")
        self.assertContains(response, "This course is educational; results are not guaranteed.")
        self.assertNotContains(response, "<script>unsafe()")
        self.assertNotIn("This course is educational", self.hero_section(response))
        self.assertContains(response, 'class="family-overview-section"')
        overview_section = body[
            body.index('class="family-overview-section"') : body.index(
                "</section>", body.index('class="family-overview-section"')
            )
        ]
        self.assertIn("This course is educational", overview_section)
        # In the body, after the hero and the opening-group quick facts,
        # ahead of the journey.
        self.assertLess(
            body.index('class="family-hero-inner"'),
            body.index('class="family-overview-section"'),
        )
        self.assertLess(
            body.index('class="family-overview-section"'),
            body.index('id="transformation-heading"'),
        )

    def test_whitespace_prerequisites_do_not_leave_an_empty_label(self):
        self.family.prerequisites = " \n "
        self.family.starting_point = " \n "
        self.family.save(update_fields=["prerequisites", "starting_point"])
        response = self.client.get(self.url)

        self.assertNotContains(response, 'id="starting-point-heading"')
        self.assertNotContains(response, 'id="fit-heading"')
        self.assertNotContains(response, "Good fit if")

    def test_repository_prerequisites_and_starting_point_render_in_separate_sections(self):
        response = self.client.get(self.url)
        body = response.content.decode()

        self.assertContains(response, self.family.prerequisites)
        starting_point_section = body.split('class="family-starting-point"', 1)[1].split(
            "</section>", 1
        )[0]
        self.assertIn(self.family.starting_point, starting_point_section)
        self.assertNotIn(self.family.prerequisites, starting_point_section)

    def test_weekly_commitment_is_escaped_and_not_in_the_hero(self):
        self.family.weekly_commitment = "Free. <script>evil()</script> Plan for about 10 hours a week."
        self.family.save(update_fields=["weekly_commitment"])

        response = self.client.get(self.url)

        self.assertContains(response, "&lt;script&gt;evil()&lt;/script&gt;")
        self.assertNotContains(response, "<script>evil()")
        # The hero no longer carries the price/hours line at all -- it is
        # its own opening-group section now (see
        # test_prerequisites_and_weekly_commitment_are_their_own_opening_sections).
        self.assertNotIn("Free.", self.hero_section(response))
        self.assertNotIn("Time and cost", self.hero_section(response))

    def test_whitespace_weekly_commitment_does_not_leave_an_empty_line(self):
        self.family.weekly_commitment = " \n "
        self.family.save(update_fields=["weekly_commitment"])

        response = self.client.get(self.url)

        self.assertNotContains(response, 'id="commitment-heading"')
        self.assertNotContains(response, "Time and cost")

    def test_self_paced_route_omits_cohort_certificate_promise(self):
        self.cohort.delivery_mode = DeliveryMode.SELF_PACED
        self.cohort.save(update_fields=["delivery_mode"])
        self.campaign.delete()
        response = self.client.get(self.url)

        self.assertContains(response, "Open the self-paced course")
        self.assertContains(response, "no deadlines, grading, or certificate")
        self.assertNotContains(response, 'id="certificate-heading"')

    def test_current_live_cohort_has_conditional_certificate_explanation(self):
        response = self.client.get(self.url)

        self.assertContains(response, 'id="certificate-heading"')
        self.assertContains(response, "Certificate eligibility depends on the 2026")
        self.assertContains(response, "does not earn a certificate")

    def test_finished_cohort_does_not_promise_a_new_certificate(self):
        self.cohort.finished = True
        self.cohort.save(update_fields=["finished"])
        response = self.client.get(self.url)

        self.assertNotContains(response, 'id="certificate-heading"')

    def test_materials_only_shared_curriculum_family_links_to_the_platform(self):
        # This family's setUp already imported a SharedCurriculum, so once its
        # only campaign is gone the materials card should keep the visitor on
        # the platform (the first published module) instead of sending them
        # to the source repository -- even though ``github_repo_url`` is set.
        self.campaign.delete()
        response = self.client.get(self.url)

        self.assertContains(response, "Learn the curriculum on the platform")
        self.assertContains(
            response,
            reverse("shared_module", args=[self.family.slug, "skill-0"]),
        )
        self.assertNotContains(response, "Course materials on GitHub")
        self.assertNotContains(response, "Register interest")
        self.assertNotContains(response, "Self-paced · start now")

    def test_shared_syllabus_renders_source_summary_beneath_linked_title(self):
        module = self.curriculum.modules.order_by("position").first()
        assert module is not None

        response = self.client.get(self.url)

        self.assertContains(response, module.summary)
        self.assertContains(
            response,
            reverse("shared_module", args=[self.family.slug, module.slug]),
        )

    def test_materials_only_legacy_family_has_an_honest_github_action(self):
        # A family with no imported SharedCurriculum has no module pages to
        # send a visitor to, so the repository stays the only real materials
        # destination.
        family = Course.objects.create(
            slug="legacy-course",
            title="Legacy Course",
            github_repo_url="https://github.com/example/legacy-course",
        )
        make_cohort(family, 2025)

        response = self.client.get(reverse("course_family", args=[family.slug]))

        self.assertContains(response, "Course materials on GitHub")
        self.assertContains(response, 'href="https://github.com/example/legacy-course"')
        self.assertNotContains(response, "Register interest")
        self.assertNotContains(response, "Self-paced · start now")

    def test_legacy_homework_syllabus_has_summaries_and_real_destinations(self):
        family = Course.objects.create(
            slug="legacy-syllabus",
            title="Legacy Syllabus",
            github_repo_url="https://github.com/example/legacy-syllabus",
        )
        cohort = make_cohort(family, 2025)
        homework = Homework.objects.create(
            course=cohort,
            slug="first-homework",
            title="Homework 1: Inspect the data",
            description="Explore and validate the source dataset.",
            due_date=timezone.now(),
        )

        response = self.client.get(reverse("course_family", args=[family.slug]))

        self.assertContains(response, homework.description)
        self.assertContains(
            response,
            reverse(
                "cohort_homework",
                args=[family.slug, cohort.identifier, homework.slug],
            ),
        )

    def test_past_cohort_end_date_does_not_promise_a_new_certificate(self):
        self.cohort.end_date = timezone.localdate() - timedelta(days=1)
        self.cohort.save(update_fields=["end_date"])
        response = self.client.get(self.url)

        self.assertNotContains(response, 'id="certificate-heading"')

    def test_starting_point_is_editable_through_the_family_admin(self):
        family_admin = admin.site._registry[Course]
        assert family_admin.fields is not None
        self.assertIn("starting_point", family_admin.fields)
        self.assertIn("prerequisites", family_admin.fields)


class CourseFamilyOutcomeStatsTests(TestCase):
    """The honest, live-computed outcome-numbers strip (courses.course_page_content
    .family_outcome_stats): real counts across every visible cohort of the family,
    with the whole strip omitted when nobody is enrolled and the certificate stat
    alone omitted when that count is zero rather than shown as a misleading "0".
    """

    def setUp(self):
        self.family = Course.objects.create(slug="stats-course", title="Stats Course")
        self.cohort = make_cohort(self.family, 2021, project_count=1)
        self.url = reverse("course_family", args=[self.family.slug])
        self._enrollment_count = 0

    def enroll(self, *, cohort=None, certificate_url=""):
        cohort = cohort or self.cohort
        self._enrollment_count += 1
        user = User.objects.create_user(username=f"stats-learner-{self._enrollment_count}")
        return Enrollment.objects.create(
            student=user, course=cohort, certificate_url=certificate_url
        )

    def submit(self, enrollment):
        return ProjectSubmission.objects.create(
            project=enrollment.course.project_set.first(),
            student=enrollment.student,
            enrollment=enrollment,
            github_link=f"https://github.com/example/repo-{enrollment.pk}",
        )

    def test_strip_shows_live_signup_certificate_and_submission_counts(self):
        self.enroll()
        graduate_one = self.enroll(certificate_url="https://example.com/certificate-1.pdf")
        graduate_two = self.enroll(certificate_url="https://example.com/certificate-2.pdf")
        self.submit(graduate_one)
        self.submit(graduate_two)

        response = self.client.get(self.url)

        stats = {stat.label: stat.value for stat in response.context["family_outcome_stats"]}
        self.assertEqual(stats["cohort since 2021"], "1")
        self.assertEqual(stats["sign ups"], "3")
        self.assertEqual(stats["graduates"], "2")
        self.assertEqual(stats["projects"], "2")
        labels = [stat.label for stat in response.context["family_outcome_stats"]]
        self.assertEqual(
            labels, ["cohort since 2021", "sign ups", "projects", "graduates"]
        )
        self.assertContains(response, 'id="outcomes-heading"')
        self.assertContains(response, "cohort since 2021")
        self.assertContains(response, "sign ups")
        self.assertContains(response, "graduates")
        self.assertContains(response, "projects")
        self.assertNotContains(response, "enrolled since 2021")

    def test_published_campaign_count_leads_with_registrations_not_enrollments(self):
        self.enroll()
        RegistrationCampaign.objects.create(
            slug="stats-course",
            title=self.family.title,
            current_course=self.cohort,
            registration_baseline_cohort=self.cohort,
            registration_baseline_count=47,
        )

        response = self.client.get(self.url)

        stats = response.context["family_outcome_stats"]
        # The cohorts count leads the strip; the published registration total
        # follows it, still ahead of every other stat.
        self.assertEqual((stats[0].value, stats[0].label), ("1", "cohort since 2021"))
        self.assertEqual((stats[1].value, stats[1].label), ("47", "registrations"))
        self.assertNotContains(response, "enrolled since 2021")
        # Not the rendered tile text -- a page-local CSS comment names every
        # possible stat label generically, so the check is scoped to the
        # actual <span> the tile renders rather than a bare substring.
        self.assertNotContains(response, "<span>sign ups</span>")

    def test_published_single_registration_uses_singular_label(self):
        RegistrationCampaign.objects.create(
            slug="stats-course",
            title=self.family.title,
            current_course=self.cohort,
            registration_baseline_cohort=self.cohort,
            registration_baseline_count=1,
        )

        response = self.client.get(self.url)

        stats = response.context["family_outcome_stats"]
        self.assertEqual((stats[1].value, stats[1].label), ("1", "registration"))

    def test_closed_family_campaign_uses_attributable_historical_registrations(self):
        self.enroll()
        self.cohort.registration_url = "https://courses.datatalks.club/register/stats-course/"
        self.cohort.save(update_fields=["registration_url"])
        campaign = RegistrationCampaign.objects.create(
            slug="stats-course",
            title=self.family.title,
            current_course=None,
        )
        CourseRegistration.objects.create(
            campaign=campaign,
            course=self.cohort,
            email="historical@example.com",
        )

        response = self.client.get(self.url)

        stats = response.context["family_outcome_stats"]
        self.assertEqual((stats[1].value, stats[1].label), ("1", "registration"))
        self.assertNotContains(response, "enrolled since 2021")
        # Not the rendered tile text -- a page-local CSS comment names every
        # possible stat label generically, so the check is scoped to the
        # actual <span> the tile renders rather than a bare substring.
        self.assertNotContains(response, "<span>sign ups</span>")

    def test_a_family_with_nobody_enrolled_omits_the_whole_strip(self):
        empty_family = Course.objects.create(slug="no-one-yet", title="No One Yet")
        make_cohort(empty_family, 2026)

        response = self.client.get(reverse("course_family", args=[empty_family.slug]))

        self.assertEqual(response.context["family_outcome_stats"], ())
        self.assertNotContains(response, 'class="family-outcomes"')
        self.assertNotContains(response, 'id="outcomes-heading"')

    def test_a_family_with_no_certificates_omits_only_that_one_stat(self):
        self.enroll()
        submitter_one = self.enroll()
        submitter_two = self.enroll()
        self.submit(submitter_one)
        self.submit(submitter_two)

        response = self.client.get(self.url)

        labels = [stat.label for stat in response.context["family_outcome_stats"]]
        self.assertEqual(labels, ["cohort since 2021", "sign ups", "projects"])
        self.assertNotContains(response, "graduates")
        self.assertContains(response, "sign ups")

    def test_hidden_cohorts_are_not_counted(self):
        self.enroll()
        hidden = make_cohort(self.family, 2027, visible=False)
        self.enroll(cohort=hidden, certificate_url="https://example.com/certificate.pdf")

        response = self.client.get(self.url)

        stats = {stat.label: stat.value for stat in response.context["family_outcome_stats"]}
        self.assertEqual(stats["cohort since 2021"], "1")
        self.assertEqual(stats["sign ups"], "1")
        self.assertNotIn("graduates", stats)


class CourseFamilySyllabusMergeTests(TestCase):
    """The syllabus's real projects, wired end to end into one numbered list
    at their real chronological slot (issue: "for ml zoomcamp we have
    midterm project in the middle of the syllabus let's include it
    chronographically"), instead of appended after every module in a
    separate boxed "Project submissions" card.
    """

    def syllabus_section(self, response) -> str:
        body = response.content.decode()
        start = body.index('id="syllabus-heading"')
        return body[start : body.index("</section>", start)]

    def test_a_midterm_lands_between_the_homeworks_it_falls_between(self):
        family = Course.objects.create(slug="ml-zoomcamp-like", title="ML-Zoomcamp-Like")
        cohort = make_cohort(family, 2026, homework_count=4)
        RegistrationCampaign.objects.create(
            slug="ml-zoomcamp-like", title=family.title, current_course=cohort
        )
        homeworks = list(cohort.homework_set.order_by("due_date"))
        midterm_due = homeworks[1].due_date + timedelta(hours=12)
        midterm = cohort.project_set.create(
            slug="midterm",
            title="Midterm project",
            submission_due_date=midterm_due,
            peer_review_due_date=midterm_due + timedelta(days=7),
        )
        capstone_due = homeworks[-1].due_date + timedelta(days=14)
        capstone = cohort.project_set.create(
            slug="capstone",
            title="Capstone project",
            submission_due_date=capstone_due,
            peer_review_due_date=capstone_due + timedelta(days=7),
        )

        response = self.client.get(reverse("course_family", args=[family.slug]))
        section = self.syllabus_section(response)

        # One list, uniform rows -- no separate boxed "Project submissions"
        # card any more.
        self.assertNotContains(response, "Project submissions")
        self.assertNotContains(response, 'class="family-syllabus-projects"')
        rows = response.context["family_syllabus_rows"]
        titles = [row.title for row in rows]
        self.assertEqual(
            titles,
            ["Homework 1", "Homework 2", "Midterm project", "Homework 3", "Homework 4", "Capstone project"],
        )
        self.assertEqual(
            [row.is_project for row in rows], [False, False, True, False, False, True]
        )
        # The document order matches: the midterm's markup sits before
        # "Homework 3" and after "Homework 2".
        self.assertLess(
            section.index("Homework 2"),
            section.index("Midterm project"),
        )
        self.assertLess(
            section.index("Midterm project"),
            section.index("Homework 3"),
        )
        self.assertIn(
            reverse(
                "cohort_project",
                kwargs={
                    "course_slug": family.slug,
                    "cohort_identifier": cohort.identifier,
                    "project_slug": midterm.slug,
                },
            ),
            section,
        )
        self.assertIn(
            reverse(
                "cohort_project",
                kwargs={
                    "course_slug": family.slug,
                    "cohort_identifier": cohort.identifier,
                    "project_slug": capstone.slug,
                },
            ),
            section,
        )


class CourseFamilyProjectGalleryTests(TestCase):
    """The inline learner-work gallery: a handful of real project-submission rows
    (courses.views.project_gallery_groups.family_project_submissions), reusing the
    same card fields the family/site project galleries already show -- submitter,
    repository link, project + cohort tag -- and hidden entirely for a family with
    no real submissions.
    """

    def setUp(self):
        self.family = Course.objects.create(slug="gallery-course", title="Gallery Course")
        self.cohort = make_cohort(self.family, 2025, project_count=1)
        self.project = self.cohort.project_set.get()
        self.url = reverse("course_family", args=[self.family.slug])
        self._count = 0

    def add_submission(self, *, cohort=None, project=None, volunteer=False):
        cohort = cohort or self.cohort
        project = project or cohort.project_set.first()
        self._count += 1
        user = User.objects.create_user(username=f"gallery-learner-{self._count}")
        enrollment = Enrollment.objects.create(student=user, course=cohort)
        return ProjectSubmission.objects.create(
            project=project,
            student=user,
            enrollment=enrollment,
            github_link=f"https://github.com/example/gallery-repo-{self._count}",
            volunteer_review_only=volunteer,
        )

    def test_inline_gallery_shows_real_submitter_repository_and_project_cohort_tag(self):
        submission = self.add_submission()

        response = self.client.get(self.url)

        self.assertContains(response, 'class="row-list family-proof-gallery"')
        self.assertContains(response, 'class="list-row submission-row"')
        self.assertContains(response, submission.enrollment.display_name)
        self.assertContains(response, submission.github_link)
        self.assertContains(response, "github.com/example/gallery-repo-1")
        self.assertContains(response, f"· {self.cohort.identifier} cohort")
        self.assertContains(
            response,
            reverse(
                "cohort_leaderboard_score_breakdown",
                kwargs={
                    "course_slug": self.family.slug,
                    "cohort_identifier": self.cohort.identifier,
                    "enrollment_id": submission.enrollment.id,
                },
            ),
        )

    def test_a_family_with_no_real_submissions_hides_the_gallery_gracefully(self):
        self.add_submission(volunteer=True)

        response = self.client.get(self.url)

        self.assertEqual(list(response.context["family_gallery_submissions"]), [])
        self.assertNotContains(response, 'class="row-list family-proof-gallery"')
        self.assertNotContains(response, 'class="list-row submission-row"')

    def test_inline_gallery_caps_at_six_even_with_more_real_submissions(self):
        for _ in range(9):
            self.add_submission()

        response = self.client.get(self.url)

        self.assertEqual(len(response.context["family_gallery_submissions"]), 6)
        self.assertEqual(response.content.decode().count('class="list-row submission-row"'), 6)


class CourseFamilyFaqPreviewTests(TestCase):
    """The family landing page's real-FAQ preview (issue: real questions, not a bare link)."""

    def test_family_with_no_faq_document_and_no_legacy_link_shows_no_panel(self):
        family = Course.objects.create(slug="no-faq-family", title="No FAQ Family")

        response = self.client.get(reverse("course_family", args=[family.slug]))

        self.assertNotContains(response, "Questions before you start?")

    def test_family_with_no_matching_document_falls_back_to_the_legacy_link(self):
        family = Course.objects.create(
            slug="legacy-faq-family",
            title="Legacy FAQ Family",
            faq_document_url="https://example.invalid/legacy-course-faq",
        )

        response = self.client.get(reverse("course_family", args=[family.slug]))

        self.assertContains(response, "Questions before you start?")
        self.assertContains(response, 'href="https://example.invalid/legacy-course-faq"')
        self.assertNotContains(response, '<details class="faq-fold"')

    def test_family_matching_a_real_faq_document_shows_real_questions_inline(self):
        from content.faq_data import faq_course, faq_questions, render_faq_answer

        # "ml-zoomcamp" is the family slug; its real FAQ document is published
        # under the FAQ's own slug, "machine-learning-zoomcamp" (content.faq_data
        # .FAQ_COURSE_SLUG_BY_FAMILY_SLUG) -- exercising the alias, not just a
        # family whose slug happens to match its FAQ document directly.
        family = Course.objects.create(slug="ml-zoomcamp", title="Machine Learning Zoomcamp")
        document = faq_course("machine-learning-zoomcamp")
        self.assertIsNotNone(document)
        assert document is not None
        first_question = faq_questions(document)[0]

        response = self.client.get(reverse("course_family", args=[family.slug]))
        body = response.content.decode()

        self.assertContains(response, "Questions before you start?")
        self.assertContains(response, first_question["question"])
        self.assertIn(render_faq_answer(first_question), body)
        self.assertContains(response, 'href="/faq/machine-learning-zoomcamp.html"')
        self.assertContains(response, "See the full course FAQ")
        # The preview is bounded, not the whole document, however many
        # questions the real FAQ carries.
        self.assertLessEqual(body.count('<details class="faq-fold"'), 5)

    def test_quick_faq_answers_fill_a_real_course_with_no_synced_document(self):
        # A real course (has a visible cohort) whose FAQ hasn't been synced and
        # carries no legacy link either used to show no FAQ panel at all --
        # exactly today's ml-zoomcamp gap. The catalogue's own objection
        # answers (free, hours, late join, certificate) now fill it.
        family = Course.objects.create(slug="unsynced-course", title="Unsynced Course")
        make_cohort(family, 2026)

        response = self.client.get(reverse("course_family", args=[family.slug]))

        self.assertContains(response, "Questions before you start?")
        self.assertContains(response, "Is it really free?")
        self.assertContains(response, "Can I join after a cohort starts?")
        self.assertContains(response, "How much time do I need?")
        self.assertContains(response, "Do I get a certificate?")
        # No document and no legacy link means nothing real to send them to.
        self.assertNotContains(response, "See the full course FAQ")

    def test_quick_faq_answers_are_omitted_for_a_family_with_no_real_cohort(self):
        # Guards the empty-family case above: the fallback is for a real
        # course, not a stub family record with nothing running.
        family = Course.objects.create(slug="stub-family", title="Stub Family")

        response = self.client.get(reverse("course_family", args=[family.slug]))

        self.assertNotContains(response, "Questions before you start?")
        self.assertNotContains(response, "Is it really free?")

    def test_quick_faq_answers_link_to_the_legacy_faq_when_one_exists(self):
        family = Course.objects.create(
            slug="unsynced-with-link",
            title="Unsynced With Link",
            faq_document_url="https://example.invalid/unsynced-with-link",
        )
        make_cohort(family, 2026)

        response = self.client.get(reverse("course_family", args=[family.slug]))

        self.assertContains(response, "Is it really free?")
        self.assertContains(response, 'href="https://example.invalid/unsynced-with-link"')
        self.assertContains(response, "See the full course FAQ")

    def test_faq_closes_the_page_as_its_own_unboxed_section(self):
        # Owner feedback (2026-09): the FAQ used to sit boxed mid-page,
        # inside the editions section; it is now the last content section,
        # framed like the /courses catalogue's own FAQ band (band-head plus
        # heading), not a bordered panel.
        family = Course.objects.create(slug="ml-zoomcamp", title="Machine Learning Zoomcamp")
        cohort = make_cohort(family, 2026, project_count=1)
        user = User.objects.create_user(username="faq-order-learner")
        enrollment = Enrollment.objects.create(student=user, course=cohort)
        ProjectSubmission.objects.create(
            project=cohort.project_set.first(),
            student=user,
            enrollment=enrollment,
            github_link="https://github.com/example/learner-project",
        )

        response = self.client.get(reverse("course_family", args=[family.slug]))
        body = response.content.decode()

        self.assertContains(response, 'class="family-faq"')
        self.assertNotContains(response, 'class="panel panel-lavender panel-outlined family-faq"')
        self.assertContains(response, 'id="faq-heading"')
        # Last content section: after both the editions strip and "See what
        # people have built", not sandwiched inside the editions section any
        # more.
        self.assertLess(
            body.index('id="editions-heading"'),
            body.index('id="faq-heading"'),
        )
        self.assertLess(
            body.index('id="built-heading"'),
            body.index('id="faq-heading"'),
        )

    def test_quick_faq_answers_do_not_repeat_a_question_the_real_document_already_asks(self):
        family = Course.objects.create(slug="ml-zoomcamp", title="Machine Learning Zoomcamp")
        make_cohort(family, 2026)

        response = self.client.get(reverse("course_family", args=[family.slug]))
        body = response.content.decode()

        # The real synced document's first five questions (general orientation)
        # don't mention a certificate, so the quick answer still appears...
        self.assertIn("Do I get a certificate?", body)
        # ...but none of the quick items duplicate a question the real preview
        # already shows.
        from content.faq_data import faq_course, faq_questions

        document = faq_course("machine-learning-zoomcamp")
        assert document is not None
        previewed = {question["question"] for question in faq_questions(document)[:5]}
        quick_questions = {
            item.question for item in response.context["family_quick_faq_items"]
        }
        self.assertEqual(previewed & quick_questions, set())
