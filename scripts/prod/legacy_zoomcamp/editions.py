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
    # Set only for an edition with no ``certificates_json`` at all (2021's ML
    # Zoomcamp -- issue #15 ruled explicitly that "no certificates for this
    # edition" is not an approved disposition). When set, certificate_import.py
    # computes each graduate's certificate URL directly as
    # sha1_hex(email + this suffix), never through certificates_json name
    # matching. This is zoomcamp-scoring's own historical convention, not a
    # guess: certificates/mlzoomcamp-2021-batch.py (commit 8654144, "redo
    # script") computed ``df['hash_new'] = (df.email + '_').apply(compute_hash)``
    # -- the URL-only hash trails a literal underscore the plain identity hash
    # (used elsewhere, e.g. the project-results CSVs) never carries. Verified
    # against the real bucket listing: all 74 roster emails' suffixed hashes
    # matched a real s3://certificate.datatalks.club/mlzoomcamp/2021/*.pdf
    # object one-to-one; the one extra object in that bucket matches no roster
    # email and is a known non-graduate example, left unmatched on purpose.
    certificate_hash_suffix: str | None = None
    # Overrides ``CERTIFICATE_REPO_SLUG[course_slug]`` for the direct-URL
    # formula above. ``CERTIFICATE_REPO_SLUG`` describes zoomcamp-scoring's
    # local directory-naming convention only and must keep meaning only that;
    # the real S3 bucket path segment for a given course/year is a separate
    # fact (see ``CERTIFICATE_URL_REPO_SLUG_OVERRIDES`` below), so it gets its
    # own field rather than overloading that constant with two meanings.
    # ``None`` means "the bucket path segment matches ``CERTIFICATE_REPO_SLUG``",
    # which is true except where this is explicitly set.
    certificate_url_repo_slug: str | None = None


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


# (course_slug, year) -> extra real-roster CSVs beyond the default
# ``old/<...>/data/graduates*.csv`` export the base pipeline already globs.
# Needed only for editions whose base export is short of the real batch --
# each path here was cross-checked, one row at a time, against a real
# ``aws s3 ls s3://certificate.datatalks.club/...`` listing (see
# DIRECT_HASH_EDITIONS below for the full archaeology): every non-"Rick
# Astley" row's ``sha1_hex(email + "_")`` matched a real object, and together
# with the base export these make the roster complete (0 unmatched either
# direction, aside from that one universal smoke-test row).
_EXTRA_CERTIFICATE_CSVS: dict[tuple[str, int], tuple[str, ...]] = {
    ("de-zoomcamp", 2022): ("courses/dezoomcamp-2022/graduates.csv",),
    ("de-zoomcamp", 2023): (
        "courses/dezoomcamp-2023/graduates-01.csv",
        "courses/dezoomcamp-2023/done.csv",
        "courses/dezoomcamp-2023/regenerate.csv",
    ),
    ("mlops-zoomcamp", 2022): (
        "courses/mlopszoomcamp-2022/graduates-01.csv",
        "courses/mlopszoomcamp-2022/graduates-02.csv",
    ),
    ("mlops-zoomcamp", 2023): (
        "courses/mlopszoomcamp-2023/graduates-01.csv",
        "courses/mlopszoomcamp-2023/graduates-02.csv",
        "courses/mlopszoomcamp-2023/graduates-manual.csv",
    ),
    ("ml-zoomcamp", 2022): (
        "courses/mlzoomcamp-2022/graduates.csv",
        "courses/mlzoomcamp-2022/graduates-02.csv",
    ),
}

# (course_slug, year) editions whose certificate URL is computed directly from
# the roster -- the same mechanism 2021's ML Zoomcamp uses (see
# ``EditionSource.certificate_hash_suffix``) -- rather than matched by name
# against a certificates_json. Two different reasons land an edition here:
#
# * de-zoomcamp and mlops-zoomcamp, 2022 and 2023: no current-format
#   certificates_json exists for them at all (only one-off single-entry
#   ``graduates-XX.json`` files from later manual reissues -- never the full
#   batch), so there is nothing to name-match against.
# * ml-zoomcamp 2022: its ``courses/mlzoomcamp-2022/graduates.json`` exists
#   but is short 2 real certificates relative to the roster and the real
#   bucket (one dropped somewhere in the original batch run; one is a later
#   manual addition recorded only in ``graduates-02.csv``/``.json``) --
#   confirmed by hashing the full roster (old export + both courses/ CSVs)
#   and finding all 102 real names match a real S3 object exactly.
#
# Every roster above was cross-checked email-by-email against a real S3
# listing: every non-"Rick Astley" row's ``sha1_hex(email + "_")`` hash
# matched a real object one-to-one, and every populated bucket's one leftover
# object is always that same course's "Rick Astley" row -- a universal
# smoke-test entry ``prepare_data.py`` (zoomcamp-scoring's shared certificate
# batch script) inserts into every course/year's batch input and then
# special-cases to a YouTube link instead of a real certificate (see its own
# ``rick = {...}; graduates.insert(0, rick)`` and the
# ``if 'fe629854...' in url`` override) -- never a real graduate.
# ``import_edition_certificates`` skips that row by name for this reason.
DIRECT_HASH_EDITIONS: frozenset[tuple[str, int]] = frozenset(
    {
        ("de-zoomcamp", 2022),
        ("de-zoomcamp", 2023),
        ("mlops-zoomcamp", 2022),
        ("mlops-zoomcamp", 2023),
        ("ml-zoomcamp", 2022),
    }
)

# (course_slug, year) -> the real S3 URL path segment that actually drove that
# course/year's certificate batch -- read directly from that course's own
# zoomcamp-scoring ``courses/<repo-slug>-<year>/config.json``
# (``vars.s3_location``), the exact value ``prepare_data.py`` substitutes into
# its ``url_template``. This is NOT ``CERTIFICATE_REPO_SLUG`` (zoomcamp-scoring's
# local directory-naming convention, unchanged): ml-zoomcamp and mlops-zoomcamp
# moved their certificate bucket path to a hyphenated form from 2022 onward
# while their local directory names stayed hyphen-free, so only those need an
# override. de-zoomcamp's config.json confirms its bucket path already matches
# CERTIFICATE_REPO_SLUG for 2022 and 2023, so it needs no entry here.
CERTIFICATE_URL_REPO_SLUG_OVERRIDES: dict[tuple[str, int], str] = {
    ("mlops-zoomcamp", 2022): "mlops-zoomcamp",
    ("mlops-zoomcamp", 2023): "mlops-zoomcamp",
    ("ml-zoomcamp", 2022): "ml-zoomcamp",
}


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
    certificate_csvs = certificate_csvs + tuple(
        path
        for relative_path in _EXTRA_CERTIFICATE_CSVS.get((course_slug, year), ())
        if (path := repo_root / relative_path).exists()
    )
    repo_slug = CERTIFICATE_REPO_SLUG[course_slug]
    certificates_dir = repo_root / "courses" / f"{repo_slug}-{year}"
    is_direct_hash = (course_slug, year) in DIRECT_HASH_EDITIONS
    # A direct-hash edition never name-matches against certificates_json (see
    # DIRECT_HASH_EDITIONS above for why) -- leaving it empty here keeps that
    # explicit rather than loading data nothing reads.
    certificates_json = (
        () if is_direct_hash else tuple(sorted(certificates_dir.glob("graduates*.json")))
    )

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
        certificate_hash_suffix="_" if is_direct_hash else None,
        certificate_url_repo_slug=CERTIFICATE_URL_REPO_SLUG_OVERRIDES.get((course_slug, year)),
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

    # 2021 predates zoomcamp-scoring's plaintext graduates.csv convention and
    # has no current-format courses/mlzoomcamp-2021/graduates.json -- but it
    # does have graduates (issue #15), and a real roster: the exact
    # email,name pairs the actual 2022-02 certificate batch run used, still
    # present (moved, not deleted) at its current path below.
    certificate_csvs = (
        repo_root / "courses" / "mlzoomcamp-2021" / "mlzoomcamp-2021-names.csv",
    )

    return EditionSource(
        cohort_slug="ml-zoomcamp-2021",
        course_slug="ml-zoomcamp",
        course_title=COURSE_TITLES["ml-zoomcamp"],
        year=2021,
        start_month=START_MONTH_BY_COURSE["ml-zoomcamp"],
        homeworks=homeworks,
        projects=projects,
        certificate_csvs=certificate_csvs,
        certificates_json=(),
        # Same shape as ``_build_pipeline_edition``: the real roster is a
        # plaintext-email source in its own right, not just a certificate
        # source, and must be scanned for email recovery too -- otherwise a
        # graduate whose real email lives only on the roster (never matched
        # by a hash in the raw weekly exports) gets a synthetic scoring
        # identity and a second, real-email-backed certificate identity for
        # the same person instead of one merged account.
        email_source_csvs=(*sorted(course_dir.glob("*.csv")), *certificate_csvs),
        certificate_hash_suffix="_",
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
