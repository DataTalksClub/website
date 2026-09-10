from __future__ import annotations

import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _targets() -> set[str]:
    target_pattern = re.compile(r"^([A-Za-z0-9_.-]+):")
    return {
        match.group(1)
        for line in (ROOT / "Makefile").read_text(encoding="utf-8").splitlines()
        if not line.startswith(".PHONY:")
        if (match := target_pattern.match(line)) is not None
    }


def test_makefile_only_exposes_the_local_development_entry_points() -> None:
    makefile = (ROOT / "Makefile").read_text(encoding="utf-8")

    assert makefile.splitlines()[0] == ".PHONY: run migrate data"
    assert _targets() == {"run", "migrate", "data"}


def test_makefile_entry_points_delegate_to_the_expected_commands() -> None:
    result = subprocess.run(
        ["make", "-n", "run", "migrate", "data"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )

    assert result.stdout.splitlines() == [
        "uv run python manage.py runserver 0.0.0.0:8000",
        "uv run python manage.py migrate",
        "uv run python scripts/seed_local_data.py",
    ]
