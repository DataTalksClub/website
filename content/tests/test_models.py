from __future__ import annotations

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import TestCase

from content.models import (
    ContentAsset,
    ContentDocument,
    ContentSource,
    validate_exact_public_path,
)

from .factories import SHA256_A, activate, make_ready_release, make_source


class ContentModelTests(TestCase):
    def test_source_identity_and_repository_tuple_are_canonical_and_unique(self) -> None:
        source = make_source(stable_id="dtc-main-site")
        source.stable_id = "DTC-main-site"
        with self.assertRaises(ValidationError):
            source.full_clean()

        for field_name, value in (
            ("repository_owner", " DataTalksClub"),
            ("repository_name", "fixture "),
            ("branch", " main"),
        ):
            source.refresh_from_db()
            setattr(source, field_name, value)
            with self.assertRaises(ValidationError):
                source.full_clean()

        source.refresh_from_db()
        with self.assertRaises(IntegrityError), transaction.atomic():
            ContentSource.objects.create(
                stable_id="another-source",
                display_name="Duplicate repo",
                repository_owner=source.repository_owner.lower(),
                repository_name=source.repository_name.upper(),
                branch=source.branch.upper(),
                path_allowlist=[],
                adapter_type="fixture",
                mount_path="/",
            )

    def test_source_stable_id_and_frozen_children_are_immutable(self) -> None:
        source = make_source()
        release = activate(source, make_ready_release(source, commit_character="a"))
        source.stable_id = "renamed-source"
        with self.assertRaises(ValidationError):
            source.save()

        document = ContentDocument.objects.get(release=release)
        document.title = "Changed"
        with self.assertRaises(ValidationError):
            document.save()

        asset = ContentAsset.objects.get(release=release)
        with self.assertRaises(ValidationError):
            asset.delete()

    def test_named_constraints_reject_duplicate_and_malformed_records(self) -> None:
        source = make_source()
        release = make_ready_release(source, commit_character="a")
        with self.assertRaises(ValidationError), transaction.atomic():
            ContentDocument.objects.create(
                release=release,
                content_kind="fixture",
                stable_key="other",
                source_path="other.md",
                checksum=SHA256_A,
                exact_public_path="/Fixture/Exact.html",
                title="Duplicate",
            )

        for malformed_path in ("/control\x1f.html", "/delete\x7f.html"):
            with (
                self.subTest(malformed_path=repr(malformed_path)),
                self.assertRaises(ValidationError),
            ):
                validate_exact_public_path(malformed_path)

    def test_admin_safe_string_representations_do_not_include_raw_content(self) -> None:
        source = make_source()
        release = make_ready_release(source, commit_character="a")
        document = ContentDocument.objects.get(release=release)
        asset = ContentAsset.objects.get(release=release)
        for rendered in (str(source), str(release), str(document), str(asset)):
            self.assertNotIn("raw commit", rendered)
            self.assertLess(len(rendered), 300)
