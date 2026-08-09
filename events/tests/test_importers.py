from __future__ import annotations

import csv
import hashlib
import io
import json
import stat
import tempfile
from collections import Counter
from pathlib import Path
from unittest.mock import patch
from zipfile import ZipFile, ZipInfo

from django.conf import settings
from django.test import SimpleTestCase, override_settings

from events.importers import (
    EVENTBRITE_SCHEMA_FINGERPRINTS,
    AggregateCandidate,
    CanonicalProposal,
    ProtectedSourceError,
    _require_pinned_eventbrite_reconciliation,
    _require_pinned_luma_reconciliation,
    derive_eventbrite,
    derive_luma,
    derive_registered_source,
)
from events.models import HistoricalRegistrationAggregateRevision


def tree_checksum(root: Path) -> str:
    digest = hashlib.sha256(b"dtc-protected-tree-v1\0")
    for path in sorted(root.iterdir(), key=lambda item: item.name):
        relative = path.relative_to(root).as_posix().encode()
        payload = path.read_bytes()
        digest.update(len(relative).to_bytes(8, "big"))
        digest.update(relative)
        digest.update(len(payload).to_bytes(8, "big"))
        digest.update(payload)
    return digest.hexdigest()


def canonical_proposal(index: int) -> CanonicalProposal:
    return CanonicalProposal(
        repository="synthetic-repository",
        revision="synthetic-revision",
        source_key=f"synthetic-source-{index}",
        slug=f"synthetic-event-{index}",
    )


def aggregate_candidate(
    index: int,
    *,
    eligible_count: int,
    excluded_count: int = 0,
    proposal: bool,
) -> AggregateCandidate:
    return AggregateCandidate(
        external_event_identifier=str(index + 1),
        eligible_count=eligible_count,
        excluded_count=excluded_count,
        quarantined_count=0,
        status_totals={},
        schema_version="synthetic-v1",
        state=HistoricalRegistrationAggregateRevision.State.STAGED,
        reason_code="",
        aggregate_checksum=hashlib.sha256(f"synthetic-{index}".encode()).hexdigest(),
        proposal=canonical_proposal(index) if proposal else None,
    )


class ProtectedSourceAdapterTests(SimpleTestCase):
    def setUp(self) -> None:
        scratch = Path(settings.BASE_DIR) / ".tmp"
        scratch.mkdir(exist_ok=True)
        self.temporary = tempfile.TemporaryDirectory(dir=scratch)
        self.root = Path(self.temporary.name)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _luma_source(self, statuses: tuple[str, ...]) -> Path:
        source = self.root / "luma"
        source.mkdir()
        (source / "synthetic.json").write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "event_id": "synthetic-event",
                    "event_url": "https://example.test/synthetic-event",
                }
            ),
            encoding="utf-8",
        )
        with (source / "synthetic.csv").open("w", encoding="utf-8", newline="") as stream:
            writer = csv.DictWriter(
                stream,
                fieldnames=("event_id", "guest_id", "approval_status", "ignored_email"),
            )
            writer.writeheader()
            for index, status in enumerate(statuses):
                writer.writerow(
                    {
                        "event_id": "synthetic-event",
                        "guest_id": f"synthetic-guest-{index}",
                        "approval_status": status,
                        "ignored_email": f"canary-{index}@example.test",
                    }
                )
        return source

    def _eventbrite_archive(
        self,
        *,
        status: str = "Attending",
        entry_name: str = "123.csv",
        include_xlsx: bool = True,
        symlink: bool = False,
    ) -> tuple[Path, dict[str, tuple[str, int]]]:
        headers = ("Order #", "Order Date", "Attendee #", "Attendee Status")
        stream = io.StringIO(newline="")
        writer = csv.DictWriter(stream, fieldnames=headers)
        writer.writeheader()
        writer.writerow(
            {
                "Order #": "synthetic-order",
                "Order Date": "2026-01-01",
                "Attendee #": "synthetic-attendee",
                "Attendee Status": status,
            }
        )
        archive_path = self.root / f"archive-{len(tuple(self.root.iterdir()))}.zip"
        with ZipFile(archive_path, "w") as archive:
            if symlink:
                entry = ZipInfo(entry_name)
                entry.create_system = 3
                entry.external_attr = (stat.S_IFLNK | 0o777) << 16
                archive.writestr(entry, "target")
            else:
                archive.writestr(entry_name, stream.getvalue())
            if include_xlsx:
                archive.writestr("synthetic.xlsx", b"never-opened")
        fingerprint = hashlib.sha256("\x1f".join(headers).encode()).hexdigest()
        return archive_path, {fingerprint: ("synthetic_eventbrite_v1", len(headers))}

    def test_luma_counts_only_statuses_and_discards_ignored_identity_values(self) -> None:
        source = self._luma_source(("approved", "approved", "declined"))
        derived = derive_luma(source, expected_checksum=tree_checksum(source))

        self.assertEqual(derived.parsed_row_total, 3)
        self.assertEqual(derived.eligible_row_total, 2)
        self.assertEqual(derived.excluded_row_total, 1)
        self.assertEqual(derived.status_totals, {"approved": 2, "declined": 1})
        self.assertNotIn("canary", repr(derived))
        self.assertEqual(
            derived.candidates[0].state,
            HistoricalRegistrationAggregateRevision.State.STAGED,
        )

    def test_luma_unknown_status_quarantines_event_and_pair_mismatch_rejects(self) -> None:
        source = self._luma_source(("approved", "unexpected"))
        derived = derive_luma(source, expected_checksum=tree_checksum(source))
        self.assertEqual(derived.quarantined_event_total, 1)
        self.assertEqual(derived.candidates[0].reason_code, "unknown_status")

        (source / "synthetic.json").unlink()
        with self.assertRaisesMessage(ProtectedSourceError, "mismatched_luma_pair"):
            derive_luma(source, expected_checksum=tree_checksum(source))

    def test_eventbrite_uses_exact_ordered_header_fingerprint_and_never_opens_xlsx(self) -> None:
        archive, schemas = self._eventbrite_archive()
        checksum = hashlib.sha256(archive.read_bytes()).hexdigest()
        derived = derive_eventbrite(
            archive,
            expected_checksum=checksum,
            allowed_schema_fingerprints=schemas,
        )

        self.assertEqual(derived.manifest_entry_total, 2)
        self.assertEqual(derived.manifest_event_total, 1)
        self.assertEqual(derived.parsed_row_total, 1)
        self.assertEqual(derived.eligible_row_total, 1)
        self.assertIn("unsupported_xlsx", derived.reason_codes)
        self.assertNotIn("synthetic-attendee", repr(derived))

    def test_eventbrite_unknown_status_and_unsupported_schema_quarantine(self) -> None:
        archive, schemas = self._eventbrite_archive(status="Unexpected", include_xlsx=False)
        checksum = hashlib.sha256(archive.read_bytes()).hexdigest()
        derived = derive_eventbrite(
            archive,
            expected_checksum=checksum,
            allowed_schema_fingerprints=schemas,
        )
        self.assertEqual(derived.quarantined_event_total, 1)
        self.assertEqual(derived.candidates[0].reason_code, "unknown_status")

        unsupported = derive_eventbrite(
            archive,
            expected_checksum=checksum,
            allowed_schema_fingerprints=EVENTBRITE_SCHEMA_FINGERPRINTS,
        )
        self.assertEqual(unsupported.parsed_row_total, 0)
        self.assertEqual(unsupported.candidates[0].reason_code, "unsupported_schema")

    def test_eventbrite_rejects_traversal_symlink_and_checksum_drift(self) -> None:
        traversal, schemas = self._eventbrite_archive(entry_name="../123.csv", include_xlsx=False)
        with self.assertRaisesMessage(ProtectedSourceError, "path_traversal"):
            derive_eventbrite(
                traversal,
                expected_checksum=hashlib.sha256(traversal.read_bytes()).hexdigest(),
                allowed_schema_fingerprints=schemas,
            )

        symlink, schemas = self._eventbrite_archive(
            entry_name="456.csv", include_xlsx=False, symlink=True
        )
        with self.assertRaisesMessage(ProtectedSourceError, "source_symlink"):
            derive_eventbrite(
                symlink,
                expected_checksum=hashlib.sha256(symlink.read_bytes()).hexdigest(),
                allowed_schema_fingerprints=schemas,
            )

        valid, schemas = self._eventbrite_archive(include_xlsx=False)
        with self.assertRaisesMessage(ProtectedSourceError, "checksum_drift"):
            derive_eventbrite(
                valid,
                expected_checksum="0" * 64,
                allowed_schema_fingerprints=schemas,
            )

    def test_pinned_eventbrite_fingerprints_are_exact(self) -> None:
        self.assertEqual(
            dict(EVENTBRITE_SCHEMA_FINGERPRINTS),
            {
                "333061583991588f9b6bc78c9873feb7ddab8711687ee999da2135a4cbef0c7e": (
                    "eventbrite_csv_v1",
                    23,
                ),
                "6f7f37db55176240fa695289cf13c8bcbaf86970f00b0ed18c4f2a1a6ee4e9ae": (
                    "eventbrite_csv_v2",
                    25,
                ),
                "c3a799fcbcee38d3e1733fc0cd317e84236f5d17241513c1a76b3646a19ea0b8": (
                    "eventbrite_csv_v3",
                    24,
                ),
            },
        )

    def test_pinned_eventbrite_checksum_requires_exact_aggregate_reconciliation(self) -> None:
        archive, schemas = self._eventbrite_archive(include_xlsx=False)
        checksum = hashlib.sha256(archive.read_bytes()).hexdigest()
        with (
            patch(
                "events.importers.PINNED_EVENTBRITE_SOURCE_CHECKSUM",
                checksum,
            ),
            self.assertRaisesMessage(ProtectedSourceError, "protected_fact_mismatch"),
        ):
            derive_eventbrite(
                archive,
                expected_checksum=checksum,
                allowed_schema_fingerprints=schemas,
            )

    def test_pinned_luma_mapping_reconciliation_accepts_only_64_and_95(self) -> None:
        candidates = tuple(
            aggregate_candidate(
                index,
                eligible_count=50_300 if index == 0 else 1 if index < 157 else 0,
                excluded_count=49 if index == 0 else 0,
                proposal=index < 64,
            )
            for index in range(159)
        )
        bridge = {f"https://example.test/{index}": canonical_proposal(index) for index in range(64)}

        def verify(test_bridge: dict[str, CanonicalProposal]) -> None:
            _require_pinned_luma_reconciliation(
                files=tuple(Path(f"synthetic-{index}") for index in range(318)),
                candidates=candidates,
                parsed_total=50_505,
                eligible_total=50_456,
                excluded_total=49,
                statuses=Counter({"approved": 50_456, "declined": 49}),
                bridge=test_bridge,
                source_missing={},
            )

        verify(bridge)
        with self.assertRaisesMessage(ProtectedSourceError, "protected_fact_mismatch"):
            verify(dict(tuple(bridge.items())[:-1]))

    def test_pinned_eventbrite_mapping_reconciliation_accepts_only_200_9_and_27(
        self,
    ) -> None:
        candidates = tuple(
            aggregate_candidate(
                index,
                eligible_count=23_793 if index == 0 else 1,
                proposal=index < 200,
            )
            for index in range(209)
        )
        bridge = {str(index + 1): canonical_proposal(index) for index in range(200)}
        missing = {str(index + 1_000): canonical_proposal(index + 1_000) for index in range(27)}

        def verify(
            test_candidates: tuple[AggregateCandidate, ...],
            test_bridge: dict[str, CanonicalProposal],
            test_missing: dict[str, CanonicalProposal],
        ) -> None:
            _require_pinned_eventbrite_reconciliation(
                entries_total=210,
                candidates=test_candidates,
                xlsx_total=1,
                parsed_total=24_001,
                eligible_total=24_001,
                excluded_total=0,
                event_ids={str(index + 1) for index in range(209)},
                statuses=Counter({"attending": 24_001}),
                schema_totals=Counter(
                    {
                        "eventbrite_csv_v1": 22,
                        "eventbrite_csv_v2": 12,
                        "eventbrite_csv_v3": 175,
                    }
                ),
                bridge=test_bridge,
                source_missing=test_missing,
            )

        verify(candidates, bridge, missing)
        changed_candidates = tuple(
            aggregate_candidate(
                index,
                eligible_count=23_793 if index == 0 else 1,
                proposal=index < 199,
            )
            for index in range(209)
        )
        for changed_candidates_value, changed_bridge, changed_missing in (
            (candidates, dict(tuple(bridge.items())[:-1]), missing),
            (candidates, bridge, dict(tuple(missing.items())[:-1])),
            (changed_candidates, bridge, missing),
        ):
            with self.subTest(
                bridge_total=len(changed_bridge),
                source_missing_total=len(changed_missing),
            ):
                with self.assertRaisesMessage(ProtectedSourceError, "protected_fact_mismatch"):
                    verify(changed_candidates_value, changed_bridge, changed_missing)

    def test_registered_sources_require_code_owned_reconciliation_profile(self) -> None:
        source = self._luma_source(("approved",))
        registry = {
            "synthetic-profile-source": {
                "provider": "luma",
                "path": str(source),
                "sha256": tree_checksum(source),
            }
        }
        with (
            override_settings(HISTORICAL_REGISTRATION_SOURCES=registry),
            self.assertRaisesMessage(ProtectedSourceError, "source_registry_invalid"),
        ):
            derive_registered_source("synthetic-profile-source")

        registry["synthetic-profile-source"]["reconciliation_profile"] = "synthetic"
        with override_settings(
            HISTORICAL_REGISTRATION_SOURCES=registry,
            HISTORICAL_REGISTRATION_ALLOW_SYNTHETIC_PROFILE=True,
        ):
            derived = derive_registered_source("synthetic-profile-source")
        self.assertEqual(derived.manifest_event_total, 1)

        with (
            override_settings(
                HISTORICAL_REGISTRATION_SOURCES=registry,
                HISTORICAL_REGISTRATION_ALLOW_SYNTHETIC_PROFILE=False,
            ),
            self.assertRaisesMessage(ProtectedSourceError, "source_registry_invalid"),
        ):
            derive_registered_source("synthetic-profile-source")

    def test_adapter_paths_invoke_pinned_mapping_reconciliation(self) -> None:
        source = self._luma_source(("approved",))
        with patch("events.importers._require_pinned_luma_reconciliation") as luma_guard:
            derive_luma(
                source,
                expected_checksum=tree_checksum(source),
                enforce_pinned_reconciliation=True,
            )
        luma_guard.assert_called_once()

        archive, schemas = self._eventbrite_archive(include_xlsx=False)
        with patch(
            "events.importers._require_pinned_eventbrite_reconciliation"
        ) as eventbrite_guard:
            derive_eventbrite(
                archive,
                expected_checksum=hashlib.sha256(archive.read_bytes()).hexdigest(),
                allowed_schema_fingerprints=schemas,
                enforce_pinned_reconciliation=True,
            )
        eventbrite_guard.assert_called_once()

    def test_mapping_evidence_rejects_unknown_and_overlapping_keys(self) -> None:
        source = self._luma_source(("approved",))
        proposal = {
            "repository": "synthetic-repository",
            "revision": "synthetic-revision",
            "source_key": "synthetic-source",
            "slug": "synthetic-event",
        }
        with self.assertRaisesMessage(ProtectedSourceError, "invalid_mapping_bridge"):
            derive_luma(
                source,
                expected_checksum=tree_checksum(source),
                mapping_bridge={"https://example.test/not-in-source": proposal},
            )

        archive, schemas = self._eventbrite_archive(include_xlsx=False)
        checksum = hashlib.sha256(archive.read_bytes()).hexdigest()
        with self.assertRaisesMessage(ProtectedSourceError, "invalid_mapping_bridge"):
            derive_eventbrite(
                archive,
                expected_checksum=checksum,
                mapping_bridge={"123": proposal},
                source_missing={"123": proposal},
                allowed_schema_fingerprints=schemas,
            )
