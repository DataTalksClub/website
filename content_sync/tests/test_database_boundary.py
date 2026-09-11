"""Keep the ``SimpleTestCase`` boundary in ``content_sync`` observable by a run.

The course catalogue moved into the database underneath this package, and every
``SimpleTestCase`` that had quietly started reading a row failed loudly -- except
the ones no environment runs. ``AcceptedDtcContentCheckoutTests`` is gated behind
``DTC_CONTENT_ACCEPTED_CHECKOUT``, which is unset in every default environment
and in CI, so its wall was never hit and the defect survived the two commits that
cleared the rest of the suite (#324).

These tests read the source rather than run it, so they hold under a plain
``manage.py test content_sync`` with no checkout, no fixtures and no network --
the property that was missing. Every ``SimpleTestCase`` subclass in the package
carries a written verdict below, and any database read the analysis can reach
from one has to be recorded here with the chain and the reason it is safe.

The decision rule, from #324:

* the unit reads a row and the test asserts on it -> ``TestCase``, and give it
  the rows;
* the unit reads a row incidentally -> push the dependency out of the unit;
  do not monkeypatch ``content.catalogue`` to keep ``SimpleTestCase``;
* the unit reads no row -> ``SimpleTestCase``, with the call chain recorded.

A file-backed or hardcoded content fallback is never an answer here
(``AGENTS.md``, ``_docs/architecture/database-only-content.md``).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from django.test import SimpleTestCase

from content_sync.tests import database_boundary

PROBE_MODULE = "content_sync.tests.fixtures.database_boundary_probe"

DECISION_RULE = (
    "Decide per class: does the unit under test read a row on the path this "
    "test exercises? Yes -> django.test.TestCase and give it the rows. "
    "Incidentally -> push the dependency out of the unit. No -> SimpleTestCase, "
    "and record the call chain in content_sync/tests/test_database_boundary.py. "
    "See #324 and _docs/architecture/database-only-content.md."
)


@dataclass(frozen=True)
class Verdict:
    """Why a class is entitled to keep refusing the database."""

    reason: str
    #: First-party callables this class reaches that the analysis reports as
    #: database work, and which the executed path provably never gets to. Each
    #: one is an over-approximation the analysis cannot see through, never a
    #: tolerated read.
    allowed_reaches: tuple[str, ...] = field(default_factory=tuple)


VERDICTS: dict[str, Verdict] = {
    "CourseRepositoryArchiveTests": Verdict(
        "content_sync.course_repository_ingest.fetch_course_repository_snapshot unpacks a "
        "patched requests response with tarfile and returns bytes; no model and no "
        "catalogue on the path.",
    ),
    "SourceCommitReachabilityTests": Verdict(
        "content_sync.course_repository_ingest.commit_is_public and public_repository_urls "
        "shell out to git in a temporary checkout and compare strings.",
    ),
    "RegistrationInputTests": Verdict(
        "content_sync.course_repository_registration.load_registration_input parses a JSON "
        "file into dataclasses; the rows are written by the management command, which is "
        "covered by CourseRepositoryRegistrationTests(TestCase).",
    ),
    "WebhookSignatureTests": Verdict(
        "content_sync.webhook_delivery.verify_webhook_signature is an HMAC over bytes.",
    ),
    "SimpleTestCaseVerdictTests": Verdict(
        "This file's own classes. They parse the package with ast and compare names; the "
        "runner reports 'Skipping setup of unused database(s)' for the module.",
    ),
    "SimpleTestCaseDatabaseReachTests": Verdict(
        "See SimpleTestCaseVerdictTests.",
    ),
    "DatabaseBoundaryAnalysisTests": Verdict(
        "See SimpleTestCaseVerdictTests. It parses the probe fixture, which is never "
        "imported or collected, so its planted catalogue reads never execute.",
    ),
}


def _module_classes() -> dict[str, str]:
    """Every ``SimpleTestCase`` subclass in the package, mapped to its module."""

    found: dict[str, str] = {}
    for module in database_boundary.test_modules():
        for name in database_boundary.simple_test_case_classes(module):
            found[name] = module
    return found


class SimpleTestCaseVerdictTests(SimpleTestCase):
    """Every class that refuses the database has said why, in writing."""

    def test_every_simple_test_case_class_carries_a_recorded_verdict(self) -> None:
        classes = _module_classes()
        unrecorded = {name: module for name, module in classes.items() if name not in VERDICTS}
        self.assertEqual(
            unrecorded,
            {},
            "SimpleTestCase subclass without a recorded verdict in VERDICTS: "
            f"{sorted(unrecorded)}. {DECISION_RULE}",
        )

    def test_no_recorded_verdict_outlives_its_class(self) -> None:
        classes = _module_classes()
        stale = sorted(set(VERDICTS) - set(classes))
        self.assertEqual(
            stale,
            [],
            "VERDICTS names a class that is no longer a SimpleTestCase in "
            f"content_sync/tests: {stale}. Delete the entry; a verdict for a class that "
            "moved to TestCase records nothing.",
        )

    def test_no_two_classes_share_one_verdict(self) -> None:
        """``VERDICTS`` is keyed by class name, so a name may occur once.

        Two same-named classes in different modules would let the second one
        inherit a justification written about the first -- a quiet way back to
        an unexamined ``SimpleTestCase``.
        """

        seen: dict[str, list[str]] = {}
        for module in database_boundary.test_modules():
            for name in database_boundary.simple_test_case_classes(module):
                seen.setdefault(name, []).append(module)
        duplicates = {name: modules for name, modules in seen.items() if len(modules) > 1}
        self.assertEqual(
            duplicates,
            {},
            f"Two SimpleTestCase classes share a name: {duplicates}. Rename one, or the "
            "verdict recorded for the first silently covers the second.",
        )

    def test_at_least_one_class_still_refuses_the_database(self) -> None:
        """Zero remaining is a failure, not a clean sweep.

        Promoting the module wholesale would delete the boundary this file
        exists to police, so the guard insists the honest ones survive.
        """

        self.assertNotEqual(_module_classes(), {})


class SimpleTestCaseDatabaseReachTests(SimpleTestCase):
    """No ``SimpleTestCase`` here may reach a row the analysis can resolve."""

    def test_no_unrecorded_database_reach_from_a_simple_test_case(self) -> None:
        offences: list[str] = []
        for module in database_boundary.test_modules():
            for reach in database_boundary.database_reaches(module):
                verdict = VERDICTS.get(reach.test_class)
                if verdict is not None and reach.entry in verdict.allowed_reaches:
                    continue
                offences.append(f"{module}: {reach.described()}")
        self.assertEqual(
            sorted(set(offences)),
            [],
            "A SimpleTestCase in content_sync/tests reaches the database. "
            f"{DECISION_RULE} Chains:\n" + "\n".join(sorted(set(offences))),
        )

    def test_every_recorded_allowance_is_still_reached(self) -> None:
        reached: set[tuple[str, str]] = set()
        for module in database_boundary.test_modules():
            for reach in database_boundary.database_reaches(module):
                reached.add((reach.test_class, reach.entry))
        stale = sorted(
            f"{name} -> {entry}"
            for name, verdict in VERDICTS.items()
            for entry in verdict.allowed_reaches
            if (name, entry) not in reached
        )
        self.assertEqual(
            stale,
            [],
            f"VERDICTS allows a database reach that no longer exists: {stale}. Delete it, "
            "so the next reader does not read a justification for something the code "
            "stopped doing.",
        )


class DatabaseBoundaryAnalysisTests(SimpleTestCase):
    """The analysis itself, exercised against planted defects.

    Without this the guard would only ever be observed green, which is exactly
    how the original defect survived: a check nobody has seen fail is a claim,
    not a test.
    """

    def test_the_analysis_finds_every_planted_database_reach(self) -> None:
        reaches = database_boundary.database_reaches(PROBE_MODULE)
        found = {reach.test_class for reach in reaches}
        self.assertEqual(
            found,
            {
                "PlantedDirectCatalogueReaderTests",
                "PlantedIndirectCatalogueReaderTests",
                "PlantedManagerAccessTests",
                "PlantedFunctionBodyImportTests",
            },
        )

    def test_the_planted_indirect_reach_reports_the_whole_chain(self) -> None:
        """The message has to name every hop, or it cannot be acted on."""

        reaches = [
            reach
            for reach in database_boundary.database_reaches(PROBE_MODULE)
            if reach.test_class == "PlantedIndirectCatalogueReaderTests"
            and reach.chain[0].endswith("test_reaches_the_catalogue_through_a_sibling_and_a_helper")
        ]
        self.assertEqual(len(reaches), 1, [reach.described() for reach in reaches])
        reach = reaches[0]
        self.assertEqual(
            reach.chain[:-1],
            (
                "PlantedIndirectCatalogueReaderTests."
                "test_reaches_the_catalogue_through_a_sibling_and_a_helper",
                "self.helper",
                f"{PROBE_MODULE}._planted_helper",
                "content_sync.dtc_content.adapter.adapt_dtc_content_checkout",
                "content_sync.dtc_content.adapter._checked_contracts",
            ),
        )
        self.assertTrue(reach.chain[-1].startswith("content.catalogue."), reach.described())
        self.assertEqual(reach.signal, database_boundary.CATALOGUE_SIGNAL)

    def test_the_analysis_resolves_a_reader_imported_inside_a_test_body(self) -> None:
        """A name bound by an import in a function body is still followed.

        This shape passed the guard once: the module-level namespace walk never
        bound the name, so the class was reported clean while dying on the
        database wall the moment it ran. It is not indirection -- the callee is
        written out in full -- and the idiom is already live in this package at
        test_course_repository_transport_parity.py:237.
        """

        # Bound nowhere but inside the planted method, so a chain can only come
        # from reading the function body.
        self.assertNotIn(
            "verify_initial_projection_parity",
            database_boundary._namespace(PROBE_MODULE),
        )
        reaches = [
            reach
            for reach in database_boundary.database_reaches(PROBE_MODULE)
            if reach.test_class == "PlantedFunctionBodyImportTests"
        ]
        self.assertEqual(len(reaches), 1, [reach.described() for reach in reaches])
        reach = reaches[0]
        self.assertEqual(
            reach.chain[:-1],
            (
                "PlantedFunctionBodyImportTests."
                "test_reads_the_catalogue_through_a_function_body_import",
                "content_sync.dtc_content.parity.verify_initial_projection_parity",
                "content_sync.dtc_content.parity.published_catalogue",
            ),
        )
        self.assertTrue(reach.chain[-1].startswith("content.catalogue."), reach.described())
        self.assertEqual(reach.signal, database_boundary.CATALOGUE_SIGNAL)

    def test_the_analysis_leaves_the_row_free_planted_class_alone(self) -> None:
        flagged = {reach.test_class for reach in database_boundary.database_reaches(PROBE_MODULE)}
        self.assertNotIn("PlantedRowFreeTests", flagged)
        planted = database_boundary.simple_test_case_classes(PROBE_MODULE)
        self.assertIn("PlantedRowFreeTests", planted)

    def test_the_probe_is_not_collected_as_a_test_module(self) -> None:
        """The planted classes must never run; they only get parsed."""

        self.assertNotIn(PROBE_MODULE, database_boundary.test_modules())
