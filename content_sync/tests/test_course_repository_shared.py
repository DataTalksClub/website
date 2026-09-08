"""Schema-2 shared-curriculum parser, layout, and release-gate tests.

The versioned fixture under ``fixtures/course_repository/llm_zoomcamp_shared/``
is the single known-output contract: the parser tests, the layout report, and
the ``verify_course_repository_curriculum`` script all consume the same tree,
and ``expected-v2.json`` is its normalized output.  It is test data only.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from content_sync.course_repository import (
    DEFAULT_LIMITS,
    CourseRepositoryValidationError,
    parse_course_repository,
)
from content_sync.course_repository import (
    PARSER_VERSION as V1_PARSER_VERSION,
)
from content_sync.course_repository import (
    SCHEMA_VERSION as V1_SCHEMA_VERSION,
)
from content_sync.course_repository_layout import (
    CourseRepositoryLayoutError,
    build_layout_report,
    check_root_modules,
)

FIXTURE_ROOT = (
    Path(__file__).parent / "fixtures" / "course_repository" / "llm_zoomcamp_shared"
)
V1_FIXTURE_ROOT = (
    Path(__file__).parent / "fixtures" / "course_repository" / "llm_zoomcamp_2026"
)
COMMIT_SHA = "b" * 40
SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "verify_course_repository_curriculum.py"


def shared_snapshot() -> dict[str, bytes]:
    return {
        path.relative_to(FIXTURE_ROOT).as_posix(): path.read_bytes()
        for path in FIXTURE_ROOT.rglob("*")
        if path.is_file()
    }


def v1_snapshot() -> dict[str, bytes]:
    return {
        path.relative_to(V1_FIXTURE_ROOT).as_posix(): path.read_bytes()
        for path in V1_FIXTURE_ROOT.rglob("*")
        if path.is_file()
    }


def replace_bytes(
    snapshot: dict[str, bytes], path: str, old: str, new: str
) -> None:
    text = snapshot[path].decode("utf-8")
    assert old in text, (path, old)
    snapshot[path] = text.replace(old, new, 1).encode("utf-8")


def diagnostic_code(
    error: pytest.ExceptionInfo[CourseRepositoryValidationError],
) -> str:
    return error.value.diagnostics[0].code


def expected_fixture() -> dict[str, object]:
    return json.loads((FIXTURE_ROOT / "expected-v2.json").read_text(encoding="utf-8"))


def layout_payload(snapshot: dict[str, bytes]) -> dict[str, object]:
    parsed = parse_course_repository(snapshot, commit_sha=COMMIT_SHA)
    return build_layout_report(parsed, snapshot).as_dict()


# -- happy path -------------------------------------------------------------


def test_v1_fixture_still_dispatches_to_the_v1_parser() -> None:
    source = parse_course_repository(v1_snapshot(), commit_sha=COMMIT_SHA)

    assert source.schema_version == V1_SCHEMA_VERSION
    assert source.parser_version == V1_PARSER_VERSION
    assert [cohort.identifier for cohort in source.cohorts] == ["2024", "2025", "2026"]


def test_shared_fixture_parses_to_the_expected_known_output() -> None:
    snapshot = shared_snapshot()
    payload = layout_payload(snapshot)
    expected = expected_fixture()

    assert payload["schema_version"] == 2
    assert payload["course"] == expected["course"]
    assert payload["archive_module_records"] == 0

    modules = payload["modules"]
    assert len(modules) == 1
    module = modules[0]
    assert module["scope"] == "shared"
    assert module["content_id"] == expected["modules"][0]["content_id"]
    assert module["slug"] == "01-agentic-rag"
    assert [lesson["slug"] for lesson in module["lessons"]] == ["01-lesson", "02-practice"]
    assert module["overview_path"] == "01-agentic-rag/README.md"

    actual_cohorts = {cohort["identifier"]: cohort for cohort in payload["cohorts"]}
    expected_cohorts = {cohort["identifier"]: cohort for cohort in expected["cohorts"]}
    assert set(actual_cohorts) == {"2026", "self-paced", "2025"}
    assert actual_cohorts["2026"] == expected_cohorts["2026"]
    assert actual_cohorts["self-paced"] == expected_cohorts["self-paced"]
    assert actual_cohorts["2025"] == expected_cohorts["2025"]


def test_both_current_deliveries_share_one_module_identity() -> None:
    source = parse_course_repository(shared_snapshot(), commit_sha=COMMIT_SHA)

    # The live and self-paced cohorts never duplicate the shared graph: there
    # is exactly one module row identity and it is registered once.
    shared_ids = [module.content_id for module in source.modules]
    assert shared_ids == ["22222222-2222-4222-8222-222222222222"]
    assert {cohort.curriculum for cohort in source.cohorts} == {
        "current",
        "github_archive",
    }


def test_archive_descendants_stay_opaque_but_mapped_homework_imports() -> None:
    snapshot = shared_snapshot()
    source = parse_course_repository(snapshot, commit_sha=COMMIT_SHA)

    archive_ids = {module.content_id for module in source.modules}
    # The archive module manifest reuses the current module's ID; opacity means
    # it is never loaded or registered, so the count above stays one and no
    # duplicate_content_id fires.
    assert archive_ids == {"22222222-2222-4222-8222-222222222222"}

    archive_cohort = next(
        cohort for cohort in source.cohorts if cohort.identifier == "2025"
    )
    assert archive_cohort.archive_notice_path == "cohorts/2025/README.md"
    assert [
        (binding.module, binding.source) for binding in archive_cohort.homework_bindings
    ] == [(None, "cohorts/2025/01-old-module/homework.yaml")]
    # Exactly one archive homework record is admitted, by explicit mapping.
    assert [
        homework.source_path
        for homework in source.homeworks
        if homework.source_path.startswith("cohorts/2025/")
    ] == ["cohorts/2025/01-old-module/homework.yaml"]


# -- refusals ----------------------------------------------------------------


def test_rejects_self_paced_cohort_with_authored_assignment() -> None:
    snapshot = shared_snapshot()
    snapshot["cohorts/self-paced/homework/01-agentic-rag/homework.yaml"] = snapshot[
        "cohorts/2026/homework/01-agentic-rag/homework.yaml"
    ]
    snapshot["cohorts/self-paced/cohort.yaml"] = snapshot[
        "cohorts/self-paced/cohort.yaml"
    ].replace(
        b"homework: []",
        (
            b"homework:\n"
            b"  - module: 01-agentic-rag\n"
            b"    source: cohorts/self-paced/homework/01-agentic-rag/homework.yaml"
        ),
    )

    with pytest.raises(CourseRepositoryValidationError) as raised:
        parse_course_repository(snapshot)

    assert diagnostic_code(raised) == "self_paced_homework_rejected"


def test_rejects_archive_homework_bound_to_a_module() -> None:
    snapshot = shared_snapshot()
    replace_bytes(
        snapshot,
        "cohorts/2025/cohort.yaml",
        "module: null",
        "module: 01-agentic-rag",
    )

    with pytest.raises(CourseRepositoryValidationError) as raised:
        parse_course_repository(snapshot)

    assert diagnostic_code(raised) == "archive_module_reference"


def test_rejects_current_homework_mapping_to_an_unknown_module() -> None:
    snapshot = shared_snapshot()
    replace_bytes(
        snapshot,
        "cohorts/2026/cohort.yaml",
        "module: 01-agentic-rag",
        "module: 99-unknown",
    )

    with pytest.raises(CourseRepositoryValidationError) as raised:
        parse_course_repository(snapshot)

    assert diagnostic_code(raised) == "curriculum_source_mismatch"


def test_rejects_current_homework_outside_the_cohort_shape() -> None:
    snapshot = shared_snapshot()
    replace_bytes(
        snapshot,
        "cohorts/2026/cohort.yaml",
        "source: cohorts/2026/homework/01-agentic-rag/homework.yaml",
        "source: cohorts/self-paced/homework/01-agentic-rag/homework.yaml",
    )

    with pytest.raises(CourseRepositoryValidationError) as raised:
        parse_course_repository(snapshot)

    assert diagnostic_code(raised) == "homework_path_outside_cohort"


def test_rejects_unreferenced_current_homework(tmp_path: Path) -> None:
    snapshot = shared_snapshot()
    stray = "cohorts/2026/homework/01-agentic-rag/stray/homework.yaml"
    snapshot[stray] = snapshot["cohorts/2026/homework/01-agentic-rag/homework.yaml"]

    with pytest.raises(CourseRepositoryValidationError) as raised:
        parse_course_repository(snapshot)

    assert diagnostic_code(raised) == "homework_unreferenced"


def test_rejects_a_v1_cohort_manifest_inside_a_v2_source() -> None:
    snapshot = shared_snapshot()
    replace_bytes(
        snapshot,
        "cohorts/2026/cohort.yaml",
        "schema_version: 2",
        "schema_version: 1",
    )

    with pytest.raises(CourseRepositoryValidationError) as raised:
        parse_course_repository(snapshot)

    assert diagnostic_code(raised) == "v2_mixed_version"


def test_rejects_a_module_manifest_inside_a_current_cohort() -> None:
    snapshot = shared_snapshot()
    snapshot["cohorts/2026/01-private/module.yaml"] = snapshot[
        "01-agentic-rag/module.yaml"
    ]

    with pytest.raises(CourseRepositoryValidationError) as raised:
        parse_course_repository(snapshot)

    assert diagnostic_code(raised) == "v2_mixed_version"


def test_rejects_an_unnumbered_root_module() -> None:
    snapshot = shared_snapshot()
    for path in list(snapshot):
        if path.startswith("01-agentic-rag/"):
            snapshot["intro-module/" + path.removeprefix("01-agentic-rag/")] = (
                snapshot.pop(path)
            )
    snapshot["course.yaml"] = snapshot["course.yaml"]  # unchanged

    with pytest.raises(CourseRepositoryValidationError) as raised:
        parse_course_repository(snapshot)

    assert diagnostic_code(raised) == "numbered_module_required"


def test_rejects_an_unnumbered_lesson_path() -> None:
    snapshot = shared_snapshot()
    replace_bytes(
        snapshot,
        "01-agentic-rag/module.yaml",
        "path: 02-practice.md",
        "path: practice.md",
    )

    with pytest.raises(CourseRepositoryValidationError) as raised:
        parse_course_repository(snapshot)

    assert diagnostic_code(raised) == "numbered_lesson_required"


def test_rejects_a_duplicate_module_number_prefix() -> None:
    snapshot = shared_snapshot()
    for path in [key for key in snapshot if key.startswith("01-agentic-rag/")]:
        moved = "01-duplicate/" + path.removeprefix("01-agentic-rag/")
        snapshot[moved] = snapshot[path].replace(b"01-agentic-rag", b"01-duplicate")

    with pytest.raises(CourseRepositoryValidationError) as raised:
        parse_course_repository(snapshot)

    assert diagnostic_code(raised) == "duplicate_number_prefix"


def test_rejects_a_duplicate_stable_id_across_modules() -> None:
    snapshot = shared_snapshot()
    snapshot["02-duplicate/module.yaml"] = snapshot["01-agentic-rag/module.yaml"].replace(
        b"01-agentic-rag", b"02-duplicate"
    )

    with pytest.raises(CourseRepositoryValidationError) as raised:
        parse_course_repository(snapshot)

    assert diagnostic_code(raised) == "duplicate_content_id"


def test_rejects_an_archive_without_its_notice() -> None:
    snapshot = shared_snapshot()
    snapshot["cohorts/2025/cohort.yaml"] = snapshot["cohorts/2025/cohort.yaml"].replace(
        b"""archive:
  notice_path: cohorts/2025/README.md
""",
        b"""archive:
  notice_path: cohorts/2025/MISSING.md
""",
    )

    with pytest.raises(CourseRepositoryValidationError) as raised:
        parse_course_repository(snapshot)

    assert diagnostic_code(raised) == "archive_notice_missing"


def test_rejects_a_current_cohort_declaring_archive_metadata() -> None:
    snapshot = shared_snapshot()
    snapshot["cohorts/2026/cohort.yaml"] = snapshot["cohorts/2026/cohort.yaml"].replace(
        b"homework:",
        b"""archive:
  notice_path: cohorts/2026/README.md
homework:""",
        1,
    )

    with pytest.raises(CourseRepositoryValidationError) as raised:
        parse_course_repository(snapshot)

    assert diagnostic_code(raised) == "archive_not_allowed"


def test_rejects_a_published_live_cohort_without_dates() -> None:
    snapshot = shared_snapshot()
    snapshot["cohorts/2026/cohort.yaml"] = snapshot["cohorts/2026/cohort.yaml"].replace(
        b'start_date: "2026-06-08"\n', b""
    )

    with pytest.raises(CourseRepositoryValidationError) as raised:
        parse_course_repository(snapshot)

    assert diagnostic_code(raised) == "published_live_dates_required"


def test_rejects_an_unknown_delivery_value() -> None:
    snapshot = shared_snapshot()
    replace_bytes(
        snapshot,
        "cohorts/2026/cohort.yaml",
        "delivery: live",
        "delivery: recorded",
    )

    with pytest.raises(CourseRepositoryValidationError) as raised:
        parse_course_repository(snapshot)

    assert diagnostic_code(raised) == "invalid_delivery"


def test_rejects_an_unknown_curriculum_value() -> None:
    snapshot = shared_snapshot()
    replace_bytes(
        snapshot,
        "cohorts/2026/cohort.yaml",
        "curriculum: current",
        "curriculum: snapshot",
    )

    with pytest.raises(CourseRepositoryValidationError) as raised:
        parse_course_repository(snapshot)

    assert diagnostic_code(raised) == "invalid_curriculum_kind"


def test_rejects_a_schema_2_source_without_root_modules() -> None:
    snapshot = shared_snapshot()
    for path in [key for key in snapshot if key.startswith("01-agentic-rag/")]:
        del snapshot[path]

    with pytest.raises(CourseRepositoryValidationError) as raised:
        parse_course_repository(snapshot)

    assert diagnostic_code(raised) == "current_module_missing"


def test_layout_gate_rejects_a_numbered_root_stub() -> None:
    snapshot = shared_snapshot()
    snapshot["02-empty/"] = b""  # directories are skipped; use a file inside
    del snapshot["02-empty/"]
    snapshot["02-empty/notes.md"] = b"stub\n"

    with pytest.raises(CourseRepositoryLayoutError) as raised:
        check_root_modules(snapshot)

    assert raised.value.code == "root_module_stub"


def test_both_transports_parse_the_shared_fixture_identically(tmp_path: Path) -> None:
    """The checkout tar and the prefixed codeload-shaped tar agree."""

    from content_sync.course_repository_ingest import (
        MAX_ARCHIVE_BYTES,
        read_course_repository_archive,
    )

    checkout = tmp_path / "course"
    subprocess.run(
        ["cp", "-r", str(FIXTURE_ROOT), str(checkout)], check=True
    )
    (checkout / "expected-v2.json").unlink()
    subprocess.run(
        ["git", "-C", str(checkout), "init", "--quiet"],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(checkout), "add", "--all"], check=True, capture_output=True
    )
    subprocess.run(
        ["git", "-C", str(checkout), "-c", "user.email=f@example.invalid",
         "-c", "user.name=F", "commit", "--quiet", "-m", "fixture"],
        check=True,
        capture_output=True,
    )
    commit_sha = subprocess.run(
        ["git", "-C", str(checkout), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    local_tar = subprocess.run(
        ["git", "-C", str(checkout), "archive", "--format=tar", commit_sha],
        check=True,
        capture_output=True,
    ).stdout
    prefixed_tar = subprocess.run(
        [
            "git",
            "-C",
            str(checkout),
            "archive",
            "--format=tar",
            f"--prefix=llm-zoomcamp-{commit_sha[:7]}/",
            commit_sha,
        ],
        check=True,
        capture_output=True,
    ).stdout

    from_checkout = read_course_repository_archive(
        local_tar, limits=DEFAULT_LIMITS, strip_root=False
    )
    # The prefixed tar is within the same shared compressed-archive ceiling.
    assert len(prefixed_tar) < MAX_ARCHIVE_BYTES
    from_codeload_shape = read_course_repository_archive(
        prefixed_tar, limits=DEFAULT_LIMITS, strip_root=True
    )
    assert from_checkout == from_codeload_shape

    checkout_source = parse_course_repository(from_checkout, commit_sha=commit_sha)
    codeload_source = parse_course_repository(
        from_codeload_shape, commit_sha=commit_sha
    )
    assert checkout_source == codeload_source
    assert checkout_source.schema_version == 2


# -- release-gate script ------------------------------------------------------


def _git_checkout(tmp_path: Path) -> tuple[Path, str]:
    checkout = tmp_path / "course"
    subprocess.run(
        ["cp", "-r", str(FIXTURE_ROOT), str(checkout)],
        check=True,
    )
    checkout.rename(tmp_path / "course-tree")
    checkout = tmp_path / "course-tree"
    (checkout / "expected-v2.json").unlink()
    def git(*arguments: str) -> str:
        return subprocess.run(
            ["git", *arguments],
            cwd=checkout,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()

    git("init", "-q")
    git("config", "user.email", "fixture@example.invalid")
    git("config", "user.name", "Fixture")
    git("add", "-A")
    git("commit", "-q", "-m", "fixture")
    return checkout, git("rev-parse", "HEAD")


@pytest.fixture()
def script_checkout(tmp_path: Path) -> tuple[Path, str]:
    return _git_checkout(tmp_path)


def test_script_help_is_deterministic(capsys: pytest.CaptureFixture[str]) -> None:
    from scripts.verify_course_repository_curriculum import main as script_main

    for _ in range(2):
        with pytest.raises(SystemExit) as raised:
            script_main(["--help"])
        assert raised.value.code == 0
        first = capsys.readouterr().out
    assert "--checkout" in first
    assert "--commit" in first


def test_script_accepts_the_fixture_commit_and_reports_the_contract(
    script_checkout: tuple[Path, str],
    tmp_path: Path,
) -> None:
    from scripts.verify_course_repository_curriculum import main as script_main

    checkout, commit_sha = script_checkout
    output = tmp_path / "report.json"
    expected_path = FIXTURE_ROOT / "expected-v2.json"
    exit_code = script_main(
        [
            "--checkout",
            str(checkout),
            "--commit",
            commit_sha,
            "--json",
            str(output),
            "--expect-json",
            str(expected_path),
        ]
    )

    assert exit_code == 0, output.read_text(encoding="utf-8") if output.exists() else ""
    report = json.loads(output.read_text(encoding="utf-8"))
    assert report["ok"] is True
    assert report["commit_sha"] == commit_sha
    assert report["archive_module_records"] == 0
    assert {cohort["identifier"] for cohort in report["cohorts"]} == {
        "2026",
        "self-paced",
        "2025",
    }


def test_script_rejects_a_drifted_checkout_against_the_expected_fixture(
    script_checkout: tuple[Path, str],
) -> None:
    from scripts.verify_course_repository_curriculum import main as script_main

    checkout, commit_sha = script_checkout
    module_manifest = checkout / "01-agentic-rag" / "module.yaml"
    module_manifest.write_text(
        module_manifest.read_text(encoding="utf-8")
        + """  - content_id: "37777777-7777-4777-8777-777777777777"
    title: Added Lesson
    path: 03-added.md
""",
        encoding="utf-8",
    )
    (checkout / "01-agentic-rag" / "03-added.md").write_text(
        "# Added\n", encoding="utf-8"
    )

    def git(*arguments: str) -> None:
        subprocess.run(
            ["git", *arguments], cwd=checkout, check=True, capture_output=True
        )

    git("add", "-A")
    git("commit", "-q", "-m", "drift")
    new_sha = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=checkout, check=True, capture_output=True, text=True
    ).stdout.strip()

    exit_code = script_main(
        [
            "--checkout",
            str(checkout),
            "--commit",
            new_sha,
            "--expect-json",
            str(FIXTURE_ROOT / "expected-v2.json"),
        ]
    )

    assert exit_code == 1


def test_script_refuses_a_branch_name_as_the_archive_identity() -> None:
    from scripts.verify_course_repository_curriculum import main as script_main

    assert script_main(["--checkout", ".", "--commit", "main"]) == 2
