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
    assert source.course.description_source_path == "SITE.md"
    assert source.course.description is not None
    assert source.course.description.startswith("A free course about building")
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


def test_schema_one_course_manifest_accepts_optional_family_landing_content() -> None:
    snapshot = fixture_snapshot()
    replace_bytes(
        snapshot,
        "course.yaml",
        b"outcome: Build, evaluate, and monitor production-style LLM applications.\n",
        (
            b"outcome: Build, evaluate, and monitor production-style LLM applications.\n"
            b"starting_point: You have a document question to answer.\n"
            b"prerequisites: You can write Python.\n"
            b"progression:\n"
            b"  - heading: I have a question\n"
            b"    description: I want to ground an answer in my documents.\n"
            b"  - heading: I connect the system\n"
            b"    description: I build retrieval and evaluation into the workflow.\n"
            b"  - heading: I ship the application\n"
            b"    description: I publish a useful project.\n"
            b"homework_summaries:\n"
            b"  - slug: hw1\n"
            b"    summary: Build the first working retrieval flow.\n"
        ),
    )

    source = parse_course_repository(snapshot, commit_sha=COMMIT_SHA)

    assert source.course.starting_point == "You have a document question to answer."
    assert source.course.prerequisites == "You can write Python."
    assert [step.heading for step in source.course.progression or ()] == [
        "I have a question",
        "I connect the system",
        "I ship the application",
    ]
    assert [item.slug for item in source.course.homework_summaries] == ["hw1"]
    assert source.course.homework_summaries[0].summary == (
        "Build the first working retrieval flow."
    )


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
        b"hashtag: &shared llmzoomcamp\noutcome: *shared\n",
        b"hashtag: !unsafe llmzoomcamp\n",
        b"hashtag: !!str llmzoomcamp\n",
    ],
)
def test_rejects_yaml_aliases_and_explicit_tags(yaml_payload: bytes) -> None:
    snapshot = fixture_snapshot()
    path = "course.yaml"
    replace_bytes(snapshot, path, b"hashtag: llmzoomcamp\n", yaml_payload)

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


def test_reads_the_course_description_from_site_md() -> None:
    snapshot = fixture_snapshot()
    snapshot["SITE.md"] = b"  Catalogue copy the repository publishes for the website.  \n"

    source = parse_course_repository(snapshot, commit_sha=COMMIT_SHA)

    assert source.course.description == "Catalogue copy the repository publishes for the website."
    assert source.course.description_source_path == "SITE.md"


def test_leaves_the_description_unset_when_the_repository_publishes_no_site_md() -> None:
    """A repository without ``SITE.md`` must not describe any website copy.

    The importer reads ``None`` as "leave the stored description alone", which is what
    protects the families whose catalogue text is curated rather than repository-authored.
    """

    snapshot = fixture_snapshot()
    del snapshot["SITE.md"]

    source = parse_course_repository(snapshot, commit_sha=COMMIT_SHA)

    assert source.course.description is None
    assert source.course.description_source_path is None


def test_never_falls_back_to_the_readme_for_the_description() -> None:
    snapshot = fixture_snapshot()
    del snapshot["SITE.md"]
    snapshot["README.md"] = b"# Banner\n\nReadme prose that must never reach the catalogue.\n"

    source = parse_course_repository(snapshot, commit_sha=COMMIT_SHA)

    assert source.course.description is None


@pytest.mark.parametrize(
    "payload",
    [
        b'<p align="center">\n  <img src="images/banner.png">\n</p>\n\nProse.\n',
        b'# Course\n\n<img src="logo.png" width="120">\n',
        b"[![Build](https://img.shields.io/badge/b.svg)](https://example.com)\n",
        b"![badge](https://img.shields.io/badge/x.svg)\n",
        b"Real copy.\n\n## Contributors\n\nThanks to everyone.\n",
    ],
)
def test_rejects_readme_shaped_site_descriptions(payload: bytes) -> None:
    snapshot = fixture_snapshot()
    snapshot["SITE.md"] = payload

    with pytest.raises(CourseRepositoryValidationError) as raised:
        parse_course_repository(snapshot)

    assert diagnostic_code(raised) == "site_description_readme_shaped"


def test_rejects_a_site_description_longer_than_the_ceiling() -> None:
    snapshot = fixture_snapshot()
    snapshot["SITE.md"] = b"a" * 4_001

    with pytest.raises(CourseRepositoryValidationError) as raised:
        parse_course_repository(snapshot)

    assert diagnostic_code(raised) == "site_description_too_long"


def test_rejects_an_empty_site_description() -> None:
    snapshot = fixture_snapshot()
    snapshot["SITE.md"] = b"   \n\n"

    with pytest.raises(CourseRepositoryValidationError) as raised:
        parse_course_repository(snapshot)

    assert diagnostic_code(raised) == "site_description_empty"


def test_accepts_a_course_yaml_that_declares_description_path_site_md() -> None:
    """Three repositories declare the pointer explicitly; it must stay valid.

    The declaration is redundant with the default, not an alternative to it, so a
    repository may keep or drop it without changing what is read.
    """

    snapshot = fixture_snapshot()
    snapshot["course.yaml"] = snapshot["course.yaml"].replace(
        b"title: LLM Zoomcamp\n",
        b"title: LLM Zoomcamp\ndescription_path: SITE.md\n",
    )

    source = parse_course_repository(snapshot, commit_sha=COMMIT_SHA)

    assert source.course.description_source_path == "SITE.md"
    assert source.course.description is not None


def test_refuses_a_description_path_that_names_anything_but_site_md() -> None:
    snapshot = fixture_snapshot()
    snapshot["course.yaml"] = snapshot["course.yaml"].replace(
        b"title: LLM Zoomcamp\n",
        b"title: LLM Zoomcamp\ndescription_path: README.md\n",
    )

    with pytest.raises(CourseRepositoryValidationError) as raised:
        parse_course_repository(snapshot)

    assert diagnostic_code(raised) == "course_description_path_not_site_md"


SHARED_FIXTURE_ROOT = (
    Path(__file__).parent / "fixtures" / "course_repository" / "llm_zoomcamp_shared"
)


def shared_snapshot() -> dict[str, bytes]:
    """The schema-2 shared-curriculum repository, one file tree."""

    return {
        path.relative_to(SHARED_FIXTURE_ROOT).as_posix(): path.read_bytes()
        for path in SHARED_FIXTURE_ROOT.rglob("*")
        if path.is_file()
    }


_CATALOG_BLOCK = (
    b"catalog:\n"
    b"  editions:\n"
    b"    - slug: llm-zoomcamp-2026\n"
    b"      finished: false\n"
    b"      homework_count: 5\n"
    b"      project_count: 3\n"
    b'      first_deadline: "2026-02-02T23:59:59+00:00"\n'
    b'      last_deadline: "2026-05-25T23:59:59+00:00"\n'
)


def test_a_schema_two_repository_may_declare_a_catalog_block() -> None:
    """The catalogue copy is authored content; the ingest accepts it.

    The block is validated with the shared rule set and then left to the
    content sync parser -- the curriculum importer carries no catalogue copy
    -- so a repository that declares one parses exactly as before.
    """

    snapshot = shared_snapshot()
    snapshot["course.yaml"] += _CATALOG_BLOCK

    source = parse_course_repository(snapshot, commit_sha=COMMIT_SHA)

    assert source.course.slug == "llm-zoomcamp"


def test_a_malformed_catalog_block_fails_the_ingest_with_the_shared_code() -> None:
    snapshot = shared_snapshot()
    snapshot["course.yaml"] += _CATALOG_BLOCK.replace(
        b"llm-zoomcamp-2026", b"llm-zoomcamp-self-paced"
    )

    with pytest.raises(CourseRepositoryValidationError) as raised:
        parse_course_repository(snapshot, commit_sha=COMMIT_SHA)

    assert diagnostic_code(raised) == "course_edition_slug_rejected"
