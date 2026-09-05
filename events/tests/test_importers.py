"""The source-reader port, exercised without any provider file format.

The Luma and Eventbrite readers live in ``scripts/prod/registration_sources``
and are covered by ``scripts/tests/test_registration_sources.py``.  What is
checked here is the half ``events`` owns: a registry entry is validated the same
way whatever provider it names, an unregistered provider fails closed with a
bounded code, and the code-owned reconciliation profile comes from the reader
rather than from the configuration.
"""

from __future__ import annotations

from pathlib import Path

from django.test import SimpleTestCase, override_settings

from events.importers import (
    AggregateCandidate,
    DerivedSource,
    ProtectedSourceError,
    SourceReader,
    clear_source_readers,
    derive_registered_source,
    register_source_reader,
    registered_source_options,
)
from events.models import HistoricalRegistrationSourceRun

PROVIDER = HistoricalRegistrationSourceRun.Provider.LUMA
PROFILE = "synthetic-code-owned-profile-v1"
CHECKSUM = "a" * 64


def derived_source(**calls: object) -> DerivedSource:
    return DerivedSource(
        provider=PROVIDER,
        adapter_version="synthetic-adapter-v1",
        schema_version="synthetic-v1",
        whole_source_checksum=CHECKSUM,
        manifest_entry_total=1,
        manifest_event_total=1,
        parsed_row_total=1,
        eligible_row_total=1,
        excluded_row_total=0,
        quarantined_event_total=0,
        status_totals={},
        state_totals={},
        reason_codes=(),
        candidates=(
            AggregateCandidate(
                external_event_identifier="synthetic-provider-event",
                eligible_count=1,
                excluded_count=0,
                quarantined_count=0,
                status_totals={},
                schema_version="synthetic-v1",
                state="staged",
                reason_code="",
                aggregate_checksum=CHECKSUM,
            ),
        ),
    )


class SourceReaderPortTests(SimpleTestCase):
    def setUp(self) -> None:
        clear_source_readers()
        self.addCleanup(clear_source_readers)
        self.calls: list[dict[str, object]] = []

    def _register(self) -> None:
        def read(path: Path, **arguments: object) -> DerivedSource:
            self.calls.append({"path": path, **arguments})
            return derived_source()

        register_source_reader(
            SourceReader(provider=PROVIDER, reconciliation_profile=PROFILE, read=read)
        )

    def _registry(self, **overrides: object) -> dict[str, dict[str, object]]:
        configuration: dict[str, object] = {
            "provider": PROVIDER,
            "path": "/synthetic/source",
            "sha256": CHECKSUM,
            "reconciliation_profile": PROFILE,
        }
        configuration.update(overrides)
        return {"synthetic-port-source": configuration}

    def test_a_registered_reader_receives_the_validated_configuration(self) -> None:
        self._register()
        with override_settings(HISTORICAL_REGISTRATION_SOURCES=self._registry()):
            derived = derive_registered_source("synthetic-port-source")

        self.assertEqual(derived.manifest_event_total, 1)
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(self.calls[0]["path"], Path("/synthetic/source"))
        self.assertEqual(self.calls[0]["expected_checksum"], CHECKSUM)
        self.assertIs(self.calls[0]["enforce_pinned_reconciliation"], True)

    def test_a_provider_with_no_registered_reader_fails_closed(self) -> None:
        """A plain web process registers no reader; it must refuse, not guess."""

        with (
            override_settings(HISTORICAL_REGISTRATION_SOURCES=self._registry()),
            self.assertRaisesMessage(ProtectedSourceError, "source_reader_unregistered"),
        ):
            derive_registered_source("synthetic-port-source")

    def test_the_reconciliation_profile_is_owned_by_the_reader_not_the_registry(self) -> None:
        self._register()
        registry = self._registry(reconciliation_profile="borrowed-profile-v1")
        with (
            override_settings(HISTORICAL_REGISTRATION_SOURCES=registry),
            self.assertRaisesMessage(ProtectedSourceError, "source_registry_invalid"),
        ):
            derive_registered_source("synthetic-port-source")

        synthetic = self._registry(reconciliation_profile="synthetic")
        with override_settings(
            HISTORICAL_REGISTRATION_SOURCES=synthetic,
            HISTORICAL_REGISTRATION_ALLOW_SYNTHETIC_PROFILE=True,
        ):
            derive_registered_source("synthetic-port-source")
        self.assertIs(self.calls[0]["enforce_pinned_reconciliation"], False)

        with (
            override_settings(
                HISTORICAL_REGISTRATION_SOURCES=synthetic,
                HISTORICAL_REGISTRATION_ALLOW_SYNTHETIC_PROFILE=False,
            ),
            self.assertRaisesMessage(ProtectedSourceError, "source_registry_invalid"),
        ):
            derive_registered_source("synthetic-port-source")

    def test_an_unknown_provider_is_rejected_before_any_reader_lookup(self) -> None:
        registry = self._registry(provider="not-a-stored-provider")
        with (
            override_settings(HISTORICAL_REGISTRATION_SOURCES=registry),
            self.assertRaisesMessage(ProtectedSourceError, "source_registry_invalid"),
        ):
            derive_registered_source("synthetic-port-source")

        with self.assertRaisesMessage(ProtectedSourceError, "source_reader_provider_invalid"):
            register_source_reader(
                SourceReader(
                    provider="not-a-stored-provider",
                    reconciliation_profile=PROFILE,
                    read=lambda path, **arguments: derived_source(),
                )
            )

    def test_the_studio_picker_labels_a_source_with_no_registered_reader(self) -> None:
        """The picker lists the configured registry, which is the same everywhere."""

        with override_settings(HISTORICAL_REGISTRATION_SOURCES=self._registry()):
            options = registered_source_options()

        self.assertEqual(len(options), 1)
        self.assertEqual(options[0]["label"], "Luma historical registration source")
