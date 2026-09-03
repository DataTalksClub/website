"""Pull entry point: load registered course repositories from local checkouts.

Content reaches this site two ways and they share one implementation.  CI/CD
*pushes* -- a course repository posts a signed push event and the webhook
enqueues a durable job that downloads the commit archive.  A developer *pulls* --
this command reads checkouts that are already on disk.  Both then call the same
:func:`content_sync.course_repository_ingest.ingest_course_repository`, so the
validation is not two implementations that could drift.

This command makes no network call: the commit, the branch and the tracked file
list all come from the checkout's own git metadata.

Which repositories exist is data, not code.  The command iterates the registered
``ContentSource`` rows, so adding a course means registering a source with
``manage.py register_course_repository``, not editing a list here or in the
Makefile.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import cast

from django.core.management.base import BaseCommand, CommandError, CommandParser

from content.models import ContentSource
from content_sync.course_repository_ingest import (
    CourseRepositoryIngestError,
    CourseRepositoryIngestResult,
    ingest_course_repository,
)
from content_sync.course_repository_webhook import COURSE_REPOSITORY_ADAPTER_TYPE
from courses.services.local_course_seed import LocalCourseSeedError, assert_local_database

GIT_TIMEOUT_SECONDS = 30


class Command(BaseCommand):
    help = (
        "Ingest registered course repositories from local checkouts, using the same "
        "service the signed GitHub push webhook drives. Makes no network call."
    )

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument(
            "--from-disk",
            dest="from_disk",
            default=None,
            help=(
                "Directory holding one checkout per registered source, named after the "
                "source stable id or the repository name."
            ),
        )
        parser.add_argument(
            "--checkout",
            action="append",
            default=[],
            metavar="STABLE_ID=PATH",
            help="Explicit checkout for one source. Repeatable.",
        )
        parser.add_argument(
            "--stable-id",
            action="append",
            default=[],
            dest="stable_ids",
            help="Limit the run to these registered sources. Repeatable.",
        )
        parser.add_argument(
            "--checkout-plan",
            action="store_true",
            help=(
                "Print the registered sources and the checkout each would be read from, "
                "then exit without reading or writing anything."
            ),
        )
        parser.add_argument(
            "--allow-modified-checkout",
            action="store_true",
            help=(
                "Proceed when a checkout has uncommitted changes or is not on the "
                "registered branch. The recorded commit is still HEAD, so the imported "
                "bytes and the provenance links will not agree; use it for an edit "
                "preview, never to build a dataset anyone else will read."
            ),
        )

    # -- source selection ---------------------------------------------------

    def _sources(self, stable_ids: list[str]) -> list[ContentSource]:
        registered = list(
            ContentSource.objects.filter(
                enabled=True,
                adapter_type=COURSE_REPOSITORY_ADAPTER_TYPE,
            ).order_by("stable_id")
        )
        if not registered:
            raise CommandError(
                "no enabled course-repository sources are registered. Register one with "
                "`manage.py register_course_repository --stable-id ... --owner ... "
                "--repository ... --enabled`."
            )
        if not stable_ids:
            return registered
        # Report the unmatched selection rather than "nothing to do": a typo must
        # not look like a clean run.
        missing = sorted(set(stable_ids) - {source.stable_id for source in registered})
        if missing:
            raise CommandError(f"not registered or not enabled: {', '.join(missing)}")
        return [source for source in registered if source.stable_id in set(stable_ids)]

    def _explicit_checkouts(self, entries: list[str]) -> dict[str, Path]:
        checkouts: dict[str, Path] = {}
        for entry in entries:
            stable_id, separator, raw_path = entry.partition("=")
            if not separator or not stable_id or not raw_path:
                raise CommandError(f"--checkout expects STABLE_ID=PATH, got {entry!r}")
            checkouts[stable_id] = Path(raw_path).expanduser()
        return checkouts

    def _checkout_for(
        self,
        source: ContentSource,
        *,
        root: Path | None,
        explicit: dict[str, Path],
    ) -> Path:
        chosen = explicit.get(source.stable_id)
        if chosen is not None:
            return chosen
        if root is None:
            raise CommandError(
                f"no checkout for {source.stable_id}: pass --from-disk or "
                f"--checkout {source.stable_id}=PATH"
            )
        for name in (source.stable_id, source.repository_name):
            candidate = root / name
            if candidate.is_dir():
                return candidate
        raise CommandError(
            f"no checkout for {source.stable_id}: expected "
            f"{root / source.stable_id} or {root / source.repository_name}"
        )

    # -- offline git reads --------------------------------------------------

    def _git(self, root: Path, *arguments: str) -> str:
        try:
            completed = subprocess.run(
                ["git", "-C", str(root), *arguments],
                check=False,
                capture_output=True,
                text=True,
                timeout=GIT_TIMEOUT_SECONDS,
            )
        except (OSError, subprocess.SubprocessError) as error:
            raise CommandError(f"git is unavailable for {root}") from error
        if completed.returncode != 0:
            raise CommandError(
                f"git {' '.join(arguments)} failed in {root}: "
                f"{completed.stderr.strip() or completed.returncode}"
            )
        return completed.stdout

    def _describe_checkout(
        self,
        source: ContentSource,
        checkout: Path,
        *,
        allow_modified: bool,
    ) -> tuple[str, list[str], list[str]]:
        """Return the commit, the tracked paths, and the waivers that were used."""

        if not checkout.is_dir():
            raise CommandError(f"{source.stable_id}: {checkout} is not a directory")
        commit_sha = self._git(checkout, "rev-parse", "HEAD").strip()
        branch = self._git(checkout, "rev-parse", "--abbrev-ref", "HEAD").strip()
        dirty = self._git(checkout, "status", "--porcelain=v1", "--untracked-files=no").strip()

        waivers: list[str] = []
        if branch != source.branch:
            message = (
                f"{source.stable_id}: {checkout} is on {branch!r} but the source is "
                f"registered for {source.branch!r}"
            )
            if not allow_modified:
                raise CommandError(
                    f"{message}; check the branch out or pass --allow-modified-checkout"
                )
            waivers.append(f"branch {branch!r} instead of {source.branch!r}")
        if dirty:
            # porcelain v1 is "XY<space>PATH"; the status columns may be blank.
            changed = [line[2:].lstrip() for line in dirty.splitlines()][:5]
            message = (
                f"{source.stable_id}: {checkout} has uncommitted changes "
                f"({', '.join(changed)}{', ...' if len(dirty.splitlines()) > 5 else ''})"
            )
            if not allow_modified:
                raise CommandError(
                    f"{message}; commit or stash them, or pass --allow-modified-checkout"
                )
            waivers.append(f"{len(dirty.splitlines())} uncommitted change(s)")

        tracked = [line for line in self._git(checkout, "ls-files", "-z").split("\0") if line]
        if not tracked:
            raise CommandError(f"{source.stable_id}: {checkout} tracks no files")
        return commit_sha, tracked, waivers

    # -- entry point --------------------------------------------------------

    def handle(self, *args: object, **options: object) -> None:
        del args
        raw_root = cast("str | None", options["from_disk"])
        root = Path(raw_root).expanduser() if raw_root else None
        explicit = self._explicit_checkouts(cast("list[str]", options["checkout"]))
        sources = self._sources(cast("list[str]", options["stable_ids"]))
        plan_only = cast(bool, options["checkout_plan"])
        allow_modified = cast(bool, options["allow_modified_checkout"])

        if plan_only:
            for source in sources:
                try:
                    target = self._checkout_for(source, root=root, explicit=explicit)
                except CommandError:
                    # Nothing is there yet, so report where a clone should land.
                    target = (root or Path(".")) / source.stable_id
                self.stdout.write(
                    f"{source.stable_id}\t{source.repository_owner}/{source.repository_name}"
                    f"\t{source.branch}\t{target}"
                )
            return

        try:
            assert_local_database()
        except LocalCourseSeedError as error:
            raise CommandError(
                "pulling content writes course rows and is refused outside a local or "
                f"test SQLite database ({error})."
            ) from None

        results: list[CourseRepositoryIngestResult] = []
        failures: list[str] = []
        single = len(sources) == 1
        for source in sources:
            # One unusable checkout must not hide what the rest would have done.
            # Every source is attempted and the aggregate failure is raised last.
            try:
                checkout = self._checkout_for(source, root=root, explicit=explicit)
                commit_sha, tracked, waivers = self._describe_checkout(
                    source, checkout, allow_modified=allow_modified
                )
            except CommandError as error:
                if single:
                    raise
                failures.append(source.stable_id)
                self.stderr.write(self.style.ERROR(f"  SKIPPED [{source.stable_id}]: {error}"))
                continue
            for waiver in waivers:
                self.stderr.write(
                    self.style.WARNING(
                        f"  waived for {source.stable_id}: {waiver}; the import still "
                        f"records {commit_sha}"
                    )
                )
            self.stdout.write(f"Pulling {source.stable_id} from {checkout} at {commit_sha}...")
            try:
                result = ingest_course_repository(
                    source=source,
                    commit_sha=commit_sha,
                    checkout_root=checkout,
                    checkout_paths=tracked,
                )
            except CourseRepositoryIngestError as error:
                failures.append(source.stable_id)
                self.stderr.write(self.style.ERROR(f"  REFUSED [{source.stable_id}]: {error}"))
                continue
            results.append(result)
            self.stdout.write(
                f"  {result.file_count} files, "
                + ", ".join(f"{key}={value}" for key, value in sorted(result.counts.items()))
                + (" (replayed)" if result.replayed else "")
            )

        self.stdout.write(json.dumps([result.summary() for result in results], sort_keys=True))
        if failures:
            raise CommandError(f"course repository pull refused: {', '.join(failures)}")
