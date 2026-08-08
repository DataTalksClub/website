from __future__ import annotations

import argparse
import base64
import hashlib
import os
import re
import stat
import sys
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, NoReturn

from deploy import gate_b_evidence as evidence

PROJECT_ROOT = Path(__file__).resolve().parents[1]
TMP_ROOT = PROJECT_ROOT / ".tmp"
SEED_PATH = PROJECT_ROOT / "deploy/gate_b_binding_seed.json"
CONTRACT_PATH = PROJECT_ROOT / "deploy/gate_b_execution_contract.json"
MANIFEST_PATH = PROJECT_ROOT / "deploy/gate_b_manifest.json"
MAX_INPUT_BYTES = 4 * 1024 * 1024
MAX_STDOUT_BYTES = 2 * 1024 * 1024
MAX_STDERR_BYTES = 16 * 1024
CAPTURE_ID_CLOCK_SKEW_SECONDS = 5
MAXIMUM_CAPTURE_DURATION_SECONDS = 900
CAPTURE_ID_PATTERN = re.compile(r"[0-9]{8}T[0-9]{6}Z-[0-9a-f]{12}")
STATUS_TIME_PATTERN = re.compile(
    r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]{1,6})?Z"
)
CREDENTIAL_EXPIRATION_GRAMMAR = (
    r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:Z|\+00:00)"
)
CREDENTIAL_EXPIRATION_PATTERN = re.compile(CREDENTIAL_EXPIRATION_GRAMMAR)
CREDENTIAL_EXPIRATION_ENCODINGS = (
    "YYYY-MM-DDTHH:MM:SSZ",
    "YYYY-MM-DDTHH:MM:SS+00:00",
)
SESSION_PATTERN = re.compile(r"phone-sandbox-[0-9a-f]{8}")
HEX_64_PATTERN = re.compile(r"[0-9a-f]{64}")
EXPECTED_SEED_FILE_SHA256 = "6a32cfabcad8c3cf18d5de8d46b30d970f8e85dd0a65a0cac700762eb4b6f52b"
EXPECTED_SEED_CANONICAL_SHA256 = "dcc02b49de0107aca1ce87dc7cdad18b3080f913e9281033449bbf14cab112dc"
EXPECTED_CONTRACT_FILE_SHA256 = "8cf5b62e78ee617d46c1338452caf493cd60e1ffa93169c2a026feab6d300342"
EXPECTED_CONTRACT_CANONICAL_SHA256 = (
    "2c54ce2bc300a4611c9bfc53141c8f3fcfad84e074944936031dee1c92589eb9"
)
EXPECTED_RESOLVED_GRAPH_SHA256 = "2cfbe086383df8f96353980fe08eb0d0e877095a3e82cb743ce3488f02a8594a"


class AssemblyError(ValueError):
    """A fail-closed error containing only an allowlisted safe code."""

    def __init__(self, code: str) -> None:
        if not re.fullmatch(r"[a-z0-9-]+", code):
            code = "invalid-gate-b-assembly"
        self.code = code
        super().__init__(code)


def _fail(code: str) -> NoReturn:
    raise AssemblyError(code)


def _object(value: Any, code: str) -> dict[str, Any]:
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        _fail(code)
    return value


def _list(value: Any, code: str) -> list[Any]:
    if not isinstance(value, list):
        _fail(code)
    return value


def _keys(value: Mapping[str, Any], required: set[str], optional: set[str], code: str) -> None:
    if not required <= set(value) or not set(value) <= required | optional:
        _fail(code)


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _canonical_sha256(value: Any) -> str:
    return _sha256_bytes(evidence.canonical_json_bytes(value))


def _read_tracked_json(path: Path, code: str) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file() or path.stat().st_size > MAX_INPUT_BYTES:
        _fail(code)
    try:
        return _object(evidence.parse_json(path.read_text(encoding="utf-8")), code)
    except (OSError, UnicodeError, evidence.EvidenceError) as exc:
        raise AssemblyError(code) from exc


def load_seed(path: Path = SEED_PATH) -> dict[str, Any]:
    if _sha256_bytes(path.read_bytes()) != EXPECTED_SEED_FILE_SHA256:
        _fail("binding-seed-code-pin-mismatch")
    seed = _read_tracked_json(path, "invalid-binding-seed")
    if _canonical_sha256(seed) != EXPECTED_SEED_CANONICAL_SHA256:
        _fail("binding-seed-code-pin-mismatch")
    return seed


def load_execution_contract(path: Path = CONTRACT_PATH) -> dict[str, Any]:
    if _sha256_bytes(path.read_bytes()) != EXPECTED_CONTRACT_FILE_SHA256:
        _fail("execution-contract-code-pin-mismatch")
    contract = _read_tracked_json(path, "invalid-execution-contract")
    if _canonical_sha256(contract) != EXPECTED_CONTRACT_CANONICAL_SHA256:
        _fail("execution-contract-code-pin-mismatch")
    return contract


def load_manifest(path: Path = MANIFEST_PATH) -> dict[str, Any]:
    manifest = _read_tracked_json(path, "invalid-gate-b-manifest")
    try:
        evidence.validate_manifest(manifest)
    except evidence.EvidenceError as exc:
        raise AssemblyError("invalid-gate-b-manifest") from exc
    return manifest


def _binding_payload(seed: Mapping[str, Any], identity: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "cloudfront": seed["cloudfront"],
        "target_group": seed["target_group"],
        "task_definitions": seed["task_definitions"],
        "secret_arns": seed["secret_arns"],
        "network": seed["network"],
        "dns_records": seed["dns_records"],
        "operator_identity": dict(identity),
        "github_repository_variables": seed["github_repository_variables"],
        "github_environment_variables": seed["github_environment_variables"],
    }


def _envelope(
    kind: str,
    payload: dict[str, Any],
    capture_id: str,
    source: Mapping[str, Any],
) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "capture_id": capture_id,
        "website_sha": source["website_sha"],
        "infra_sha": source["infra_sha"],
        "kind": kind,
        "payload": payload,
        "payload_sha256": _canonical_sha256(payload),
    }


def validate_seed(seed: Mapping[str, Any], manifest: Mapping[str, Any]) -> dict[str, Any]:
    required = {
        "schema_version",
        "seed_id",
        "source_binding",
        "cloudfront",
        "target_group",
        "task_definitions",
        "secret_arns",
        "network",
        "dns_records",
        "dns_records_sha256",
        "normalized_dns_full_records",
        "normalized_dns_full_records_sha256",
        "source_dns_full_records_bytes",
        "source_dns_full_records_sha256",
        "github_repository_variables",
        "github_environment_variables",
        "operator_parent",
        "terraform_address_count",
    }
    if set(seed) != required or seed["schema_version"] != 1:
        _fail("invalid-binding-seed")
    if seed["seed_id"] != "website-sandbox-gate-b-binding-v1":
        _fail("invalid-binding-seed")
    if seed["source_binding"] != manifest["source_binding"]:
        _fail("seed-source-mismatch")
    if seed["dns_records_sha256"] != _canonical_sha256(seed["dns_records"]):
        _fail("seed-dns-digest-mismatch")
    if seed["normalized_dns_full_records_sha256"] != _canonical_sha256(
        seed["normalized_dns_full_records"]
    ):
        _fail("seed-normalized-dns-digest-mismatch")
    if seed["source_dns_full_records_bytes"] != 1242 or seed["source_dns_full_records_sha256"] != (
        "4cadb0505d61e04a7e652b7f2c2e303bfa573407a65dffc30d9fbf6d2708b0e7"
    ):
        _fail("seed-provenance-mismatch")
    parent = _object(seed["operator_parent"], "invalid-operator-parent")
    if parent != {
        "account_id": "817685572750",
        "role_arn": "arn:aws:iam::817685572750:role/phone-aws-sandbox-role",
        "role_id": "AROA34YO3VSHI2OCVBKTW",
        "session_name_pattern": "^phone-sandbox-[0-9a-f]{8}$",
        "forbidden_role_names": [
            "website-sandbox-github-publisher",
            "website-sandbox-github-deployer",
            "website-sandbox-task-application",
            "website-sandbox-task-execution",
        ],
    }:
        _fail("invalid-operator-parent")
    if seed["terraform_address_count"] != 98:
        _fail("invalid-terraform-provenance")
    provisional = {
        "account_id": parent["account_id"],
        "arn": (
            "arn:aws:sts::817685572750:assumed-role/phone-aws-sandbox-role/phone-sandbox-00000000"
        ),
        "user_id": f"{parent['role_id']}:phone-sandbox-00000000",
    }
    document = _envelope(
        "bindings",
        _binding_payload(seed, provisional),
        "20260808T000000Z-000000000000",
        manifest["source_binding"],
    )
    try:
        evidence.validate_bindings(document, manifest)
    except evidence.EvidenceError as exc:
        raise AssemblyError("invalid-binding-seed") from exc
    return {
        "seed_id": seed["seed_id"],
        "seed_sha256": _canonical_sha256(seed),
        "status": "PASS",
    }


def validate_operator_identity(identity: Mapping[str, Any], seed: Mapping[str, Any]) -> None:
    if set(identity) != {"account_id", "arn", "user_id"}:
        _fail("operator-identity-mismatch")
    parent = _object(seed["operator_parent"], "invalid-operator-parent")
    arn = identity.get("arn")
    user_id = identity.get("user_id")
    if not isinstance(arn, str) or not isinstance(user_id, str):
        _fail("operator-identity-mismatch")
    prefix = "arn:aws:sts::817685572750:assumed-role/phone-aws-sandbox-role/"
    if not arn.startswith(prefix):
        _fail("operator-identity-mismatch")
    session = arn.removeprefix(prefix)
    if (
        identity.get("account_id") != parent["account_id"]
        or not SESSION_PATTERN.fullmatch(session)
        or user_id != f"{parent['role_id']}:{session}"
    ):
        _fail("operator-identity-mismatch")


def build_bindings_envelope(
    seed: Mapping[str, Any],
    manifest: Mapping[str, Any],
    capture_id: str,
    sts_response: Mapping[str, Any],
) -> dict[str, Any]:
    if not CAPTURE_ID_PATTERN.fullmatch(capture_id):
        _fail("invalid-capture-id")
    if set(sts_response) != {"Account", "Arn", "UserId"}:
        _fail("invalid-sts-response")
    identity = {
        "account_id": sts_response["Account"],
        "arn": sts_response["Arn"],
        "user_id": sts_response["UserId"],
    }
    validate_operator_identity(identity, seed)
    document = _envelope(
        "bindings", _binding_payload(seed, identity), capture_id, manifest["source_binding"]
    )
    try:
        evidence.validate_bindings(document, manifest)
    except evidence.EvidenceError as exc:
        raise AssemblyError("binding-validation-stop") from exc
    return document


def _resolve(value: Any, flattened: Mapping[str, str]) -> Any:
    if isinstance(value, str):
        match = re.fullmatch(r"\$\{([a-z0-9_]+)\}", value)
        if match:
            if match.group(1) not in flattened:
                _fail("unresolved-simulator-binding")
            return flattened[match.group(1)]
        return value
    if isinstance(value, dict):
        return {key: _resolve(child, flattened) for key, child in value.items()}
    if isinstance(value, list):
        return [_resolve(child, flattened) for child in value]
    return value


def _context_entries(context: Mapping[str, str]) -> list[dict[str, Any]]:
    return [
        {
            "ContextKeyName": key,
            "ContextKeyValues": [value],
            "ContextKeyType": "arn" if value.startswith("arn:") else "string",
        }
        for key, value in sorted(context.items())
    ]


def resolve_simulator_plan(
    contract: Mapping[str, Any], manifest: Mapping[str, Any], bindings: Mapping[str, Any]
) -> list[dict[str, Any]]:
    binding_payload = _object(bindings["payload"], "invalid-bindings-envelope")
    flattened = {
        "cloudfront_distribution_arn": binding_payload["cloudfront"]["distribution_arn"],
        "cloudfront_distribution_id": binding_payload["cloudfront"]["distribution_id"],
        "cloudfront_domain_name": binding_payload["cloudfront"]["domain_name"],
        "target_group_arn": binding_payload["target_group"]["arn"],
        "web_task_definition_arn": binding_payload["task_definitions"]["web"],
        "worker_task_definition_arn": binding_payload["task_definitions"]["worker"],
        "migration_task_definition_arn": binding_payload["task_definitions"]["migration"],
        "database_secret_arn": binding_payload["secret_arns"]["database_url"],
    }
    graph = _object(contract["graph"], "invalid-execution-contract")
    recipe = _object(graph["simulator_recipe"], "invalid-execution-contract")
    roles = manifest["static"]["roles"]
    result: list[dict[str, Any]] = []
    for index, raw_row in enumerate(manifest["simulator_rows"], start=85):
        row = _resolve(raw_row, flattened)
        context = _object(row["context"], "invalid-simulator-row")
        argv = [
            *recipe["argv_prefix"],
            "--policy-source-arn",
            roles[row["principal"]]["arn"],
            "--action-names",
            row["action"],
            "--resource-arns",
            row["resource"],
        ]
        if context:
            argv += [
                "--context-entries",
                evidence.canonical_json_bytes(_context_entries(context)).decode("utf-8"),
            ]
        argv += recipe["argv_suffix"]
        result.append(
            {
                "id": f"simulator-{row['id']}",
                "row_id": row["id"],
                "sequence": index,
                "phase": "simulator",
                "provider": "aws",
                "mapper": "simulator-result-v1",
                "service": "iam",
                "operation": "SimulatePrincipalPolicy",
                "target": row["id"],
                "expected": {"exit_code": 0},
                "argv": argv,
                "request": {
                    "policy_source_arn": roles[row["principal"]]["arn"],
                    "action_names": [row["action"]],
                    "resource_arns": [row["resource"]],
                    "context_entries": _context_entries(context),
                },
            }
        )
    if len(result) != 90:
        _fail("simulator-plan-count")
    return result


def complete_operation_specs(
    contract: Mapping[str, Any], manifest: Mapping[str, Any], bindings: Mapping[str, Any]
) -> list[dict[str, Any]]:
    readbacks = _list(contract["graph"]["operations"], "invalid-execution-contract")
    specs = [
        *_object_list(readbacks, "invalid-execution-contract"),
        *resolve_simulator_plan(contract, manifest, bindings),
    ]
    if execution_graph_sha256(specs) != EXPECTED_RESOLVED_GRAPH_SHA256:
        _fail("resolved-graph-code-pin-mismatch")
    return specs


def _object_list(values: Sequence[Any], code: str) -> list[dict[str, Any]]:
    return [_object(value, code) for value in values]


def execution_graph_sha256(specs: Sequence[Mapping[str, Any]]) -> str:
    return _canonical_sha256(list(specs))


def validate_execution_contract(
    contract: Mapping[str, Any],
    seed: Mapping[str, Any],
    manifest: Mapping[str, Any],
) -> dict[str, Any]:
    if set(contract) != {
        "schema_version",
        "contract_id",
        "source_binding",
        "accepted_execution_binding",
        "bindings",
        "evidence",
        "credential_process",
        "tools",
        "child_environments",
        "limits",
        "graph",
        "raw_capture",
        "outputs",
    }:
        _fail("invalid-execution-contract")
    if (
        contract["schema_version"] != 1
        or contract["contract_id"] != "website-sandbox-gate-b-execution-v1"
        or contract["source_binding"] != manifest["source_binding"]
    ):
        _fail("invalid-execution-contract")
    if contract["accepted_execution_binding"] != {
        "website": {
            "sha": "eded5f05336ceaaa35ff7c62397a2785e74b4e62",
            "tree": "40887d94f6149d1a2841483f9ada5a8bda5843a1",
            "green_run_id": 31227813522,
        },
        "infrastructure": {
            "sha": "95d93f7e07ded19e482a0c6d6471fbd93fb608d8",
            "tree": "1c38fdf6872a448d92e8191282525bafd3ab3410",
            "green_run_id": 31221824132,
        },
        "aws_gate": {
            "sha": "440b03d27f744873b8cdca27a50a7e544b232f1f",
            "tree": "e85f6904d4398a3fdd0c2f3cee3dccf8548dcd3f",
        },
    }:
        _fail("accepted-execution-binding-mismatch")
    validate_seed(seed, manifest)
    bindings = _object(contract["bindings"], "invalid-execution-contract")
    if bindings != {
        "seed_path": "deploy/gate_b_binding_seed.json",
        "seed_file_sha256": _sha256_bytes(SEED_PATH.read_bytes()),
        "seed_canonical_sha256": _canonical_sha256(seed),
    }:
        _fail("contract-seed-mismatch")
    evidence_contract = _object(contract["evidence"], "invalid-execution-contract")
    tracked = {
        "manifest_path": "deploy/gate_b_manifest.json",
        "manifest_file_sha256": _sha256_bytes(MANIFEST_PATH.read_bytes()),
        "manifest_canonical_sha256": _canonical_sha256(manifest),
        "validator_path": "deploy/gate_b_evidence.py",
        "validator_file_sha256": _sha256_bytes(
            (PROJECT_ROOT / "deploy/gate_b_evidence.py").read_bytes()
        ),
        "tests_path": "core/tests/test_gate_b_evidence.py",
        "tests_file_sha256": _sha256_bytes(
            (PROJECT_ROOT / "core/tests/test_gate_b_evidence.py").read_bytes()
        ),
    }
    if evidence_contract != tracked:
        _fail("contract-evidence-mismatch")
    credential = _object(contract["credential_process"], "invalid-execution-contract")
    if credential != {
        "configured_argv": [
            "python3",
            "/home/alexey/git/phone-aws-auth/tools/install_aws_gate.py",
            "credential-process",
            "--env",
            "/home/alexey/.config/aws-gate/env",
        ],
        "execution_argv": [
            "/usr/bin/python3",
            "/home/alexey/git/phone-aws-auth/tools/install_aws_gate.py",
            "credential-process",
            "--env",
            "/home/alexey/.config/aws-gate/env",
        ],
        "interpreter_resolved_path": "/usr/bin/python3.12",
        "interpreter_file_sha256": (
            "1643dacd9feaedc58f3cc581e4d22577dfe25c09b10282936186ccf0f2e61118"
        ),
        "interpreter_mode": "0755",
        "interpreter_owner_uid": 0,
        "script_path": "/home/alexey/git/phone-aws-auth/tools/install_aws_gate.py",
        "script_file_sha256": "b9800465d1adde58fd8811b73bfad2b06b1f444ea641db6e45de0096f5d03c85",
        "script_mode": "0775",
        "config_path": "/home/alexey/.aws/config",
        "config_file_sha256": ("3733f5834c795cae404516e6b6e5ba6ecd3011b67eb8a0b4bf766b16e18c8c68"),
        "config_section": "default",
        "config_mode": "0600",
        "env_path": "/home/alexey/.config/aws-gate/env",
        "env_mode": "0600",
        "expiration_grammar": CREDENTIAL_EXPIRATION_GRAMMAR,
        "accepted_expiration_encodings": list(CREDENTIAL_EXPIRATION_ENCODINGS),
        "resolve_count": 1,
        "accepted_duration_seconds": 900,
        "minimum_ttl_seconds_at_start": 840,
        "hard_reserve_seconds": 120,
        "refresh_allowed": False,
    }:
        _fail("invalid-credential-contract")
    environments = _object(contract["child_environments"], "invalid-execution-contract")
    aws_fixed = _object(environments.get("aws_fixed_values"), "invalid-execution-contract")
    if (
        aws_fixed.get("AWS_CONFIG_FILE") != "/dev/null"
        or aws_fixed.get("AWS_SHARED_CREDENTIALS_FILE") != "/dev/null"
    ):
        _fail("unsafe-aws-child-environment")
    if contract["tools"] != {
        "aws": {
            "invocation_path": "/home/alexey/.local/bin/aws",
            "resolved_path": "/home/alexey/.local/share/uv/tools/awscli/bin/aws",
            "version": "aws-cli/1.44.80",
            "file_sha256": "5fb14ac1e1a08a5a9e0d98084999cd6ed938c7afc2fa0b7d0994428b5ea81987",
            "mode": "0775",
            "owner_uid": 1000,
            "interpreter_invocation_path": ("/home/alexey/.local/share/uv/tools/awscli/bin/python"),
            "interpreter_resolved_path": (
                "/home/alexey/.local/share/uv/python/cpython-3.14.3-linux-x86_64-gnu/bin/python3.14"
            ),
            "interpreter_file_sha256": (
                "b9128fe43e6c043b07e58f05d71c404a34095d04c4219d573bdede3225df37fc"
            ),
            "interpreter_mode": "0775",
            "interpreter_owner_uid": 1000,
        },
        "github": {
            "invocation_path": "/usr/bin/gh",
            "resolved_path": "/usr/bin/gh",
            "version": "gh version 2.87.0",
            "file_sha256": "890c9d795f468127872c4cd83a38c750b560dafbbb6f8e5ab555909459877069",
            "mode": "0755",
            "owner_uid": 0,
        },
    }:
        _fail("invalid-tool-contract")
    limits = _object(contract["limits"], "invalid-execution-contract")
    if limits != {
        "capture_directory_mode": "0700",
        "private_file_mode": "0600",
        "require_owner_euid": True,
        "require_single_link": True,
        "max_stdout_bytes": 2097152,
        "max_stderr_bytes": 16384,
        "command_timeout_seconds": 30,
        "credential_timeout_seconds": 30,
        "capture_id_clock_skew_seconds": CAPTURE_ID_CLOCK_SKEW_SECONDS,
        "maximum_capture_duration_seconds": MAXIMUM_CAPTURE_DURATION_SECONDS,
        "max_concurrency": 8,
        "success_stderr": "empty",
        "accepted_error_stdout": "empty",
        "accepted_error_stderr": "one-anchored-aws-cli-error",
        "retry_count": 0,
        "resume_allowed": False,
    }:
        _fail("invalid-execution-limits")
    graph = _object(contract["graph"], "invalid-execution-contract")
    operations = _object_list(
        _list(graph.get("operations"), "invalid-execution-contract"), "invalid-execution-contract"
    )
    if len(operations) != 84:
        _fail("readback-operation-count")
    if [item.get("sequence") for item in operations] != list(range(1, 85)):
        _fail("invalid-operation-sequence")
    ids = [item.get("id") for item in operations]
    if not all(isinstance(item, str) and re.fullmatch(r"[A-Za-z0-9_-]+", item) for item in ids):
        _fail("invalid-operation-id")
    if len(set(ids)) != 84 or ids[0] != "sts-caller":
        _fail("invalid-operation-id")
    if operations[0].get("phase") != "identity" or any(
        item.get("phase") != "readback" for item in operations[1:]
    ):
        _fail("invalid-operation-phase")
    if (
        sum(item.get("provider") == "aws" for item in operations) != 58
        or sum(item.get("provider") == "github" for item in operations) != 26
    ):
        _fail("invalid-provider-count")
    allowed_executables = {
        contract["tools"]["aws"]["resolved_path"],
        contract["tools"]["github"]["resolved_path"],
    }
    for item in operations:
        argv = item.get("argv")
        if (
            not isinstance(argv, list)
            or not argv
            or argv[0] not in allowed_executables
            or not all(isinstance(arg, str) and "${" not in arg for arg in argv)
        ):
            _fail("invalid-operation-argv")
    expected_errors = {
        "s3-policy": "NoSuchBucketPolicy",
        "s3-lock-object": "404",
        "ecr-zero-digest": "ImageNotFoundException",
        "ecr-repository-policy": "RepositoryPolicyNotFoundException",
        "ecr-registry-policy": "RegistryPolicyNotFoundException",
    }
    actual_errors = {
        item["id"]: item["expected"].get("error_code")
        for item in operations
        if item["expected"].get("exit_code") == "nonzero"
    }
    if actual_errors != expected_errors:
        _fail("invalid-absence-contract")
    if graph.get("readback_graph_sha256") != _canonical_sha256(operations):
        _fail("readback-graph-mismatch")
    recipe = _object(graph.get("simulator_recipe"), "invalid-execution-contract")
    if (
        recipe.get("count") != 90
        or recipe.get("source_sha256") != _canonical_sha256(manifest["simulator_rows"])
        or graph.get("provider_operation_count") != 174
        or graph.get("expected_nonzero_count") != 5
    ):
        _fail("invalid-simulator-recipe")
    raw_contract = _object(contract["raw_capture"], "invalid-execution-contract")
    if raw_contract != {
        "directory": "raw",
        "triplet_suffixes": [
            ".response.json",
            ".status.json",
            ".error.json",
        ],
        "response_wrapper_schema": ["stdout_base64"],
        "error_wrapper_schema": ["stderr_base64"],
        "status_schema": list(CONTRACT_STATUS_FIELDS),
        "success_error_document": {},
        "expected_error_response_document": {},
        "safe_error_schema": ["code", "operation", "provider", "service", "target"],
    }:
        _fail("invalid-raw-contract")
    if contract["outputs"] != [
        "bindings.json",
        "bindings.result.json",
        "policies.json",
        "policies.result.json",
        "resources.json",
        "resources.result.json",
        "simulator.json",
        "simulator.result.json",
        "summary.json",
        "execution-attestation.json",
    ]:
        _fail("invalid-output-contract")
    return {
        "contract_id": contract["contract_id"],
        "readback_graph_sha256": graph["readback_graph_sha256"],
        "status": "PASS",
    }


def _safe_capture_directory(path: Path) -> Path:
    if ".." in path.parts:
        _fail("capture-path-outside-tmp")
    try:
        resolved_root = TMP_ROOT.resolve(strict=True)
        resolved = path.resolve(strict=True)
    except OSError as exc:
        raise AssemblyError("invalid-capture-directory") from exc
    root_info = TMP_ROOT.lstat()
    if (
        stat.S_ISLNK(root_info.st_mode)
        or not stat.S_ISDIR(root_info.st_mode)
        or stat.S_IMODE(root_info.st_mode) != 0o700
        or root_info.st_uid != os.geteuid()
        or root_info.st_nlink < 2
    ):
        _fail("unsafe-tmp-root")
    if resolved_root not in resolved.parents or resolved.parent != resolved_root:
        _fail("capture-path-outside-tmp")
    if path.is_symlink() or resolved.is_symlink():
        _fail("capture-symlink")
    info = resolved.stat()
    if (
        not stat.S_ISDIR(info.st_mode)
        or stat.S_IMODE(info.st_mode) != 0o700
        or info.st_uid != os.geteuid()
        or info.st_nlink < 2
    ):
        _fail("unsafe-capture-directory")
    return resolved


def _open_private_directory(path: Path) -> int:
    if ".." in path.parts:
        _fail("capture-path-outside-tmp")
    absolute = path if path.is_absolute() else PROJECT_ROOT / path
    flags = os.O_RDONLY
    if hasattr(os, "O_DIRECTORY"):
        flags |= os.O_DIRECTORY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(absolute.anchor, flags)
    try:
        for part in absolute.parts[1:]:
            child = os.open(part, flags, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = child
        info = os.fstat(descriptor)
        if (
            not stat.S_ISDIR(info.st_mode)
            or stat.S_IMODE(info.st_mode) != 0o700
            or info.st_uid != os.geteuid()
            or info.st_nlink < 2
        ):
            _fail("unsafe-raw-directory")
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


def _read_private_json(path: Path) -> dict[str, Any]:
    file_flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        file_flags |= os.O_NOFOLLOW
    try:
        directory_descriptor = _open_private_directory(path.parent)
    except OSError as exc:
        raise AssemblyError("missing-raw-capture") from exc
    try:
        directory_info = os.fstat(directory_descriptor)
        if (
            not stat.S_ISDIR(directory_info.st_mode)
            or stat.S_IMODE(directory_info.st_mode) != 0o700
            or directory_info.st_uid != os.geteuid()
            or directory_info.st_nlink < 2
        ):
            _fail("unsafe-raw-directory")
        descriptor = os.open(path.name, file_flags, dir_fd=directory_descriptor)
        try:
            info = os.fstat(descriptor)
            if (
                not stat.S_ISREG(info.st_mode)
                or stat.S_IMODE(info.st_mode) != 0o600
                or info.st_uid != os.geteuid()
                or info.st_nlink != 1
                or info.st_size > MAX_INPUT_BYTES
            ):
                _fail("unsafe-raw-capture")
            data = bytearray()
            while len(data) <= MAX_INPUT_BYTES:
                chunk = os.read(descriptor, min(64 * 1024, MAX_INPUT_BYTES + 1 - len(data)))
                if not chunk:
                    break
                data.extend(chunk)
            if len(data) > MAX_INPUT_BYTES:
                _fail("unsafe-raw-capture")
        finally:
            os.close(descriptor)
    except AssemblyError:
        raise
    except OSError as exc:
        raise AssemblyError("missing-raw-capture") from exc
    finally:
        os.close(directory_descriptor)
    try:
        return _object(evidence.parse_json(bytes(data).decode("utf-8")), "invalid-raw-json")
    except (UnicodeError, evidence.EvidenceError) as exc:
        raise AssemblyError("invalid-raw-json") from exc


def _decode_raw_wrapper(document: Mapping[str, Any], field: str) -> bytes:
    if set(document) != {field}:
        _fail("invalid-raw-wrapper")
    encoded = document[field]
    if not isinstance(encoded, str):
        _fail("invalid-raw-wrapper")
    try:
        raw = base64.b64decode(encoded, validate=True)
    except (ValueError, TypeError) as exc:
        raise AssemblyError("invalid-raw-wrapper") from exc
    return raw


def _parse_stdout(raw: bytes, maximum: int) -> dict[str, Any]:
    if len(raw) > maximum:
        _fail("provider-output-too-large")
    if raw.strip() == b"":
        return {}
    try:
        value = evidence.parse_json(raw.decode("utf-8"))
    except (UnicodeError, evidence.EvidenceError) as exc:
        raise AssemblyError("invalid-provider-json") from exc
    return _object(value, "invalid-provider-json")


def _parse_anchored_error(raw: bytes, spec: Mapping[str, Any], maximum: int) -> str:
    if len(raw) > maximum:
        _fail("provider-error-too-large")
    try:
        message = raw.decode("utf-8")
    except UnicodeError as exc:
        raise AssemblyError("invalid-provider-error") from exc
    code = spec["expected"]["error_code"]
    pattern = re.compile(
        rf"^An error occurred \({re.escape(code)}\) when calling the "
        rf"{re.escape(spec['operation'])} operation: [^\r\n]+\n?$"
    )
    if not pattern.fullmatch(message):
        _fail("unexpected-provider-error")
    return str(code)


def _validated_status_times(
    capture_id: str,
    started_value: Any,
    finished_value: Any,
    *,
    identity: bool = False,
) -> tuple[datetime, datetime]:
    try:
        capture_time = datetime.strptime(capture_id[:16], "%Y%m%dT%H%M%SZ").replace(tzinfo=UTC)
    except ValueError as exc:
        raise AssemblyError("invalid-capture-id") from exc
    if (
        not isinstance(started_value, str)
        or not isinstance(finished_value, str)
        or not STATUS_TIME_PATTERN.fullmatch(started_value)
        or not STATUS_TIME_PATTERN.fullmatch(finished_value)
    ):
        _fail("invalid-raw-status-time")
    try:
        started = datetime.fromisoformat(started_value.removesuffix("Z") + "+00:00")
        finished = datetime.fromisoformat(finished_value.removesuffix("Z") + "+00:00")
    except ValueError as exc:
        raise AssemblyError("invalid-raw-status-time") from exc
    earliest = capture_time - timedelta(seconds=CAPTURE_ID_CLOCK_SKEW_SECONDS)
    latest = capture_time + timedelta(seconds=MAXIMUM_CAPTURE_DURATION_SECONDS)
    if (
        finished < started
        or started < earliest
        or started > latest
        or (identity and started > capture_time + timedelta(seconds=CAPTURE_ID_CLOCK_SKEW_SECONDS))
        or finished > latest
        or finished - started > timedelta(seconds=31)
    ):
        _fail("invalid-raw-status-time")
    return started, finished


def _record_phase_finished(
    phase: str,
    started: datetime,
    finished: datetime,
    phase_finished: dict[str, datetime],
) -> None:
    if phase == "readback" and "identity" in phase_finished:
        if started < phase_finished["identity"]:
            _fail("invalid-raw-phase-order")
    if phase == "simulator" and "readback" in phase_finished:
        if started < phase_finished["readback"]:
            _fail("invalid-raw-phase-order")
    phase_finished[phase] = max(finished, phase_finished.get(phase, finished))


def load_raw_capture_set(
    capture_dir: Path,
    specs: Sequence[Mapping[str, Any]],
    *,
    expected_graph_sha256: str | None = None,
) -> dict[str, dict[str, Any]]:
    directory = _safe_capture_directory(capture_dir)
    raw_dir = directory / "raw"
    if raw_dir.is_symlink() or not raw_dir.is_dir():
        _fail("invalid-raw-directory")
    raw_info = raw_dir.stat()
    if (
        stat.S_IMODE(raw_info.st_mode) != 0o700
        or raw_info.st_uid != os.geteuid()
        or raw_info.st_nlink < 2
    ):
        _fail("unsafe-raw-directory")
    expected_names = {
        f"{spec['id']}{suffix}"
        for spec in specs
        for suffix in (".response.json", ".status.json", ".error.json")
    }
    actual_names = {entry.name for entry in raw_dir.iterdir()}
    if actual_names != expected_names:
        _fail("raw-inventory-mismatch")
    graph_hash = expected_graph_sha256 or execution_graph_sha256(specs)
    capture_id = directory.name.removeprefix("gate-b-")
    if directory.name != f"gate-b-{capture_id}" or not CAPTURE_ID_PATTERN.fullmatch(capture_id):
        _fail("invalid-capture-id")
    phase_finished: dict[str, datetime] = {}
    result: dict[str, dict[str, Any]] = {}
    for spec in specs:
        command_id = spec["id"]
        response_wrapper = _read_private_json(raw_dir / f"{command_id}.response.json")
        error_wrapper = _read_private_json(raw_dir / f"{command_id}.error.json")
        stdout = _decode_raw_wrapper(response_wrapper, "stdout_base64")
        stderr = _decode_raw_wrapper(error_wrapper, "stderr_base64")
        status_doc = _read_private_json(raw_dir / f"{command_id}.status.json")
        expected_status_keys = set(CONTRACT_STATUS_FIELDS)
        if set(status_doc) != expected_status_keys:
            _fail("invalid-raw-status")
        if (
            isinstance(status_doc["schema_version"], bool)
            or not isinstance(status_doc["schema_version"], int)
            or isinstance(status_doc["sequence"], bool)
            or not isinstance(status_doc["sequence"], int)
        ):
            _fail("raw-status-mismatch")
        if (
            status_doc["schema_version"] != 1
            or status_doc["capture_id"] != capture_id
            or status_doc["command_id"] != command_id
            or status_doc["sequence"] != spec["sequence"]
            or status_doc["phase"] != spec["phase"]
            or status_doc["provider"] != spec["provider"]
            or status_doc["argv_sha256"] != _canonical_sha256(spec["argv"])
            or status_doc["graph_sha256"] != graph_hash
            or status_doc["response_sha256"] != _sha256_bytes(stdout)
            or status_doc["error_sha256"] != _sha256_bytes(stderr)
        ):
            _fail("raw-status-mismatch")
        started, finished = _validated_status_times(
            capture_id,
            status_doc["started_at"],
            status_doc["finished_at"],
            identity=spec["phase"] == "identity",
        )
        phase = str(spec["phase"])
        _record_phase_finished(phase, started, finished, phase_finished)
        expected = spec["expected"]
        if isinstance(status_doc["exit_code"], bool) or not isinstance(
            status_doc["exit_code"], int
        ):
            _fail("unexpected-provider-result")
        if expected["exit_code"] == 0:
            response = _parse_stdout(stdout, MAX_STDOUT_BYTES)
            error: dict[str, Any] = {}
            if status_doc["exit_code"] != 0 or stderr != b"":
                _fail("unexpected-provider-result")
        else:
            if not isinstance(status_doc["exit_code"], int) or status_doc["exit_code"] == 0:
                _fail("unexpected-provider-result")
            code = _parse_anchored_error(stderr, spec, MAX_STDERR_BYTES)
            response = {}
            error = {
                "provider": spec["provider"],
                "service": spec["service"],
                "operation": spec["operation"],
                "target": spec["target"],
                "code": code,
            }
            if stdout != b"":
                _fail("unexpected-provider-result")
        result[command_id] = {
            "response": response,
            "error": error,
            "status": status_doc,
            "stdout_sha256": _sha256_bytes(stdout),
            "stderr_sha256": _sha256_bytes(stderr),
        }
    return result


CONTRACT_STATUS_FIELDS = (
    "argv_sha256",
    "capture_id",
    "command_id",
    "error_sha256",
    "exit_code",
    "finished_at",
    "graph_sha256",
    "phase",
    "provider",
    "response_sha256",
    "schema_version",
    "sequence",
    "started_at",
)


def _response(raw: Mapping[str, Mapping[str, Any]], command_id: str) -> dict[str, Any]:
    try:
        return _object(raw[command_id]["response"], "invalid-provider-response")
    except KeyError as exc:
        raise AssemblyError("missing-provider-response") from exc


def _one(value: Any, code: str) -> dict[str, Any]:
    values = _list(value, code)
    if len(values) != 1:
        _fail(code)
    return _object(values[0], code)


def _nonnegative_int(value: Any, code: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        _fail(code)
    return value


def _boolean(value: Any, code: str) -> bool:
    if not isinstance(value, bool):
        _fail(code)
    return value


def _nonempty_string(value: Any, code: str) -> str:
    if not isinstance(value, str) or not value:
        _fail(code)
    return value


def _aware_timestamp(value: Any, code: str) -> str:
    timestamp = _nonempty_string(value, code)
    try:
        parsed = datetime.fromisoformat(
            timestamp.removesuffix("Z") + "+00:00" if timestamp.endswith("Z") else timestamp
        )
    except ValueError as exc:
        raise AssemblyError(code) from exc
    if parsed.tzinfo is None:
        _fail(code)
    return timestamp


def _projected_count(
    response: Mapping[str, Any],
    *,
    count_field: str,
    type_field: str,
    code: str,
    token_field: str | None = None,
    truncated_code: str | None = None,
) -> int:
    required = {count_field, type_field}
    if token_field is not None:
        required.add(token_field)
    _keys(response, required, set(), code)
    if response[type_field] != "array":
        _fail(code)
    if token_field is not None and response[token_field] is not None:
        _fail(truncated_code or code)
    return _nonnegative_int(response[count_field], code)


def _no_pagination(value: Mapping[str, Any], code: str) -> None:
    if (
        ("IsTruncated" in value and value["IsTruncated"] is not False)
        or ("Truncated" in value and value["Truncated"] is not False)
        or value.get("NextToken") is not None
        or value.get("Marker") is not None
        or value.get("NextMarker") is not None
    ):
        _fail(code)


def _role_payload(raw: Mapping[str, Mapping[str, Any]], role_class: str) -> dict[str, Any]:
    role_doc = _response(raw, f"iam-{role_class}-role")
    _keys(role_doc, {"Role"}, set(), "invalid-iam-role-response")
    role = _object(role_doc["Role"], "invalid-iam-role-response")
    required = {"RoleName", "Path", "Arn", "MaxSessionDuration", "AssumeRolePolicyDocument"}
    optional = {
        "RoleId",
        "CreateDate",
        "Description",
        "PermissionsBoundary",
        "Tags",
        "RoleLastUsed",
    }
    _keys(role, required, optional, "invalid-iam-role-response")
    if "PermissionsBoundary" in role:
        _fail("iam-permissions-boundary-mismatch")
    if "RoleId" in role:
        _nonempty_string(role["RoleId"], "invalid-iam-role-response")
    if "CreateDate" in role:
        _aware_timestamp(role["CreateDate"], "invalid-iam-role-response")
    if "Description" in role and not isinstance(role["Description"], str):
        _fail("invalid-iam-role-response")
    if "Tags" in role:
        for item in _list(role["Tags"], "invalid-iam-role-response"):
            tag = _object(item, "invalid-iam-role-response")
            _keys(tag, {"Key", "Value"}, set(), "invalid-iam-role-response")
            _nonempty_string(tag["Key"], "invalid-iam-role-response")
            if not isinstance(tag["Value"], str):
                _fail("invalid-iam-role-response")
    if "RoleLastUsed" in role:
        last_used = _object(role["RoleLastUsed"], "invalid-iam-role-response")
        _keys(
            last_used,
            set(),
            {"LastUsedDate", "Region"},
            "invalid-iam-role-response",
        )
        if "LastUsedDate" in last_used:
            _aware_timestamp(last_used["LastUsedDate"], "invalid-iam-role-response")
        if "Region" in last_used:
            _nonempty_string(last_used["Region"], "invalid-iam-role-response")
    max_session_duration = _nonnegative_int(role["MaxSessionDuration"], "invalid-iam-role-response")
    if max_session_duration < 1:
        _fail("invalid-iam-role-response")
    inline_list = _response(raw, f"iam-{role_class}-inline-list")
    _keys(inline_list, {"PolicyNames"}, {"IsTruncated", "Marker"}, "invalid-iam-policy-list")
    _no_pagination(inline_list, "truncated-iam-policy-list")
    attached_list = _response(raw, f"iam-{role_class}-attached-list")
    _keys(attached_list, {"AttachedPolicies"}, {"IsTruncated", "Marker"}, "invalid-iam-policy-list")
    _no_pagination(attached_list, "truncated-iam-policy-list")
    policy = _response(raw, f"iam-{role_class}-inline")
    _keys(
        policy,
        {"RoleName", "PolicyName", "PolicyDocument"},
        set(),
        "invalid-iam-policy-response",
    )
    policy_names = _list(inline_list["PolicyNames"], "invalid-iam-policy-list")
    if (
        policy_names != [role["RoleName"]]
        or policy["PolicyName"] != role["RoleName"]
        or policy["RoleName"] != role["RoleName"]
    ):
        _fail("iam-policy-inventory-mismatch")
    return {
        "name": role["RoleName"],
        "arn": role["Arn"],
        "path": role["Path"],
        "max_session_duration": max_session_duration,
        "trust_policy": role["AssumeRolePolicyDocument"],
        "inline_policies": {policy["PolicyName"]: policy["PolicyDocument"]},
        "attached_policies": attached_list["AttachedPolicies"],
        "permissions_boundary": None,
    }


def build_policy_envelope(
    raw: Mapping[str, Mapping[str, Any]],
    manifest: Mapping[str, Any],
    bindings: Mapping[str, Any],
) -> dict[str, Any]:
    key_doc = _response(raw, "kms-key")
    _keys(key_doc, {"KeyMetadata"}, set(), "invalid-kms-response")
    key = _object(key_doc["KeyMetadata"], "invalid-kms-response")
    required = {
        "AWSAccountId",
        "KeyId",
        "Arn",
        "CreationDate",
        "Enabled",
        "Description",
        "KeyUsage",
        "KeyState",
        "Origin",
        "KeyManager",
        "CustomerMasterKeySpec",
        "KeySpec",
        "EncryptionAlgorithms",
        "MultiRegion",
    }
    optional = {
        "DeletionDate",
        "ValidTo",
        "SigningAlgorithms",
        "MultiRegionConfiguration",
        "PendingDeletionWindowInDays",
        "MacAlgorithms",
        "XksKeyConfiguration",
    }
    _keys(key, required, optional, "invalid-kms-response")
    alias_doc = _response(raw, "kms-alias")
    _keys(alias_doc, {"KeyMetadata"}, set(), "invalid-kms-response")
    alias_key = _object(alias_doc["KeyMetadata"], "invalid-kms-response")
    _keys(alias_key, required, optional, "invalid-kms-response")
    if _canonical_sha256(alias_key) != _canonical_sha256(key):
        _fail("kms-alias-target-mismatch")
    if set(key) != required:
        _fail("invalid-kms-response")
    if (
        key["AWSAccountId"] != "817685572750"
        or key["KeyUsage"] != "ENCRYPT_DECRYPT"
        or key["KeyState"] != "Enabled"
        or key["Origin"] != "AWS_KMS"
        or key["KeyManager"] != "CUSTOMER"
        or key["CustomerMasterKeySpec"] != "SYMMETRIC_DEFAULT"
        or key["KeySpec"] != "SYMMETRIC_DEFAULT"
        or key["CustomerMasterKeySpec"] != key["KeySpec"]
        or _list(key["EncryptionAlgorithms"], "invalid-kms-response") != ["SYMMETRIC_DEFAULT"]
        or not isinstance(key["Description"], str)
    ):
        _fail("invalid-kms-response")
    _aware_timestamp(key["CreationDate"], "invalid-kms-response")
    rotation = _response(raw, "kms-rotation")
    _keys(
        rotation,
        {"KeyRotationEnabled"},
        {"RotationPeriodInDays", "NextRotationDate", "OnDemandRotationStartDate"},
        "invalid-kms-response",
    )
    if "RotationPeriodInDays" in rotation:
        rotation_period = _nonnegative_int(rotation["RotationPeriodInDays"], "invalid-kms-response")
        if rotation_period < 1:
            _fail("invalid-kms-response")
    for timestamp_field in ("NextRotationDate", "OnDemandRotationStartDate"):
        if timestamp_field in rotation:
            _aware_timestamp(rotation[timestamp_field], "invalid-kms-response")
    policy = _response(raw, "kms-policy")
    _keys(policy, {"Policy"}, {"PolicyName"}, "invalid-kms-response")
    if "PolicyName" in policy and policy["PolicyName"] != "default":
        _fail("invalid-kms-response")
    grants = _response(raw, "kms-grants")
    _keys(grants, {"Grants"}, {"Truncated", "NextMarker", "NextToken"}, "invalid-kms-response")
    _no_pagination(grants, "truncated-kms-grants")
    payload = {
        "roles": {
            role_class: _role_payload(raw, role_class) for role_class in ("publisher", "deployer")
        },
        "kms": {
            "arn": key["Arn"],
            "key_id": key["KeyId"],
            "alias_name": "alias/website-sandbox-runtime",
            "alias_target_key_id": alias_key.get("KeyId"),
            "enabled": _boolean(key["Enabled"], "invalid-kms-response"),
            "key_state": key["KeyState"],
            "key_manager": key["KeyManager"],
            "origin": key["Origin"],
            "key_usage": key["KeyUsage"],
            "spec": key["KeySpec"],
            "multi_region": _boolean(key["MultiRegion"], "invalid-kms-response"),
            "rotation_enabled": _boolean(rotation["KeyRotationEnabled"], "invalid-kms-response"),
            "policy_name": "default",
            "key_policy": policy["Policy"],
            "grant_inventory": grants["Grants"],
            "grant_inventory_truncated": False,
        },
    }
    return _envelope(
        "policies",
        payload,
        str(bindings["capture_id"]),
        manifest["source_binding"],
    )


def _secret_payload(raw: Mapping[str, Mapping[str, Any]], key: str) -> dict[str, Any]:
    metadata = _response(raw, f"secret-{key}-metadata")
    expected = {
        "ARN",
        "Name",
        "Description",
        "KmsKeyId",
        "RotationEnabled",
        "OwningService",
        "PrimaryRegion",
        "DeletedDate",
        "VersionIdsToStages",
    }
    if set(metadata) != expected:
        _fail("invalid-secret-metadata")
    policy = _response(raw, f"secret-{key}-policy")
    if (
        set(policy) != {"ARN", "Name", "ResourcePolicyPresent"}
        or policy["ResourcePolicyPresent"] is not False
    ):
        _fail("secret-policy-present-or-ambiguous")
    return {
        "arn": metadata["ARN"],
        "name": metadata["Name"],
        "description": metadata["Description"],
        "kms_key_id": metadata["KmsKeyId"],
        "rotation_enabled": _boolean(metadata["RotationEnabled"], "invalid-secret-metadata"),
        "owning_service": metadata["OwningService"],
        "primary_region": metadata["PrimaryRegion"],
        "deleted_date": metadata["DeletedDate"],
        "version_ids_to_stages": metadata["VersionIdsToStages"],
        "resource_policy_response": {"ARN": policy["ARN"], "Name": policy["Name"]},
    }


def _normalized_route53_record(response: Mapping[str, Any]) -> dict[str, Any]:
    _keys(
        response,
        {"ResourceRecordSets"},
        {"NextToken"},
        "invalid-route53-response",
    )
    if "NextToken" in response and (
        not isinstance(response["NextToken"], str) or not response["NextToken"]
    ):
        _fail("invalid-route53-response")
    record = _one(response["ResourceRecordSets"], "invalid-route53-response")
    string_values: tuple[Any, ...]
    if "AliasTarget" in record:
        _keys(
            record,
            {"Name", "Type", "AliasTarget"},
            set(),
            "invalid-route53-response",
        )
        alias = _object(record["AliasTarget"], "invalid-route53-response")
        _keys(
            alias,
            {"DNSName", "HostedZoneId", "EvaluateTargetHealth"},
            set(),
            "invalid-route53-response",
        )
        if alias["EvaluateTargetHealth"] is not False:
            _fail("invalid-route53-response")
        string_values = (record["Name"], record["Type"], alias["DNSName"], alias["HostedZoneId"])
    else:
        _keys(
            record,
            {"Name", "Type", "TTL", "ResourceRecords"},
            set(),
            "invalid-route53-response",
        )
        if _nonnegative_int(record["TTL"], "invalid-route53-response") != 300:
            _fail("invalid-route53-response")
        resource = _one(record.get("ResourceRecords"), "invalid-route53-response")
        if set(resource) != {"Value"}:
            _fail("invalid-route53-response")
        string_values = (record["Name"], record["Type"], resource["Value"])
    if not all(isinstance(item, str) for item in string_values):
        _fail("invalid-route53-response")
    return dict(record)


def _route53_record(response: Mapping[str, Any]) -> dict[str, str]:
    record = _normalized_route53_record(response)
    value = (
        record["AliasTarget"]["DNSName"]
        if "AliasTarget" in record
        else record["ResourceRecords"][0]["Value"]
    )
    return {"name": record["Name"], "type": record["Type"], "value": value}


def _validated_route53_records(
    responses: Sequence[Mapping[str, Any]], seed: Mapping[str, Any]
) -> list[dict[str, str]]:
    normalized = [_normalized_route53_record(response) for response in responses]
    if normalized != seed["normalized_dns_full_records"]:
        _fail("route53-full-record-binding-mismatch")
    compact = [_route53_record(response) for response in responses]
    if compact != seed["dns_records"]:
        _fail("route53-binding-mismatch")
    return compact


def _task_definition(response: Mapping[str, Any]) -> dict[str, Any]:
    _keys(response, {"taskDefinition"}, set(), "invalid-task-definition-response")
    task = _object(response["taskDefinition"], "invalid-task-definition-response")
    required = {
        "taskDefinitionArn",
        "family",
        "revision",
        "status",
        "taskRoleArn",
        "executionRoleArn",
    }
    _keys(task, required, set(), "invalid-task-definition-response")
    revision = _nonnegative_int(task["revision"], "invalid-task-definition-response")
    if revision < 1:
        _fail("invalid-task-definition-response")
    return {
        "arn": task["taskDefinitionArn"],
        "family": task["family"],
        "revision": revision,
        "status": task["status"],
        "task_role_arn": task["taskRoleArn"],
        "execution_role_arn": task["executionRoleArn"],
    }


def build_resource_envelope(
    raw: Mapping[str, Mapping[str, Any]],
    seed: Mapping[str, Any],
    manifest: Mapping[str, Any],
    bindings: Mapping[str, Any],
) -> dict[str, Any]:
    binding_payload = _object(bindings["payload"], "invalid-bindings-envelope")
    if _response(raw, "s3-bucket") != {}:
        _fail("invalid-s3-response")
    location = _response(raw, "s3-location")
    _keys(location, {"LocationConstraint"}, set(), "invalid-s3-response")
    location_constraint = location["LocationConstraint"]
    if location_constraint is None:
        bucket_location = "us-east-1"
    elif location_constraint == "us-east-1" and isinstance(location_constraint, str):
        bucket_location = location_constraint
    else:
        _fail("invalid-s3-response")
    ownership = _response(raw, "s3-ownership")
    _keys(ownership, {"OwnershipControls"}, set(), "invalid-s3-response")
    controls = _object(ownership.get("OwnershipControls"), "invalid-s3-response")
    _keys(controls, {"Rules"}, set(), "invalid-s3-response")
    rule = _one(controls.get("Rules"), "invalid-s3-response")
    _keys(rule, {"ObjectOwnership"}, set(), "invalid-s3-response")
    encryption = _response(raw, "s3-encryption")
    _keys(
        encryption,
        {"ServerSideEncryptionConfiguration"},
        set(),
        "invalid-s3-response",
    )
    enc_config = _object(encryption.get("ServerSideEncryptionConfiguration"), "invalid-s3-response")
    enc_rule = _one(enc_config.get("Rules"), "invalid-s3-response")
    _keys(
        enc_rule,
        {"ApplyServerSideEncryptionByDefault"},
        {"BucketKeyEnabled"},
        "invalid-s3-response",
    )
    if "BucketKeyEnabled" in enc_rule and enc_rule["BucketKeyEnabled"] is not False:
        _fail("invalid-s3-response")
    default_encryption = _object(
        enc_rule.get("ApplyServerSideEncryptionByDefault"), "invalid-s3-response"
    )
    _keys(
        default_encryption,
        {"SSEAlgorithm"},
        {"KMSMasterKeyID"},
        "invalid-s3-response",
    )
    if default_encryption["SSEAlgorithm"] != "AES256" or "KMSMasterKeyID" in default_encryption:
        _fail("invalid-s3-response")
    versioning = _response(raw, "s3-versioning")
    _keys(versioning, {"Status"}, {"MFADelete"}, "invalid-s3-response")
    if "MFADelete" in versioning and versioning["MFADelete"] not in {
        "Enabled",
        "Disabled",
    }:
        _fail("invalid-s3-response")
    public = _response(raw, "s3-public-access")
    _keys(public, {"PublicAccessBlockConfiguration"}, set(), "invalid-s3-response")
    public_access = _object(public["PublicAccessBlockConfiguration"], "invalid-s3-response")
    public_access_fields = {
        "BlockPublicAcls",
        "IgnorePublicAcls",
        "BlockPublicPolicy",
        "RestrictPublicBuckets",
    }
    _keys(public_access, public_access_fields, set(), "invalid-s3-response")
    for field in public_access_fields:
        _boolean(public_access[field], "invalid-s3-response")
    state_metadata = _response(raw, "s3-state-object")
    if set(state_metadata) != {
        "AcceptRanges",
        "LastModified",
        "ContentLength",
        "ETag",
        "VersionId",
        "ServerSideEncryption",
    }:
        _fail("missing-state-object")
    if state_metadata["AcceptRanges"] != "bytes":
        _fail("invalid-state-object")
    _aware_timestamp(state_metadata["LastModified"], "invalid-state-object")
    content_length = _nonnegative_int(state_metadata["ContentLength"], "invalid-state-object")
    if content_length < 1:
        _fail("invalid-state-object")
    etag = _nonempty_string(state_metadata["ETag"], "invalid-state-object")
    if not re.fullmatch(r'"[^"\r\n]+"', etag):
        _fail("invalid-state-object")
    _nonempty_string(state_metadata["VersionId"], "invalid-state-object")
    if state_metadata["ServerSideEncryption"] != "AES256":
        _fail("invalid-state-object")

    repository_doc = _response(raw, "ecr-repository")
    _keys(repository_doc, {"repositories", "nextToken"}, set(), "invalid-ecr-response")
    if repository_doc["nextToken"] is not None:
        _fail("truncated-ecr-response")
    repository = _one(repository_doc["repositories"], "invalid-ecr-response")
    required_repo = {
        "repositoryArn",
        "registryId",
        "repositoryName",
        "repositoryUri",
        "createdAt",
        "imageTagMutability",
        "imageScanningConfiguration",
        "encryptionConfiguration",
    }
    _keys(repository, required_repo, set(), "invalid-ecr-response")
    if (
        repository["repositoryUri"]
        != seed["github_repository_variables"]["SANDBOX_ECR_REPOSITORY_URI"]
    ):
        _fail("ecr-repository-binding-mismatch")
    _aware_timestamp(repository["createdAt"], "invalid-ecr-response")
    images = _response(raw, "ecr-images")
    image_count = _projected_count(
        images,
        count_field="ImageCount",
        type_field="ImageDetailsType",
        token_field="NextToken",
        code="invalid-ecr-response",
        truncated_code="truncated-ecr-response",
    )
    encryption_config = _object(repository["encryptionConfiguration"], "invalid-ecr-response")
    scanning_config = _object(repository["imageScanningConfiguration"], "invalid-ecr-response")
    _keys(
        encryption_config,
        {"encryptionType", "kmsKey"},
        set(),
        "invalid-ecr-response",
    )
    if encryption_config["encryptionType"] != "KMS":
        _fail("invalid-ecr-response")
    _keys(scanning_config, {"scanOnPush"}, set(), "invalid-ecr-response")
    scan_on_push = _boolean(scanning_config["scanOnPush"], "invalid-ecr-response")

    cloudfront = _response(raw, "cloudfront-distribution")
    if set(cloudfront) != {"Id", "ARN", "Status", "DomainName", "Enabled", "Aliases"}:
        _fail("invalid-cloudfront-response")
    aliases = _object(cloudfront["Aliases"], "invalid-cloudfront-response")
    _keys(aliases, {"Quantity"}, {"Items"}, "invalid-cloudfront-response")
    alias_items = (
        _list(aliases["Items"], "invalid-cloudfront-response") if "Items" in aliases else []
    )
    alias_quantity = _nonnegative_int(aliases["Quantity"], "invalid-cloudfront-response")
    if alias_quantity != len(alias_items):
        _fail("invalid-cloudfront-response")
    cloudfront_enabled = _boolean(cloudfront["Enabled"], "invalid-cloudfront-response")

    cluster_doc = _response(raw, "ecs-cluster")
    _keys(cluster_doc, {"clusters", "failures"}, set(), "invalid-ecs-response")
    if cluster_doc["failures"] != []:
        _fail("ecs-readback-failure")
    cluster = _one(cluster_doc["clusters"], "invalid-ecs-response")
    required_cluster = {
        "clusterArn",
        "clusterName",
        "status",
        "registeredContainerInstancesCount",
        "runningTasksCount",
        "pendingTasksCount",
        "activeServicesCount",
    }
    _keys(cluster, required_cluster, set(), "invalid-ecs-response")
    cluster_counts = {
        field: _nonnegative_int(cluster[field], "invalid-ecs-response")
        for field in (
            "registeredContainerInstancesCount",
            "runningTasksCount",
            "pendingTasksCount",
            "activeServicesCount",
        )
    }
    services_doc = _response(raw, "ecs-services")
    _keys(services_doc, {"services", "failures"}, set(), "invalid-ecs-response")
    if services_doc["failures"] != []:
        _fail("ecs-readback-failure")
    services = _list(services_doc["services"], "invalid-ecs-response")
    if len(services) != 2:
        _fail("invalid-ecs-response")
    mapped_services: dict[str, Any] = {}
    for value in services:
        service = _object(value, "invalid-ecs-response")
        required_service = {
            "serviceArn",
            "serviceName",
            "status",
            "desiredCount",
            "runningCount",
            "pendingCount",
            "taskDefinition",
        }
        _keys(service, required_service, set(), "invalid-ecs-response")
        service_counts = {
            field: _nonnegative_int(service[field], "invalid-ecs-response")
            for field in ("desiredCount", "runningCount", "pendingCount")
        }
        name = str(service["serviceName"]).removeprefix("website-sandbox-")
        if name not in {"web", "worker"} or name in mapped_services:
            _fail("invalid-ecs-response")
        mapped_services[name] = {
            "arn": service["serviceArn"],
            "name": service["serviceName"],
            "status": service["status"],
            "desired": service_counts["desiredCount"],
            "running": service_counts["runningCount"],
            "pending": service_counts["pendingCount"],
            "task_definition": service["taskDefinition"],
        }
    task_counts: dict[str, int] = {}
    for state in ("running", "pending", "stopped"):
        task_list = _response(raw, f"ecs-{state}-tasks")
        task_counts[state] = _projected_count(
            task_list,
            count_field="TaskCount",
            type_field="TaskArnsType",
            token_field="NextToken",
            code="invalid-ecs-task-list",
            truncated_code="truncated-ecs-task-list",
        )
    definitions = {
        name: _task_definition(_response(raw, f"ecs-{name}-task-definition"))
        for name in ("web", "worker", "migration")
    }

    target_doc = _response(raw, "target-group")
    _keys(
        target_doc,
        {"TargetGroups", "NextMarker"},
        set(),
        "invalid-target-group-response",
    )
    if target_doc["NextMarker"] is not None:
        _fail("truncated-target-group-response")
    target = _one(target_doc["TargetGroups"], "invalid-target-group-response")
    target_required = {
        "TargetGroupArn",
        "TargetGroupName",
        "Protocol",
        "Port",
        "VpcId",
        "TargetType",
        "HealthCheckPath",
    }
    _keys(target, target_required, set(), "invalid-target-group-response")
    target_port = _nonnegative_int(target["Port"], "invalid-target-group-response")
    if target_port < 1:
        _fail("invalid-target-group-response")
    health = _response(raw, "target-health")
    target_count = _projected_count(
        health,
        count_field="TargetCount",
        type_field="TargetHealthDescriptionsType",
        code="invalid-target-health-response",
    )

    database_doc = _response(raw, "rds-database")
    _keys(database_doc, {"DBInstances", "Marker"}, set(), "invalid-rds-response")
    if database_doc["Marker"] is not None:
        _fail("truncated-rds-response")
    database = _one(database_doc["DBInstances"], "invalid-rds-response")
    db_required = {
        "DBInstanceIdentifier",
        "DBInstanceArn",
        "DBInstanceStatus",
        "StorageEncrypted",
        "KmsKeyId",
        "PubliclyAccessible",
    }
    _keys(database, db_required, set(), "invalid-rds-response")
    database_encrypted = _boolean(database["StorageEncrypted"], "invalid-rds-response")
    database_public = _boolean(database["PubliclyAccessible"], "invalid-rds-response")

    dns_command_ids = [
        "route53-certificate-origin",
        "route53-certificate-web",
        "route53-origin-a",
        "route53-origin-aaaa",
        "route53-web-a",
        "route53-web-aaaa",
    ]
    route53_responses = [_response(raw, item) for item in dns_command_ids]
    dns_records = _validated_route53_records(route53_responses, seed)

    repository_variables: dict[str, str] = {}
    for name in manifest["static"]["repository_variable_names"]:
        value = _response(raw, f"github-repository-{name}")
        if (
            set(value) != {"name", "value"}
            or value["name"] != name
            or not isinstance(value["value"], str)
        ):
            _fail("github-variable-mismatch")
        repository_variables[name] = value["value"]
    environment_variables: dict[str, str] = {}
    for name in manifest["static"]["environment_variable_names"]:
        value = _response(raw, f"github-environment-{name}")
        if (
            set(value) != {"name", "value"}
            or value["name"] != name
            or not isinstance(value["value"], str)
        ):
            _fail("github-variable-mismatch")
        environment_variables[name] = value["value"]
    branch_doc = _response(raw, "github-sandbox-branch-policy")
    _keys(branch_doc, {"total_count", "branch_policies"}, set(), "invalid-github-branch-policy")
    branch_count = _nonnegative_int(branch_doc["total_count"], "invalid-github-branch-policy")
    branches = _list(branch_doc["branch_policies"], "invalid-github-branch-policy")
    if branch_count != 1 or len(branches) != 1:
        _fail("invalid-github-branch-policy")
    branch = _object(branches[0], "invalid-github-branch-policy")
    if (
        set(branch) != {"id", "node_id", "name", "type"}
        or _nonnegative_int(branch["id"], "invalid-github-branch-policy") < 1
        or not _nonempty_string(branch["node_id"], "invalid-github-branch-policy")
        or branch["name"] != "main"
        or branch["type"] != "branch"
    ):
        _fail("invalid-github-branch-policy")

    secrets = {key: _secret_payload(raw, key) for key in manifest["static"]["secret_names"]}
    payload = {
        "s3": {
            "bucket": manifest["static"]["state"]["bucket"],
            "bucket_location": bucket_location,
            "key": manifest["static"]["state"]["key"],
            "expected_bucket_owner": "817685572750",
            "object_exists": True,
            "ownership": rule.get("ObjectOwnership"),
            "encryption_algorithm": default_encryption.get("SSEAlgorithm"),
            "versioning": versioning["Status"],
            "public_access_block": public_access,
            "bucket_policy_error": raw["s3-policy"]["error"]["code"],
        },
        "ecr": {
            "name": repository["repositoryName"],
            "arn": repository["repositoryArn"],
            "registry_id": repository["registryId"],
            "image_tag_mutability": repository["imageTagMutability"],
            "scan_on_push": scan_on_push,
            "kms_key_arn": encryption_config.get("kmsKey"),
            "zero_digest_present": False,
            "zero_digest_error": raw["ecr-zero-digest"]["error"]["code"],
            "repository_policy_error": raw["ecr-repository-policy"]["error"]["code"],
            "registry_policy_error": raw["ecr-registry-policy"]["error"]["code"],
        },
        "secrets": secrets,
        "cloudfront": {
            "distribution_id": cloudfront["Id"],
            "arn": cloudfront["ARN"],
            "domain_name": cloudfront["DomainName"],
            "enabled": cloudfront_enabled,
            "status": cloudfront["Status"],
            "aliases": alias_items,
            "route53_web_targets": {
                "A": binding_payload["cloudfront"]["domain_name"],
                "AAAA": binding_payload["cloudfront"]["domain_name"],
            },
        },
        "runtime": {
            "ecs_cluster": {
                "active_services": cluster_counts["activeServicesCount"],
                "arn": cluster["clusterArn"],
                "name": cluster["clusterName"],
                "pending_tasks": cluster_counts["pendingTasksCount"],
                "registered_container_instances": cluster_counts[
                    "registeredContainerInstancesCount"
                ],
                "running_tasks": cluster_counts["runningTasksCount"],
                "status": cluster["status"],
            },
            "ecs_services": mapped_services,
            "task_definitions": definitions,
            "running_tasks": task_counts["running"],
            "pending_tasks": task_counts["pending"],
            "stopped_tasks": task_counts["stopped"],
            "ecr_image_count": image_count,
            "target_count": target_count,
            "target_group": {
                "arn": target["TargetGroupArn"],
                "name": target["TargetGroupName"],
                "protocol": target["Protocol"],
                "port": target_port,
                "vpc_id": target["VpcId"],
                "target_type": target["TargetType"],
                "health_check_path": target["HealthCheckPath"],
            },
            "database": {
                "identifier": database["DBInstanceIdentifier"],
                "arn": database["DBInstanceArn"],
                "status": database["DBInstanceStatus"],
                "encrypted": database_encrypted,
                "kms_key_arn": database["KmsKeyId"],
                "publicly_accessible": database_public,
            },
        },
        "caller_identity": binding_payload["operator_identity"],
        "dns_records": dns_records,
        "route53": {
            "record_count": len(dns_records),
            "records_sha256": _canonical_sha256(dns_records),
            "zone_id": manifest["static"]["route53"]["zone_id"],
        },
        "github": {
            "repository_variables": repository_variables,
            "environment_variables": environment_variables,
            "branch_policy": ["main"],
        },
        "terraform": {
            "address_count": seed["terraform_address_count"],
            "locked": False,
            "state_metadata_sha256": _canonical_sha256(state_metadata),
        },
    }
    return _envelope("resources", payload, str(bindings["capture_id"]), manifest["source_binding"])


def build_simulator_envelope(
    raw: Mapping[str, Mapping[str, Any]],
    contract: Mapping[str, Any],
    manifest: Mapping[str, Any],
    bindings: Mapping[str, Any],
) -> dict[str, Any]:
    results: list[dict[str, Any]] = []
    expected_decisions = {row["id"]: row["expected"] for row in manifest["simulator_rows"]}
    for spec in resolve_simulator_plan(contract, manifest, bindings):
        response = _response(raw, str(spec["id"]))
        _keys(
            response,
            {"EvaluationResults"},
            {"IsTruncated", "Marker", "NextToken"},
            "invalid-simulator-provider-response",
        )
        if (
            ("IsTruncated" in response and response["IsTruncated"] is not False)
            or response.get("Marker") is not None
            or response.get("NextToken") is not None
        ):
            _fail("truncated-simulator-provider-response")
        evaluation = _one(response["EvaluationResults"], "invalid-simulator-provider-response")
        required = {
            "EvalActionName",
            "EvalResourceName",
            "EvalDecision",
            "MissingContextValues",
        }
        optional = {
            "MatchedStatements",
            "OrganizationsDecisionDetail",
            "PermissionsBoundaryDecisionDetail",
            "EvalDecisionDetails",
            "ResourceSpecificResults",
        }
        _keys(evaluation, required, optional, "invalid-simulator-provider-response")
        if (
            evaluation["EvalActionName"] != spec["request"]["action_names"][0]
            or evaluation["EvalResourceName"] != spec["request"]["resource_arns"][0]
            or evaluation["EvalDecision"] != expected_decisions[spec["row_id"]]
            or evaluation["MissingContextValues"] != []
        ):
            _fail("simulator-echo-mismatch")
        results.append(
            {
                "row_id": spec["row_id"],
                "request": spec["request"],
                "is_truncated": False,
                "EvaluationResults": [{key: evaluation[key] for key in sorted(required)}],
            }
        )
    return _envelope(
        "simulator",
        {"results": results},
        str(bindings["capture_id"]),
        manifest["source_binding"],
    )


def raw_capture_sha256(
    raw: Mapping[str, Mapping[str, Any]], specs: Sequence[Mapping[str, Any]]
) -> str:
    inventory = [
        {
            "command_id": spec["id"],
            "stdout_sha256": raw[str(spec["id"])]["stdout_sha256"],
            "stderr_sha256": raw[str(spec["id"])]["stderr_sha256"],
            "status_sha256": _canonical_sha256(raw[str(spec["id"])]["status"]),
        }
        for spec in specs
    ]
    return _canonical_sha256(inventory)


def build_execution_attestation(
    seed: Mapping[str, Any],
    contract: Mapping[str, Any],
    specs: Sequence[Mapping[str, Any]],
    raw: Mapping[str, Mapping[str, Any]],
    bindings: Mapping[str, Any],
    policies: Mapping[str, Any],
    resources: Mapping[str, Any],
    simulator: Mapping[str, Any],
    results: Sequence[Mapping[str, Any]],
    summary: Mapping[str, Any],
) -> dict[str, Any]:
    identity = _object(bindings["payload"]["operator_identity"], "invalid-bindings-envelope")
    sts = _response(raw, "sts-caller")
    if identity != {
        "account_id": sts.get("Account"),
        "arn": sts.get("Arn"),
        "user_id": sts.get("UserId"),
    }:
        _fail("attestation-sts-mismatch")
    by_kind = {str(item["kind"]): item for item in results}
    if set(by_kind) != {"bindings", "policies", "resources", "simulator"}:
        _fail("invalid-attestation-results")
    return {
        "schema_version": 1,
        "attestation_id": "website-sandbox-gate-b-execution-attestation-v1",
        "capture_id": bindings["capture_id"],
        "status": "PASS",
        "source_binding": contract["source_binding"],
        "accepted_execution_binding": contract["accepted_execution_binding"],
        "operator_parent_role_arn": seed["operator_parent"]["role_arn"],
        "operator_identity": identity,
        "seed_sha256": _canonical_sha256(seed),
        "execution_contract_sha256": _canonical_sha256(contract),
        "manifest_sha256": contract["evidence"]["manifest_canonical_sha256"],
        "execution_graph_sha256": execution_graph_sha256(specs),
        "raw_capture_sha256": raw_capture_sha256(raw, specs),
        "provider_operation_count": len(specs),
        "aws_operation_count": sum(item["provider"] == "aws" for item in specs),
        "github_operation_count": sum(item["provider"] == "github" for item in specs),
        "envelope_sha256": {
            "bindings": _canonical_sha256(bindings),
            "policies": _canonical_sha256(policies),
            "resources": _canonical_sha256(resources),
            "simulator": _canonical_sha256(simulator),
        },
        "result_sha256": {kind: _canonical_sha256(by_kind[kind]) for kind in sorted(by_kind)},
        "summary_sha256": _canonical_sha256(summary),
        "sts_triplet_sha256": {
            "stdout": raw["sts-caller"]["stdout_sha256"],
            "stderr": raw["sts-caller"]["stderr_sha256"],
            "status": _canonical_sha256(raw["sts-caller"]["status"]),
        },
    }


def validate_complete_chain(
    seed: Mapping[str, Any],
    contract: Mapping[str, Any],
    manifest: Mapping[str, Any],
    raw: Mapping[str, Mapping[str, Any]],
    bindings: Mapping[str, Any],
) -> dict[str, Any]:
    try:
        binding_result = evidence.validate_bindings(bindings, manifest)
        policies = build_policy_envelope(raw, manifest, bindings)
        policy_result = evidence.validate_policy_bundle(policies, manifest, binding_result)
        resources = build_resource_envelope(raw, seed, manifest, bindings)
        resource_result = evidence.validate_resource_bundle(
            resources, manifest, bindings, binding_result
        )
        simulator = build_simulator_envelope(raw, contract, manifest, bindings)
        simulator_result = evidence.validate_simulator_bundle(
            simulator, manifest, bindings, binding_result
        )
        results = [binding_result, policy_result, resource_result, simulator_result]
        summary = evidence.build_final_summary(manifest, results)
    except evidence.EvidenceError as exc:
        raise AssemblyError("gate-b-evidence-stop") from exc
    specs = complete_operation_specs(contract, manifest, bindings)
    attestation = build_execution_attestation(
        seed,
        contract,
        specs,
        raw,
        bindings,
        policies,
        resources,
        simulator,
        results,
        summary,
    )
    return {
        "bindings": bindings,
        "bindings.result": binding_result,
        "policies": policies,
        "policies.result": policy_result,
        "resources": resources,
        "resources.result": resource_result,
        "simulator": simulator,
        "simulator.result": simulator_result,
        "summary": summary,
        "execution-attestation": attestation,
    }


def validate_sealed_binding_outputs(
    capture_dir: Path, documents: Mapping[str, Mapping[str, Any]]
) -> None:
    if (
        _read_private_json(capture_dir / "bindings.json") != documents["bindings"]
        or _read_private_json(capture_dir / "bindings.result.json") != documents["bindings.result"]
    ):
        _fail("sealed-binding-output-mismatch")


def _write_outputs(capture_dir: Path, documents: Mapping[str, Mapping[str, Any]]) -> None:
    directory = _safe_capture_directory(capture_dir)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        directory_descriptor = _open_private_directory(directory)
    except OSError as exc:
        raise AssemblyError("unsafe-assembly-output") from exc
    try:
        for name, document in documents.items():
            data = evidence.canonical_json_bytes(document) + b"\n"
            try:
                descriptor = os.open(f"{name}.json", flags, 0o600, dir_fd=directory_descriptor)
                with os.fdopen(descriptor, "wb") as stream:
                    stream.write(data)
                info = os.stat(
                    f"{name}.json",
                    dir_fd=directory_descriptor,
                    follow_symlinks=False,
                )
            except OSError as exc:
                raise AssemblyError("unsafe-assembly-output") from exc
            if (
                not stat.S_ISREG(info.st_mode)
                or stat.S_IMODE(info.st_mode) != 0o600
                or info.st_uid != os.geteuid()
                or info.st_nlink != 1
            ):
                _fail("unsafe-assembly-output")
    finally:
        os.close(directory_descriptor)


class _SafeArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> NoReturn:
        del message
        _fail("invalid-cli-arguments")


def _parser() -> argparse.ArgumentParser:
    parser = _SafeArgumentParser(description="Assemble one frozen Gate B capture offline")
    subparsers = parser.add_subparsers(dest="mode", required=True)
    validate = subparsers.add_parser("validate-contract")
    validate.add_argument("--seed", default=str(SEED_PATH))
    validate.add_argument("--contract", default=str(CONTRACT_PATH))
    assemble = subparsers.add_parser("assemble")
    assemble.add_argument("--seed", default=str(SEED_PATH))
    assemble.add_argument("--contract", default=str(CONTRACT_PATH))
    assemble.add_argument("--capture-dir", required=True)
    return parser


def _require_tracked_path(value: str, tracked: Path, code: str) -> Path:
    path = Path(value)
    try:
        if path.resolve(strict=True) != tracked.resolve(strict=True):
            _fail(code)
    except OSError as exc:
        raise AssemblyError(code) from exc
    return tracked


def main(argv: Sequence[str] | None = None) -> int:
    try:
        args = _parser().parse_args(argv)
        seed_path = _require_tracked_path(args.seed, SEED_PATH, "untracked-seed-path")
        contract_path = _require_tracked_path(
            args.contract, CONTRACT_PATH, "untracked-contract-path"
        )
        seed = load_seed(seed_path)
        contract = load_execution_contract(contract_path)
        manifest = load_manifest()
        result = validate_execution_contract(contract, seed, manifest)
        if args.mode == "validate-contract":
            sys.stdout.buffer.write(evidence.canonical_json_bytes(result) + b"\n")
            return 0
        capture_dir = _safe_capture_directory(Path(args.capture_dir))
        bindings_path = capture_dir / "bindings.json"
        bindings = _read_private_json(bindings_path)
        specs = complete_operation_specs(contract, manifest, bindings)
        raw = load_raw_capture_set(capture_dir, specs)
        expected_bindings = build_bindings_envelope(
            seed,
            manifest,
            capture_dir.name.removeprefix("gate-b-"),
            _response(raw, "sts-caller"),
        )
        if bindings != expected_bindings:
            _fail("sealed-binding-mismatch")
        documents = validate_complete_chain(seed, contract, manifest, raw, bindings)
        validate_sealed_binding_outputs(capture_dir, documents)
        _write_outputs(
            capture_dir,
            {
                key: value
                for key, value in documents.items()
                if key not in {"bindings", "bindings.result"}
            },
        )
        sys.stdout.buffer.write(
            evidence.canonical_json_bytes(
                {
                    "capture_id": bindings["capture_id"],
                    "status": "PASS",
                    "summary_sha256": _canonical_sha256(documents["summary"]),
                    "attestation_sha256": _canonical_sha256(documents["execution-attestation"]),
                }
            )
            + b"\n"
        )
        return 0
    except (AssemblyError, evidence.EvidenceError, KeyboardInterrupt):
        print("gate-b-assembly-stop", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
