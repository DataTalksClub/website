import uuid
from datetime import timedelta

from django.test import TestCase
from django.utils import timezone

from courses.models import Cohort, Course, CurriculumFormat, Homework, Module, Unit
from courses.services.unit_assets import rewrite_unit_image_sources, unit_repository

RAW_BASE = "https://raw.githubusercontent.com/DataTalksClub/machine-learning-zoomcamp/main"


class UnitAssetResolutionTests(TestCase):
    def setUp(self):
        self.course = Course.objects.create(
            slug="ml-zoomcamp",
            title="Machine Learning Zoomcamp",
            github_repo_url="https://github.com/DataTalksClub/machine-learning-zoomcamp",
        )
        self.cohort = Cohort.objects.create(
            course=self.course,
            slug="ml-zoomcamp-2026",
            identifier="2026",
            year=2026,
            title="Machine Learning Zoomcamp 2026",
            description="A module-format cohort.",
            curriculum_format=CurriculumFormat.MODULES,
        )
        homework = Homework.objects.create(
            course=self.cohort,
            slug="intro-homework",
            title="Introduction Homework",
            due_date=timezone.now() + timedelta(days=7),
        )
        self.module = Module.objects.create(
            cohort=self.cohort,
            position=1,
            slug="01-intro",
            title="Introduction to Machine Learning",
            terminal_homework=homework,
        )
        self.unit = Unit.objects.create(
            module=self.module,
            position=1,
            slug="01-what-is-ml",
            title="1.1 Introduction to Machine Learning",
            source_content_id=uuid.uuid4(),
            source_path="01-intro/01-what-is-ml.md",
            source_commit_sha="a" * 40,
            source_checksum="b" * 64,
        )

    def rewrite(self, markdown):
        return rewrite_unit_image_sources(markdown, self.unit)

    def test_repository_falls_back_to_the_course_family_and_the_main_branch(self):
        repository = unit_repository(self.unit)

        assert repository is not None
        self.assertEqual(repository.repository_path, "DataTalksClub/machine-learning-zoomcamp")
        self.assertEqual(repository.branch, "main")
        self.assertEqual(repository.source_directory, "01-intro")
        self.assertTrue(repository.is_github)

    def test_sibling_directory_image_resolves_against_the_unit_source_path(self):
        self.assertEqual(
            self.rewrite('<img src="images/thumbnail-1-01.jpg">'),
            f'<img src="{RAW_BASE}/01-intro/images/thumbnail-1-01.jpg"'
            ' alt="1.1 Introduction to Machine Learning">',
        )

    def test_percent_encoded_and_parent_relative_paths_resolve_to_real_files(self):
        self.assertIn(
            f"{RAW_BASE}/01-intro/images/TPR_FPR.png",
            self.rewrite("![Rates](images%2FTPR_FPR.png)"),
        )
        self.assertIn(
            f"{RAW_BASE}/02-regression/images/plot.png",
            self.rewrite("![Plot](../02-regression/images/plot.png)"),
        )

    def test_repository_root_relative_paths_resolve_from_the_repository_root(self):
        self.assertIn(
            f"{RAW_BASE}/images/banner.png",
            self.rewrite("![Banner](/images/banner.png)"),
        )

    def test_references_outside_the_repository_are_dropped(self):
        self.assertEqual(self.rewrite("![Escape](../../../etc/passwd)"), "Escape")
        self.assertEqual(self.rewrite('<img src="../../../etc/passwd">'), "")

    def test_a_non_github_repository_keeps_no_guessed_raw_layout(self):
        self.course.github_repo_url = "https://gitlab.example/DataTalksClub/ml"
        self.course.save(update_fields=["github_repo_url"])

        self.assertEqual(self.rewrite('<img src="images/a.png">'), "")
        self.assertEqual(self.rewrite("![Diagram](images/a.png)"), "Diagram")

    def test_absolute_and_data_sources_are_never_rewritten(self):
        for source in (
            "https://example.test/a.png",
            "http://example.test/a.png",
            "data:image/png;base64,AAAA",
        ):
            with self.subTest(source=source):
                self.assertIn(source, self.rewrite(f"![Shared]({source})"))

    def test_an_existing_description_is_preserved(self):
        self.assertIn(
            'alt="Course thumbnail"',
            self.rewrite('<img alt="Course thumbnail" src="images/thumbnail.jpg">'),
        )
