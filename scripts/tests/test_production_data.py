"""The local dataset runner issues the runbook's step table, once each, in order.

``scripts/production_data.py dataset`` used to pre-migrate and pre-register the course
sources before ``prepare_local_data.py`` did both again -- so ``PRODUCTION_PREP_FRESH=1``
always refused -- ran the legacy Zoomcamp import only for ``bootstrap``, and ran no
editorial sync at all, so a rebuilt dataset failed its own editorial gate.  Each step
now has one owner (issue #463).  :data:`STEP_TABLE` is the table
``_docs/runbooks/data-ingest.md`` §11 shows verbatim; these tests hold the code to it.
"""

from __future__ import annotations

import contextlib
import io
import os
import subprocess
import tempfile
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from unittest import mock

from django.core.management.base import CommandError
from django.test import SimpleTestCase, TestCase, override_settings

import scripts.prepare_local_data as prepare
import scripts.production_data as production_data
from scripts.prod import COURSE_CATALOGUE_ORDER
from scripts.production_data import DEFAULT_DATASET_ROOT, build_parser

PROJECT_ROOT = Path(__file__).resolve().parents[2]
RUNBOOK = PROJECT_ROOT / "_docs" / "runbooks" / "data-ingest.md"
SCRATCH_ROOT = PROJECT_ROOT / ".tmp"
EDITORIAL_SLUGS = ("dtc-content", "dtc-main-site", "dtc-docs", "dtc-faq", "dtc-podwiki")

#: The runbook's step table, verbatim.  Its first column is the command
#: ``production_data.py dataset`` issues; the nested rows
#: (marked ``↳``) are what ``prepare_local_data.py`` does inside it, by
#: ``STEP_ORDER`` name.
STEP_TABLE = """\
| Step | What it does |
| --- | --- |
| `scripts/rebuild_gate.py` | refuses an unsafe dataset path, removes the old database |
| `scripts/prepare_local_data.py` | owns every step below, in this order |
| ↳ `migrate` | `manage.py migrate` on the absent (fresh) database |
| ↳ `course_catalog` | `manage.py seed_local_courses` |
| ↳ `course_repository_sources` | registers the pinned course-repository sources |
| ↳ `legacy_zoomcamp` | `import_legacy_zoomcamp`, with `--legacy-zoomcamp-source` |
| ↳ `course_repository_checkouts` | `scripts/content.py checkouts`, with `--refresh-checkouts` |
| ↳ `course_modules` | `sync_course_repositories` pull from the checkouts |
| ↳ `cmp_content` | the CMP content reconcile, with `--cmp-source-db` |
| ↳ `one_time_imports` | sponsors, testimonials, tour, project-gallery enrichment |
| ↳ `editorial_seed` | `manage.py seed_content_sources`, with `--editorial-checkout-root` |
| ↳ `editorial_checkouts` | `scripts/content.py editorial-checkouts`, with `--refresh-checkouts` |
| ↳ `editorial_sync` | `sync_content --source <slug> --from-disk <root>/<slug>`, five sources |
| ↳ `events` | `import_events.run()`, then the Luma and Eventbrite attendee exports |
| `scripts/verify_local_dataset.py` | the dataset gate, editorial and media store included |
"""


def _table_steps() -> tuple[tuple[str, ...], tuple[str, ...]]:
    commands: list[str] = []
    steps: list[str] = []
    for line in STEP_TABLE.splitlines()[2:]:
        first = line.split("|")[1].strip()
        name = first.removeprefix("↳ ").strip("`")
        (steps if first.startswith("↳") else commands).append(name)
    return tuple(commands), tuple(steps)


class StepTableTests(SimpleTestCase):
    def test_the_table_names_exactly_the_declared_commands_and_steps(self) -> None:
        commands, steps = _table_steps()
        self.assertEqual(commands, production_data.DATASET_STEPS)
        self.assertEqual(steps, prepare.STEP_ORDER)

    def test_the_runbook_shows_the_table_verbatim(self) -> None:
        self.assertTrue(
            STEP_TABLE in RUNBOOK.read_text(encoding="utf-8"),
            "the runbook §11 step table must match STEP_TABLE verbatim",
        )

    def test_the_course_steps_follow_the_declared_catalogue_order(self) -> None:
        positions = {
            "import_legacy_zoomcamp": prepare.STEP_ORDER.index("legacy_zoomcamp"),
            "sync_course_repositories": prepare.STEP_ORDER.index("course_modules"),
            "import_cmp_content": prepare.STEP_ORDER.index("cmp_content"),
        }
        self.assertEqual(
            sorted(COURSE_CATALOGUE_ORDER, key=positions.__getitem__),
            list(COURSE_CATALOGUE_ORDER),
        )
        self.assertLess(prepare.STEP_ORDER.index("migrate"), positions["import_legacy_zoomcamp"])


class _Recorder:
    """Stand-in for ``subprocess.run`` that records each command."""

    def __init__(self, database: Path, fail_on: str | None = None) -> None:
        self.database = database
        self.fail_on = fail_on
        self.commands: list[tuple[str, ...]] = []
        self.database_existed_at_prepare: bool | None = None

    def __call__(self, command: Any, **kwargs: Any) -> subprocess.CompletedProcess[str]:
        command = tuple(str(part) for part in command)
        self.commands.append(command)
        if command[4] == "scripts/prepare_local_data.py":
            self.database_existed_at_prepare = self.database.exists()
        if self.fail_on and command[4] == self.fail_on:
            raise subprocess.CalledProcessError(2, command)
        return subprocess.CompletedProcess(command, 0, "", "")

    @property
    def scripts(self) -> list[str]:
        return [command[4] for command in self.commands]


class ProductionDataSequenceTests(SimpleTestCase):
    def setUp(self) -> None:
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory(dir=SCRATCH_ROOT)))
        self.legacy = self.root / "zoomcamp-scoring"
        self.legacy.mkdir()
        self.environment = {
            "PRODUCTION_PREP_DATASET_ROOT": str(self.root),
            "PRODUCTION_PREP_CMP_SOURCE": "",
            "LEGACY_ZOOMCAMP_SOURCE": str(self.legacy),
            "PRODUCTION_PREP_LOCAL_ARGS": "",
        }

    def _run(self, command: str, *, fail_on: str | None = None, **environment: str):
        recorder = _Recorder(self.root / "dataset.sqlite3", fail_on=fail_on)
        stderr = io.StringIO()
        with (
            mock.patch.dict(os.environ, {**self.environment, **environment}),
            mock.patch.object(production_data.subprocess, "run", recorder),
            contextlib.redirect_stderr(stderr),
        ):
            if "PRODUCTION_PREP_FRESH" not in environment:
                os.environ.pop("PRODUCTION_PREP_FRESH", None)
            status = production_data.main([command])
        return status, recorder, stderr.getvalue()

    def test_dataset_issues_exactly_the_three_steps_once_each(self) -> None:
        status, recorder, _stderr = self._run("dataset")
        self.assertEqual(status, 0)
        self.assertEqual(recorder.scripts, list(production_data.DATASET_STEPS))
        flattened = " ".join(" ".join(command) for command in recorder.commands)
        for absent in ("migrate", "scripts/content.py", "import_legacy_zoomcamp.py"):
            self.assertNotIn(absent, flattened)

    def test_the_rehearsal_owns_legacy_checkouts_and_the_editorial_stage(self) -> None:
        _status, recorder, _stderr = self._run("dataset")
        prepare_command = recorder.commands[1]
        self.assertIn("--refresh-checkouts", prepare_command)
        self.assertEqual(
            prepare_command[prepare_command.index("--legacy-zoomcamp-source") + 1],
            str(self.legacy),
        )
        self.assertEqual(
            prepare_command[prepare_command.index("--editorial-checkout-root") + 1],
            str(self.root / "editorial-sources"),
        )
        self.assertNotIn("--cmp-source-db", prepare_command)

    def test_bootstrap_is_the_same_sequence(self) -> None:
        _status, dataset, _ = self._run("dataset")
        _status, bootstrap, _ = self._run("bootstrap")
        self.assertEqual(bootstrap.commands, dataset.commands)

    def test_fresh_reaches_the_rehearsal_with_the_database_still_absent(self) -> None:
        status, recorder, _stderr = self._run("dataset", PRODUCTION_PREP_FRESH="1")
        self.assertEqual(status, 0)
        self.assertIn("--fresh", recorder.commands[1])
        self.assertIs(recorder.database_existed_at_prepare, False)

    def test_a_failed_rehearsal_step_fails_the_run_and_is_named(self) -> None:
        status, recorder, stderr = self._run("dataset", fail_on="scripts/prepare_local_data.py")
        self.assertEqual(status, 2)
        self.assertNotIn("scripts/verify_local_dataset.py", recorder.scripts)
        self.assertIn("scripts/prepare_local_data.py exited 2", stderr)

    def test_a_missing_legacy_checkout_is_refused(self) -> None:
        status, recorder, stderr = self._run(
            "dataset", LEGACY_ZOOMCAMP_SOURCE=str(self.root / "absent")
        )
        self.assertEqual(status, 2)
        self.assertIn("LEGACY_ZOOMCAMP_SOURCE", stderr)
        self.assertNotIn("scripts/prepare_local_data.py", recorder.scripts)


class _RehearsalFixture:
    """Inputs ``prepare_local_data.run`` checks for, below ``.tmp/``."""

    def __init__(self, root: Path) -> None:
        self.database = root / "rehearsal.sqlite3"
        self.course_root = root / "course-sources"
        self.course_root.mkdir()
        self.editorial_root = root / "editorial-sources"
        for slug in EDITORIAL_SLUGS:
            (self.editorial_root / slug).mkdir(parents=True)
        self.legacy = root / "zoomcamp-scoring"
        self.legacy.mkdir()
        self.manifest = root / "manifest.json"
        self.manifest.write_text("{}", encoding="utf-8")
        self.luma = root / "luma"
        self.luma.mkdir()
        self.eventbrite = root / "eventbrite.zip"
        self.eventbrite.write_bytes(b"")


@contextlib.contextmanager
def _stubbed_rehearsal(order: list[str]) -> Iterator[None]:
    """Replace every step's work with a recorder of its name."""

    def step(name: str, result: Any = None) -> Any:
        def record(*args: Any, **kwargs: Any) -> Any:
            order.append(name)
            return {} if result is None else result

        return record

    def management(name: str, **options: Any) -> dict[str, Any]:
        order.append({"migrate": "migrate", "seed_local_courses": "course_catalog"}[name])
        return {}

    def refresh(command: str, **kwargs: Any) -> None:
        order.append(
            {
                "checkouts": "course_repository_checkouts",
                "editorial-checkouts": "editorial_checkouts",
            }[command]
        )

    def sync(slug: str, checkout: Path) -> dict[str, Any]:
        order.append("editorial_sync")
        return {"status": "success", "checkout": checkout.name}

    pipeline: dict[str, dict[str, Any]] = {
        "identities": {},
        "new_event_identities": {},
        "event_content": {},
        "new_event_content": {},
        "eventbrite_descriptions": {},
    }

    class _Registrants:
        def as_dict(self) -> dict[str, Any]:
            order.append("events")
            return {}

    with (
        mock.patch.dict(os.environ),
        mock.patch("django.setup"),
        mock.patch.object(prepare, "_json_management_command", management),
        mock.patch.object(
            prepare, "sync_course_repository_sources", step("course_repository_sources")
        ),
        mock.patch("scripts.prod.import_legacy_zoomcamp.run", step("legacy_zoomcamp")),
        mock.patch.object(prepare, "_refresh_checkouts", refresh),
        mock.patch.object(prepare, "select_course_repository_sources", lambda *a, **k: ()),
        mock.patch.object(prepare, "pull_course_repositories", step("course_modules")),
        mock.patch(
            "courses.services.cmp_content_import.import_cmp_course_content",
            lambda *a, **k: mock.Mock(summary=step("cmp_content")),
        ),
        mock.patch.object(
            prepare, "_import_sponsor_and_testimonial_content", step("one_time_imports")
        ),
        mock.patch("scripts.prod.import_project_repo_enrichment.run", step("one_time_imports")),
        mock.patch.object(prepare, "_seed_editorial_sources", step("editorial_seed", [])),
        mock.patch.object(prepare, "_sync_editorial_source", sync),
        mock.patch(
            "scripts.content.declared_editorial_slugs",
            lambda: EDITORIAL_SLUGS,
        ),
        mock.patch.object(prepare, "run_event_pipeline", step("events", pipeline)),
        mock.patch(
            "scripts.prod.registrant_import.import_registrants",
            lambda **kwargs: _Registrants(),
        ),
        mock.patch(
            "scripts.prod.registration_sources.luma_registrants.luma_registrant_sources",
            lambda *a, **k: (),
        ),
        mock.patch(
            "scripts.prod.registration_sources.eventbrite_registrants.eventbrite_registrant_sources",
            lambda *a, **k: (),
        ),
    ):
        yield


def _collapsed(order: list[str]) -> list[str]:
    collapsed: list[str] = []
    for name in order:
        if not collapsed or collapsed[-1] != name:
            collapsed.append(name)
    return collapsed


class RehearsalSequenceTests(SimpleTestCase):
    def setUp(self) -> None:
        root = Path(self.enterContext(tempfile.TemporaryDirectory(dir=SCRATCH_ROOT)))
        self.fixture = _RehearsalFixture(root)

    def _run(self, order: list[str], **options: Any) -> dict[str, Any]:
        fixture = self.fixture
        arguments: dict[str, Any] = {
            "database": fixture.database,
            "course_checkout_root": fixture.course_root,
            "identity_manifest": fixture.manifest,
            "luma_source": fixture.luma,
            "eventbrite_source": fixture.eventbrite,
            "luma_identities": None,
            "cmp_source_db": None,
            "fresh": True,
        }
        arguments.update(options)
        with _stubbed_rehearsal(order):
            return prepare.run(**arguments)

    def test_every_step_runs_once_in_the_declared_order(self) -> None:
        order: list[str] = []
        report = self._run(
            order,
            cmp_source_db=self.fixture.manifest,
            legacy_zoomcamp_source=self.fixture.legacy,
            editorial_checkout_root=self.fixture.editorial_root,
            refresh_checkouts=True,
        )
        self.assertEqual(_collapsed(order), list(prepare.STEP_ORDER))
        self.assertEqual(order.count("legacy_zoomcamp"), 1)
        self.assertEqual(order.count("migrate"), 1)
        self.assertEqual(order.count("course_repository_sources"), 1)
        self.assertEqual(order.count("editorial_sync"), len(EDITORIAL_SLUGS))
        self.assertEqual(sorted(report["steps"]["editorial"]["sources"]), sorted(EDITORIAL_SLUGS))
        self.assertTrue(report["steps"]["legacy_zoomcamp"]["imported"])

    def test_a_fresh_run_on_an_absent_database_is_not_refused(self) -> None:
        self.assertFalse(self.fixture.database.exists())
        report = self._run([])
        self.assertTrue(report["database"]["fresh_requested"])

    def test_without_an_editorial_root_the_stage_is_skipped_explicitly(self) -> None:
        order: list[str] = []
        report = self._run(order)
        self.assertEqual(report["steps"]["editorial"], {"skipped": "checkout_root_not_supplied"})
        self.assertEqual(
            report["steps"]["legacy_zoomcamp"],
            {"imported": False, "skipped": "source_not_supplied"},
        )
        for absent in ("editorial_seed", "editorial_sync", "legacy_zoomcamp"):
            self.assertNotIn(absent, order)
        # Offline by default: nothing is refreshed.
        self.assertNotIn("course_repository_checkouts", order)
        self.assertNotIn("editorial_checkouts", order)

    def test_a_missing_editorial_checkout_names_its_slug(self) -> None:
        (self.fixture.editorial_root / "dtc-faq").rmdir()
        synced: list[str] = []
        real_sync = prepare._sync_editorial_source

        def sync(slug: str, checkout: Path) -> dict[str, Any]:
            if checkout.is_dir():
                synced.append(slug)
                return {"status": "success"}
            return real_sync(slug, checkout)

        with (
            mock.patch.object(prepare, "_seed_editorial_sources", lambda: []),
            mock.patch.object(prepare, "_sync_editorial_source", sync),
            mock.patch(
                "scripts.content.declared_editorial_slugs",
                lambda: EDITORIAL_SLUGS,
            ),
        ):
            with self.assertRaises(prepare.LocalPreparationError) as raised:
                prepare._run_editorial_stage(
                    database=self.fixture.database,
                    checkout_root=self.fixture.editorial_root,
                    refresh_checkouts=False,
                )
        self.assertEqual(str(raised.exception), "editorial_checkout_unavailable_dtc-faq")
        self.assertEqual(synced, ["dtc-content", "dtc-main-site", "dtc-docs"])


SYNTHETIC_SECRETS = "synthetic-issue-463-webhook-secret"


def _content_sources_with_secrets() -> dict[str, Any]:
    from django.conf import settings

    declared = [
        {**declaration, "webhook_secret": SYNTHETIC_SECRETS}
        for declaration in settings.COMMUNITY_BASE["CONTENT_SOURCES"]
    ]
    return {**settings.COMMUNITY_BASE, "CONTENT_SOURCES": declared}


class EditorialStageTests(TestCase):
    """The stage against real seeded rows; ``sync_content`` itself is stubbed."""

    def setUp(self) -> None:
        root = Path(self.enterContext(tempfile.TemporaryDirectory(dir=SCRATCH_ROOT)))
        self.fixture = _RehearsalFixture(root)
        self.enterContext(override_settings(COMMUNITY_BASE=_content_sources_with_secrets()))

    def _sync_command(self, outcome: dict[str, str]):
        from community_base.content_sync.models import ContentSource, SyncLog

        def call_command(name: str, **options: Any) -> None:
            self.assertEqual(name, "sync_content")
            slug = options["source_slug"]
            self.calls.append((slug, Path(options["from_disk"])))
            status = outcome.get(slug, "success")
            SyncLog.objects.create(source=ContentSource.objects.get(slug=slug), status=status)
            if status in {"failed", "partial"}:
                raise CommandError("One or more content sources did not sync successfully")

        return call_command

    def _stage(self, outcome: dict[str, str] | None = None) -> dict[str, Any]:
        from django.core import management

        self.calls: list[tuple[str, Path]] = []
        real = management.call_command

        def dispatch(name: str, *args: Any, **options: Any) -> Any:
            if name == "sync_content":
                return self._sync_command(outcome or {})(name, **options)
            return real(name, *args, **options)

        with mock.patch("django.core.management.call_command", dispatch):
            return prepare._run_editorial_stage(
                database=self.fixture.database,
                checkout_root=self.fixture.editorial_root,
                refresh_checkouts=False,
            )

    def test_the_five_declared_sources_are_seeded_and_synced_from_their_checkouts(self) -> None:
        report = self._stage()
        self.assertEqual(
            self.calls,
            [(slug, self.fixture.editorial_root / slug) for slug in EDITORIAL_SLUGS],
        )
        self.assertEqual(len(report["seeded"]), len(EDITORIAL_SLUGS))
        self.assertEqual(
            {slug: source["status"] for slug, source in report["sources"].items()},
            dict.fromkeys(EDITORIAL_SLUGS, "success"),
        )

    def test_a_failed_sync_fails_the_stage_and_names_the_slug(self) -> None:
        with self.assertRaises(prepare.LocalPreparationError) as raised:
            self._stage({"dtc-faq": "failed"})
        self.assertEqual(str(raised.exception), "editorial_sync_failed_dtc-faq")

    def test_a_sync_that_is_not_a_success_fails_the_stage(self) -> None:
        with self.assertRaises(prepare.LocalPreparationError) as raised:
            self._stage({"dtc-docs": "skipped"})
        self.assertEqual(str(raised.exception), "editorial_sync_failed_dtc-docs")

    def test_the_command_line_refusal_names_the_slug_and_exits_two(self) -> None:
        stderr = io.StringIO()
        with (
            mock.patch.object(
                prepare,
                "run",
                side_effect=prepare.LocalPreparationError("editorial_sync_failed_dtc-faq"),
            ),
            mock.patch.object(prepare, "_main_checkout_root", lambda: SCRATCH_ROOT),
            contextlib.redirect_stderr(stderr),
        ):
            status = prepare.main(
                [
                    "--database",
                    str(self.fixture.database),
                    "--course-checkout-root",
                    str(self.fixture.course_root),
                    "--editorial-checkout-root",
                    str(self.fixture.editorial_root),
                ]
            )
        self.assertEqual(status, 2)
        self.assertIn("editorial_sync_failed_dtc-faq", stderr.getvalue())


def test_production_data_parser_keeps_seed_and_rehearsal_commands_separate() -> None:
    assert DEFAULT_DATASET_ROOT == ".tmp/production-prep-dataset"
    assert build_parser().parse_args(["dataset"]).command == "dataset"
    assert build_parser().parse_args(["bootstrap"]).command == "bootstrap"
    assert (
        build_parser()
        .parse_args(
            [
                "local",
                "--database",
                "db",
                "--course-checkout-root",
                "src",
                "--fresh",
                "--",
                "--extra-option",
            ]
        )
        .command
        == "local"
    )
    assert build_parser().parse_args(["verify", "--database", "db"]).command == "verify"
