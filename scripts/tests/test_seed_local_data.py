from __future__ import annotations

import os

import pytest

from scripts.seed_local_data import SEED_COMMANDS, configure_local_environment


def test_local_seed_commands_are_local_only() -> None:
    assert SEED_COMMANDS == (
        "seed_local_courses",
        "seed_local_questions",
        "seed_local_social_providers",
    )


def test_local_environment_is_selected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DTC_ENVIRONMENT", raising=False)
    monkeypatch.delenv("DJANGO_SETTINGS_MODULE", raising=False)

    configure_local_environment()

    assert os.environ["DTC_ENVIRONMENT"] == "local"
    assert os.environ["DJANGO_SETTINGS_MODULE"] == "website.settings.local"


def test_local_seed_refuses_a_different_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DTC_ENVIRONMENT", "production")

    with pytest.raises(RuntimeError, match="DTC_ENVIRONMENT=local"):
        configure_local_environment()
