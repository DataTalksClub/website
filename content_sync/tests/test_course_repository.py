from __future__ import annotations

from dataclasses import FrozenInstanceError, replace
from pathlib import Path

import pytest

from content_sync.course_repository import (
    CourseRepositoryLimits,
    CourseRepositoryValidationError,
    ModuleFlowSource,
    ProjectFlowSource,
    parse_course_repository,
)

FIXTURE_ROOT = Path(__file__).parent / "fixtures" / "course_repository" / "llm_zoomcamp_2026"
COMMIT_SHA = "a" * 40


def fixture_snapshot() -> dict[str, bytes]:
    return {
        path.relative_to(FIXTURE_ROOT).as_posix(): path.read_bytes()
        for path in FIXTURE_ROOT.rglob("*")
        if path.is_file()
    }


def replace_bytes(snapshot: dict[str, bytes], path: str, old: bytes, new: bytes) -> None:
    assert old in snapshot[path]
    snapshot[path] = snapshot[path].replace(old, new, 1)


def diagnostic_code(error: pytest.ExceptionInfo[CourseRepositoryValidationError]) -> str:
    return error.value.diagnostics[0].code


def test_parses_llm_zoomcamp_modules_and_legacy_cohorts_without_database() -> None:
    source = parse_course_repository(fixture_snapshot(), commit_sha=COMMIT_SHA)

    assert source.commit_sha == COMMIT_SHA
    assert source.course.slug == "llm-zoomcamp"
    assert source.course.description_source_path == "README.md"
    assert [cohort.identifier for cohort in source.cohorts] == ["2024", "2025", "2026"]

    implicit_legacy, explicit_legacy, modules_cohort = source.cohorts
    assert implicit_legacy.format == "legacy"
    assert implicit_legacy.is_implicit_legacy is True
    assert implicit_legacy.source_path is None
    assert explicit_legacy.format == "legacy"
    assert explicit_legacy.is_implicit_legacy is False
    assert explicit_legacy.flow == ()

    assert modules_cohort.format == "modules"
    module_item, project_item = modules_cohort.flow
    assert isinstance(module_item, ModuleFlowSource)
    assert module_item.module.slug == "01-agentic-rag"
    assert [unit.source_path for unit in module_item.module.units] == [
        "cohorts/2026/01-agentic-rag/lessons/01-intro.md",
        "cohorts/2026/01-agentic-rag/lessons/02-environment.md",
    ]
    assert [unit.slug for unit in module_item.module.units] == ["01-intro", "02-environment"]
    intro = module_item.module.units[0]
    assert intro.markdown == "# Introduction\n\nThe first lesson in the Agentic RAG module.\n"
    assert intro.metadata.video_url == "https://www.youtube.com/watch?v=fixture-intro"
    assert [(source.label, source.source_path) for source in intro.metadata.code] == [
        ("notebook.ipynb", "cohorts/2026/01-agentic-rag/code/notebook.ipynb")
    ]
    assert module_item.homework.source_path == "cohorts/2026/01-agentic-rag/homework.yaml"
    assert isinstance(project_item, ProjectFlowSource)
    assert project_item.slug == "project-01"

    answer = module_item.homework.questions[0].answer
    assert answer is not None
    assert answer["algorithm"] == "A256GCM"
    with pytest.raises(TypeError):
        answer["algorithm"] = "plaintext"  # type: ignore[index]
    with pytest.raises(FrozenInstanceError):
        source.course.slug = "changed"  # type: ignore[misc]


def test_rejects_non_youtube_lesson_video_url() -> None:
    snapshot = fixture_snapshot()
    path = "cohorts/2026/01-agentic-rag/lessons/01-intro.md"
    replace_bytes(
        snapshot,
        path,
        b"https://www.youtube.com/watch?v=fixture-intro",
        b"https://example.com/video",
    )

    with pytest.raises(CourseRepositoryValidationError) as raised:
        parse_course_repository(snapshot)

    assert diagnostic_code(raised) == "lesson_video_url_invalid"


def test_rejects_unknown_lesson_frontmatter_keys() -> None:
    snapshot = fixture_snapshot()
    path = "cohorts/2026/01-agentic-rag/lessons/01-intro.md"
    replace_bytes(snapshot, path, b"video_url:", b"published: true\nvideo_url:")

    with pytest.raises(CourseRepositoryValidationError) as raised:
        parse_course_repository(snapshot)

    assert diagnostic_code(raised) == "unknown_key"


def test_accepts_an_arbitrary_slug_like_cohort_identifier() -> None:
    snapshot = fixture_snapshot()
    old_prefix = "cohorts/2025/"
    for path in tuple(snapshot):
        if path.startswith(old_prefix):
            snapshot[path.replace(old_prefix, "cohorts/spring-2027/", 1)] = snapshot.pop(path)
    path = "cohorts/spring-2027/cohort.yaml"
    replace_bytes(snapshot, path, b'identifier: "2025"', b"identifier: spring-2027")
    replace_bytes(snapshot, path, b"year: 2025", b"year: 2027")
    replace_bytes(snapshot, path, b"llm-zoomcamp-2025", b"llm-zoomcamp-spring-2027")

    source = parse_course_repository(snapshot)

    cohort = next(cohort for cohort in source.cohorts if cohort.identifier == "spring-2027")
    assert cohort.year == 2027
    assert cohort.format == "legacy"


@pytest.mark.parametrize(
    ("bad_path", "code"),
    [
        ("../course.yaml", "invalid_repository_path"),
        ("/course.yaml", "invalid_repository_path"),
        ("cohorts\\2026\\cohort.yaml", "invalid_repository_path"),
        ("https://example.com/course.yaml", "invalid_repository_path"),
    ],
)
def test_rejects_non_posix_or_escaping_snapshot_paths(bad_path: str, code: str) -> None:
    snapshot = fixture_snapshot()
    snapshot[bad_path] = b"content"

    with pytest.raises(CourseRepositoryValidationError) as raised:
        parse_course_repository(snapshot)

    assert diagnostic_code(raised) == code


def test_rejects_escaping_source_references() -> None:
    snapshot = fixture_snapshot()
    path = "cohorts/2026/01-agentic-rag/module.yaml"
    replace_bytes(snapshot, path, b"lessons/01-intro.md", b"../README.md")

    with pytest.raises(CourseRepositoryValidationError) as raised:
        parse_course_repository(snapshot)

    assert diagnostic_code(raised) == "invalid_repository_path"


def test_rejects_contract_manifests_outside_their_prescribed_locations() -> None:
    snapshot = fixture_snapshot()
    snapshot["cohorts/2026/nested/cohort.yaml"] = snapshot["cohorts/2026/cohort.yaml"]

    with pytest.raises(CourseRepositoryValidationError) as raised:
        parse_course_repository(snapshot)

    assert diagnostic_code(raised) == "manifest_path_invalid"


def test_rejects_unknown_keys_with_a_bounded_pointer() -> None:
    snapshot = fixture_snapshot()
    path = "course.yaml"
    snapshot[path] += b"unexpected: true\n"

    with pytest.raises(CourseRepositoryValidationError) as raised:
        parse_course_repository(snapshot)

    diagnostic = raised.value.diagnostics[0]
    assert diagnostic.code == "unknown_key"
    assert diagnostic.source_path == path
    assert diagnostic.pointer == "/unexpected"
    assert len(raised.value.diagnostics) == 1


@pytest.mark.parametrize(
    "yaml_payload",
    [
        b"description: &shared copied\noutcome: *shared\n",
        b"description: !unsafe copied\n",
        b"description: !!str copied\n",
    ],
)
def test_rejects_yaml_aliases_and_explicit_tags(yaml_payload: bytes) -> None:
    snapshot = fixture_snapshot()
    path = "course.yaml"
    replace_bytes(snapshot, path, b"description_path: README.md\n", yaml_payload)

    with pytest.raises(CourseRepositoryValidationError) as raised:
        parse_course_repository(snapshot)

    assert diagnostic_code(raised) == "invalid_or_unsafe_yaml"


def test_rejects_duplicate_yaml_mapping_keys() -> None:
    snapshot = fixture_snapshot()
    snapshot["course.yaml"] += b"title: A second title\n"

    with pytest.raises(CourseRepositoryValidationError) as raised:
        parse_course_repository(snapshot)

    assert diagnostic_code(raised) == "invalid_or_unsafe_yaml"


@pytest.mark.parametrize(
    ("old", "new", "code"),
    [
        (
            b"  - project: project-01\n",
            b"  - project: project-01\n  - project: project-01\n",
            "duplicate_project_reference",
        ),
        (
            b"  - project: project-01\n",
            (
                b"  - module:\n"
                b"      source: cohorts/2026/01-agentic-rag/module.yaml\n"
                b"      homework: cohorts/2026/01-agentic-rag/homework.yaml\n"
            ),
            "duplicate_module_or_homework_reference",
        ),
    ],
)
def test_rejects_duplicate_flow_references(old: bytes, new: bytes, code: str) -> None:
    snapshot = fixture_snapshot()
    path = "cohorts/2026/cohort.yaml"
    replace_bytes(snapshot, path, old, new)

    with pytest.raises(CourseRepositoryValidationError) as raised:
        parse_course_repository(snapshot)

    assert diagnostic_code(raised) == code


def test_rejects_a_module_without_its_terminal_homework_reference() -> None:
    snapshot = fixture_snapshot()
    path = "cohorts/2026/cohort.yaml"
    replace_bytes(
        snapshot,
        path,
        b"      homework: cohorts/2026/01-agentic-rag/homework.yaml\n",
        b"",
    )

    with pytest.raises(CourseRepositoryValidationError) as raised:
        parse_course_repository(snapshot)

    assert diagnostic_code(raised) == "required_key_missing"
    assert raised.value.diagnostics[0].pointer.endswith("/homework")


def test_rejects_broken_cross_references() -> None:
    snapshot = fixture_snapshot()
    path = "cohorts/2026/cohort.yaml"
    replace_bytes(
        snapshot,
        path,
        b"cohorts/2026/01-agentic-rag/module.yaml",
        b"cohorts/2026/02-missing/module.yaml",
    )

    with pytest.raises(CourseRepositoryValidationError) as raised:
        parse_course_repository(snapshot)

    assert diagnostic_code(raised) == "source_path_missing"


@pytest.mark.parametrize("plaintext_key", ["correct_answer", "answer_value", "solution"])
def test_rejects_plaintext_answer_keys(plaintext_key: str) -> None:
    snapshot = fixture_snapshot()
    path = "cohorts/2026/01-agentic-rag/homework.yaml"
    replace_bytes(
        snapshot,
        path,
        b"    answer:\n",
        f"    {plaintext_key}: pages-24\n    answer:\n".encode(),
    )

    with pytest.raises(CourseRepositoryValidationError) as raised:
        parse_course_repository(snapshot)

    assert diagnostic_code(raised) == "plaintext_answer_not_allowed"


def test_rejects_an_answer_envelope_copied_to_a_different_question_context() -> None:
    snapshot = fixture_snapshot()
    path = "cohorts/2026/01-agentic-rag/homework.yaml"
    replace_bytes(snapshot, path, b"id: lesson-page-count", b"id: renamed-question")

    with pytest.raises(CourseRepositoryValidationError) as raised:
        parse_course_repository(snapshot)

    assert diagnostic_code(raised) == "answer_context_mismatch"


@pytest.mark.parametrize(
    ("old", "new", "code"),
    [
        (
            b"32222222-2222-4222-8222-222222222222",
            b"31111111-1111-4111-8111-111111111111",
            "duplicate_unit_id_or_slug",
        ),
        (
            b"lessons/02-environment.md",
            b"lessons/01-intro.md",
            "duplicate_unit_id_or_slug",
        ),
    ],
)
def test_rejects_duplicate_stable_content_ids_slugs_and_sources(
    old: bytes, new: bytes, code: str
) -> None:
    snapshot = fixture_snapshot()
    path = "cohorts/2026/01-agentic-rag/module.yaml"
    replace_bytes(snapshot, path, old, new)

    with pytest.raises(CourseRepositoryValidationError) as raised:
        parse_course_repository(snapshot)

    assert diagnostic_code(raised) == code


@pytest.mark.parametrize(
    ("limits", "code"),
    [
        (replace(CourseRepositoryLimits(), max_files=1), "file_count_limit_exceeded"),
        (replace(CourseRepositoryLimits(), max_total_bytes=10), "repository_size_limit_exceeded"),
        (replace(CourseRepositoryLimits(), max_file_bytes=10), "file_size_limit_exceeded"),
        (replace(CourseRepositoryLimits(), max_yaml_nodes=4), "invalid_or_unsafe_yaml"),
        (replace(CourseRepositoryLimits(), max_string_chars=10), "string_size_limit_exceeded"),
        (replace(CourseRepositoryLimits(), max_list_items=1), "list_size_limit_exceeded"),
    ],
)
def test_enforces_bounded_repository_and_yaml_limits(
    limits: CourseRepositoryLimits, code: str
) -> None:
    with pytest.raises(CourseRepositoryValidationError) as raised:
        parse_course_repository(fixture_snapshot(), limits=limits)

    assert diagnostic_code(raised) == code


def test_rejects_invalid_optional_commit_sha() -> None:
    with pytest.raises(CourseRepositoryValidationError) as raised:
        parse_course_repository(fixture_snapshot(), commit_sha="main")

    assert diagnostic_code(raised) == "source_commit_invalid"
