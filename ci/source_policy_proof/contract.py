"""D5.3a contracts executed only in the locked v0.5.21 proof environment."""

from __future__ import annotations

import shutil
from pathlib import Path

import yaml
from community_base.content_sync.check import check_repository
from community_base.content_sync.convert.courses import convert_course_repository
from community_base.coursework.manifests import read_cohort_homework
from community_base.curriculum.parsers import parse_course_repository, read_courses

from content_sync.course_repository import ModuleFlowSource
from content_sync.course_repository import parse_course_repository as parse_site_repository

FIXTURE_ROOT = Path(__file__).parents[2] / "content_sync/tests/fixtures/course_repository"
FIXTURE = FIXTURE_ROOT / "llm_zoomcamp_shared"
LEGACY_FIXTURE = FIXTURE_ROOT / "llm_zoomcamp_2026"
MODULE_ID = "22222222-2222-4222-8222-222222222222"
LESSON_IDS = (
    "33333333-3333-4333-8333-333333333333",
    "34444444-4444-4444-8444-444444444444",
)
QUIZ_ID = "81111111-1111-4111-8111-111111111111"


def _snapshot(root: Path) -> dict[str, bytes]:
    files: dict[str, bytes] = {}
    for path in root.rglob("*"):
        if path.is_file():
            files[path.relative_to(root).as_posix()] = path.read_bytes()
    return files


def _current_fixture(tmp_path: Path) -> Path:
    root = tmp_path / "llm-zoomcamp"
    shutil.copytree(FIXTURE, root)
    shutil.rmtree(root / "cohorts/2025")
    course = root / "course.yaml"
    course.write_text(
        course.read_text().replace(
            '  - identifier: "2025"\n    content: cohorts/2025\n    legacy: true\n', ""
        )
    )
    return root


def _retain_site_routes(root: Path) -> None:
    """Materialize the current site's numeric URL slugs as authored keys."""

    for name in ("01-lesson", "02-practice"):
        path = root / "01-agentic-rag" / f"{name}.md"
        body = path.read_text()
        if body.startswith("---\n"):
            path.write_text(body.replace("---\n", f"---\nslug: {name}\n", 1))
        else:
            path.write_text(f"---\nslug: {name}\n---\n{body}")


def _homework(root: Path):
    parsed = parse_course_repository(root)
    result = read_courses(root)
    return read_cohort_homework(result, result.collections[0], parsed)


def _add_nested_module(root: Path) -> None:
    module = root / "01-agentic-rag/module.yaml"
    module.write_text(module.read_text() + "syllabus_section: Foundations\nbonus: true\n")
    child = root / "01-agentic-rag/03-deeper"
    child.mkdir()
    (child / "module.yaml").write_text(
        "content_id: 71111111-1111-4111-8111-111111111111\ntitle: Deeper topic\n"
    )
    (child / "01-inside.md").write_text(
        "---\ncontent_id: 72222222-2222-4222-8222-222222222222\n"
        "title: Inside lesson\n---\nA nested lesson.\n"
    )


def _add_quiz(root: Path) -> None:
    quiz = root / "01-agentic-rag/04-quiz"
    quiz.mkdir()
    (quiz / "homework.yaml").write_text(
        f"content_id: {QUIZ_ID}\n"
        "title: Authored quiz\n"
        "due_at: 2026-07-01T23:00:00+00:00\n"
        "questions:\n"
        "  - content_id: 83333333-3333-4333-8333-333333333333\n"
        "    id: first\n"
        "    type: multiple_choice\n"
        "    prompt: Pick Beta\n"
        "    points: 2\n"
        "    step_label: Quiz step\n"
        "    options:\n"
        "      - {id: alpha, label: Alpha}\n"
        "      - {id: beta, label: Beta}\n"
        "    correct: '2'\n"
    )
    (quiz / "homework.md").write_text("Authored instructions.\n")
    cohort = root / "cohorts/2026/cohort.yaml"
    cohort.write_text(cohort.read_text() + f"  - module: 01-agentic-rag\n    unit: {QUIZ_ID}\n")


def _add_ignore(root: Path) -> None:
    course = root / "course.yaml"
    course.write_text(course.read_text() + "ignore:\n  - scratch/**\n")
    ignored = root / "scratch/ignored.md"
    ignored.parent.mkdir()
    ignored.write_text("This is explicitly outside the course source.\n")


def _add_mixed_siblings(root: Path) -> None:
    _add_nested_module(root)
    _add_quiz(root)
    _add_ignore(root)


def _assert_flat_graph(root: Path) -> None:
    parsed = parse_course_repository(root)
    module = parsed.course.modules[0]
    assert (module.slug, module.content_id, module.source_path) == (
        "01-agentic-rag",
        MODULE_ID,
        "01-agentic-rag/module.yaml",
    )
    assert [(unit.slug, unit.content_id, unit.source_path) for unit in module.units] == [
        ("01-lesson", LESSON_IDS[0], "01-agentic-rag/01-lesson.md"),
        ("02-practice", LESSON_IDS[1], "01-agentic-rag/02-practice.md"),
    ]
    assert [(cohort.slug, cohort.module_refs) for cohort in parsed.course.cohorts] == [
        ("2026", None),
        ("self-paced", None),
    ]


def _assert_flat_homework(root: Path) -> None:
    assignments = _homework(root)
    assert [(item.module_slug, item.source_path) for item in assignments] == [
        ("01-agentic-rag", "cohorts/2026/homework/01-agentic-rag/homework.yaml")
    ]
    assert [question.content_id for question in assignments[0].questions] == [
        "61111111-1111-4111-8111-111111111111",
        "62222222-2222-4222-8222-222222222222",
    ]
    assert [option.id for option in assignments[0].questions[0].options] == ["pages-24", "pages-72"]
    assert assignments[0].questions[0].answer is not None
    source_homework = yaml.safe_load(
        _snapshot(FIXTURE)["cohorts/2026/homework/01-agentic-rag/homework.yaml"]
    )
    assert dict(assignments[0].questions[0].answer) == source_homework["questions"][0]["answer"]
    assert assignments[0].form.learning_in_public_cap == 7


def test_flat_source_keeps_identity_and_explicit_dtc_route_policy(tmp_path: Path) -> None:
    original = parse_site_repository(_snapshot(FIXTURE), commit_sha="a" * 40)
    assert [(unit.slug, unit.content_id) for unit in original.modules[0].units] == list(
        zip(("01-lesson", "02-practice"), LESSON_IDS, strict=True)
    )
    root = _current_fixture(tmp_path)
    _retain_site_routes(root)
    report = convert_course_repository(root)

    assert report.ok, report.render()
    assert check_repository(root) == []
    _assert_flat_graph(root)
    _assert_flat_homework(root)
    assert convert_course_repository(root).converted == 0


def _assert_mixed_graph(root: Path) -> None:
    module = parse_course_repository(root).course.modules[0]
    assert (module.syllabus_section, module.is_bonus) == ("Foundations", True)
    assert [(type(item).__name__, item.slug) for item in module.siblings] == [
        ("UnitGraph", "01-lesson"),
        ("UnitGraph", "02-practice"),
        ("ModuleGraph", "deeper"),
        ("UnitGraph", "quiz"),
    ]
    assert [item.source_sibling_position for item in module.siblings] == [0, 1, 2, 3]
    assert module.children[0].units[0].content_id == "72222222-2222-4222-8222-222222222222"
    assert (module.units[-1].kind, module.units[-1].content_id) == ("homework", QUIZ_ID)


def _assert_mixed_homework(root: Path) -> None:
    legacy, quiz = _homework(root)
    assert (legacy.module_slug, legacy.unit_content_id) == ("01-agentic-rag", None)
    assert legacy.questions[0].content_id == "61111111-1111-4111-8111-111111111111"
    assert (quiz.module_slug, quiz.unit_content_id, quiz.course_tree) == (
        "01-agentic-rag",
        QUIZ_ID,
        True,
    )
    assert [(question.stable_id, question.content_id) for question in quiz.questions] == [
        ("first", "83333333-3333-4333-8333-333333333333")
    ]
    assert [(option.id, option.label) for option in quiz.questions[0].options] == [
        ("alpha", "Alpha"),
        ("beta", "Beta"),
    ]
    assert quiz.questions[0].correct == "2"
    assert quiz.questions[0].step_label == "Quiz step"


def test_mixed_tree_preserves_authored_order_metadata_and_binding(tmp_path: Path) -> None:
    root = _current_fixture(tmp_path)
    _retain_site_routes(root)
    _add_mixed_siblings(root)
    ignored = (root / "scratch/ignored.md").read_bytes()
    dry_run = convert_course_repository(root, apply=False)

    assert dry_run.ok, dry_run.render()
    assert "content.yaml" not in _snapshot(root)
    report = convert_course_repository(root)
    assert report.ok, report.render()
    assert report.verify() == []
    assert check_repository(root) == []
    assert (root / "scratch/ignored.md").read_bytes() == ignored
    assert "scratch/**" in yaml.safe_load((root / "content.yaml").read_text())["ignore"]
    _assert_mixed_graph(root)
    _assert_mixed_homework(root)
    assert convert_course_repository(root).converted == 0


def test_archived_legacy_homework_refuses_only_its_cohort(tmp_path: Path) -> None:
    root = tmp_path / "llm-zoomcamp"
    shutil.copytree(FIXTURE, root)
    archived = root / "cohorts/2025/cohort.yaml"
    before = archived.read_bytes()
    course_before = (root / "course.yaml").read_bytes()

    report = convert_course_repository(root)

    assert [(item.path, item.rule) for item in report.refusals] == [
        ("cohorts/2025/cohort.yaml", "3.8")
    ]
    assert report.verify() == []
    assert archived.read_bytes() == before
    assert (root / "course.yaml").read_bytes() != course_before
    assert "schema_version" not in (root / "course.yaml").read_text()
    assert not convert_course_repository(root, apply=False).ok


def test_authored_cross_module_next_url_does_not_replace_dtc_navigation(
    tmp_path: Path,
) -> None:
    root = _current_fixture(tmp_path)
    lesson = root / "01-agentic-rag/01-lesson.md"
    lesson.write_text(
        lesson.read_text().replace("---\n", "---\nnext_url: https://example.com/cross-module\n", 1)
    )
    report = convert_course_repository(root)

    assert report.ok, report.render()
    assert "next_url" not in yaml.safe_load(lesson.read_text().split("---", 2)[1])
    assert "next_url" in report.render()


def test_site_parser_keeps_legacy_project_reference_until_adoption() -> None:
    source = parse_site_repository(_snapshot(LEGACY_FIXTURE), commit_sha="b" * 40)
    cohorts = {item.identifier: item for item in source.cohorts}
    cohort = cohorts["2026"]

    assert cohort.format == "modules"
    assert [(type(item).__name__, getattr(item, "slug", None)) for item in cohort.flow] == [
        ("ModuleFlowSource", None),
        ("ProjectFlowSource", "project-01"),
    ]
    module_flow = cohort.flow[0]
    assert isinstance(module_flow, ModuleFlowSource)
    assert module_flow.module.slug == "01-agentic-rag"


def test_project_flow_refuses_its_cohort_without_blocking_other_scopes(tmp_path: Path) -> None:
    root = _current_fixture(tmp_path)
    cohort = root / "cohorts/2026/cohort.yaml"
    legacy = LEGACY_FIXTURE / "cohorts/2026/cohort.yaml"
    flow = yaml.safe_load(legacy.read_text())["flow"]
    cohort.write_text(cohort.read_text() + yaml.safe_dump({"flow": flow}))
    before = _snapshot(cohort.parent)
    self_paced = root / "cohorts/self-paced/cohort.yaml"
    unrelated_before = self_paced.read_bytes()
    report = convert_course_repository(root)
    assert [(item.path, item.rule) for item in report.refusals] == [
        ("cohorts/2026/cohort.yaml", "3.8")
    ]
    assert "ordered module/project placement" in report.refusals[0].message
    assert not report.ok
    assert report.verify() == []
    assert _snapshot(cohort.parent) == before
    assert self_paced.read_bytes() != unrelated_before
    assert (root / "content.yaml").is_file()
    assert "schema_version" not in yaml.safe_load(self_paced.read_text())
    after = _snapshot(root)
    repeated = convert_course_repository(root)
    assert repeated.converted == 0
    assert [(item.path, item.rule) for item in repeated.refusals] == [
        ("cohorts/2026/cohort.yaml", "3.8")
    ]
    assert _snapshot(root) == after
