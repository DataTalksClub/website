import json
import tempfile
from pathlib import Path
from unittest import mock

from django.contrib import admin
from django.contrib.staticfiles.storage import staticfiles_storage
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import TestCase

from courses.models import Course, Testimonial, TestimonialPlacement
from courses.models.testimonial import INTERIM_SITE_ASSET_STATIC_PREFIX
from courses.services.testimonials import (
    TestimonialImportError,
    homepage_testimonials,
    import_homepage_testimonials,
    load_reviewed_homepage_testimonials,
)


class TestimonialPlacementConstraintTests(TestCase):
    """Placement is checked by the database, not by the code that writes a row.

    The scope of a testimonial decides where a person's words are published, so
    "homepage rows leave the course empty" has to be a constraint rather than a
    habit of whichever writer happens to create the row.
    """

    def setUp(self) -> None:
        super().setUp()
        self.course = Course.objects.create(slug="constraint-family", title="Constraint Family")

    def _create(self, **fields) -> Testimonial:
        return Testimonial.objects.create(
            name="Someone",
            attribution="Role · City",
            quote="A quote.",
            **fields,
        )

    def test_a_homepage_testimonial_cannot_name_a_course(self) -> None:
        with transaction.atomic(), self.assertRaises(IntegrityError):
            self._create(placement=TestimonialPlacement.HOMEPAGE, course=self.course)

    def test_a_course_testimonial_must_name_a_course(self) -> None:
        with transaction.atomic(), self.assertRaises(IntegrityError):
            self._create(placement=TestimonialPlacement.COURSE, course=None)

    def test_an_unknown_placement_is_refused_outright(self) -> None:
        with transaction.atomic(), self.assertRaises(IntegrityError):
            self._create(placement="somewhere-else", course=None)

    def test_both_valid_scopes_are_accepted(self) -> None:
        homepage = self._create(placement=TestimonialPlacement.HOMEPAGE, course=None)
        course_scoped = self._create(placement=TestimonialPlacement.COURSE, course=self.course)

        self.assertIsNone(homepage.course)
        self.assertEqual(course_scoped.course, self.course)
        self.assertEqual(course_scoped, self.course.testimonials.get())

    def test_clean_reports_the_stored_constraint_as_a_field_error(self) -> None:
        for placement, course in (
            (TestimonialPlacement.HOMEPAGE, self.course),
            (TestimonialPlacement.COURSE, None),
        ):
            with self.subTest(placement=placement):
                testimonial = Testimonial(
                    placement=placement,
                    course=course,
                    name="Someone",
                    quote="A quote.",
                )
                with self.assertRaises(ValidationError) as raised:
                    testimonial.clean()
                self.assertIn("course", raised.exception.message_dict)


class HomepageTestimonialReadTests(TestCase):
    def setUp(self) -> None:
        super().setUp()
        Testimonial.objects.all().delete()

    def test_only_published_homepage_rows_are_returned_in_editor_order(self) -> None:
        course = Course.objects.create(slug="read-family", title="Read Family")
        Testimonial.objects.create(
            placement=TestimonialPlacement.HOMEPAGE,
            name="Second",
            quote="Second quote.",
            position=2,
            published=True,
        )
        Testimonial.objects.create(
            placement=TestimonialPlacement.HOMEPAGE,
            name="First",
            quote="First quote.",
            position=1,
            published=True,
        )
        Testimonial.objects.create(
            placement=TestimonialPlacement.HOMEPAGE,
            name="Unpublished",
            quote="Not live yet.",
            position=0,
            published=False,
        )
        Testimonial.objects.create(
            placement=TestimonialPlacement.COURSE,
            course=course,
            name="Course scoped",
            quote="Belongs to a course page.",
            position=0,
            published=True,
        )

        self.assertEqual(
            [story.name for story in homepage_testimonials()],
            ["First", "Second"],
        )

    def test_the_read_is_one_query(self) -> None:
        Testimonial.objects.create(
            placement=TestimonialPlacement.HOMEPAGE,
            name="Only",
            quote="Only quote.",
            published=True,
        )

        with self.assertNumQueries(1):
            self.assertEqual(len(homepage_testimonials()), 1)

    def test_an_empty_table_yields_nothing_rather_than_raising(self) -> None:
        self.assertEqual(homepage_testimonials(), ())


class SeededHomepageTestimonialTests(TestCase):
    """The reviewed set carries the retired Python tuple over verbatim.

    The rows arrive through ``courses.services.testimonials`` from
    ``courses/homepage_testimonials.json`` -- in a test database via
    ``test_support.reference_data``, in production via
    ``scripts/prod/import_testimonials.py``.
    """

    def test_six_published_homepage_rows_arrive_with_portrait_and_source(self) -> None:
        stories = homepage_testimonials()

        self.assertEqual(len(stories), 6)
        self.assertEqual([story.position for story in stories], [0, 1, 2, 3, 4, 5])
        for story in stories:
            with self.subTest(position=story.position):
                self.assertIsNone(story.course)
                self.assertTrue(story.published)
                self.assertTrue(story.quote.strip())
                self.assertTrue(story.attribution.strip())
                self.assertTrue(
                    story.portrait_asset_key.startswith("testimonials/"),
                    story.portrait_asset_key,
                )
                self.assertTrue(story.source_url.startswith("https://"), story.source_url)
                # Nothing invented a role transition or an elapsed time.
                self.assertEqual(story.role_before, "")
                self.assertEqual(story.role_after, "")
                self.assertEqual(story.elapsed, "")

    def test_every_seeded_portrait_is_a_checked_in_static_file(self) -> None:
        """The stored key carries no prefix, so resolve it the way the model does."""

        from django.contrib.staticfiles import finders

        for story in homepage_testimonials():
            with self.subTest(key=story.portrait_asset_key):
                self.assertIsNotNone(
                    finders.find(f"{INTERIM_SITE_ASSET_STATIC_PREFIX}{story.portrait_asset_key}")
                )
                self.assertTrue(story.portrait_url)


class PortraitResolutionTests(TestCase):
    """A stored key must never be able to take the page down.

    ``portrait_url`` is read inside the homepage's story loop.  Under manifest
    static storage an unknown reference raises rather than 404s, so a key an
    editor mistyped would abandon the whole render -- the other five cards and
    every other band with it.  Degrading to the decorative avatar is the only
    acceptable outcome, and these pin it.
    """

    def _story(self, key: str) -> Testimonial:
        return Testimonial(
            placement=TestimonialPlacement.HOMEPAGE,
            name="Someone",
            quote="A quote.",
            portrait_asset_key=key,
        )

    def test_a_key_that_cannot_be_resolved_degrades_instead_of_raising(self) -> None:
        story = self._story("testimonials/does-not-exist.jpg")

        with mock.patch.object(
            staticfiles_storage,
            "url",
            side_effect=ValueError("Missing staticfiles manifest entry"),
        ):
            self.assertEqual(story.portrait_url, "")

    def test_the_guard_holds_against_the_real_production_storage(self) -> None:
        """Not a mock: the storage the release actually builds and serves with.

        The tests around this one inject the failure, which proves the guard but
        assumes the premise.  This one installs
        ``CompressedManifestStaticFilesStorage`` over a manifest that knows one
        portrait and not the other, so the raise is the storage's own.
        """

        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[2] / ".tmp") as root:
            (Path(root) / "staticfiles.json").write_text(
                json.dumps(
                    {
                        "version": "1.1",
                        "hash": "test",
                        "paths": {
                            "core/testimonials/present.jpg": (
                                "core/testimonials/present.abc123.jpg"
                            )
                        },
                    }
                ),
                encoding="utf-8",
            )
            with self.settings(
                STATIC_ROOT=root,
                STATIC_URL="/static/",
                STORAGES={
                    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
                    "staticfiles": {
                        "BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage"
                    },
                },
            ):
                # The premise: this storage raises for an unknown reference.
                with self.assertRaises(ValueError):
                    staticfiles_storage.url("core/testimonials/absent.jpg")

                self.assertEqual(
                    self._story("testimonials/present.jpg").portrait_url,
                    "/static/core/testimonials/present.abc123.jpg",
                )
                self.assertEqual(self._story("testimonials/absent.jpg").portrait_url, "")

    def test_no_failure_of_asset_resolution_reaches_the_caller(self) -> None:
        """The guard is deliberately broad; narrowing it reintroduces the hazard."""

        story = self._story("testimonials/example.jpg")
        for error in (ValueError("missing entry"), OSError("manifest unreadable"), RuntimeError()):
            with self.subTest(error=type(error).__name__):
                with mock.patch.object(staticfiles_storage, "url", side_effect=error):
                    self.assertEqual(story.portrait_url, "")

    def test_an_empty_key_resolves_to_nothing_without_touching_storage(self) -> None:
        for key in ("", "   "):
            with self.subTest(key=repr(key)):
                with mock.patch.object(staticfiles_storage, "url") as url:
                    self.assertEqual(self._story(key).portrait_url, "")
                url.assert_not_called()

    def test_the_stored_key_is_resolved_under_the_interim_prefix(self) -> None:
        story = self._story("testimonials/tim-claytor.jpg")

        with mock.patch.object(staticfiles_storage, "url", return_value="/static/x.jpg") as url:
            self.assertEqual(story.portrait_url, "/static/x.jpg")

        url.assert_called_once_with("core/testimonials/tim-claytor.jpg")
        self.assertEqual(INTERIM_SITE_ASSET_STATIC_PREFIX, "core/")

    def test_a_key_that_escapes_its_prefix_is_refused_by_validation(self) -> None:
        for key in (
            "/etc/passwd",
            "../secrets/key.jpg",
            "https://evil.invalid/pixel.gif",
            "//evil.invalid/pixel.gif",
            "testimonials\\example.jpg",
            "data:image/gif;base64,AAAA",
        ):
            with self.subTest(key=key):
                with self.assertRaises(ValidationError):
                    self._story(key).full_clean(exclude=("course",))

    def test_an_ordinary_relative_key_validates(self) -> None:
        self._story("testimonials/example.jpg").full_clean(exclude=("course",))


class HomepageTestimonialImportTests(TestCase):
    """The reviewed set is imported, not migrated: replay is safe and bounded."""

    def _write(self, payload: object) -> Path:
        scratch = Path(__file__).resolve().parents[2] / ".tmp"
        scratch.mkdir(parents=True, exist_ok=True)
        handle = tempfile.NamedTemporaryFile(
            "w", suffix=".json", dir=scratch, delete=False, encoding="utf-8"
        )
        with handle:
            json.dump(payload, handle)
        path = Path(handle.name)
        self.addCleanup(path.unlink, True)
        return path

    def test_replaying_the_reviewed_file_writes_no_second_row(self) -> None:
        before = Testimonial.objects.filter(placement=TestimonialPlacement.HOMEPAGE).count()

        report = import_homepage_testimonials()

        self.assertEqual(report.total, 6)
        self.assertTrue(report.replayed)
        self.assertEqual(
            Testimonial.objects.filter(placement=TestimonialPlacement.HOMEPAGE).count(),
            before,
        )

    def test_it_bootstraps_an_empty_table_and_reports_what_it_created(self) -> None:
        Testimonial.objects.all().delete()

        report = import_homepage_testimonials()

        self.assertEqual((report.total, report.created, report.updated), (6, 6, 0))
        self.assertFalse(report.replayed)
        self.assertEqual(len(homepage_testimonials()), 6)

    def test_a_row_an_editor_added_is_never_touched(self) -> None:
        editor_row = Testimonial.objects.create(
            placement=TestimonialPlacement.HOMEPAGE,
            name="Editor Added",
            quote="Added in the admin after the import ran.",
            source_url="https://example.invalid/editor-added",
            position=99,
            published=True,
        )

        import_homepage_testimonials()

        editor_row.refresh_from_db()
        self.assertEqual(editor_row.name, "Editor Added")
        self.assertEqual(editor_row.position, 99)

    def test_a_malformed_reviewed_file_is_refused_by_condition_code(self) -> None:
        for payload, code in (
            ({"schema_version": 2, "testimonials": []}, "schema_invalid"),
            ({"schema_version": 1, "testimonials": []}, "empty"),
            ({"schema_version": 1, "testimonials": [{"name": "No quote"}]}, "shape_invalid"),
        ):
            with self.subTest(code=code):
                with self.assertRaises(TestimonialImportError) as raised:
                    load_reviewed_homepage_testimonials(self._write(payload))
                self.assertIn(code, str(raised.exception))

    def test_two_entries_sharing_a_source_link_are_refused(self) -> None:
        entry = {
            "name": "Someone",
            "attribution": "Role · City",
            "quote": "A quote.",
            "source_url": "https://example.invalid/same",
            "portrait_asset_key": "",
        }
        with self.assertRaises(TestimonialImportError) as raised:
            load_reviewed_homepage_testimonials(
                self._write({"schema_version": 1, "testimonials": [entry, dict(entry)]})
            )
        self.assertIn("duplicated", str(raised.exception))


class TestimonialAdminTests(TestCase):
    def test_the_model_is_editable_in_django_admin(self) -> None:
        self.assertIn(Testimonial, admin.site._registry)
        model_admin = admin.site._registry[Testimonial]
        self.assertIn("placement", model_admin.list_filter)
        self.assertIn("published", model_admin.list_display)
