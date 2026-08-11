from __future__ import annotations

import os
from pathlib import Path

import pytest

from scripts.check_database_portability import (
    AUTHORITATIVE_SPECIFICATIONS,
    COMPATIBILITY_SPECIFICATIONS,
    EXPECTED_SPECIFICATIONS,
    FORMER_AWS_SPEC_NAME,
    check_specification,
    check_specification_text,
    check_specifications,
)

FIXTURE_ROOT = Path(__file__).parent / "fixtures" / "database_portability"


def test_current_normative_specifications_satisfy_the_portability_policy() -> None:
    assert check_specifications() == []


def test_legitimate_portability_and_deployed_boundary_references_pass() -> None:
    fixture = FIXTURE_ROOT / "positive" / "portable-boundary.md"

    assert check_specification(fixture, display_root=FIXTURE_ROOT) == []


@pytest.mark.parametrize(
    ("fixture_name", "diagnostic"),
    (
        (
            "postgresql-foundation.md",
            "requires PostgreSQL in the application foundation",
        ),
        (
            "postgresql-search.md",
            "requires PostgreSQL-specific search behavior",
        ),
        (
            "postgresql-application-tests.md",
            "requires PostgreSQL-specific application constraints/concurrency/tests",
        ),
        (
            "postgresql-unclassified.md",
            "contains an unclassified PostgreSQL reference",
        ),
        (
            "prohibition-does-not-mask-mandate.md",
            "requires PostgreSQL-specific search behavior",
        ),
        (
            "deployment-does-not-mask-feature.md",
            "requires PostgreSQL-specific application behavior",
        ),
    ),
)
def test_postgresql_application_mandates_fail_with_stable_diagnostics(
    fixture_name: str,
    diagnostic: str,
) -> None:
    fixture = FIXTURE_ROOT / "negative" / fixture_name

    assert check_specification(fixture, display_root=FIXTURE_ROOT) == [
        f"negative/{fixture_name}:3: {diagnostic}"
    ]


@pytest.mark.parametrize(
    ("text", "line_number", "diagnostic"),
    (
        (
            "No PostgreSQL service is used in CI, yet a PostgreSQL search projection is required.",
            1,
            "requires PostgreSQL-specific search behavior",
        ),
        (
            "Production services require PostgreSQL serializable transactions for "
            "application writes.",
            1,
            "requires PostgreSQL-specific application behavior",
        ),
        (
            "Post\u200bgreSQL search projection is required.",
            1,
            "requires PostgreSQL-specific search behavior",
        ),
        (
            "Post\u2060greSQL search projection is required.",
            1,
            "requires PostgreSQL-specific search behavior",
        ),
        (
            "Postgre \t SQL search projection is required.",
            1,
            "requires PostgreSQL-specific search behavior",
        ),
        (
            "\uff30\uff4f\uff53\uff54\uff47\uff52\uff45\uff33\uff31\uff2c search projection "
            "is required.",
            1,
            "requires PostgreSQL-specific search behavior",
        ),
        (
            "&#80;&#111;&#115;&#116;&#103;&#114;&#101;&#83;&#81;&#76; search projection "
            "is required.",
            1,
            "requires PostgreSQL-specific search behavior",
        ),
        (
            "Application tests must avoid PostgreSQL-only behavior.\n"
            "```yaml\n"
            "search_engine: postgresql\n"
            "```\n",
            3,
            "requires PostgreSQL-specific search behavior",
        ),
        (
            "Production stores durable state in RDS PostgreSQL, yet PostgreSQL indexing "
            "is required.",
            1,
            "requires PostgreSQL-specific search behavior",
        ),
        (
            "Historical adoption records describe former PostgreSQL fields, but PostgreSQL "
            "triggers are required.",
            1,
            "requires PostgreSQL-specific application behavior",
        ),
        (
            "SEARCH_ENGINE:\tPoStGrEsQl",
            1,
            "requires PostgreSQL-specific search behavior",
        ),
        (
            "RDS PostgreSQL supplies application transaction isolation semantics.",
            1,
            "requires PostgreSQL-specific application behavior",
        ),
        (
            "PostgreSQL is forbidden in ordinary CI but required for search.",
            1,
            "requires PostgreSQL-specific search behavior",
        ),
        (
            "PostgreSQL is not used in tests yet is required for indexing.",
            1,
            "requires PostgreSQL-specific search behavior",
        ),
        (
            "PostgreSQL is prohibited for application tests and required for search.",
            1,
            "requires PostgreSQL-specific search behavior",
        ),
        (
            "PostgreSQL is forbidden in CI. It is required for search.",
            1,
            "requires PostgreSQL-specific search behavior",
        ),
        (
            "PostgreSQL is forbidden in CI. Search relies on the engine.",
            1,
            "requires PostgreSQL-specific search behavior",
        ),
        (
            "PostgreSQL is forbidden in CI. Search relies upon the engine.",
            1,
            "requires PostgreSQL-specific search behavior",
        ),
        (
            "PostgreSQL is forbidden in CI. Search relied upon the engine.",
            1,
            "requires PostgreSQL-specific search behavior",
        ),
        (
            "PostgreSQL is forbidden in CI. Search depends upon this database.",
            1,
            "requires PostgreSQL-specific search behavior",
        ),
        (
            "PostgreSQL is forbidden in CI. Search is powered by the engine.",
            1,
            "requires PostgreSQL-specific search behavior",
        ),
        (
            "Search must be powered by PostgreSQL.",
            1,
            "requires PostgreSQL-specific search behavior",
        ),
        (
            "Search runs on PostgreSQL.",
            1,
            "requires PostgreSQL-specific search behavior",
        ),
        (
            "Search is implemented by PostgreSQL.",
            1,
            "requires PostgreSQL-specific search behavior",
        ),
        (
            "Application constraints are guaranteed by PostgreSQL.",
            1,
            "requires PostgreSQL-specific application constraints/concurrency/tests",
        ),
        (
            "PostgreSQL is forbidden in CI. Search has been implemented by the engine.",
            1,
            "requires PostgreSQL-specific search behavior",
        ),
        (
            "Application constraints are backed by PostgreSQL.",
            1,
            "requires PostgreSQL-specific application constraints/concurrency/tests",
        ),
        (
            "RDS PostgreSQL is deployed storage. Concurrency is driven by the database.",
            1,
            "requires PostgreSQL-specific application constraints/concurrency/tests",
        ),
        (
            "PostgreSQL supports search indexing.",
            1,
            "requires PostgreSQL-specific search behavior",
        ),
        (
            "PostgreSQL drove search ranking.",
            1,
            "requires PostgreSQL-specific search behavior",
        ),
        (
            "PostgreSQL is forbidden in CI but powers search.",
            1,
            "requires PostgreSQL-specific search behavior",
        ),
        (
            "PostgreSQL is forbidden in CI. Search needs this database.",
            1,
            "requires PostgreSQL-specific search behavior",
        ),
        (
            "RDS PostgreSQL is deployed storage. Application constraints depend on the engine.",
            1,
            "requires PostgreSQL-specific application constraints/concurrency/tests",
        ),
        (
            "PostgreSQL is forbidden in CI but must power search.",
            1,
            "requires PostgreSQL-specific search behavior",
        ),
        (
            "PostgreSQL is forbidden in CI but is required for portable search.",
            1,
            "requires PostgreSQL-specific search behavior",
        ),
        (
            "PostgreSQL is forbidden in CI but it is required for a SQLite search "
            "compatibility test.",
            1,
            "requires PostgreSQL-specific search behavior",
        ),
        (
            "Application tests must avoid PostgreSQL-only behavior, while search requires "
            "that engine.",
            1,
            "requires PostgreSQL-specific search behavior",
        ),
        (
            "Application tests exclude PostgreSQL-specific behavior as well as requiring "
            "its search projection.",
            1,
            "requires PostgreSQL-specific search behavior",
        ),
        (
            "PostgreSQL is forbidden in CI but search uses it.",
            1,
            "requires PostgreSQL-specific search behavior",
        ),
        (
            "PostgreSQL is forbidden in CI but is mandatory for indexing.",
            1,
            "requires PostgreSQL-specific search behavior",
        ),
        (
            "PostgreSQL is forbidden in CI but remains necessary for concurrency.",
            1,
            "requires PostgreSQL-specific application constraints/concurrency/tests",
        ),
        (
            "PostgreSQL is forbidden in CI but search depends on it.",
            1,
            "requires PostgreSQL-specific search behavior",
        ),
        (
            "RDS PostgreSQL is durable storage yet application concurrency relies on it.",
            1,
            "requires PostgreSQL-specific application constraints/concurrency/tests",
        ),
        (
            "PostgreSQL is forbidden in CI but portable documentation says it is required "
            "for search.",
            1,
            "requires PostgreSQL-specific search behavior",
        ),
        (
            "PostgreSQL is forbidden in CI but portable documentation says it must power search.",
            1,
            "requires PostgreSQL-specific search behavior",
        ),
        (
            "PostgreSQL is forbidden in CI but is required to validate portable search.",
            1,
            "requires PostgreSQL-specific search behavior",
        ),
        (
            "PostgreSQL triggers were used historically; they are required again for "
            "application writes.",
            1,
            "requires PostgreSQL-specific application behavior",
        ),
        (
            "Historically, PostgreSQL powered search and PostgreSQL is required now for indexing.",
            1,
            "requires PostgreSQL-specific search behavior",
        ),
        (
            "Historically, PostgreSQL powered search, and now it powers indexing.",
            1,
            "requires PostgreSQL-specific search behavior",
        ),
        (
            "PostgreSQL powered search historically and still powers indexing.",
            1,
            "requires PostgreSQL-specific search behavior",
        ),
        (
            "Historically, PostgreSQL powered search and it now powers indexing.",
            1,
            "requires PostgreSQL-specific search behavior",
        ),
        (
            "Historically, PostgreSQL powered search and indexing still runs on it.",
            1,
            "requires PostgreSQL-specific search behavior",
        ),
        (
            "Before migration, PostgreSQL powered search, and now it powers indexing.",
            1,
            "requires PostgreSQL-specific search behavior",
        ),
        (
            "If PostgreSQL powers search, that is a rejected condition, but PostgreSQL powers "
            "indexing.",
            1,
            "requires PostgreSQL-specific search behavior",
        ),
        (
            "If PostgreSQL powers search, PostgreSQL powers indexing.",
            1,
            "requires PostgreSQL-specific search behavior",
        ),
        (
            "For example, PostgreSQL might power search, PostgreSQL guarantees indexing.",
            1,
            "requires PostgreSQL-specific search behavior",
        ),
        (
            'Forbidden example: "PostgreSQL powers search"; PostgreSQL powers indexing.',
            1,
            "requires PostgreSQL-specific search behavior",
        ),
        (
            "RDS PostgreSQL is durable storage but required for application concurrency.",
            1,
            "requires PostgreSQL-specific application constraints/concurrency/tests",
        ),
        (
            "Postgre\N{EN DASH}SQL search projection is required.",
            1,
            "requires PostgreSQL-specific search behavior",
        ),
        (
            "Postgre\N{NON-BREAKING HYPHEN}SQL search projection is required.",
            1,
            "requires PostgreSQL-specific search behavior",
        ),
        (
            "\N{FULLWIDTH AMPERSAND}amp;\u200b#80;ostgreSQL search projection is required.",
            1,
            "requires PostgreSQL-specific search behavior",
        ),
    ),
)
def test_normalized_mandates_and_masking_combinations_fail(
    text: str,
    line_number: int,
    diagnostic: str,
) -> None:
    assert check_specification_text(text, display_path=Path("adversarial.md")) == [
        f"adversarial.md:{line_number}: {diagnostic}"
    ]


@pytest.mark.parametrize(
    "text",
    (
        "Application tests must avoid PostgreSQL-only behavior.",
        "Application tests exclude PostgreSQL-specific behavior.",
        "PostgreSQL isn’t used by ordinary CI.",
        "Local settings ignore ambient DATABASE_URL.",
        "Historical PostgreSQL functions must be inventoried before production migration.",
        "Previously deployed PostgreSQL indexes are documented for provenance.",
        "Never use PostgreSQL in application tests.",
        "Application tests cannot use PostgreSQL.",
        "PostgreSQL must never be used in application tests.",
        "Application tests can't use PostgreSQL.",
        "Application tests don't use PostgreSQL.",
        "Application tests shall not use PostgreSQL.",
        "PostgreSQL may not be used by application tests.",
        "PostgreSQL is disallowed in application tests.",
        "PostgreSQL must not power search.",
        "PostgreSQL shall not power search.",
        "PostgreSQL no longer powers search.",
        "PostgreSQL is not permitted to power search.",
        "PostgreSQL is barred from powering search.",
        "Search is not permitted to run on PostgreSQL.",
        "Application constraints are barred from depending upon PostgreSQL.",
        "Application tests disallow PostgreSQL-only behavior.",
        "Application tests omit PostgreSQL-specific branches.",
        "PostgreSQL triggers were used historically.",
        "PostgreSQL indexes were previously used for search.",
        "PostgreSQL triggers had been used historically.",
        "PostgreSQL powered search historically.",
        "Historically, PostgreSQL powered search.",
        "In the past, PostgreSQL backed application constraints.",
        "PostgreSQL formerly powered search.",
        "PostgreSQL used to power search.",
        "Historically, PostgreSQL drove search ranking.",
        "PostgreSQL supported indexing in the past.",
        "Before migration, PostgreSQL powered search.",
        "Once, PostgreSQL powered search.",
        "PostgreSQL had powered search.",
        "PostgreSQL had implemented application constraints.",
        "Before migration, search ran on PostgreSQL.",
        "Once, search ran on PostgreSQL.",
        "Search had run on PostgreSQL.",
        "Application constraints had been implemented by PostgreSQL.",
        "PostgreSQL had been powering search.",
        "PostgreSQL isn't required for search.",
        "PostgreSQL is not allowed in application tests.",
        "The production database uses PostgreSQL.",
        "The production DATABASE_URL selects PostgreSQL.",
        "The deployed runtime database engine is PostgreSQL.",
        "PostgreSQL is forbidden in ordinary CI, while SQLite is required for search.",
        "RDS PostgreSQL is deployed storage, while a portable search projection is required.",
        "PostgreSQL is forbidden in CI; SQLite is required for search.",
        "PostgreSQL is forbidden in CI. SQLite is required for search.",
        "PostgreSQL is forbidden in CI while a portable search projection is required.",
        "RDS PostgreSQL is deployed storage while backend-portable search is required.",
        "RDS PostgreSQL is deployed storage while SQLite constraints are required.",
        "Historical PostgreSQL indexes are documented; portable indexes are required.",
        "The runtime psycopg dependency provides deployed connectivity; SQLite is required "
        "for application tests.",
        "Application tests must avoid PostgreSQL-only behavior, while search requires SQLite.",
        "PostgreSQL is forbidden in CI and SQLite is required for search.",
        "PostgreSQL is forbidden in CI; SQLite must power search.",
        "PostgreSQL is forbidden in CI; portable constraints must be exercised.",
        "PostgreSQL is forbidden in CI; backend-portable search must be maintained.",
        "PostgreSQL is forbidden in CI. The SQLite backend is required for search.",
        "PostgreSQL is forbidden in CI. The SQLite backend must power search.",
        "PostgreSQL is forbidden in CI. The SQLite storage engine is required for search.",
        "SQLite, rather than PostgreSQL, is required for search.",
        "Rather than PostgreSQL, SQLite must power search.",
        "SQLite, instead of PostgreSQL, must power search.",
        "Instead of PostgreSQL, SQLite powers search.",
        "SQLite\N{EM DASH}not PostgreSQL\N{EM DASH}powers search.",
        "SQLite, not PostgreSQL, powers search.",
        "Search is powered by SQLite, not PostgreSQL.",
        "Search is implemented by SQLite instead of PostgreSQL.",
        "Search is guaranteed by SQLite rather than PostgreSQL.",
        "Search runs on SQLite, never PostgreSQL.",
        "Only SQLite powers search; PostgreSQL does not.",
        "Only SQLite guarantees application constraints; PostgreSQL does not guarantee them.",
        "Search must use SQLite, never PostgreSQL.",
        "PostgreSQL is forbidden in CI. SQLite powers search.",
        "PostgreSQL is forbidden in CI. A portable backend drives indexing.",
        "If PostgreSQL were required for search, the specification would violate this policy.",
        "If a PostgreSQL search projection were required, the policy would reject it.",
        "Assuming PostgreSQL is required for search, the specification is only an example.",
        "Were PostgreSQL required for search, the gate would fail.",
        "Imagine requiring PostgreSQL for search; the example must fail.",
        "A hypothetical specification might require PostgreSQL for search.",
        "An example specification could require PostgreSQL indexing.",
        "For example, a specification might require PostgreSQL search.",
        "Consider a specification in which PostgreSQL powers search.",
        "Consider a specification where search runs on PostgreSQL.",
        "Suppose that PostgreSQL powers search; the gate would reject the example.",
        "If PostgreSQL powers search, the gate rejects the condition.",
        "If PostgreSQL were to power search, the gate would reject the condition.",
        "Were PostgreSQL to power search, the gate would reject the condition.",
        "Were search to run on PostgreSQL, the gate would reject the condition.",
        "PostgreSQL were to power search in this hypothetical condition.",
        'Forbidden example: "PostgreSQL powers search".',
        'The rejected wording "PostgreSQL powers search" is documentation.',
        'Invalid example: "Search is implemented by PostgreSQL".',
        'Forbidden example: "PostgreSQL powers search, and PostgreSQL guarantees indexing".',
        "Suppose PostgreSQL were required for search; that hypothetical mandate must fail "
        "the gate.",
    ),
)
def test_explicit_negative_database_forms_pass(text: str) -> None:
    assert check_specification_text(text, display_path=Path("negative-form.md")) == []


COMPOSITIONAL_CLASSIFIER_CASES: tuple[tuple[str, str, str | None], ...] = (
    (
        "current-passive-hosted",
        "Search is hosted by PostgreSQL.",
        "requires PostgreSQL-specific search behavior",
    ),
    (
        "current-passive-enabled",
        "Application constraints are enabled by PostgreSQL.",
        "requires PostgreSQL-specific application constraints/concurrency/tests",
    ),
    (
        "current-active-backs",
        "PostgreSQL backs indexing.",
        "requires PostgreSQL-specific search behavior",
    ),
    (
        "current-progressive-continues",
        "PostgreSQL continues powering search.",
        "requires PostgreSQL-specific search behavior",
    ),
    (
        "current-after-though",
        "Historically, PostgreSQL powered search, though at present it backs indexing.",
        "requires PostgreSQL-specific search behavior",
    ),
    (
        "current-after-even-though",
        "PostgreSQL was used historically, even though search is hosted by it.",
        "requires PostgreSQL-specific search behavior",
    ),
    (
        "current-after-and-continues",
        "PostgreSQL powered search until migration and continues powering indexing.",
        "requires PostgreSQL-specific search behavior",
    ),
    (
        "current-after-at-present",
        "Years ago PostgreSQL enabled search, at present it backs indexing.",
        "requires PostgreSQL-specific search behavior",
    ),
    (
        "current-anaphoric-hosted",
        "PostgreSQL is forbidden. Search is hosted by the engine.",
        "requires PostgreSQL-specific search behavior",
    ),
    (
        "current-after-sqlite-switch",
        "PostgreSQL is forbidden. We select SQLite. It powers search. PostgreSQL hosts indexing.",
        "requires PostgreSQL-specific search behavior",
    ),
    ("negative-cannot-provider", "PostgreSQL cannot power search.", None),
    ("negative-cannot-consumer", "Search cannot run on PostgreSQL.", None),
    (
        "negative-forbidden-provider",
        "PostgreSQL is forbidden from powering search.",
        None,
    ),
    (
        "negative-forbidden-consumer",
        "Search is forbidden from using PostgreSQL.",
        None,
    ),
    ("negative-not-to-be-used", "PostgreSQL is not to be used for search.", None),
    (
        "negative-prevented-consumer",
        "The application is prevented from using PostgreSQL for search.",
        None,
    ),
    (
        "negative-prevented-provider",
        "PostgreSQL is prevented from backing indexing.",
        None,
    ),
    ("negative-shall-not-host", "PostgreSQL shall not host search.", None),
    ("history-until-prefix", "Until migration, PostgreSQL powered search.", None),
    ("history-until-suffix", "PostgreSQL powered search until migration.", None),
    ("history-prior-prefix", "Prior to migration, PostgreSQL hosted search.", None),
    ("history-prior-suffix", "Search ran on PostgreSQL prior to migration.", None),
    ("history-years-prefix", "Years ago, PostgreSQL backed indexing.", None),
    ("history-years-suffix", "PostgreSQL enabled search years ago.", None),
    ("history-had-active", "PostgreSQL had hosted search.", None),
    ("history-had-passive", "Search had been enabled by PostgreSQL.", None),
    ("sqlite-and-not-active", "SQLite and not PostgreSQL powers search.", None),
    (
        "sqlite-and-not-passive",
        "Search is hosted by SQLite and not PostgreSQL.",
        None,
    ),
    ("sqlite-cleft-comma", "It is SQLite that powers search, not PostgreSQL.", None),
    (
        "sqlite-cleft-dash",
        "It is SQLite—not PostgreSQL—that backs indexing.",
        None,
    ),
    (
        "sqlite-current-instead",
        "At present SQLite hosts search instead of PostgreSQL.",
        None,
    ),
    (
        "sqlite-leading-instead",
        "Instead of PostgreSQL, SQLite enables search.",
        None,
    ),
    (
        "sqlite-select-reset",
        "PostgreSQL is forbidden. We select SQLite. It powers search.",
        None,
    ),
    (
        "sqlite-database-reset",
        "PostgreSQL is forbidden. The database is SQLite. At present it backs indexing.",
        None,
    ),
    (
        "hypothetical-had-provider",
        "Had PostgreSQL powered search, the gate would fail.",
        None,
    ),
    (
        "hypothetical-had-consumer",
        "Had search run on PostgreSQL, the gate would fail.",
        None,
    ),
    (
        "hypothetical-should-provider",
        "Should PostgreSQL host search, the gate would fail.",
        None,
    ),
    (
        "hypothetical-should-passive",
        "Should search be enabled by PostgreSQL, the gate would fail.",
        None,
    ),
    (
        "hypothetical-assume",
        "Assume that PostgreSQL backs search as a counterexample.",
        None,
    ),
    (
        "hypothetical-consider",
        "Consider PostgreSQL hosting search as a counterexample.",
        None,
    ),
    ("report-former-inline", "Former proposal: `PostgreSQL powers search`.", None),
    (
        "report-invalid-inline",
        "The report quotes the invalid proposal `Search is hosted by PostgreSQL`.",
        None,
    ),
    (
        "scope-assume-current",
        "Assume PostgreSQL powers search, PostgreSQL backs indexing.",
        "requires PostgreSQL-specific search behavior",
    ),
    (
        "scope-report-current",
        "Former proposal: `PostgreSQL powers search`; PostgreSQL hosts indexing.",
        "requires PostgreSQL-specific search behavior",
    ),
    (
        "scope-history-current",
        "Until migration PostgreSQL powered search, though at present PostgreSQL hosts indexing.",
        "requires PostgreSQL-specific search behavior",
    ),
    (
        "scope-sqlite-current",
        "PostgreSQL is forbidden. The database is SQLite. It enables search. "
        "PostgreSQL guarantees application constraints.",
        "requires PostgreSQL-specific application constraints/concurrency/tests",
    ),
)


@pytest.mark.parametrize(
    ("_case_id", "text", "diagnostic"),
    COMPOSITIONAL_CLASSIFIER_CASES,
    ids=[case_id for case_id, _text, _diagnostic in COMPOSITIONAL_CLASSIFIER_CASES],
)
def test_compositional_database_grammar(
    _case_id: str,
    text: str,
    diagnostic: str | None,
) -> None:
    errors = check_specification_text(text, display_path=Path("compositional.md"))

    assert errors == ([] if diagnostic is None else [f"compositional.md:1: {diagnostic}"])


@pytest.mark.parametrize(
    ("text", "diagnostic"),
    (
        (
            "PostgreSQL is forbidden in CI. Search is enabled by the backend.",
            "requires PostgreSQL-specific search behavior",
        ),
        (
            "RDS PostgreSQL is deployed storage. Application constraints are hosted by that "
            "backend.",
            "requires PostgreSQL-specific application constraints/concurrency/tests",
        ),
        ("SQLite, and not PostgreSQL, powers search.", None),
        ("SQLite instead of PostgreSQL is currently powering search.", None),
        ("The text `PostgreSQL powers search` is a rejected example.", None),
        ("`PostgreSQL powers search` is invalid wording.", None),
        ("The policy rejects the statement `PostgreSQL powers search`.", None),
        (
            "PostgreSQL is forbidden in CI. SQLite is selected. This engine powers search.",
            None,
        ),
        ("The statement `PostgreSQL powers search` is forbidden.", None),
        ("SQLite, and not PostgreSQL, backs application constraints.", None),
        (
            "PostgreSQL is forbidden in CI. Ranking is hosted by this backend.",
            "requires PostgreSQL-specific search behavior",
        ),
        (
            "RDS PostgreSQL is deployed storage. Search is enabled by that engine.",
            "requires PostgreSQL-specific search behavior",
        ),
        ("SQLite, and not PostgreSQL, enables indexing.", None),
        ("SQLite rather than PostgreSQL is still backing application constraints.", None),
        (
            "PostgreSQL is forbidden in CI. SQLite was selected. The backend hosts search.",
            None,
        ),
        ("The wording `PostgreSQL backs indexing` was invalid.", None),
        ("The specification forbids the proposal `PostgreSQL enables search`.", None),
        (
            "The statement `PostgreSQL powers search` is forbidden, but PostgreSQL hosts indexing.",
            "requires PostgreSQL-specific search behavior",
        ),
        (
            "SQLite is selected. This engine powers search. PostgreSQL hosts indexing.",
            "requires PostgreSQL-specific search behavior",
        ),
        (
            "SQLite, and not PostgreSQL, backs application constraints, but PostgreSQL hosts "
            "search.",
            "requires PostgreSQL-specific search behavior",
        ),
        (
            "The policy rejects the statement `PostgreSQL powers search`; search is hosted by "
            "PostgreSQL.",
            "requires PostgreSQL-specific search behavior",
        ),
        (
            "PostgreSQL is forbidden. Search is enabled by the backend, though SQLite is selected.",
            "requires PostgreSQL-specific search behavior",
        ),
        ("SQLite, and not PostgreSQL, currently backs application constraints.", None),
        (
            "PostgreSQL is forbidden in CI. We chose SQLite. This database backs indexing.",
            None,
        ),
        ("SQLite, and not PostgreSQL, still enables search.", None),
        ("SQLite instead of PostgreSQL now hosts indexing.", None),
        (
            "PostgreSQL is forbidden in CI. We selected SQLite. The database hosts indexing.",
            None,
        ),
        (
            "PostgreSQL is forbidden in CI. We have chosen SQLite. This backend enables search.",
            None,
        ),
        (
            "SQLite, and not PostgreSQL, currently backs application constraints, but PostgreSQL "
            "hosts search.",
            "requires PostgreSQL-specific search behavior",
        ),
        (
            "PostgreSQL is forbidden in CI. We chose SQLite. This database backs indexing. "
            "PostgreSQL guarantees application constraints.",
            "requires PostgreSQL-specific application constraints/concurrency/tests",
        ),
    ),
)
def test_reported_and_neighboring_compositional_grammar(
    text: str,
    diagnostic: str | None,
) -> None:
    errors = check_specification_text(text, display_path=Path("reported-grammar.md"))

    assert errors == ([] if diagnostic is None else [f"reported-grammar.md:1: {diagnostic}"])


def _stale_link_cases() -> tuple[tuple[str, int], ...]:
    old_name = FORMER_AWS_SPEC_NAME
    encoded_name = old_name.replace("0", "%30", 1)
    return (
        (f"[AWS deployment]: {old_name}", 1),
        (f'[AWS deployment]({old_name} "legacy")', 1),
        (f"[AWS deployment]({old_name}?old=1)", 1),
        (f'<a href="{old_name}">AWS deployment</a>', 1),
        (f"[AWS deployment]({encoded_name})", 1),
        (f"<{old_name}>", 1),
        (f"../specs/{old_name}#legacy", 1),
        (old_name, 1),
        (f"[outer [nested]\nlabel]({old_name})", 2),
        (f"[outer [nested]\nlabel]: {old_name}", 2),
        (f"[multiline destination](\n{old_name})", 1),
        (f"https://example.invalid/{old_name}", 1),
        (f"[current](08-aws-development-terraform.md?former={old_name})", 1),
    )


@pytest.mark.parametrize(("text", "line_number"), _stale_link_cases())
def test_former_aws_specification_link_forms_fail(text: str, line_number: int) -> None:
    assert check_specification_text(text, display_path=Path("stale-link.md")) == [
        f"stale-link.md:{line_number}: links to the former AWS specification path"
    ]


def test_former_aws_filename_in_historical_prose_is_not_a_link() -> None:
    text = (
        f"Historical prose names {FORMER_AWS_SPEC_NAME} as the former filename without linking it."
    )

    assert check_specification_text(text, display_path=Path("history.md")) == []


def test_repeated_html_entity_decoding_reaches_a_fixed_point() -> None:
    encoded = "&#80;ostgreSQL search projection is required."
    for _ in range(6):
        encoded = encoded.replace("&", "&amp;")

    assert check_specification_text(encoded, display_path=Path("nested-entity.md")) == [
        "nested-entity.md:1: requires PostgreSQL-specific search behavior"
    ]


def test_unbounded_nested_html_entity_encoding_fails_closed() -> None:
    encoded = "&#80;ostgreSQL"
    for _ in range(32):
        encoded = encoded.replace("&", "&amp;")

    assert check_specification_text(encoded, display_path=Path("nested-entity.md")) == [
        "nested-entity.md:1: contains unresolved nested encoding"
    ]


def test_unbounded_nested_percent_encoding_fails_closed() -> None:
    encoded = FORMER_AWS_SPEC_NAME.replace("0", "%30", 1)
    for _ in range(32):
        encoded = encoded.replace("%", "%25")

    assert check_specification_text(
        f"[AWS deployment]({encoded})",
        display_path=Path("nested-percent.md"),
    ) == ["nested-percent.md:1: contains unresolved nested encoding"]


def test_stale_link_template_remains_deterministic() -> None:
    fixture = FIXTURE_ROOT / "negative" / "stale-aws-spec-link.template.md"
    text = fixture.read_text(encoding="utf-8").replace("{{FORMER_AWS_SPEC}}", FORMER_AWS_SPEC_NAME)

    assert check_specification_text(
        text,
        display_path=Path("negative/stale-aws-spec-link.template.md"),
    ) == ["negative/stale-aws-spec-link.template.md:3: links to the former AWS specification path"]


def _write_clean_catalog(root: Path) -> None:
    root.mkdir(parents=True)
    for name in EXPECTED_SPECIFICATIONS:
        (root / name).write_text("# Clean specification\n", encoding="utf-8")


def test_catalog_is_exact_and_current() -> None:
    assert AUTHORITATIVE_SPECIFICATIONS == (
        "README.md",
        "01-platform-architecture.md",
        "02-url-link-seo-compatibility.md",
        "03-github-content-and-people.md",
        "04-courses-and-cohorts.md",
        "05-events-registration-email.md",
        "06-studio-and-admin-api.md",
        "07-security-privacy-operations.md",
        "08-aws-development-terraform.md",
        "09-migration-rollout-roadmap.md",
        "10-verification-strategy.md",
        "open-decisions.md",
    )
    assert COMPATIBILITY_SPECIFICATIONS == (FORMER_AWS_SPEC_NAME,)


def test_complete_synthetic_catalog_passes(tmp_path: Path) -> None:
    root = tmp_path / "specs"
    _write_clean_catalog(root)

    assert check_specifications(root) == []


@pytest.mark.parametrize("filename", ("ignored.MD", "ignored.markdown"))
def test_extra_case_variant_markdown_candidates_fail(tmp_path: Path, filename: str) -> None:
    root = tmp_path / "specs"
    _write_clean_catalog(root)
    (root / filename).write_text("PostgreSQL search projection is required.\n", encoding="utf-8")

    assert f"unexpected specification candidate: {filename}" in check_specifications(root)


def test_readme_only_cannot_mask_missing_authoritative_catalog(tmp_path: Path) -> None:
    root = tmp_path / "specs"
    root.mkdir()
    (root / "README.md").write_text("# Clean specification\n", encoding="utf-8")

    errors = check_specifications(root)

    assert "authoritative specification is missing: 01-platform-architecture.md" in errors
    assert len(errors) == len(EXPECTED_SPECIFICATIONS) - 1


def test_external_authoritative_file_symlink_is_rejected(tmp_path: Path) -> None:
    root = tmp_path / "specs"
    _write_clean_catalog(root)
    target = tmp_path / "external.md"
    target.write_text("PostgreSQL search projection is required.\n", encoding="utf-8")
    path = root / AUTHORITATIVE_SPECIFICATIONS[1]
    path.unlink()
    path.symlink_to(target)

    assert (
        f"unexpected symbolic link in specification directory: {path.name}"
        in check_specifications(root)
    )


def test_symlinked_directory_with_prohibited_spec_is_rejected(tmp_path: Path) -> None:
    root = tmp_path / "specs"
    _write_clean_catalog(root)
    external = tmp_path / "external-specs"
    external.mkdir()
    (external / "prohibited.md").write_text(
        "PostgreSQL search projection is required.\n",
        encoding="utf-8",
    )
    (root / "nested").symlink_to(external, target_is_directory=True)

    assert "unexpected symbolic link in specification directory: nested" in check_specifications(
        root
    )


def test_symlinked_specification_root_is_rejected(tmp_path: Path) -> None:
    real_root = tmp_path / "real-specs"
    _write_clean_catalog(real_root)
    linked_root = tmp_path / "linked-specs"
    linked_root.symlink_to(real_root, target_is_directory=True)

    assert check_specifications(linked_root) == [
        f"normative specification root is a symbolic link: {linked_root}"
    ]


def test_symbolic_link_ancestor_is_rejected(tmp_path: Path) -> None:
    real_parent = tmp_path / "real-parent"
    root = real_parent / "specs"
    _write_clean_catalog(root)
    linked_parent = tmp_path / "linked-parent"
    linked_parent.symlink_to(real_parent, target_is_directory=True)
    linked_root = linked_parent / "specs"

    errors = check_specifications(linked_root)

    assert len(errors) == 1
    assert errors[0].startswith("normative specification root has a symbolic-link ancestor:")


def test_broken_symlink_and_special_file_are_rejected(tmp_path: Path) -> None:
    root = tmp_path / "specs"
    _write_clean_catalog(root)
    (root / "broken").symlink_to(tmp_path / "missing")
    os.mkfifo(root / "special.markdown")

    errors = check_specifications(root)

    assert "unexpected symbolic link in specification directory: broken" in errors
    assert (
        "unexpected special filesystem entry in specification directory: special.markdown" in errors
    )
