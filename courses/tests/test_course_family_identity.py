"""Course family identity is derived, never looked up in a curated table.

A course repository's ``course.yaml`` declares its own family slug directly --
that slug matches the repository's own name, the same as every course family
(``de-zoomcamp``, ``ml-zoomcamp``, ``llm-zoomcamp``, ``mlops-zoomcamp``).  The
importer projects that slug as-is, with one reviewed exception:
``ai-dev-tools-zoomcamp`` is normalized to the site's canonical
``ai-dev-tools`` on every sync (``FAMILY_SLUG_OVERRIDES`` in
``curriculum_import.py``), so its tests below check the published slug rather
than the raw repository one.  ``Cohort.save()``'s convenience fallback derives
a family slug/title mechanically from the cohort's own slug/title and knows
nothing about that override.  These are the tests for that mechanical
behaviour, plus the identity protection ``curriculum_import.py`` still owns:
two different sources may never claim the same family slug.
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
from courses.models import Cohort, Course
from courses.services.course_family_identity import (
    UnparseableEditionSlug,
    family_and_year_from_edition_slug,
    family_title_from_edition_title,
)
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
# The raw slug the repository's own course.yaml declares (its repository name).
FAMILY_SLUG = "ai-dev-tools-zoomcamp"
# The site's canonical published family slug, after FAMILY_SLUG_OVERRIDES.
CANONICAL_FAMILY_SLUG = "ai-dev-tools"


def repository_named_source() -> CourseRepositorySource:
    """Return a source graph named after its own GitHub repository.

    This is the real shape of the AI Dev Tools repository: the course slug and every
    cohort ``legacy_slug`` carry the repository's own ``-zoomcamp`` suffix, which is
    also the published family slug -- there is no separate "published" spelling to
    normalize against any more.
    """

    snapshot = {
        path.relative_to(FIXTURE_ROOT).as_posix(): path.read_bytes()
        for path in FIXTURE_ROOT.rglob("*")
        if path.is_file()
    }
    source = parse_course_repository(snapshot, commit_sha=COMMIT)
    course = replace(
        source.course,
        slug=FAMILY_SLUG,
        title="AI Dev Tools Zoomcamp",
        repository_url=f"https://github.com/DataTalksClub/{FAMILY_SLUG}",
    )
    cohorts = tuple(
        replace(
            cohort,
            legacy_slug=(
                f"{FAMILY_SLUG}-{cohort.identifier}" if cohort.legacy_slug else cohort.legacy_slug
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
        source_stable_id=FAMILY_SLUG,
        repository_owner="DataTalksClub",
        repository_name=FAMILY_SLUG,
        repository_branch="main",
        commit_sha=COMMIT,
    )


class CourseFamilyIdentityFunctionsTests(TestCase):
    """The pure derivation helpers ``cmp_content_import.py`` and friends rely on."""

    def test_splits_a_family_and_year_from_an_edition_slug(self):
        self.assertEqual(
            family_and_year_from_edition_slug("de-zoomcamp-2022"), ("de-zoomcamp", 2022)
        )
        self.assertEqual(
            family_and_year_from_edition_slug(f"{FAMILY_SLUG}-2026"), (FAMILY_SLUG, 2026)
        )

    def test_refuses_a_slug_without_a_trailing_year(self):
        with self.assertRaises(UnparseableEditionSlug):
            family_and_year_from_edition_slug("ai-buildcamp-2")

    def test_strips_a_trailing_year_from_a_title(self):
        self.assertEqual(
            family_title_from_edition_title("AI Dev Tools Zoomcamp 2026"),
            "AI Dev Tools Zoomcamp",
        )

    def test_title_without_a_trailing_year_is_left_alone(self):
        self.assertEqual(
            family_title_from_edition_title("AI Dev Tools Zoomcamp"), "AI Dev Tools Zoomcamp"
        )


class CurriculumImportFamilyIdentityTests(TestCase):
    """The importer publishes the repository's own family slug, never a lookup --
    except for the one reviewed ``FAMILY_SLUG_OVERRIDES`` correction below."""

    def test_import_without_an_existing_family_creates_it_under_the_canonical_slug(self):
        result = import_course_repository_curriculum(import_command(repository_named_source()))

        self.assertEqual(result.course.slug, CANONICAL_FAMILY_SLUG)
        self.assertEqual(
            list(Course.objects.values_list("slug", flat=True)), [CANONICAL_FAMILY_SLUG]
        )
        self.assertEqual(
            set(Cohort.objects.values_list("course__slug", flat=True)), {CANONICAL_FAMILY_SLUG}
        )

    def test_import_reuses_an_existing_family_with_the_same_slug(self):
        family = Course.objects.create(slug=CANONICAL_FAMILY_SLUG, title="AI Dev Tools")

        result = import_course_repository_curriculum(import_command(repository_named_source()))

        self.assertEqual(result.course.pk, family.pk)
        self.assertEqual(Course.objects.count(), 1)

    def test_import_refuses_a_slug_already_owned_by_a_different_source(self):
        Course.objects.create(
            slug=CANONICAL_FAMILY_SLUG,
            title="AI Dev Tools",
            source_stable_id="some-other-repository",
            source_content_id=UUID("21000000-0000-4000-8000-000000000000"),
            source_path="course.yaml",
            source_commit_sha=COMMIT,
            source_checksum="d" * 64,
        )

        with self.assertRaises(CurriculumImportError) as caught:
            import_course_repository_curriculum(import_command(repository_named_source()))

        self.assertEqual(caught.exception.code, "course_slug_collision")
        self.assertEqual(Course.objects.filter(slug=CANONICAL_FAMILY_SLUG).count(), 1)


class CohortSaveFamilyFallbackTests(TestCase):
    """A cohort created without a family derives one mechanically, from itself."""

    def test_derives_the_family_slug_and_title_from_the_cohort_itself(self):
        cohort = Cohort.objects.create(
            slug=f"{FAMILY_SLUG}-2026",
            title="AI Dev Tools Zoomcamp 2026",
            description="The 2026 cohort.",
        )

        self.assertEqual(cohort.course.slug, FAMILY_SLUG)
        self.assertEqual(cohort.course.title, "AI Dev Tools Zoomcamp")
        self.assertEqual(Course.objects.count(), 1)

    def test_a_second_cohort_reuses_the_family_the_first_one_created(self):
        Cohort.objects.create(
            slug=f"{FAMILY_SLUG}-2026",
            title="AI Dev Tools Zoomcamp 2026",
            description="The 2026 cohort.",
        )

        second = Cohort.objects.create(
            slug=f"{FAMILY_SLUG}-2027",
            title="AI Dev Tools Zoomcamp 2027",
            description="The 2027 cohort.",
        )

        self.assertEqual(Course.objects.count(), 1)
        self.assertEqual(second.course.slug, FAMILY_SLUG)

    def test_a_title_without_a_trailing_year_falls_back_to_the_slug(self):
        cohort = Cohort.objects.create(
            slug="de-zoomcamp-2026",
            title="",
            description="The 2026 cohort.",
        )

        self.assertEqual(cohort.course.slug, "de-zoomcamp")
        self.assertEqual(cohort.course.title, "De Zoomcamp")


class CourseCatalogueUniquenessTests(TestCase):
    """``/courses`` lists exactly one card per real family row."""

    FAMILIES = {
        "de-zoomcamp": "Data Engineering Zoomcamp",
        "ml-zoomcamp": "Machine Learning Zoomcamp",
        "llm-zoomcamp": "LLM Zoomcamp",
        "mlops-zoomcamp": "MLOps Zoomcamp",
        "sma-zoomcamp": "Stock Markets Analytics Zoomcamp",
        FAMILY_SLUG: "AI Dev Tools Zoomcamp",
    }

    def setUp(self):
        for family_slug, title in self.FAMILIES.items():
            family = Course.objects.create(slug=family_slug, title=title)
            Cohort.objects.create(
                course=family,
                slug=f"{family_slug}-2026",
                identifier="2026",
                year=2026,
                title=f"{title} 2026",
                description=f"The 2026 cohort of {title}.",
            )

    def test_course_list_lists_one_card_per_family(self):
        response = self.client.get(reverse("course_list"))

        self.assertEqual(response.status_code, 200)
        card_slugs = sorted(card.family.slug for card in response.context["course_family_cards"])
        self.assertEqual(card_slugs, sorted(self.FAMILIES))
