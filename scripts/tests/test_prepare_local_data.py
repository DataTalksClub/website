from __future__ import annotations

import pytest
from django.test import SimpleTestCase

from scripts.prepare_local_data import (
    ORCHESTRATOR_SCHEMA_VERSION,
    LocalPreparationError,
    _local_database_path,
    _orchestrator_report,
)


def test_local_database_path_stays_under_the_repository_tmp_directory() -> None:
    path = _local_database_path(".tmp/rehearsal.sqlite3")

    assert path.name == "rehearsal.sqlite3"
    assert path.suffix == ".sqlite3"


@pytest.mark.parametrize("value", ("/tmp/rehearsal.sqlite3", ".tmp/rehearsal.db"))
def test_local_database_path_rejects_unsafe_targets(value: str) -> None:
    with pytest.raises(LocalPreparationError):
        _local_database_path(value)


class OrchestratorReportTests(SimpleTestCase):
    """The report carries the event stage's result key for key, not merged."""

    #: Report key -> `import_events.run()` result key. The two reviewed legs
    #: keep their report names; every other key carries over unchanged.
    EVENT_PIPELINE = {
        "identities": {"applied": 1},
        "new_event_identities": {"luma": {"created": 2}},
        "event_content": {"records": 3},
        "new_event_content": {"descriptions": 4},
        "registration_sources": {"providers": ["luma"]},
        "registration_import": {"staged": 5},
        "aggregate_auto_resolution": {"activated": 6},
        "activation_coverage": {"activated_share": 0.5},
    }
    REPORT_KEYS = {
        "event_identities": "identities",
        "new_event_identities": "new_event_identities",
        "event_content": "event_content",
        "new_event_content": "new_event_content",
        "registration_sources": "registration_sources",
        "registration_import": "registration_import",
        "aggregate_auto_resolution": "aggregate_auto_resolution",
        "activation_coverage": "activation_coverage",
    }

    def _report(self) -> dict:
        return _orchestrator_report(
            fresh=True,
            migrations={"applied": 0},
            catalog={"courses": 1},
            course_sources={"registered": 1},
            modules={"pulled": 1},
            cmp_content={"imported": False},
            editorial_content={"public_content": {"replayed": True}},
            event_pipeline=dict(self.EVENT_PIPELINE),
        )

    def test_every_event_result_is_exposed_under_its_own_name(self) -> None:
        """Provenance stays separable: an automatic discovery must not be
        readable as a reviewed manifest change or an activation."""
        report = self._report()
        for report_key, pipeline_key in self.REPORT_KEYS.items():
            self.assertEqual(report[report_key], self.EVENT_PIPELINE[pipeline_key])

    def test_the_course_and_editorial_steps_stay_under_steps(self) -> None:
        report = self._report()
        assert report["schema_version"] == ORCHESTRATOR_SCHEMA_VERSION
        assert report["steps"]["course_catalog"] == {"courses": 1}
        assert report["steps"]["editorial_content"] == {"public_content": {"replayed": True}}
        assert "event_identities" not in report["steps"]
