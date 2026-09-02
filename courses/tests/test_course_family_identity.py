"""One course, one family row.

``DataTalksClub/ai-dev-tools-zoomcamp`` declares ``slug: ai-dev-tools-zoomcamp`` in
its ``course.yaml`` while the course is published as ``ai-dev-tools``.  Projecting
that repository slug verbatim minted a second family row, orphaned the 2026 cohort
under it, and made ``/courses`` list seven families for six courses.  These are the
tests that would have caught it.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from uuid import UUID

from django.test import TestCase
from django.urls import reverse

from content_sync.course_repository import (
    CourseRepositorySource,
    ProjectFlowSource,
    parse_course_repository,
)
from courses.course_family_catalog import (
    COHORT_FAMILY_IDENTITIES,
    COURSE_FAMILY_TITLES,
    canonical_family_slug,
    duplicate_family_identities,
    family_identity,
    family_slug_variants,
)
from courses.migration_family_identity import (
    CourseFamilyMergeError,
    merge_duplicate_course_families,
)
from courses.models import Cohort, Course
from courses.services.curriculum_import import (
    CurriculumImportCommand,
    CurriculumImportError,
    import_course_repository_curriculum,
)

FIXTURE_ROOT = (
    Path(__file__).parents[2]
    / "content_sync"
    / "tests"
    / "fixtures"
    / "course_repository"
    / "llm_zoomcamp_2026"
)
COMMIT = "c" * 40
SOURCE_UUID = UUID("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb")
REPOSITORY_SLUG = "ai-dev-tools-zoomcamp"
PUBLISHED_SLUG = "ai-dev-tools"


def repository_named_source() -> CourseRepositorySource:
    """Return a source graph that names itself after its GitHub repository.

    This is exactly the shape of the AI Dev Tools repository: the course slug and
    every cohort ``legacy_slug`` carry the ``-zoomcamp`` repository suffix that the
    published catalogue does not use.
    """

    snapshot = {
        path.relative_to(FIXTURE_ROOT).as_posix(): path.read_bytes()
        for path in FIXTURE_ROOT.rglob("*")
        if path.is_file()
    }
    source = parse_course_repository(snapshot, commit_sha=COMMIT)
    course = replace(
        source.course,
        slug=REPOSITORY_SLUG,
        title="AI Dev Tools Zoomcamp",
        repository_url=f"https://github.com/DataTalksClub/{REPOSITORY_SLUG}",
    )
    cohorts = tuple(
        replace(
            cohort,
            legacy_slug=(
                f"{REPOSITORY_SLUG}-{cohort.identifier}"
                if cohort.legacy_slug
                else cohort.legacy_slug
            ),
            # Projects are not created by the importer; keep this fixture to the
            # module flow so the test measures family identity only.
            flow=tuple(
                item for item in cohort.flow if not isinstance(item, ProjectFlowSource)
            ),
        )
        for cohort in source.cohorts
    )
    return replace(source, course=course, cohorts=cohorts)


def import_command(source: CourseRepositorySource) -> CurriculumImportCommand:
    return CurriculumImportCommand(
        source=source,
        source_uuid=SOURCE_UUID,
        source_stable_id=REPOSITORY_SLUG,
        repository_owner="DataTalksClub",
        repository_name=REPOSITORY_SLUG,
        repository_branch="main",
        commit_sha=COMMIT,
    )


class ReviewedCatalogIdentityTests(TestCase):
    """The reviewed catalogue itself must never describe one course twice."""

    def test_no_two_reviewed_families_share_an_identity(self):
        self.assertEqual(duplicate_family_identities(COURSE_FAMILY_TITLES), {})

    def test_every_mapped_cohort_points_at_a_reviewed_family(self):
        for cohort_slug, (family_slug, _year) in COHORT_FAMILY_IDENTITIES.items():
            with self.subTest(cohort=cohort_slug):
                self.assertIn(family_slug, COURSE_FAMILY_TITLES)
                self.assertEqual(canonical_family_slug(family_slug), family_slug)

    def test_repository_slug_resolves_to_the_published_family(self):
        self.assertEqual(canonical_family_slug(REPOSITORY_SLUG), PUBLISHED_SLUG)

    def test_no_reviewed_family_is_a_repository_suffixed_twin(self):
        """The loser slug is merged away, not aliased beside the winner.

        The rule cannot be a blanket ban on ``-zoomcamp``: five families really are
        published as ``de-zoomcamp``, ``ml-zoomcamp``, ``llm-zoomcamp``,
        ``mlops-zoomcamp`` and ``sma-zoomcamp``, and ``courses.datatalks.club``
        serves those slugs.  What must never exist is a ``…-zoomcamp`` family whose
        de-suffixed form is already a family — that is one course listed twice.
        """

        self.assertNotIn(REPOSITORY_SLUG, COURSE_FAMILY_TITLES)
        for family_slug in COURSE_FAMILY_TITLES:
            with self.subTest(family=family_slug):
                self.assertNotIn(
                    family_identity(family_slug),
                    COURSE_FAMILY_TITLES.keys() - {family_slug},
                )

    def test_published_zoomcamp_families_keep_their_slug(self):
        for family_slug in ("de-zoomcamp", "ml-zoomcamp", "llm-zoomcamp", "mlops-zoomcamp"):
            with self.subTest(family=family_slug):
                self.assertEqual(canonical_family_slug(family_slug), family_slug)

    def test_unknown_slug_is_left_alone(self):
        self.assertEqual(canonical_family_slug("fake-course"), "fake-course")
        self.assertEqual(canonical_family_slug("fake-course-zoomcamp"), "fake-course-zoomcamp")

    def test_variants_cover_both_spellings_of_one_identity(self):
        self.assertEqual(
            family_slug_variants(REPOSITORY_SLUG),
            (PUBLISHED_SLUG, REPOSITORY_SLUG),
        )
        self.assertEqual(family_slug_variants(PUBLISHED_SLUG), (PUBLISHED_SLUG, REPOSITORY_SLUG))
        self.assertEqual(family_identity("ml-zoomcamp"), "ml")


class CurriculumImportFamilyIdentityTests(TestCase):
    """The importer publishes the family slug, not the repository slug."""

    def published_family(self) -> Course:
        return Course.objects.create(
            slug=PUBLISHED_SLUG,
            title=COURSE_FAMILY_TITLES[PUBLISHED_SLUG],
        )

    def test_import_adopts_the_published_family(self):
        family = self.published_family()
        Cohort.objects.create(
            course=family,
            slug="ai-dev-tools-2025",
            identifier="2025",
            year=2025,
            title="AI Dev Tools Zoomcamp 2025",
            description="The 2025 cohort.",
        )

        result = import_course_repository_curriculum(import_command(repository_named_source()))

        self.assertEqual(result.course.pk, family.pk)
        self.assertEqual(list(Course.objects.values_list("slug", flat=True)), [PUBLISHED_SLUG])
        self.assertEqual(
            sorted(Cohort.objects.values_list("slug", flat=True)),
            ["ai-dev-tools-2025", "ai-dev-tools-2026"],
        )
        self.assertEqual(
            set(Cohort.objects.values_list("course__slug", flat=True)),
            {PUBLISHED_SLUG},
        )

    def test_import_without_an_existing_family_still_publishes_the_family_slug(self):
        import_course_repository_curriculum(import_command(repository_named_source()))

        self.assertEqual(list(Course.objects.values_list("slug", flat=True)), [PUBLISHED_SLUG])
        self.assertNotIn(
            REPOSITORY_SLUG,
            set(Cohort.objects.values_list("slug", flat=True)),
        )

    def test_import_refuses_when_a_duplicate_family_already_exists(self):
        self.published_family()
        Course.objects.create(slug=REPOSITORY_SLUG, title="AI Dev Tools Zoomcamp")

        with self.assertRaises(CurriculumImportError) as caught:
            import_course_repository_curriculum(import_command(repository_named_source()))

        self.assertEqual(caught.exception.code, "course_family_identity_conflict")
        self.assertEqual(Course.objects.filter(slug=REPOSITORY_SLUG).count(), 1)

    def test_cohort_save_fallback_does_not_mint_a_repository_family(self):
        self.published_family()

        cohort = Cohort.objects.create(
            slug="ai-dev-tools-zoomcamp-2026",
            title="AI Dev Tools Zoomcamp 2026",
            description="The 2026 cohort.",
        )

        self.assertEqual(cohort.course.slug, PUBLISHED_SLUG)
        self.assertEqual(Course.objects.count(), 1)


class CourseFamilyMergeTests(TestCase):
    """An existing database converges on the published family."""

    def setUp(self):
        self.published = Course.objects.create(
            slug=PUBLISHED_SLUG,
            title=COURSE_FAMILY_TITLES[PUBLISHED_SLUG],
        )
        self.imported = Course.objects.create(
            slug=REPOSITORY_SLUG,
            title="AI Dev Tools Zoomcamp",
            description="A hands-on course on AI-native software engineering.",
            outcome="Ship an AI-assisted application.",
            source_stable_id=REPOSITORY_SLUG,
            source_content_id=UUID("21000000-0000-4000-8000-000000000000"),
            source_path="course.yaml",
            source_commit_sha=COMMIT,
            source_checksum="d" * 64,
        )
        self.legacy = Cohort.objects.create(
            course=self.published,
            slug="ai-dev-tools-2025",
            identifier="2025",
            year=2025,
            title="AI Dev Tools Zoomcamp 2025",
            description="The 2025 cohort.",
        )
        self.orphan = Cohort.objects.create(
            course=self.imported,
            slug="ai-dev-tools-zoomcamp-2026",
            identifier="2026",
            year=2026,
            title="AI Dev Tools Zoomcamp 2026",
            description="The 2026 cohort.",
            curriculum_format=Cohort.CurriculumFormat.MODULES,
        )

    def test_merge_moves_the_orphan_cohort_and_removes_the_duplicate(self):
        merged = merge_duplicate_course_families(Course, Cohort)

        self.assertEqual([item.canonical_slug for item in merged], [PUBLISHED_SLUG])
        self.assertEqual(merged[0].removed_slugs, (REPOSITORY_SLUG,))
        self.assertEqual(list(Course.objects.values_list("slug", flat=True)), [PUBLISHED_SLUG])

        self.orphan.refresh_from_db()
        self.assertEqual(self.orphan.course_id, self.published.pk)
        self.assertEqual(self.orphan.slug, "ai-dev-tools-2026")
        self.assertEqual(self.orphan.curriculum_format, Cohort.CurriculumFormat.MODULES)
        self.legacy.refresh_from_db()
        self.assertEqual(self.legacy.course_id, self.published.pk)

    def test_merge_keeps_the_imported_source_identity(self):
        merge_duplicate_course_families(Course, Cohort)

        self.published.refresh_from_db()
        self.assertEqual(self.published.source_stable_id, REPOSITORY_SLUG)
        self.assertEqual(self.published.source_path, "course.yaml")
        self.assertEqual(self.published.outcome, "Ship an AI-assisted application.")

    def test_merge_is_idempotent(self):
        merge_duplicate_course_families(Course, Cohort)

        self.assertEqual(merge_duplicate_course_families(Course, Cohort), ())
        self.assertEqual(Course.objects.count(), 1)
        self.assertEqual(Cohort.objects.count(), 2)

    def test_merge_refuses_when_both_families_own_the_same_cohort(self):
        Cohort.objects.create(
            course=self.published,
            slug="ai-dev-tools-2026",
            identifier="2026",
            year=2026,
            title="AI Dev Tools Zoomcamp 2026",
            description="A conflicting 2026 cohort.",
        )

        with self.assertRaises(CourseFamilyMergeError):
            merge_duplicate_course_families(Course, Cohort)

    def test_merge_leaves_a_healthy_catalogue_alone(self):
        self.imported.delete()

        self.assertEqual(merge_duplicate_course_families(Course, Cohort), ())
        self.assertEqual(list(Course.objects.values_list("slug", flat=True)), [PUBLISHED_SLUG])


class CourseCatalogueUniquenessTests(TestCase):
    """``/courses`` lists exactly one entry per real course."""

    def setUp(self):
        for family_slug, title in COURSE_FAMILY_TITLES.items():
            family = Course.objects.create(slug=family_slug, title=title)
            Cohort.objects.create(
                course=family,
                slug=f"{family_slug}-2026",
                identifier="2026",
                year=2026,
                title=f"{title} 2026",
                description=f"The 2026 cohort of {title}.",
            )

    def test_every_cohort_resolves_to_exactly_one_family(self):
        families = list(Course.objects.values_list("slug", flat=True))
        self.assertEqual(duplicate_family_identities(families), {})
        for cohort in Cohort.objects.select_related("course"):
            with self.subTest(cohort=cohort.slug):
                siblings = [
                    slug
                    for slug in families
                    if family_identity(slug) == family_identity(cohort.course.slug)
                ]
                self.assertEqual(siblings, [cohort.course.slug])

    def test_course_list_lists_one_card_per_course(self):
        response = self.client.get(reverse("course_list"))

        self.assertEqual(response.status_code, 200)
        card_slugs = sorted(card.family.slug for card in response.context["course_family_cards"])
        self.assertEqual(card_slugs, sorted(COURSE_FAMILY_TITLES))

    def test_course_list_lists_one_card_per_course_after_a_merge(self):
        duplicate = Course.objects.create(slug=REPOSITORY_SLUG, title="AI Dev Tools Zoomcamp")
        Cohort.objects.create(
            course=duplicate,
            slug="ai-dev-tools-zoomcamp-2027",
            identifier="2027",
            year=2027,
            title="AI Dev Tools Zoomcamp 2027",
            description="The 2027 cohort.",
        )
        self.assertEqual(
            len(self.client.get(reverse("course_list")).context["course_family_cards"]),
            len(COURSE_FAMILY_TITLES) + 1,
        )

        merge_duplicate_course_families(Course, Cohort)

        response = self.client.get(reverse("course_list"))
        card_slugs = sorted(card.family.slug for card in response.context["course_family_cards"])
        self.assertEqual(card_slugs, sorted(COURSE_FAMILY_TITLES))
        published = Cohort.objects.filter(course__slug=PUBLISHED_SLUG)
        self.assertEqual(
            sorted(published.values_list("slug", flat=True)),
            ["ai-dev-tools-2026", "ai-dev-tools-2027"],
        )
