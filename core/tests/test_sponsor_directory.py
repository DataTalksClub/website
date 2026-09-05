"""The reviewed public sponsor directory: its logo guard, import, and read paths.

A reviewed directory file replaced ``core.sponsor_history``'s hardcoded
``FEATURED_SUPPORTERS``/``PAST_SUPPORTERS`` tuples.  Sponsors are database rows
now; a reviewed file is one-time ingestion input, so these tests write the
input they import rather than asserting the contents of a checked-in one.  This
file covers the three things that moved: :attr:`core.models.Sponsor.logo_url`'s never-raise
guard (the same treatment ``courses.models.Testimonial.portrait_url`` got),
the reviewed file's loader and importer
(:mod:`courses.tests.test_testimonials` is the template), and
:func:`core.sponsors.public_supporter_history`.  ``core/tests/test_sponsors.py``
covers everything already built around ``Sponsor`` before this (Studio, the
admin API, the events_hub placement).
"""

from __future__ import annotations

import json
import tempfile
import uuid
from pathlib import Path
from unittest import mock

from django.contrib.staticfiles.storage import staticfiles_storage
from django.core.exceptions import ValidationError
from django.db import DatabaseError
from django.test import TestCase
from django.urls import reverse

from core.models import Sponsor, SponsorPlacementAssignment
from core.sponsors import (
    SPONSOR_PLACEMENT_PUBLIC_DIRECTORY,
    InvalidSponsor,
    SponsorDirectoryImportError,
    create_sponsor,
    import_public_sponsor_directory,
    load_reviewed_sponsor_directory,
    public_sponsors,
    public_supporter_history,
)

#: A reviewed directory the tests own: two featured sponsors and two the site
#: has stopped featuring.  Its shape is the contract; its contents are nobody's
#: business but this file's.
REVIEWED_SPONSORS = (
    {
        "key": "northwind",
        "name": "Northwind Analytics",
        "url": "https://northwind.example.invalid",
        "lifecycle": "active",
        "description": "A synthetic featured sponsor.",
        "logo_asset_key": "sponsors/northwind.png",
        "position": 1,
    },
    {
        "key": "contoso",
        "name": "Contoso Data",
        "url": "https://contoso.example.invalid",
        "lifecycle": "active",
        "description": "A second synthetic featured sponsor.",
        "logo_asset_key": "sponsors/contoso.png",
        "position": 2,
    },
    {
        "key": "adventureworks",
        "name": "AdventureWorks",
        "url": "https://adventureworks.example.invalid",
        "lifecycle": "archived",
        "description": "A synthetic sponsor the site has thanked.",
        "logo_asset_key": "sponsors/adventureworks.png",
        "position": None,
    },
    {
        "key": "fabrikam",
        "name": "fabrikam",
        "url": "https://fabrikam.example.invalid",
        "lifecycle": "archived",
        "description": "A second synthetic archived sponsor.",
        "logo_asset_key": "sponsors/fabrikam.png",
        "position": None,
    },
)
REVIEWED_TOTAL = len(REVIEWED_SPONSORS)
REVIEWED_ACTIVE = tuple(
    entry["name"] for entry in REVIEWED_SPONSORS if entry["lifecycle"] == "active"
)


def _write_json(test: TestCase, payload: object) -> Path:
    """Write ``payload`` to a scratch file the test cleans up, and return its path."""

    scratch = Path(__file__).resolve().parents[2] / ".tmp"
    scratch.mkdir(parents=True, exist_ok=True)
    handle = tempfile.NamedTemporaryFile(
        "w", suffix=".json", dir=scratch, delete=False, encoding="utf-8"
    )
    with handle:
        json.dump(payload, handle)
    path = Path(handle.name)
    test.addCleanup(path.unlink, True)
    return path


def _reviewed_directory(test: TestCase, **overrides: object) -> Path:
    sponsors = [dict(entry) for entry in REVIEWED_SPONSORS]
    for entry in sponsors:
        entry.update(overrides.get(str(entry["key"]), {}))  # type: ignore[call-overload]
    return _write_json(test, {"schema_version": 1, "sponsors": sponsors})


def _unsaved(**overrides: object) -> Sponsor:
    values: dict[str, object] = {
        "key": "acme-test",
        "name": "Acme",
        "lifecycle": Sponsor.Lifecycle.DRAFT,
        "source": "import",
    }
    values.update(overrides)
    return Sponsor(**values)


class SponsorLogoResolutionTests(TestCase):
    """A stored logo key must never be able to take the page down.

    ``logo_url`` is read inside the homepage's and the sponsor directory's
    render loops.  Under manifest static storage an unknown reference raises
    rather than 404s, so an unguarded lookup would abandon the whole render
    for one bad row.  Degrading to no logo is the only acceptable outcome --
    the mirror image of ``courses.tests.test_testimonials.PortraitResolutionTests``.
    """

    def test_a_key_that_cannot_be_resolved_degrades_instead_of_raising(self) -> None:
        sponsor = _unsaved(logo_asset_key="sponsors/does-not-exist.png")

        with mock.patch.object(
            staticfiles_storage,
            "url",
            side_effect=ValueError("Missing staticfiles manifest entry"),
        ):
            self.assertEqual(sponsor.logo_url, "")

    def test_no_failure_of_asset_resolution_reaches_the_caller(self) -> None:
        sponsor = _unsaved(logo_asset_key="sponsors/example.png")
        for error in (ValueError("missing entry"), OSError("manifest unreadable"), RuntimeError()):
            with self.subTest(error=type(error).__name__):
                with mock.patch.object(staticfiles_storage, "url", side_effect=error):
                    self.assertEqual(sponsor.logo_url, "")

    def test_an_empty_key_resolves_to_nothing_without_touching_storage(self) -> None:
        for key in ("", "   "):
            with self.subTest(key=repr(key)):
                with mock.patch.object(staticfiles_storage, "url") as url:
                    self.assertEqual(_unsaved(logo_asset_key=key).logo_url, "")
                url.assert_not_called()

    def test_the_stored_key_is_resolved_under_the_interim_prefix(self) -> None:
        sponsor = _unsaved(logo_asset_key="sponsors/dlthub.png")

        with mock.patch.object(staticfiles_storage, "url", return_value="/static/x.png") as url:
            self.assertEqual(sponsor.logo_url, "/static/x.png")

        url.assert_called_once_with("core/sponsors/dlthub.png")

    def test_a_key_that_escapes_its_prefix_is_refused_by_validation(self) -> None:
        for key in (
            "/etc/passwd",
            "../secrets/key.png",
            "https://evil.invalid/pixel.gif",
            "//evil.invalid/pixel.gif",
            "sponsors\\example.png",
            "data:image/gif;base64,AAAA",
        ):
            with self.subTest(key=key):
                with self.assertRaises(ValidationError):
                    _unsaved(logo_asset_key=key).full_clean()

    def test_an_ordinary_relative_key_validates(self) -> None:
        _unsaved(logo_asset_key="sponsors/example.png").full_clean()

    def test_the_service_layer_refuses_the_same_keys_the_model_does(self) -> None:
        """``create_sponsor``/``update_sponsor`` never call ``full_clean`` -- see
        ``core.sponsors``' own validation, which is what actually stands
        between a Studio or import payload and the database."""

        for key in ("/etc/passwd", "../secrets/key.png", "sponsors\\example.png"):
            with self.subTest(key=key):
                with self.assertRaises(InvalidSponsor):
                    create_sponsor(
                        payload={
                            "key": f"bad-{uuid.uuid4().hex[:8]}",
                            "name": "Bad Logo",
                            "lifecycle": "draft",
                            "logo_asset_key": key,
                            "assignments": [],
                        },
                        source="import",
                        idempotency_key=str(uuid.uuid4()),
                        actor_ref="user:188",
                    )
                self.assertFalse(Sponsor.objects.filter(name="Bad Logo").exists())


class SponsorLogoDegradesOnThePublicPageTests(TestCase):
    """A bad row costs one card its logo, not the page -- proved end to end."""

    def test_a_stale_logo_key_does_not_take_the_homepage_or_directory_down(self) -> None:
        create_sponsor(
            payload={
                "key": "good-logo",
                "name": "Good Logo",
                "lifecycle": "active",
                "logo_asset_key": "sponsors/dlthub.png",
                "assignments": [
                    {
                        "placement": SPONSOR_PLACEMENT_PUBLIC_DIRECTORY,
                        "position": 1,
                        "enabled": True,
                    },
                ],
            },
            source="import",
            idempotency_key=str(uuid.uuid4()),
            actor_ref="user:188",
        )
        create_sponsor(
            payload={
                "key": "stale-logo",
                "name": "Stale Logo",
                "lifecycle": "active",
                "logo_asset_key": "sponsors/removed-after-the-manifest-was-built.png",
                "assignments": [
                    {
                        "placement": SPONSOR_PLACEMENT_PUBLIC_DIRECTORY,
                        "position": 2,
                        "enabled": True,
                    },
                ],
            },
            source="import",
            idempotency_key=str(uuid.uuid4()),
            actor_ref="user:188",
        )

        real_url = staticfiles_storage.url

        def manifest_url(name: str) -> str:
            if "removed-after-the-manifest-was-built" in name:
                raise ValueError(f"Missing staticfiles manifest entry for '{name}'")
            return real_url(name)

        with mock.patch.object(staticfiles_storage, "url", side_effect=manifest_url):
            home = self.client.get(reverse("home"))
            directory = self.client.get(reverse("sponsors"))

        for response in (home, directory):
            self.assertEqual(response.status_code, 200)
            body = response.content.decode()
            self.assertIn("Good Logo", body)
            self.assertIn("Stale Logo", body)
            self.assertNotIn("removed-after-the-manifest-was-built", body)
        self.assertIn('src="/static/core/sponsors/dlthub.png"', home.content.decode())
        self.assertIn('<img src="" alt="Stale Logo">', home.content.decode())


class ReviewedSponsorDirectoryLoadTests(TestCase):
    def _write(self, payload: object) -> Path:
        return _write_json(self, payload)

    def test_a_well_formed_file_loads_as_validated_entries(self) -> None:
        entries = load_reviewed_sponsor_directory(_reviewed_directory(self))

        self.assertEqual(len(entries), REVIEWED_TOTAL)
        active = [entry for entry in entries if entry["lifecycle"] == "active"]
        archived = [entry for entry in entries if entry["lifecycle"] == "archived"]
        self.assertEqual([entry["name"] for entry in active], list(REVIEWED_ACTIVE))
        self.assertEqual(len(archived), REVIEWED_TOTAL - len(REVIEWED_ACTIVE))
        for entry in active:
            self.assertIsNotNone(entry["position"])
            self.assertTrue(entry["logo_asset_key"].startswith("sponsors/"))
            self.assertTrue(entry["description"])
        for entry in archived:
            self.assertIsNone(entry["position"])

    def test_a_malformed_reviewed_file_is_refused_by_condition_code(self) -> None:
        base_entry = {
            "key": "acme",
            "name": "Acme",
            "url": "",
            "lifecycle": "active",
            "description": "",
            "logo_asset_key": "",
            "position": 1,
        }
        for payload, code in (
            ({"schema_version": 2, "sponsors": []}, "schema_invalid"),
            ({"schema_version": 1, "sponsors": []}, "empty"),
            ({"schema_version": 1, "sponsors": [{"name": "No key"}]}, "shape_invalid"),
            (
                {"schema_version": 1, "sponsors": [{**base_entry, "lifecycle": "draft"}]},
                "lifecycle_invalid",
            ),
            (
                {"schema_version": 1, "sponsors": [{**base_entry, "position": None}]},
                "active_needs_position",
            ),
            (
                {
                    "schema_version": 1,
                    "sponsors": [{**base_entry, "lifecycle": "archived", "position": 1}],
                },
                "archived_has_position",
            ),
            (
                {"schema_version": 1, "sponsors": [base_entry, dict(base_entry)]},
                "key_duplicated",
            ),
            (
                {
                    "schema_version": 1,
                    "sponsors": [
                        base_entry,
                        {**base_entry, "key": "other"},
                    ],
                },
                "position_duplicated",
            ),
        ):
            with self.subTest(code=code):
                with self.assertRaises(SponsorDirectoryImportError) as raised:
                    load_reviewed_sponsor_directory(self._write(payload))
                self.assertIn(code, str(raised.exception))


class SponsorDirectoryImportTests(TestCase):
    """The reviewed set is imported, not migrated: replay is safe and bounded."""

    def setUp(self) -> None:
        self.reviewed = _reviewed_directory(self)

    def test_it_bootstraps_an_empty_table_and_reports_what_it_created(self) -> None:
        report = import_public_sponsor_directory(self.reviewed)

        self.assertEqual(
            (report.total, report.created, report.updated),
            (REVIEWED_TOTAL, REVIEWED_TOTAL, 0),
        )
        self.assertFalse(report.replayed)
        self.assertEqual(Sponsor.objects.count(), REVIEWED_TOTAL)
        self.assertEqual(
            Sponsor.objects.filter(lifecycle=Sponsor.Lifecycle.ACTIVE).count(),
            len(REVIEWED_ACTIVE),
        )
        self.assertEqual(
            Sponsor.objects.filter(lifecycle=Sponsor.Lifecycle.ARCHIVED).count(),
            REVIEWED_TOTAL - len(REVIEWED_ACTIVE),
        )
        self.assertEqual(
            SponsorPlacementAssignment.objects.filter(
                placement_key=SPONSOR_PLACEMENT_PUBLIC_DIRECTORY
            ).count(),
            len(REVIEWED_ACTIVE),
        )
        self.assertEqual(
            [sponsor["name"] for sponsor in public_sponsors()],
            list(REVIEWED_ACTIVE),
        )

    def test_replaying_the_reviewed_file_writes_nothing(self) -> None:
        import_public_sponsor_directory(self.reviewed)
        before = Sponsor.objects.count()

        report = import_public_sponsor_directory(self.reviewed)

        self.assertEqual(report.total, REVIEWED_TOTAL)
        self.assertTrue(report.replayed)
        self.assertEqual(Sponsor.objects.count(), before)

    def test_a_sponsor_an_editor_added_is_never_touched(self) -> None:
        editor_row = create_sponsor(
            payload={
                "key": "editor-added",
                "name": "Editor Added",
                "lifecycle": "draft",
                "assignments": [],
            },
            source="studio",
            idempotency_key=str(uuid.uuid4()),
            actor_ref="user:188",
        ).sponsor

        import_public_sponsor_directory(self.reviewed)

        refreshed = Sponsor.objects.get(key="editor-added")
        self.assertEqual(refreshed.name, "Editor Added")
        self.assertEqual(refreshed.revision, editor_row["revision"])

    def test_a_content_correction_reaches_an_already_archived_row(self) -> None:
        """Archived sponsors cannot be edited directly; the import reactivates,
        edits, and re-archives -- the same steps a Studio editor would take."""

        import_public_sponsor_directory(self.reviewed)
        archived_key = "adventureworks"
        before = Sponsor.objects.get(key=archived_key)
        self.assertEqual(before.lifecycle, Sponsor.Lifecycle.ARCHIVED)

        corrected = _reviewed_directory(self, **{archived_key: {"name": "AdventureWorks Group"}})

        report = import_public_sponsor_directory(corrected)

        self.assertEqual(report.updated, 1)
        after = Sponsor.objects.get(key=archived_key)
        self.assertEqual(after.name, "AdventureWorks Group")
        self.assertEqual(after.lifecycle, Sponsor.Lifecycle.ARCHIVED)
        self.assertFalse(after.assignments.exists())


class PublicSupporterHistoryTests(TestCase):
    def test_active_and_archived_sponsors_are_named_alphabetically(self) -> None:
        import_public_sponsor_directory(_reviewed_directory(self))

        names = public_supporter_history()

        self.assertEqual(len(names), REVIEWED_TOTAL)
        self.assertEqual(list(names), sorted(names, key=str.casefold))
        # Case-insensitive ordering is the point: a lowercase name sorts by its
        # letters, not behind every capitalised one.
        self.assertIn("fabrikam", names)
        self.assertIn("Northwind Analytics", names)

    def test_a_draft_sponsor_is_excluded(self) -> None:
        create_sponsor(
            payload={
                "key": "not-yet-public",
                "name": "Not Yet Public",
                "lifecycle": "draft",
                "assignments": [],
            },
            source="studio",
            idempotency_key=str(uuid.uuid4()),
            actor_ref="user:188",
        )

        self.assertNotIn("Not Yet Public", public_supporter_history())

    def test_an_unavailable_database_yields_nothing_rather_than_raising(self) -> None:
        with mock.patch(
            "core.sponsors.Sponsor.objects.using",
            side_effect=DatabaseError("unavailable"),
        ):
            self.assertEqual(public_supporter_history(), ())
