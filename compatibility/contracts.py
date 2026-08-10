"""Strict, immutable inventory of the legacy public URL contracts.

The source-side artifacts deliberately describe different kinds of evidence.  This module
combines them without turning a proposed ``preserve`` classification into an approval: parity
is approved only after a later source/production review.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass, replace
from enum import StrEnum
from pathlib import Path
from typing import Final
from urllib.parse import parse_qsl, quote, unquote, unquote_plus, urlsplit, urlunsplit

from compatibility.models import ReviewState
from compatibility.redaction import (
    fragment_requires_redaction,
    is_redacted_value,
    value_requires_redaction,
)

DEFAULT_CONTRACT_DIRECTORY: Final = Path(__file__).resolve().parents[1] / "_docs/compatibility"
SCHEMA_VERSION: Final = 1
_SHA1 = re.compile(r"[0-9a-f]{40}")
_PERCENT_ESCAPE = re.compile(r"%[0-9A-Fa-f]{2}")
_CONTRACT_KINDS: Final = frozenset(
    {"api", "asset", "calendar", "fragment", "html", "json", "path", "query", "text", "xml"}
)


class ContractInventoryError(ValueError):
    """Raised when a checked-in contract artifact is malformed or contradictory."""


class ContractClassification(StrEnum):
    """The compatibility action requested for the public contract."""

    PRESERVE = "preserve"
    REDIRECT = "redirect"
    RETIRE = "retire"


@dataclass(frozen=True, slots=True)
class PublicContract:
    """One exact, immutable public URL contract and its source provenance."""

    contract_id: str
    public_url: str
    public_reference: str
    percent_encoded_public_reference: str
    path: str
    query: str
    fragment: str
    source_id: str
    source_repository: str
    source_revision: str
    source_locator: str
    source_path: str | None
    contract_kind: str
    expected_status: int | None
    machine_contract: bool
    classification: ContractClassification
    review_state: ReviewState
    route_pattern: str | None = None
    route_name: str | None = None
    route_urlconf: str | None = None
    route_callback: str | None = None

    def __post_init__(self) -> None:
        if not re.fullmatch(r"contract-[0-9a-f]{24}", self.contract_id):
            raise ContractInventoryError("contract_id must be a stable contract digest")
        parsed_url = urlsplit(self.public_url)
        parsed_reference = urlsplit(self.public_reference)
        if (
            parsed_url.scheme != "https"
            or not parsed_url.netloc
            or parsed_url.username is not None
            or parsed_url.password is not None
            or any(character.isspace() for character in self.public_url)
        ):
            raise ContractInventoryError("public_url must be an absolute HTTPS URL")
        if (
            not self.public_reference.startswith("/")
            or self.public_reference.startswith("//")
            or parsed_reference.netloc
            or any(
                character.isspace()
                for character in f"{parsed_reference.query}{parsed_reference.fragment}"
            )
            or any(character.isspace() and character != " " for character in self.public_reference)
        ):
            raise ContractInventoryError("public_reference must be root-relative")
        encoded = urlsplit(self.percent_encoded_public_reference)
        if (
            not self.percent_encoded_public_reference.startswith("/")
            or self.percent_encoded_public_reference.startswith("//")
            or encoded.netloc
            or any(character.isspace() for character in self.percent_encoded_public_reference)
        ):
            raise ContractInventoryError(
                "percent-encoded reference must be a safe root-relative URL"
            )
        canonical_reference = canonical_percent_encoded_reference(self.public_reference)
        if (
            self.percent_encoded_public_reference != canonical_reference
            and unquote(self.percent_encoded_public_reference) != self.public_reference
        ):
            raise ContractInventoryError(
                "percent-encoded reference must be the canonical public reference encoding"
            )
        origin = f"{parsed_url.scheme}://{parsed_url.netloc}"
        if self.public_url != f"{origin}{self.percent_encoded_public_reference}":
            raise ContractInventoryError("public_url must use the canonical network reference")
        for key, value in parse_qsl(parsed_reference.query, keep_blank_values=True):
            if value_requires_redaction(key, value) and not is_redacted_value(value):
                raise ContractInventoryError("public reference contains a sensitive query value")
        decoded_fragment = unquote_plus(parsed_reference.fragment)
        if (
            decoded_fragment
            and fragment_requires_redaction(decoded_fragment)
            and not is_redacted_value(decoded_fragment)
        ):
            raise ContractInventoryError("public reference contains a sensitive fragment value")
        if (self.path, self.query, self.fragment) != (
            parsed_reference.path,
            parsed_reference.query,
            parsed_reference.fragment,
        ):
            raise ContractInventoryError("path, query, and fragment must preserve the reference")
        if not self.source_id or not self.source_locator:
            raise ContractInventoryError("source id and locator are required")
        repository = urlsplit(self.source_repository)
        if (
            repository.scheme != "https"
            or repository.netloc != "github.com"
            or not repository.path.endswith(".git")
        ):
            raise ContractInventoryError("source repository must be an HTTPS GitHub clone URL")
        if not _SHA1.fullmatch(self.source_revision):
            raise ContractInventoryError("source revision must be a full lowercase Git SHA")
        if self.contract_kind not in _CONTRACT_KINDS:
            raise ContractInventoryError(f"unsupported contract kind: {self.contract_kind}")
        if not isinstance(self.machine_contract, bool):
            raise ContractInventoryError("machine contract marker must be a boolean")
        if not isinstance(self.classification, ContractClassification):
            raise ContractInventoryError("contract classification must be explicit")
        if not isinstance(self.review_state, ReviewState):
            raise ContractInventoryError("review state must be explicit")
        if (
            self.classification is not ContractClassification.PRESERVE
            or self.review_state is not ReviewState.PROPOSED_PRESERVE
        ):
            raise ContractInventoryError(
                "public contract v1 only supports a proposed preserve classification"
            )
        if self.expected_status is not None and (
            isinstance(self.expected_status, bool) or not 100 <= self.expected_status <= 599
        ):
            raise ContractInventoryError("expected status must be null or an HTTP status")
        route_values = (self.route_pattern, self.route_urlconf, self.route_callback)
        if any(value is not None for value in route_values) and not all(
            value is not None for value in route_values
        ):
            raise ContractInventoryError("course route provenance must be complete")

    def as_record(self) -> dict[str, object]:
        """Return the stable JSON representation used by inventory snapshots."""

        record = asdict(self)
        record["schema_version"] = SCHEMA_VERSION
        record["classification"] = self.classification.value
        record["review_state"] = self.review_state.value
        return record


@dataclass(frozen=True, slots=True)
class _Source:
    source_id: str
    repository: str
    revision: str
    origin: str


def load_public_contract_inventory(
    directory: Path = DEFAULT_CONTRACT_DIRECTORY,
) -> tuple[PublicContract, ...]:
    """Load and reconcile every checked-in source contract artifact.

    Generated paths and their configured machine samples are one contract, with
    ``machine_contract=True`` retaining the second source of evidence. Course routes use route
    identity rather than example URL identity, so colliding illustrative paths remain distinct.
    """

    directory = Path(directory)
    sources = _load_sources(directory / "source-build-provenance.json")
    contracts: list[PublicContract] = []
    reference_index: dict[tuple[str, str], list[int]] = {}

    def add(contract: PublicContract) -> None:
        contracts.append(contract)
        reference_index.setdefault((contract.source_id, contract.public_reference), []).append(
            len(contracts) - 1
        )

    for row in _load_jsonl(directory / "generated-path-baseline.jsonl"):
        _require_keys(
            row,
            {
                "classification",
                "content_sha256",
                "contract_kind",
                "expected_status",
                "machine_contract_seed",
                "public_path",
                "public_path_percent_encoded",
                "schema_version",
                "source_id",
                "source_path",
                "source_revision",
            },
            "generated path",
        )
        _require_baseline_values(row)
        source = _source_for(row, sources)
        reference = _string(row, "public_path")
        add(
            _contract(
                source=source,
                reference=reference,
                percent_encoded_reference=_string(row, "public_path_percent_encoded"),
                source_locator=_string(row, "source_path"),
                source_path=_string(row, "source_path"),
                contract_kind=_string(row, "contract_kind"),
                expected_status=_nullable_status(row, "expected_status"),
                machine_contract=_boolean(row, "machine_contract_seed"),
            )
        )

    for row in _load_jsonl(directory / "faq-fragment-contracts.jsonl"):
        _require_keys(
            row,
            {
                "classification",
                "course_slug",
                "fragment_id",
                "public_path",
                "public_path_with_fragment",
                "public_path_with_fragment_percent_encoded",
                "schema_version",
                "source_id",
                "source_path",
                "source_revision",
            },
            "FAQ fragment",
        )
        _require_baseline_values(row)
        source = _source_for(row, sources)
        expected_status = _target_status(
            contracts,
            reference_index,
            source.source_id,
            _string(row, "public_path"),
        )
        add(
            _contract(
                source=source,
                reference=_string(row, "public_path_with_fragment"),
                percent_encoded_reference=_string(row, "public_path_with_fragment_percent_encoded"),
                source_locator=_string(row, "source_path"),
                source_path=_string(row, "source_path"),
                contract_kind="fragment",
                expected_status=expected_status,
                machine_contract=False,
            )
        )

    for row in _load_jsonl(directory / "podwiki-graph-fragment-contracts.jsonl"):
        _require_keys(
            row,
            {
                "classification",
                "fragment_id",
                "public_path",
                "public_path_with_fragment",
                "public_path_with_fragment_percent_encoded",
                "schema_version",
                "source_id",
                "source_path",
                "source_revision",
                "target_type",
                "target_url",
            },
            "Podwiki graph fragment",
        )
        _require_baseline_values(row)
        source = _source_for(row, sources)
        expected_status = _target_status(
            contracts,
            reference_index,
            source.source_id,
            _string(row, "public_path"),
        )
        add(
            _contract(
                source=source,
                reference=_string(row, "public_path_with_fragment"),
                percent_encoded_reference=_string(row, "public_path_with_fragment_percent_encoded"),
                source_locator=_string(row, "source_path"),
                source_path=_string(row, "source_path"),
                contract_kind="fragment",
                expected_status=expected_status,
                machine_contract=False,
            )
        )

    course_document = _load_json(directory / "course-route-contracts.json")
    _require_keys(
        course_document,
        {
            "adoption_inventory",
            "authenticated_production_probe_reason",
            "authenticated_production_probes_performed",
            "classification_default",
            "route_count",
            "routes",
            "schema_version",
            "source_id",
            "source_revision",
        },
        "course route document",
    )
    if course_document["classification_default"] != "preserve":
        raise ContractInventoryError("course routes do not default to preserve")
    if _integer(course_document, "schema_version") != SCHEMA_VERSION:
        raise ContractInventoryError("unsupported course route schema version")
    course_source = _source_for(course_document, sources)
    if course_source.source_id != "dtc-course-platform":
        raise ContractInventoryError("course route document has the wrong source")
    if course_document["authenticated_production_probes_performed"] is not False:
        raise ContractInventoryError("authenticated course probes must remain not performed")
    course_rows = _object_list(course_document, "routes")
    if _integer(course_document, "route_count") != len(course_rows):
        raise ContractInventoryError("course route count does not match its rows")
    for row in course_rows:
        _require_keys(
            row,
            {
                "authenticated_production_probe",
                "callback",
                "classification",
                "contract_kind",
                "example_path",
                "expected_status",
                "host",
                "name",
                "route_pattern",
                "source_example_path",
                "source_id",
                "source_name",
                "source_route_pattern",
                "source_revision",
                "surface",
                "urlconf",
            },
            "course route",
        )
        _require_preserve(row)
        if row["authenticated_production_probe"] != "not_performed":
            raise ContractInventoryError("course route unexpectedly contains production evidence")
        source = _source_for(row, sources)
        reference = _string(row, "source_example_path")
        urlconf = _string(row, "urlconf")
        route_pattern = _string(row, "source_route_pattern")
        callback = _string(row, "callback")
        if row["surface"] == "Studio Courses":
            target_example = _string(row, "example_path")
            target_pattern = _string(row, "route_pattern")
            target_name = _string(row, "name")
            expected_reference = (
                "/cadmin/"
                if target_example == "/studio/courses"
                else f"/cadmin/{target_example.removeprefix('/studio/courses/')}"
            )
            expected_pattern = (
                "/cadmin/"
                if target_pattern == "/studio/courses"
                else f"/cadmin/{target_pattern.removeprefix('/studio/courses/')}"
            )
            if (
                not (
                    target_example == "/studio/courses"
                    or target_example.startswith("/studio/courses/")
                )
                or not (
                    target_pattern == "/studio/courses"
                    or target_pattern.startswith("/studio/courses/")
                )
                or not target_name.startswith("studio_courses_")
                or reference != expected_reference
                or route_pattern != expected_pattern
                or _string(row, "source_name")
                != f"cadmin_{target_name.removeprefix('studio_courses_')}"
            ):
                raise ContractInventoryError(
                    "Studio Courses target/source compatibility mapping is invalid"
                )
        course_source_path = f"{urlconf.replace('.', '/')}.py"
        if urlsplit(source.origin).netloc != _string(row, "host"):
            raise ContractInventoryError("course route host contradicts source provenance")
        add(
            _contract(
                source=source,
                reference=reference,
                percent_encoded_reference=None,
                source_locator=f"{course_source_path}:{route_pattern}",
                source_path=course_source_path,
                contract_kind=_string(row, "contract_kind"),
                expected_status=_nullable_status(row, "expected_status"),
                machine_contract=False,
                route_pattern=route_pattern,
                route_name=_nullable_string(row, "source_name"),
                route_urlconf=urlconf,
                route_callback=callback,
            )
        )

    machine_document = _load_json(directory / "machine-contract-samples.json")
    _require_keys(
        machine_document,
        {"classification_default", "sample_count", "samples", "schema_version"},
        "machine contract document",
    )
    machine_rows = _object_list(machine_document, "samples")
    if machine_document["classification_default"] != "preserve":
        raise ContractInventoryError("machine contracts do not default to preserve")
    if _integer(machine_document, "schema_version") != SCHEMA_VERSION:
        raise ContractInventoryError("unsupported machine contract schema version")
    if _integer(machine_document, "sample_count") != len(machine_rows):
        raise ContractInventoryError("machine contract count does not match its rows")
    for row in machine_rows:
        _require_keys(
            row,
            {
                "classification",
                "contract_kind",
                "course_route_contract_present",
                "fragment",
                "path",
                "public_contract",
                "query",
                "schema_version",
                "source_id",
                "source_output_path",
                "source_output_present",
                "source_revision",
            },
            "machine contract",
        )
        _require_baseline_values(row)
        source = _source_for(row, sources)
        reference = _string(row, "public_contract")
        parsed = urlsplit(reference)
        if (row["path"], row["query"], row["fragment"]) != (
            parsed.path,
            parsed.query,
            parsed.fragment,
        ):
            raise ContractInventoryError("machine contract URL parts are inconsistent")
        expected_kind = "fragment" if parsed.fragment else "query" if parsed.query else "path"
        if _string(row, "contract_kind") != expected_kind:
            raise ContractInventoryError("machine contract kind contradicts its URL")
        source_output_present = _boolean(row, "source_output_present")
        course_route_present = _nullable_boolean(row, "course_route_contract_present")
        matches = reference_index.get((source.source_id, reference), [])
        if matches:
            is_course = source.source_id == "dtc-course-platform"
            if is_course != (course_route_present is True):
                raise ContractInventoryError("course machine-contract marker is inconsistent")
            if not is_course and source_output_present:
                output_path = _nullable_string(row, "source_output_path")
                if output_path is None or any(
                    contracts[index].source_path != output_path for index in matches
                ):
                    raise ContractInventoryError("machine contract source path is inconsistent")
            for index in matches:
                contracts[index] = replace(contracts[index], machine_contract=True)
            continue
        if course_route_present is True:
            raise ContractInventoryError("machine sample references a missing course route")
        machine_source_path = _nullable_string(row, "source_output_path")
        add(
            _contract(
                source=source,
                reference=reference,
                percent_encoded_reference=reference,
                source_locator=machine_source_path or f"configured-machine-contract:{reference}",
                source_path=machine_source_path,
                contract_kind=_string(row, "contract_kind"),
                expected_status=None,
                machine_contract=True,
            )
        )

    result = tuple(sorted(contracts, key=lambda item: (item.public_url, item.contract_id)))
    ids = [contract.contract_id for contract in result]
    if len(ids) != len(set(ids)):
        raise ContractInventoryError("stable contract ID collision")
    return result


def dumps_public_contract_inventory(contracts: tuple[PublicContract, ...]) -> str:
    """Serialize contracts as canonical, diff-friendly JSON Lines."""

    ordered = sorted(contracts, key=lambda item: (item.public_url, item.contract_id))
    return "".join(
        f"{
            json.dumps(
                contract.as_record(), ensure_ascii=False, sort_keys=True, separators=(',', ':')
            )
        }\n"
        for contract in ordered
    )


def public_contract_inventory_sha256(contracts: tuple[PublicContract, ...]) -> str:
    """Return a stable digest for a fully reconciled inventory."""

    return hashlib.sha256(dumps_public_contract_inventory(contracts).encode()).hexdigest()


def public_contract_id(
    source_id: str,
    public_reference: str,
    *,
    route_pattern: str | None = None,
    route_name: str | None = None,
    route_urlconf: str | None = None,
    route_callback: str | None = None,
) -> str:
    """Build a stable ID, retaining route identity when illustrative URLs collide."""

    identity = [source_id, public_reference]
    if route_pattern is not None:
        identity.extend(
            [route_urlconf or "", route_pattern, route_name or "", route_callback or ""]
        )
    digest = hashlib.sha256("\0".join(identity).encode()).hexdigest()[:24]
    return f"contract-{digest}"


def canonical_percent_encoded_reference(reference: str) -> str:
    """Encode a source-spelling reference into its exact fetchable network form.

    ``public_reference`` is archival evidence and therefore retains two known legacy paths with
    literal spaces. The corresponding public URL never does: this canonical representation
    percent-encodes path and fragment characters while preserving query delimiters, public ``+``
    semantics, and existing escapes with their exact hexadecimal case preserved.
    """

    parsed = urlsplit(reference)
    if not reference.startswith("/") or reference.startswith("//") or parsed.netloc:
        raise ContractInventoryError("public_reference must be root-relative")
    path = _quote_preserving_escapes(parsed.path, safe="/@")
    query = _quote_preserving_escapes(parsed.query, safe="!$&'()*+,;=:@/?")
    fragment = _quote_preserving_escapes(parsed.fragment, safe="")
    return urlunsplit(("", "", path, query, fragment))


def _quote_preserving_escapes(value: str, *, safe: str) -> str:
    parts: list[str] = []
    start = 0
    for match in _PERCENT_ESCAPE.finditer(value):
        parts.append(quote(value[start : match.start()], safe=safe))
        parts.append(match.group())
        start = match.end()
    parts.append(quote(value[start:], safe=safe))
    return "".join(parts)


def _contract(
    *,
    source: _Source,
    reference: str,
    percent_encoded_reference: str | None,
    source_locator: str,
    source_path: str | None,
    contract_kind: str,
    expected_status: int | None,
    machine_contract: bool,
    route_pattern: str | None = None,
    route_name: str | None = None,
    route_urlconf: str | None = None,
    route_callback: str | None = None,
) -> PublicContract:
    parsed = urlsplit(reference)
    canonical_reference = canonical_percent_encoded_reference(reference)
    network_reference = percent_encoded_reference or canonical_reference
    return PublicContract(
        contract_id=public_contract_id(
            source.source_id,
            reference,
            route_pattern=route_pattern,
            route_name=route_name,
            route_urlconf=route_urlconf,
            route_callback=route_callback,
        ),
        public_url=f"{source.origin}{network_reference}",
        public_reference=reference,
        percent_encoded_public_reference=network_reference,
        path=parsed.path,
        query=parsed.query,
        fragment=parsed.fragment,
        source_id=source.source_id,
        source_repository=source.repository,
        source_revision=source.revision,
        source_locator=source_locator,
        source_path=source_path,
        contract_kind=contract_kind,
        expected_status=expected_status,
        machine_contract=machine_contract,
        classification=ContractClassification.PRESERVE,
        review_state=ReviewState.PROPOSED_PRESERVE,
        route_pattern=route_pattern,
        route_name=route_name,
        route_urlconf=route_urlconf,
        route_callback=route_callback,
    )


def _load_sources(path: Path) -> dict[str, _Source]:
    document = _load_json(path)
    records = _object_list(document, "records")
    sources: dict[str, _Source] = {}
    for record in records:
        source_id = _string(record, "source_id")
        base = urlsplit(_string(record, "public_base_url"))
        if base.scheme != "https" or not base.netloc:
            raise ContractInventoryError("source public base URL must be HTTPS")
        source = _Source(
            source_id=source_id,
            repository=_string(record, "repository"),
            revision=_string(record, "revision"),
            origin=f"{base.scheme}://{base.netloc}",
        )
        if source_id in sources:
            raise ContractInventoryError(f"duplicate source provenance: {source_id}")
        sources[source_id] = source
    return sources


def _source_for(row: dict[str, object], sources: dict[str, _Source]) -> _Source:
    source_id = _string(row, "source_id")
    try:
        source = sources[source_id]
    except KeyError as error:
        raise ContractInventoryError(f"unknown source id: {source_id}") from error
    if _string(row, "source_revision") != source.revision:
        raise ContractInventoryError(f"source revision mismatch: {source_id}")
    return source


def _target_status(
    contracts: list[PublicContract],
    reference_index: dict[tuple[str, str], list[int]],
    source_id: str,
    target_reference: str,
) -> int:
    matches = reference_index.get((source_id, target_reference), [])
    if len(matches) != 1:
        raise ContractInventoryError(
            f"fragment target must resolve to one generated path: {source_id} {target_reference}"
        )
    expected_status = contracts[matches[0]].expected_status
    if expected_status is None:
        raise ContractInventoryError("fragment target has no expected status")
    return expected_status


def _load_json(path: Path) -> dict[str, object]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=_unique_object)
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ContractInventoryError(f"cannot read strict JSON artifact: {path.name}") from error
    if not isinstance(value, dict):
        raise ContractInventoryError(f"JSON artifact must be an object: {path.name}")
    return value


def _load_jsonl(path: Path) -> tuple[dict[str, object], ...]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError) as error:
        raise ContractInventoryError(f"cannot read JSONL artifact: {path.name}") from error
    if not lines or any(not line for line in lines):
        raise ContractInventoryError(f"JSONL artifact must contain non-empty rows: {path.name}")
    rows: list[dict[str, object]] = []
    for number, line in enumerate(lines, start=1):
        try:
            value = json.loads(line, object_pairs_hook=_unique_object)
        except json.JSONDecodeError as error:
            raise ContractInventoryError(f"invalid JSONL row: {path.name}:{number}") from error
        if not isinstance(value, dict):
            raise ContractInventoryError(f"JSONL row must be an object: {path.name}:{number}")
        rows.append(value)
    return tuple(rows)


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ContractInventoryError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _require_keys(row: dict[str, object], expected: set[str], label: str) -> None:
    if set(row) != expected:
        missing = sorted(expected - set(row))
        extra = sorted(set(row) - expected)
        raise ContractInventoryError(f"{label} fields differ; missing={missing}, extra={extra}")


def _require_preserve(row: dict[str, object]) -> None:
    if row.get("classification") != "preserve":
        raise ContractInventoryError("source contract is not a proposed preserve")


def _require_baseline_values(row: dict[str, object]) -> None:
    _require_preserve(row)
    if _integer(row, "schema_version") != SCHEMA_VERSION:
        raise ContractInventoryError("unsupported source contract schema version")


def _string(row: dict[str, object], key: str) -> str:
    value = row.get(key)
    if not isinstance(value, str) or not value:
        raise ContractInventoryError(f"{key} must be a non-empty string")
    return value


def _nullable_string(row: dict[str, object], key: str) -> str | None:
    value = row.get(key)
    if value is None:
        return None
    if not isinstance(value, str) or not value:
        raise ContractInventoryError(f"{key} must be null or a non-empty string")
    return value


def _boolean(row: dict[str, object], key: str) -> bool:
    value = row.get(key)
    if not isinstance(value, bool):
        raise ContractInventoryError(f"{key} must be a boolean")
    return value


def _nullable_boolean(row: dict[str, object], key: str) -> bool | None:
    value = row.get(key)
    if value is None:
        return None
    if not isinstance(value, bool):
        raise ContractInventoryError(f"{key} must be null or a boolean")
    return value


def _integer(row: dict[str, object], key: str) -> int:
    value = row.get(key)
    if isinstance(value, bool) or not isinstance(value, int):
        raise ContractInventoryError(f"{key} must be an integer")
    return value


def _nullable_status(row: dict[str, object], key: str) -> int | None:
    value = row.get(key)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or not 100 <= value <= 599:
        raise ContractInventoryError(f"{key} must be null or an HTTP status")
    return value


def _object_list(row: dict[str, object], key: str) -> list[dict[str, object]]:
    value = row.get(key)
    if not isinstance(value, list) or not all(isinstance(item, dict) for item in value):
        raise ContractInventoryError(f"{key} must be a list of objects")
    return value
