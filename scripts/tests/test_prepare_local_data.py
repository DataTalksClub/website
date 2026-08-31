from __future__ import annotations

import pytest

from scripts.prepare_local_data import LocalPreparationError, _local_database_path


def test_local_database_path_stays_under_the_repository_tmp_directory() -> None:
    path = _local_database_path(".tmp/rehearsal.sqlite3")

    assert path.name == "rehearsal.sqlite3"
    assert path.suffix == ".sqlite3"


@pytest.mark.parametrize("value", ("/tmp/rehearsal.sqlite3", ".tmp/rehearsal.db"))
def test_local_database_path_rejects_unsafe_targets(value: str) -> None:
    with pytest.raises(LocalPreparationError):
        _local_database_path(value)
