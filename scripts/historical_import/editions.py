"""Locate the historical files for each pre-2024 cohort.

``zoomcamp-scoring`` used the same "raw -> processed -> graded" pipeline
shape for every 2022/2023 cohort (Data Engineering, MLOps, and ML Zoomcamp):

* ``old/<course>-<year>/data/processed/hw-<slug>.csv`` -- one row per learner
  (keyed by the upstream ``sha1(email)`` hash), one column per question
  (points earned), plus ``learning_in_public``/``faq_score``/``total_score``.
* ``old/<course>-<year>/data/answers/answers-<slug>.json`` -- the question
  text and point value behind each ``hw-<slug>.csv`` column.
* ``old/<course>-<year>/data/processed/project-<slug>.csv`` -- one row per
  learner, rubric dimension scores, peer-review scores, and pass/fail.
* ``old/<course>-<year>/data/project/assignment-<slug>.csv`` -- the GitHub
  link and commit behind each project submission, keyed by the same hash.
* ``old/<course>-<year>/data/graduates*.csv`` -- plaintext ``email,name``
  for everyone who earned a certificate that edition.
* ``courses/<repo-slug>-<year>/graduates.json`` -- the hosted certificate
  PDF URL for each graduate, joined back to the CSV above by name.

2021's ML Zoomcamp predates that pipeline and used a flatter, per-week layout
(``old/ml-zoomcamp/homework-N-results.csv`` etc.) with no per-cohort
subdirectory; it is described directly by ``ML_ZOOMCAMP_2021``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

HOMEWORK_FILE_RE = re.compile(r"^hw-([a-z0-9]+)\.csv$")
PROJECT_FILE_RE = re.compile(r"^project-([a-z0-9]+)\.csv$")
GRADUATES_FILE_RE = re.compile(r"^graduates(-\d+)?\.csv$")

# Roughly matches each course family's real-world cadence; used only to give
# imported homeworks/projects plausible, ordered due dates. Not a claim about
# the exact historical schedule.
START_MONTH_BY_COURSE = {
    "de-zoomcamp": 1,
    "mlops-zoomcamp": 5,
    "ml-zoomcamp": 9,
}

COURSE_TITLES = {
    "de-zoomcamp": "Data Engineering Zoomcamp",
    "mlops-zoomcamp": "MLOps Zoomcamp",
    "ml-zoomcamp": "Machine Learning Zoomcamp",
}

# course_slug -> the hyphen-free directory name zoomcamp-scoring uses under
# courses/<repo-slug>-<year>/ for the current-format certificate exports.
CERTIFICATE_REPO_SLUG = {
    "de-zoomcamp": "dezoomcamp",
    "mlops-zoomcamp": "mlopszoomcamp",
    "ml-zoomcamp": "mlzoomcamp",
}


@dataclass(frozen=True, slots=True)
class HomeworkSource:
    slug_part: str
    results_csv: Path
    answers_json: Path | None


@dataclass(frozen=True, slots=True)
class ProjectSource:
    slug_part: str
    title: str
    results_csv: Path
    assignment_csv: Path | None


@dataclass(frozen=True, slots=True)
class EditionSource:
    cohort_slug: str
    course_slug: str
    course_title: str
    year: int
    start_month: int
    homeworks: tuple[HomeworkSource, ...]
    projects: tuple[ProjectSource, ...]
    certificate_csvs: tuple[Path, ...]
    certificates_json: tuple[Path, ...]
    # Every CSV worth scanning for a plaintext ``email``-like column: raw
    # weekly exports, graduate lists, and any leaderboard-email reveal. Used
    # only to recover the real address behind an upstream ``sha1(email)``
    # hash so historical learners can be attached to their own account; see
    # ``email_recovery.py``.
    email_source_csvs: tuple[Path, ...]


def _sorted_matches(directory: Path, pattern: re.Pattern) -> list[tuple[str, Path]]:
    if not directory.exists():
        return []
    matches = []
    for path in directory.iterdir():
        match = pattern.match(path.name)
        if match:
            matches.append((match.group(1), path))
    matches.sort(key=lambda item: item[0])
    return matches


def _optional(path: Path) -> Path | None:
    return path if path.exists() else None


def _build_pipeline_edition(
    repo_root: Path,
    *,
    course_slug: str,
    year: int,
    old_dir_name: str,
) -> EditionSource:
    data_dir = repo_root / "old" / old_dir_name / "data"

    homeworks = tuple(
        HomeworkSource(
            slug_part=slug_part,
            results_csv=path,
            answers_json=_optional(data_dir / "answers" / f"answers-{slug_part}.json"),
        )
        for slug_part, path in _sorted_matches(data_dir / "processed", HOMEWORK_FILE_RE)
    )
    projects = tuple(
        ProjectSource(
            slug_part=slug_part,
            title=f"Project {slug_part}",
            results_csv=path,
            assignment_csv=_optional(data_dir / "project" / f"assignment-{slug_part}.csv"),
        )
        for slug_part, path in _sorted_matches(data_dir / "processed", PROJECT_FILE_RE)
    )
    certificate_csvs = tuple(
        path
        for path in sorted(data_dir.glob("graduates*.csv"))
        if GRADUATES_FILE_RE.match(path.name)
    )
    repo_slug = CERTIFICATE_REPO_SLUG[course_slug]
    certificates_dir = repo_root / "courses" / f"{repo_slug}-{year}"
    certificates_json = tuple(sorted(certificates_dir.glob("graduates*.json")))

    email_source_csvs = (
        *sorted((data_dir / "raw").glob("*.csv")),
        *certificate_csvs,
        *(
            path
            for path in sorted(data_dir.glob("leaderboard_emails*.csv"))
            if "test" not in path.name.lower()
        ),
    )

    return EditionSource(
        cohort_slug=f"{course_slug}-{year}",
        course_slug=course_slug,
        course_title=COURSE_TITLES[course_slug],
        year=year,
        start_month=START_MONTH_BY_COURSE[course_slug],
        homeworks=homeworks,
        projects=projects,
        certificate_csvs=certificate_csvs,
        certificates_json=certificates_json,
        email_source_csvs=email_source_csvs,
    )


# (course_slug, year, the old/<...> directory name)
PIPELINE_EDITIONS = (
    ("de-zoomcamp", 2022, "de-zoomcamp-2022"),
    ("de-zoomcamp", 2023, "de-zoomcamp-2023"),
    ("mlops-zoomcamp", 2022, "mlops-zoomcamp-2022"),
    ("mlops-zoomcamp", 2023, "mlops-zoomcamp-2023"),
    ("ml-zoomcamp", 2022, "ml-zoomcamp-2022"),
    ("ml-zoomcamp", 2023, "ml-zoomcamp-2023"),
)


def _build_ml_zoomcamp_2021(repo_root: Path) -> EditionSource:
    course_dir = repo_root / "old" / "ml-zoomcamp"

    homeworks = tuple(
        HomeworkSource(
            slug_part=str(n),
            results_csv=course_dir / f"homework-{n}-results.csv",
            answers_json=_optional(course_dir / f"week{n}_answers.json"),
        )
        for n in (1, 2, 3, 4, 5, 6, 8, 9, 10)
        if (course_dir / f"homework-{n}-results.csv").exists()
    )
    project_specs = (
        ("midterm", "Midterm project", "midterm-project-results.csv", None),
        ("capstone", "Capstone project", "capstone-project-results.csv", "capstone-peer-reviews.csv"),
        ("project-3", "Third project", "project-3-results.csv", None),
    )
    projects = tuple(
        ProjectSource(
            slug_part=slug_part,
            title=title,
            results_csv=course_dir / results_name,
            assignment_csv=_optional(course_dir / assignment_name) if assignment_name else None,
        )
        for slug_part, title, results_name, assignment_name in project_specs
        if (course_dir / results_name).exists()
    )

    return EditionSource(
        cohort_slug="ml-zoomcamp-2021",
        course_slug="ml-zoomcamp",
        course_title=COURSE_TITLES["ml-zoomcamp"],
        year=2021,
        start_month=START_MONTH_BY_COURSE["ml-zoomcamp"],
        homeworks=homeworks,
        projects=projects,
        # 2021 predates zoomcamp-scoring's plaintext graduates.csv convention
        # and has no current-format courses/mlzoomcamp-2021/graduates.json;
        # certificates are not imported for this edition.
        certificate_csvs=(),
        certificates_json=(),
        email_source_csvs=tuple(sorted(course_dir.glob("*.csv"))),
    )


def build_editions(repo_root: Path) -> list[EditionSource]:
    editions = [
        _build_pipeline_edition(
            repo_root,
            course_slug=course_slug,
            year=year,
            old_dir_name=old_dir_name,
        )
        for course_slug, year, old_dir_name in PIPELINE_EDITIONS
    ]
    editions.append(_build_ml_zoomcamp_2021(repo_root))
    editions.sort(key=lambda edition: edition.cohort_slug)
    return editions
