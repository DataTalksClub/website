"""Every `scripts/prod` entry point is runnable, or says why it is not.

Four importers used to be runbook-only prose: nothing in the `Makefile`
executed them and nothing held them to an order. This module is the rule that
stops that recurring, so the checks are written as properties of the file
rather than as a list of the eight target names that happen to exist today.

The central one is a **closure**: every non-package module in `scripts/prod` is
either invoked by a Makefile recipe line or carries a written reason in
`scripts.prod.MAKE_TARGET_EXCLUSIONS`. Comments are stripped first --
`sync_public_media_hydrate` is named in the Makefile only inside a comment, and
a name in prose is not a way to run anything.

The rest hold the properties the runbooks state about these particular targets:
they fail closed, with no default, on the variables that choose the export and
the destination database; they never pass `--apply` to the account
reconciliation; they never carry a deployed-write flag; and none of them is
reachable from a local dataset rebuild.

`make -n` executes any recipe line containing `$(MAKE)`, so `-n` is used on leaf
targets only and the composite targets are checked by parsing the file.

Reading the Makefile
--------------------

Reachability follows both edges out of a target -- a prerequisite and a `$(MAKE)`
recipe line -- and finds the recursive call anywhere in a reassembled command, not
only as its first token. Both matter for the same reason: a rebuild that reaches an
identity importer through a shell conditional, or through a prerequisite, pulls real
accounts into a development database just as surely as a plain recipe line would, and
a block that delegates through `$(MAKE)` never names the script it runs, so no
script-path check would see it either.
"""

from __future__ import annotations

import os
import pkgutil
import re
import subprocess
from pathlib import Path

from django.test import SimpleTestCase

import scripts.prod
from scripts.prod import CMP_LEARNER_ORDER, MAKE_TARGET_EXCLUSIONS

PROD_ROOT = Path(scripts.prod.__file__).resolve().parent
PROJECT_ROOT = PROD_ROOT.parents[1]
MAKEFILE = PROJECT_ROOT / "Makefile"

# The targets that touch real accounts or real learner history.
# `import-cmp-content` is deliberately not one of them: it reads content rows only.
IDENTITY_TARGETS = (
    "import-cmp-learners",
    "import-cmp-learners-status",
    "import-cmp-learner-history",
    "import-cmp-learner-history-status",
    "import-cmp-learner-data",
    "import-account-reconciliation",
    "import-account-reconciliation-rollback-check",
)

# Variables the new targets refuse without. Cleared from the environment before
# every guard check, because make lets an environment value win over `?=`.
GUARDED_VARIABLES = (
    "CMP_EXPORT",
    "IMPORT_LEARNER_DATABASE",
    "IMPORT_DATABASE",
    "CMP_CLAIMS_FILE",
    "CMP_CLAIMS_DIR",
    "SNAPSHOT_ID",
    "ACCOUNT_RECONCILIATION_MAPPING",
    "ACCOUNT_RECONCILIATION_OUTPUT",
)

REBUILD_TARGETS = (
    "production-prep-bootstrap",
    "production-prep-dataset",
    "production-prep-local",
)


def _entry_point_names() -> list[str]:
    return sorted(
        module.name
        for module in pkgutil.iter_modules([str(PROD_ROOT)])
        if not module.ispkg and module.name not in scripts.prod.LIBRARY_MODULES
    )


def _makefile_lines() -> list[str]:
    return MAKEFILE.read_text(encoding="utf-8").splitlines()


def _recipe_lines() -> list[str]:
    """Recipe lines only, with comment lines dropped.

    A recipe line starts with a tab. A line that does not is a variable
    assignment, a target header or a make comment, and none of those runs
    anything. A tab-indented line whose first character is `#` is a shell
    comment and does not run anything either.
    """

    recipes = []
    for line in _makefile_lines():
        if not line.startswith("\t"):
            continue
        body = line.lstrip("\t").strip()
        if body.startswith("#"):
            continue
        recipes.append(body)
    return recipes


TARGET_NAME = re.compile(r"^[A-Za-z0-9._-]+$")


def _target_blocks() -> dict[str, list[str]]:
    """Every target header mapped to its recipe lines, comments stripped."""

    blocks: dict[str, list[str]] = {}
    for name, (_prerequisites, body) in _target_headers().items():
        blocks[name] = body
    return blocks


def _target_headers() -> dict[str, tuple[list[str], list[str]]]:
    """Every target mapped to its prerequisite names and its recipe lines.

    Both edges out of a target are captured. A prerequisite reaches a target just
    as really as a `$(MAKE)` recipe line does, so a reachability question that
    reads only one of them is not a question about this file.
    """

    header = re.compile(r"^(?P<names>[A-Za-z0-9._$()%/-][^:=]*):(?!=)(?P<prerequisites>.*)$")
    headers: dict[str, tuple[list[str], list[str]]] = {}
    lines = _makefile_lines()
    for index, line in enumerate(lines):
        if line.startswith("\t"):
            continue
        match = header.match(line)
        if match is None:
            continue
        body: list[str] = []
        for recipe in lines[index + 1 :]:
            if not recipe.startswith("\t"):
                break
            stripped = recipe.lstrip("\t").strip()
            if stripped.startswith("#"):
                continue
            body.append(stripped)
        prerequisites = [
            token for token in match.group("prerequisites").split() if TARGET_NAME.match(token)
        ]
        for name in match.group("names").split():
            if name.startswith("."):
                continue
            headers[name] = (prerequisites, body)
    return headers


def _logical_recipe_lines(body: list[str]) -> list[str]:
    """Join make's backslash continuations back into one command each."""

    joined: list[str] = []
    pending = ""
    for line in body:
        if line.endswith("\\"):
            pending += line[:-1].strip() + " "
            continue
        joined.append((pending + line).strip())
        pending = ""
    if pending:
        joined.append(pending.strip())
    return joined


def _submake_targets(body: list[str]) -> list[str]:
    """Every target a recipe starts with `$(MAKE)`, wherever it sits in the line.

    Searched across the whole reassembled command, not anchored at its first
    token. A recursive make is just as real inside a shell conditional --

        @if test -n "$(CMP_EXPORT)"; then \\
            $(MAKE) import-cmp-learners ...; \\
        fi

    -- and that is the shape a future author reaches for precisely because
    `CMP_EXPORT` has no default. Anchoring here let that through twice over: the
    logical line begins with `@if`, and no path check catches it either, because
    a block that delegates through `$(MAKE)` never names the script it runs.
    """

    return [
        match.group(1)
        for command in _logical_recipe_lines(body)
        for match in re.finditer(r"\$\(MAKE\)\s+([A-Za-z0-9._-]+)", command)
    ]


def _reachable_targets(root: str) -> set[str]:
    """Every target `root` reaches, transitively, by either kind of edge.

    Both a `$(MAKE)` recipe line and a prerequisite really run the target they
    name, so both are followed.
    """

    headers = _target_headers()
    seen: set[str] = set()
    queue = [root]
    while queue:
        prerequisites, body = headers.get(queue.pop(), ([], []))
        for target in [*prerequisites, *_submake_targets(body)]:
            if target not in seen:
                seen.add(target)
                queue.append(target)
    return seen


def _modules_without_a_way_in(exclusions: dict[str, str]) -> set[str]:
    """The closure rule itself: entry points with neither a recipe nor a reason.

    It runs over the modules present on disk, so an exclusion naming a module that
    has not landed yet is simply unused.
    """

    recipes = "\n".join(_recipe_lines())
    return {
        name
        for name in _entry_point_names()
        if f"scripts/prod/{name}.py" not in recipes and not exclusions.get(name, "").strip()
    }


def _variable_assignments(variable: str) -> list[str]:
    source = MAKEFILE.read_text(encoding="utf-8")
    return re.findall(rf"^{re.escape(variable)}\s*[:?]?=(?P<value>.*)$", source, re.MULTILINE)


def _clean_environment(**overrides: str) -> dict[str, str]:
    environment = dict(os.environ)
    for name in GUARDED_VARIABLES:
        environment.pop(name, None)
    # A parent `make` exports its flags and command-line overrides to every
    # sub-make it starts, this test's subprocess included.
    for name in ("MAKEFLAGS", "MAKEOVERRIDES", "MAKELEVEL", "MFLAGS"):
        environment.pop(name, None)
    environment.update(overrides)
    return environment


def _run_make(*arguments: str, **overrides: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["make", *arguments],
        cwd=PROJECT_ROOT,
        env=_clean_environment(**overrides),
        capture_output=True,
        text=True,
        check=False,
    )


class MakefileClosureTests(SimpleTestCase):
    """A new entry point is runnable, or its absence is written down."""

    def test_every_entry_point_is_invoked_or_excluded_with_a_reason(self) -> None:
        unreachable = _modules_without_a_way_in(MAKE_TARGET_EXCLUSIONS)
        self.assertEqual(
            unreachable,
            set(),
            f"{sorted(unreachable)}: invoked by no Makefile recipe and carrying no "
            f"MAKE_TARGET_EXCLUSIONS entry. Give each one a target, or record in "
            f"scripts/prod/__init__.py why it is deliberately reachable only by a "
            f"typed command.",
        )

    def test_an_excluded_module_is_not_also_invoked(self) -> None:
        """An exclusion whose module is reachable anyway is a stale claim."""

        recipes = "\n".join(_recipe_lines())
        for name in sorted(MAKE_TARGET_EXCLUSIONS):
            with self.subTest(module=name):
                self.assertNotIn(
                    f"scripts/prod/{name}.py",
                    recipes,
                    f"{name} is excluded from the Makefile but a recipe runs it; drop the "
                    f"MAKE_TARGET_EXCLUSIONS entry.",
                )

    def test_every_exclusion_carries_a_non_empty_reason(self) -> None:
        for name, reason in sorted(MAKE_TARGET_EXCLUSIONS.items()):
            with self.subTest(module=name):
                self.assertTrue(reason.strip(), f"{name} needs a written reason")

    def test_a_comment_only_mention_does_not_count_as_reachable(self) -> None:
        """`sync_public_media_hydrate` is in the Makefile, inside a comment."""

        self.assertIn(
            "scripts/prod/sync_public_media_hydrate.py",
            MAKEFILE.read_text(encoding="utf-8"),
        )
        self.assertNotIn(
            "scripts/prod/sync_public_media_hydrate.py",
            "\n".join(_recipe_lines()),
        )
        self.assertIn("sync_public_media_hydrate", MAKE_TARGET_EXCLUSIONS)

    def test_the_undecided_syncs_name_the_epic_rather_than_invent_a_rule(self) -> None:
        for name in (
            "sync_content",
            "sync_public_media_hydrate",
            "sync_public_media_publish",
            "sync_public_media_verify",
        ):
            with self.subTest(module=name):
                self.assertIn("#310", MAKE_TARGET_EXCLUSIONS[name])

    def test_a_declared_exclusion_for_an_absent_module_is_tolerated(self) -> None:
        """Matching the bootstrapping check: a script can be in flight elsewhere.

        The closure runs over the modules on disk, so an exclusion naming a module
        that has not landed yet is unused rather than a failure.
        """

        self.assertNotIn("import_not_landed_yet", _entry_point_names())
        phantom = dict(MAKE_TARGET_EXCLUSIONS, import_not_landed_yet="in flight elsewhere")
        self.assertEqual(_modules_without_a_way_in(phantom), set())


class CmpTargetShapeTests(SimpleTestCase):
    """What each new recipe passes, and what it must never pass."""

    def test_the_four_cmp_entry_points_have_targets(self) -> None:
        blocks = _target_blocks()
        expected = {
            "import-cmp-content": "scripts/prod/import_cmp_content.py",
            "import-cmp-learners": "scripts/prod/import_cmp_learners.py",
            "import-cmp-learners-status": "scripts/prod/import_cmp_learners.py",
            "import-cmp-learner-history": "scripts/prod/import_cmp_learner_history.py",
            "import-cmp-learner-history-status": "scripts/prod/import_cmp_learner_history.py",
            "import-account-reconciliation": "scripts/prod/import_account_reconciliation.py",
            "import-account-reconciliation-rollback-check": (
                "scripts/prod/import_account_reconciliation.py"
            ),
        }
        for target, script in expected.items():
            with self.subTest(target=target):
                self.assertIn(target, blocks, f"Makefile target {target} is missing")
                self.assertIn(script, "\n".join(blocks[target]))

    def test_the_content_target_writes_to_the_ordinary_import_database(self) -> None:
        """Content only -- no account, enrollment, submission or registration row."""

        block = "\n".join(_target_blocks()["import-cmp-content"])
        self.assertIn('--database "$(IMPORT_DATABASE)"', block)
        self.assertIn('--source "$(CMP_EXPORT)"', block)

    def test_the_learner_targets_write_to_their_own_database_variable(self) -> None:
        blocks = _target_blocks()
        for target in IDENTITY_TARGETS:
            if target == "import-cmp-learner-data":
                continue
            with self.subTest(target=target):
                block = "\n".join(blocks[target])
                self.assertIn('--database "$(IMPORT_LEARNER_DATABASE)"', block)
                self.assertNotIn("$(IMPORT_DATABASE)", block)

    def test_every_new_target_is_phony(self) -> None:
        match = re.search(
            r"^\.PHONY:(?P<names>(?:[^\n]*\\\n)*[^\n]*)",
            MAKEFILE.read_text(encoding="utf-8"),
            re.MULTILINE,
        )
        assert match is not None
        declared = set(match.group("names").replace("\\", " ").split())
        for target in ("import-cmp-content", *IDENTITY_TARGETS):
            with self.subTest(target=target):
                self.assertIn(target, declared)

    def test_the_export_and_learner_database_have_no_default(self) -> None:
        for variable in ("CMP_EXPORT", "IMPORT_LEARNER_DATABASE"):
            with self.subTest(variable=variable):
                assignments = _variable_assignments(variable)
                self.assertEqual(len(assignments), 1, f"{variable} must be assigned exactly once")
                self.assertEqual(
                    assignments[0].strip(),
                    "",
                    f"{variable} must have no default value: a guessed one is how the "
                    f"wrong export, or the dev dataset, receives 20,000 real people.",
                )

    def test_no_new_default_points_into_the_export_directory(self) -> None:
        for variable in (
            "CMP_EXPORT",
            "IMPORT_LEARNER_DATABASE",
            "CMP_CLAIMS_FILE",
            "CMP_CLAIMS_DIR",
            "ACCOUNT_RECONCILIATION_MAPPING",
            "ACCOUNT_RECONCILIATION_OUTPUT",
        ):
            for value in _variable_assignments(variable):
                with self.subTest(variable=variable):
                    self.assertNotIn("/data/tmp/", value)
        for command in _recipe_lines():
            with self.subTest(command=command):
                self.assertNotIn("/data/tmp/", command)

    def test_no_recipe_passes_apply_to_the_account_reconciliation(self) -> None:
        """The one migration step with no rollback stays a typed command."""

        for target, body in sorted(_target_blocks().items()):
            text = "\n".join(body)
            if "import_account_reconciliation.py" not in text:
                continue
            with self.subTest(target=target):
                self.assertNotIn(
                    "--apply",
                    text,
                    f"{target} passes --apply; applying a reviewed merge mapping has no "
                    f"rollback and must stay a consciously typed command.",
                )

    def test_no_recipe_can_write_to_a_deployed_target(self) -> None:
        """`production-data-migration.md` §4 states this about the make wrappers."""

        for command in _recipe_lines():
            with self.subTest(command=command):
                self.assertNotIn("--deployment-target", command)
                self.assertNotIn("--allow-production-write", command)

    def test_no_recipe_copies_the_export(self) -> None:
        """It is passed to `--source` and read in place."""

        for target, body in sorted(_target_blocks().items()):
            for command in _logical_recipe_lines(body):
                if "$(CMP_EXPORT)" not in command:
                    continue
                with self.subTest(target=target, command=command):
                    self.assertNotRegex(command, r"(^|\s)(cp|rsync|install)\s")
                    self.assertNotIn(".backup", command)

    def test_the_status_targets_never_open_the_export(self) -> None:
        blocks = _target_blocks()
        for target in ("import-cmp-learners-status", "import-cmp-learner-history-status"):
            with self.subTest(target=target):
                text = "\n".join(blocks[target])
                self.assertIn("--status", text)
                self.assertNotIn("--source", text)
                self.assertNotIn("CMP_EXPORT", text)

    def test_a_status_target_runs_without_the_export_variable(self) -> None:
        result = _run_make(
            "-n",
            "import-cmp-learners-status",
            IMPORT_LEARNER_DATABASE=".tmp/does-not-exist.sqlite3",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        printed = " ".join(result.stdout.split()).replace('"', "")
        self.assertIn("--status", printed)
        self.assertNotIn("--source", printed)

    def test_the_history_target_takes_the_same_database_as_the_accounts_run(self) -> None:
        """The account claims live in the target database, so there is no
        claims file to pair -- the two runs share the database instead."""

        result = _run_make(
            "-n",
            "import-cmp-learner-history",
            IMPORT_LEARNER_DATABASE=".tmp/does-not-exist.sqlite3",
            CMP_EXPORT="/nonexistent/export.db",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        printed = " ".join(result.stdout.split()).replace('"', "")
        self.assertIn("--source /nonexistent/export.db", printed)
        self.assertNotIn("--user-claims-file", printed)
        self.assertNotIn("--claims-dir", printed)
        self.assertNotIn("--deployment-target", printed)

    def test_the_dry_run_target_writes_a_report_and_takes_no_mapping(self) -> None:
        result = _run_make(
            "-n",
            "import-account-reconciliation",
            IMPORT_LEARNER_DATABASE=".tmp/does-not-exist.sqlite3",
            SNAPSHOT_ID="a" * 64,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        printed = " ".join(result.stdout.split()).replace('"', "")
        self.assertIn("--output .tmp/account-reconciliation-dry-run.json", printed)
        self.assertNotIn("--apply", printed)
        self.assertNotIn("--mapping", printed)

    def test_the_rollback_check_target_consumes_the_reviewed_mapping(self) -> None:
        result = _run_make(
            "-n",
            "import-account-reconciliation-rollback-check",
            IMPORT_LEARNER_DATABASE=".tmp/does-not-exist.sqlite3",
            SNAPSHOT_ID="a" * 64,
            ACCOUNT_RECONCILIATION_MAPPING=".tmp/does-not-exist-mapping.json",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        printed = " ".join(result.stdout.split()).replace('"', "")
        self.assertIn("--rollback-check", printed)
        self.assertIn("--mapping .tmp/does-not-exist-mapping.json", printed)
        self.assertNotIn("--apply", printed)


class CmpLearnerOrderTests(SimpleTestCase):
    """The declared order, and the composite target that runs it."""

    def test_the_declared_order_puts_the_reconciler_last(self) -> None:
        self.assertEqual(
            CMP_LEARNER_ORDER,
            ("import_cmp_content", "import_cmp_learners", "import_cmp_learner_history"),
        )
        present = set(_entry_point_names())
        for name in CMP_LEARNER_ORDER:
            with self.subTest(module=name):
                self.assertIn(name, present)

    def test_the_composite_runs_the_two_learner_legs_in_the_declared_order(self) -> None:
        invoked = _submake_targets(_target_blocks()["import-cmp-learner-data"])
        self.assertEqual(invoked, [name.replace("_", "-") for name in CMP_LEARNER_ORDER[1:]])

    def test_the_composite_uses_recipe_lines_not_prerequisites(self) -> None:
        """`make -j` must not interleave two writers on one SQLite file."""

        header = re.search(
            r"^import-cmp-learner-data:(?P<prerequisites>.*)$",
            MAKEFILE.read_text(encoding="utf-8"),
            re.MULTILINE,
        )
        assert header is not None
        self.assertEqual(header.group("prerequisites").strip(), "")

    def test_the_composite_passes_every_variable_through(self) -> None:
        commands = _logical_recipe_lines(_target_blocks()["import-cmp-learner-data"])
        accounts = next(command for command in commands if "import-cmp-learners" in command)
        history = next(command for command in commands if "import-cmp-learner-history" in command)
        # Claims are database rows now, so the only variables that matter are
        # the shared database and the export.
        for variable in ("IMPORT_LEARNER_DATABASE", "CMP_EXPORT"):
            with self.subTest(variable=variable):
                self.assertIn(f'{variable}="$({variable})"', accounts)
                self.assertIn(f'{variable}="$({variable})"', history)

    def test_no_target_runs_the_history_importer_before_the_accounts_importer(self) -> None:
        accounts = "scripts/prod/import_cmp_learners.py"
        history = "scripts/prod/import_cmp_learner_history.py"
        for target, body in sorted(_target_blocks().items()):
            commands = _logical_recipe_lines(body)
            positions = {
                script: [index for index, command in enumerate(commands) if script in command]
                for script in (accounts, history)
            }
            if not positions[accounts] or not positions[history]:
                continue
            with self.subTest(target=target):
                self.assertLess(positions[accounts][0], positions[history][0])


class RebuildExclusionTests(SimpleTestCase):
    """A local dataset rebuild never pulls real accounts in on its way past."""

    def test_no_rebuild_target_reaches_a_pii_or_identity_target(self) -> None:
        for root in REBUILD_TARGETS:
            reachable = _reachable_targets(root)
            for target in IDENTITY_TARGETS:
                with self.subTest(root=root, target=target):
                    self.assertNotIn(target, reachable)

    def test_no_rebuild_target_names_a_pii_or_identity_target(self) -> None:
        """The other half: the target name must not *appear* in the blocks either.

        A block that delegates through `$(MAKE)` never names the script it runs, so
        the script-path check below cannot see it. Reachability and appearance catch
        different halves of the same rule and neither is redundant.
        """

        blocks = _target_blocks()
        for root in REBUILD_TARGETS:
            text = "\n".join(
                "\n".join(blocks.get(target, []))
                for target in sorted({root, *_reachable_targets(root)})
            )
            for target in IDENTITY_TARGETS:
                with self.subTest(root=root, target=target):
                    self.assertNotIn(
                        target,
                        text,
                        f"{root} names {target} in a recipe it reaches; a local dataset "
                        f"rebuild must not run an identity importer on its way past.",
                    )

    def test_no_rebuild_target_runs_a_pii_or_identity_script(self) -> None:
        blocks = _target_blocks()
        forbidden = (
            "scripts/prod/import_cmp_learners.py",
            "scripts/prod/import_cmp_learner_history.py",
            "scripts/prod/import_account_reconciliation.py",
            "scripts/prod/import_event_registrants.py",
            "scripts/prod/import_mailchimp_event_tags.py",
            "scripts/prod/import_mailchimp_subscriptions.py",
        )
        for root in REBUILD_TARGETS:
            reachable = {root, *_reachable_targets(root)}
            text = "\n".join("\n".join(blocks.get(target, [])) for target in sorted(reachable))
            for script in forbidden:
                with self.subTest(root=root, script=script):
                    self.assertNotIn(
                        script,
                        text,
                        f"{root} runs {script} in a recipe it reaches; a local dataset "
                        f"rebuild must not read attendee or learner personal data.",
                    )

    def test_a_sub_make_inside_a_shell_conditional_is_resolved(self) -> None:
        """The blind spot this check was rejected for, kept closed.

        `_logical_recipe_lines` already reassembles the whole command; anchoring the
        search at its first token threw that away again. Planted here so the
        resolution cannot silently narrow back to the first token.
        """

        planted = [
            '@if test -n "$(CMP_EXPORT)"; then \\',
            "$(MAKE) import-cmp-learners \\",
            'IMPORT_LEARNER_DATABASE="$(PRODUCTION_PREP_DATASET_DATABASE)"; \\',
            "fi",
        ]
        self.assertEqual(_submake_targets(planted), ["import-cmp-learners"])

    def test_a_prerequisite_is_an_edge_too(self) -> None:
        """`$(MAKE)` is not the only way one target runs another."""

        self.assertIn(
            "production-prep-course-registry",
            _reachable_targets("production-prep-course-sources"),
        )

    def test_the_reachability_models_the_file_it_reads(self) -> None:
        """A closure that misses a real edge is not a closure of this Makefile.

        `production-prep-bootstrap` really does run `import-legacy-zoomcamp`, inside
        exactly the `@if test -n "$(LEGACY_ZOOMCAMP_SOURCE)"` shape a conditional CMP
        import would take. If this fails, the exclusion checks above are reading a
        Makefile that is not the one on disk.
        """

        self.assertIn("import-legacy-zoomcamp", _reachable_targets("production-prep-bootstrap"))

    def test_the_content_import_is_not_invoked_a_second_time(self) -> None:
        """`production-prep-bootstrap` already runs it through `prepare_local_data.py`."""

        reachable = {
            "production-prep-bootstrap",
            *_reachable_targets("production-prep-bootstrap"),
        }
        self.assertNotIn("import-cmp-content", reachable)

    def test_the_event_import_is_not_invoked_a_second_time(self) -> None:
        """`prepare_local_data.py` already runs the whole §11 step-5 pipeline.

        It composes `import_events.run()` itself, so a second `import-events`
        call after it re-parsed both provider exports and re-ran every leg to
        compensate for drift the first pass had not produced.
        """

        reachable = {
            "production-prep-bootstrap",
            *_reachable_targets("production-prep-bootstrap"),
        }
        self.assertNotIn("import-events", reachable)

    def test_the_bootstrap_comment_records_what_is_left_out_and_why(self) -> None:
        preamble = MAKEFILE.read_text(encoding="utf-8").split("production-prep-bootstrap:")[0]
        for phrase in (
            "import-cmp-learners",
            "import-cmp-learner-history",
            "import-account-reconciliation",
        ):
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, preamble)


class FailClosedTests(SimpleTestCase):
    """A missing variable is exit 2, never a skip, a warning or a default."""

    def _assert_refused(
        self, target: str, variable: str, **overrides: str
    ) -> subprocess.CompletedProcess[str]:
        result = _run_make(target, **overrides)
        output = result.stdout + result.stderr
        self.assertEqual(
            result.returncode,
            2,
            f"{target} without {variable} must exit 2, got {result.returncode}: {output}",
        )
        self.assertIn(variable, output, f"{target} must name {variable}: {output}")
        self.assertNotIn(
            "uv run",
            output,
            f"{target} must refuse before any Python starts: {output}",
        )
        return result

    def test_every_export_reading_target_refuses_without_the_export(self) -> None:
        for target in (
            "import-cmp-content",
            "import-cmp-learners",
            "import-cmp-learner-history",
            "import-cmp-learner-data",
        ):
            with self.subTest(target=target):
                self._assert_refused(
                    target,
                    "CMP_EXPORT",
                    IMPORT_LEARNER_DATABASE=".tmp/does-not-exist.sqlite3",
                )

    def test_every_identity_target_refuses_without_a_destination_database(self) -> None:
        for target in IDENTITY_TARGETS:
            with self.subTest(target=target):
                self._assert_refused(
                    target,
                    "IMPORT_LEARNER_DATABASE",
                    CMP_EXPORT="/nonexistent/export.db",
                    SNAPSHOT_ID="a" * 64,
                    ACCOUNT_RECONCILIATION_MAPPING=".tmp/does-not-exist-mapping.json",
                )

    def test_an_export_path_that_does_not_exist_is_refused_by_name(self) -> None:
        for target in (
            "import-cmp-content",
            "import-cmp-learners",
            "import-cmp-learner-history",
            "import-cmp-learner-data",
        ):
            with self.subTest(target=target):
                result = self._assert_refused(
                    target,
                    "CMP_EXPORT",
                    IMPORT_LEARNER_DATABASE=".tmp/does-not-exist.sqlite3",
                    CMP_EXPORT="/nonexistent/export.db",
                )
                self.assertIn("/nonexistent/export.db", result.stdout + result.stderr)

    def test_the_reconciliation_targets_refuse_without_a_snapshot(self) -> None:
        for target in (
            "import-account-reconciliation",
            "import-account-reconciliation-rollback-check",
        ):
            with self.subTest(target=target):
                self._assert_refused(
                    target,
                    "SNAPSHOT_ID",
                    IMPORT_LEARNER_DATABASE=".tmp/does-not-exist.sqlite3",
                    ACCOUNT_RECONCILIATION_MAPPING=".tmp/does-not-exist-mapping.json",
                )

    def test_the_rollback_check_refuses_without_the_reviewed_mapping(self) -> None:
        self._assert_refused(
            "import-account-reconciliation-rollback-check",
            "ACCOUNT_RECONCILIATION_MAPPING",
            IMPORT_LEARNER_DATABASE=".tmp/does-not-exist.sqlite3",
            SNAPSHOT_ID="a" * 64,
        )

    def test_a_refusal_is_never_a_skip_or_a_warning(self) -> None:
        result = _run_make("import-cmp-learners")
        output = (result.stdout + result.stderr).lower()
        self.assertEqual(result.returncode, 2)
        for word in ("skipping", "warning"):
            with self.subTest(word=word):
                self.assertNotIn(word, output)
