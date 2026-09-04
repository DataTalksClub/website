from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _target_block(target: str) -> tuple[str, str]:
    lines = (ROOT / "Makefile").read_text(encoding="utf-8").splitlines()
    header = re.compile(rf"^{re.escape(target)}:(?P<prerequisites>.*)$")
    for index, line in enumerate(lines):
        match = header.match(line)
        if match is None:
            continue
        body: list[str] = []
        for recipe in lines[index + 1 :]:
            if not recipe.startswith(("\t", " ")):
                break
            body.append(recipe)
        return match.group("prerequisites").strip(), "\n".join(body)
    raise AssertionError(f"Makefile target {target!r} is missing")


def test_local_test_target_runs_the_full_django_suite() -> None:
    prerequisites, body = _target_block("test")

    assert prerequisites.split() == ["test-django-full"]
    assert body == ""


def test_ci_full_django_target_has_no_prerequisite() -> None:
    prerequisites, body = _target_block("test-django-full")

    assert prerequisites == ""
    assert "manage.py test --parallel --noinput" in body
