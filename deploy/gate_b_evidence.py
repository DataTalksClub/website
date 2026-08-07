from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import stat
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, NoReturn
from urllib.parse import unquote

PROJECT_ROOT = Path(__file__).resolve().parents[1]
TMP_ROOT = PROJECT_ROOT / ".tmp"
MANIFEST_PATH = PROJECT_ROOT / "deploy/gate_b_manifest.json"
MAX_INPUT_BYTES = 4 * 1024 * 1024
SCHEMA_VERSION = 1
EXPECTED_SIMULATOR_MATRIX_SHA256 = (
    "838fa6daca8b0760350e13a60e5e42fa059cbf51f5526749098b5a6aeafd9ad1"
)
EXPECTED_MANIFEST_SHA256 = "633fd7c6febeda1698abf9fb690e003c6606001a22e1c6652b64c291c1459b60"
CAPTURE_ID_PATTERN = re.compile(r"^[0-9]{8}T[0-9]{6}Z-[0-9a-f]{12}$")
SHA_PATTERN = re.compile(r"^[0-9a-f]{40}$")
HEX_64_PATTERN = re.compile(r"^[0-9a-f]{64}$")
CF_ID_PATTERN = re.compile(r"^[A-Z0-9]{8,32}$")
IAM_CALLER_ARN_PATTERN = re.compile(
    r"^arn:aws:(?:iam::817685572750:(?:user|role)/[A-Za-z0-9+=,.@_/-]+|"
    r"sts::817685572750:assumed-role/[A-Za-z0-9+=,.@_/-]+/[A-Za-z0-9+=,.@_-]+)$"
)
KMS_PRINCIPAL_ARN_PATTERN = re.compile(r"^arn:aws:(?:iam|sts)::[0-9]{12}:.+$")
VPC_ID_PATTERN = re.compile(r"^vpc-[0-9a-f]{17}$")
SUBNET_ID_PATTERN = re.compile(r"^subnet-[0-9a-f]{17}$")
SECURITY_GROUP_ID_PATTERN = re.compile(r"^sg-[0-9a-f]{17}$")
TG_ARN_PATTERN = re.compile(
    r"^arn:aws:elasticloadbalancing:eu-west-1:817685572750:"
    r"targetgroup/website-sandbox-web/[0-9a-f]{16}$"
)
TASK_DEFINITION_PATTERN = re.compile(
    r"^arn:aws:ecs:eu-west-1:817685572750:"
    r"task-definition/website-sandbox-(web|worker|migration):([1-9][0-9]*)$"
)
SECRET_ARN_PATTERN = re.compile(
    r"^arn:aws:secretsmanager:eu-west-1:817685572750:"
    r"secret:(website-sandbox/[a-z0-9-]+)-([A-Za-z0-9]{6})$"
)
MALFORMED_PERCENT = re.compile(r"%(?![0-9A-Fa-f]{2})")
TEMPLATE_PATTERN = re.compile(r"^\$\{([a-z0-9_]+)\}$")

SET_ARRAY_KEYS = frozenset({"Action", "NotAction", "Resource", "NotResource"})
KMS_GRANT_OPERATIONS = frozenset(
    {
        "CreateGrant",
        "Decrypt",
        "DescribeKey",
        "Encrypt",
        "GenerateDataKey",
        "GenerateDataKeyPair",
        "GenerateDataKeyPairWithoutPlaintext",
        "GenerateDataKeyWithoutPlaintext",
        "GenerateMac",
        "GetPublicKey",
        "ReEncryptFrom",
        "ReEncryptTo",
        "RetireGrant",
        "Sign",
        "Verify",
        "VerifyMac",
    }
)
SENSITIVE_KEYS = frozenset(
    {
        "authorization",
        "accesstoken",
        "accesskeyid",
        "apikey",
        "clientsecret",
        "credential",
        "credentials",
        "idtoken",
        "privatekey",
        "refreshtoken",
        "secretaccesskey",
        "sessiontoken",
        "token",
        "webidentitytoken",
        "secretstring",
        "secretbinary",
        "password",
        "customheader",
        "customheaders",
        "originverificationheader",
        "origincustomheader",
        "origincustomheaders",
        "terraformstate",
    }
)


class EvidenceError(ValueError):
    """A fail-closed Gate B evidence contract violation with a safe error code."""

    def __init__(self, code: str) -> None:
        if not re.fullmatch(r"[a-z0-9-]+", code):
            code = "invalid-evidence"
        self.code = code
        super().__init__(code)


def _fail(code: str) -> NoReturn:
    raise EvidenceError(code)


def _reject_constant(_value: str) -> NoReturn:
    _fail("non-finite-json")


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            _fail("duplicate-json-key")
        result[key] = value
    return result


def parse_json(text: str) -> Any:
    if text.startswith("\ufeff"):
        _fail("json-bom")
    try:
        return json.loads(
            text,
            object_pairs_hook=_strict_object,
            parse_constant=_reject_constant,
        )
    except EvidenceError:
        raise
    except (json.JSONDecodeError, RecursionError, TypeError, UnicodeError) as exc:
        raise EvidenceError("invalid-json") from exc


def _expect_object(value: Any, code: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        _fail(code)
    return value


def _expect_list(value: Any, code: str) -> list[Any]:
    if not isinstance(value, list):
        _fail(code)
    return value


def _expect_keys(value: Mapping[str, Any], expected: set[str], code: str) -> None:
    if set(value) != expected:
        _fail(code)


def _assert_safe_data(value: Any) -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            normalized = re.sub(r"[^a-z0-9]", "", key.lower())
            if normalized in SENSITIVE_KEYS:
                _fail("sensitive-field")
            _assert_safe_data(child)
    elif isinstance(value, list):
        for child in value:
            _assert_safe_data(child)
    elif isinstance(value, float) and not math.isfinite(value):
        _fail("non-finite-json")


def _validate_tmp_parent(path: Path) -> Path:
    if not TMP_ROOT.exists() or not TMP_ROOT.is_dir() or TMP_ROOT.is_symlink():
        _fail("unsafe-tmp-root")
    if stat.S_IMODE(TMP_ROOT.stat().st_mode) != 0o700:
        _fail("unsafe-tmp-mode")

    absolute = path if path.is_absolute() else PROJECT_ROOT / path
    try:
        relative = absolute.relative_to(PROJECT_ROOT)
    except ValueError:
        _fail("path-outside-project")
    if not relative.parts or relative.parts[0] != ".tmp" or ".." in relative.parts:
        _fail("path-outside-tmp")

    current = PROJECT_ROOT
    for part in relative.parts[:-1]:
        current /= part
        if current.is_symlink():
            _fail("symlink-path")
    parent = absolute.parent
    if not parent.exists() or not parent.is_dir() or parent.is_symlink():
        _fail("unsafe-output-parent")
    if stat.S_IMODE(parent.stat().st_mode) != 0o700:
        _fail("unsafe-output-parent-mode")
    return absolute


def _read_tmp_json(path_value: str) -> dict[str, Any]:
    path = _validate_tmp_parent(Path(path_value))
    if not path.exists() or not path.is_file() or path.is_symlink():
        _fail("unsafe-input-file")
    if path.stat().st_size > MAX_INPUT_BYTES:
        _fail("input-too-large")
    if stat.S_IMODE(path.stat().st_mode) != 0o600:
        _fail("unsafe-input-mode")
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise EvidenceError("unreadable-input") from exc
    result = _expect_object(parse_json(text), "input-root-not-object")
    _assert_safe_data(result)
    return result


def write_safe_summary(path_value: str, value: Mapping[str, Any]) -> None:
    path = _validate_tmp_parent(Path(path_value))
    _assert_safe_data(value)
    data = canonical_json_bytes(value) + b"\n"
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags, 0o600)
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(data)
    except OSError as exc:
        raise EvidenceError("unsafe-output-write") from exc
    if stat.S_IMODE(path.stat().st_mode) != 0o600:
        _fail("unsafe-output-mode")


def _decode_policy_document(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if not isinstance(value, str) or not value:
        _fail("invalid-policy-type")

    decoded = value
    if "%" in decoded:
        if MALFORMED_PERCENT.search(decoded):
            _fail("malformed-policy-encoding")
        try:
            decoded = unquote(decoded, encoding="utf-8", errors="strict")
        except UnicodeDecodeError as exc:
            raise EvidenceError("malformed-policy-encoding") from exc
    parsed = parse_json(decoded)
    if isinstance(parsed, str):
        parsed = parse_json(parsed)
        if isinstance(parsed, str):
            _fail("double-encoded-policy")
    return _expect_object(parsed, "policy-root-not-object")


def _sort_set_array(values: Sequence[Any]) -> list[Any]:
    normalized = [_normalize_json(value) for value in values]
    return sorted(normalized, key=canonical_json_bytes)


def _normalize_json(value: Any, *, key: str | None = None, parents: tuple[str, ...] = ()) -> Any:
    if isinstance(value, dict):
        normalized: dict[str, Any] = {}
        for child_key in sorted(value):
            normalized[child_key] = _normalize_json(
                value[child_key], key=child_key, parents=(*parents, key or "")
            )
        if key == "Statement":
            _fail("statement-not-array")
        return normalized
    if isinstance(value, list):
        if key == "Statement":
            statements: list[dict[str, Any]] = []
            seen: set[str] = set()
            for statement in value:
                item = _expect_object(statement, "statement-not-object")
                sid = item.get("Sid")
                if not isinstance(sid, str) or not sid:
                    _fail("invalid-statement-sid")
                if sid in seen:
                    _fail("duplicate-statement-sid")
                seen.add(sid)
                statements.append(_normalize_json(item, parents=(*parents, "Statement")))
            return sorted(statements, key=lambda item: item["Sid"])
        if key in SET_ARRAY_KEYS:
            return _sort_set_array(value)
        if "Principal" in parents and key not in {"Statement", None}:
            return _sort_set_array(value)
        if "Condition" in parents and len(parents) >= 1:
            return _sort_set_array(value)
        return [_normalize_json(child, parents=(*parents, key or "")) for child in value]
    if isinstance(value, float) and not math.isfinite(value):
        _fail("non-finite-json")
    return value


def canonicalize_policy(value: Any) -> bytes:
    policy = _decode_policy_document(value)
    if set(policy) - {"Id", "Statement", "Version"}:
        _fail("unexpected-policy-field")
    if not isinstance(policy.get("Version"), str):
        _fail("invalid-policy-version")
    statements = policy.get("Statement")
    if not isinstance(statements, list) or not statements:
        _fail("invalid-policy-statements")
    normalized = _normalize_json(policy)
    return canonical_json_bytes(normalized)


def canonical_json_bytes(value: Any) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    except (RecursionError, TypeError, ValueError, UnicodeError) as exc:
        raise EvidenceError("uncanonicalizable-json") from exc


def policy_sha256(value: Any) -> str:
    return hashlib.sha256(canonicalize_policy(value)).hexdigest()


def compare_policy(identifier: str, expected: Any, actual: Any) -> dict[str, str]:
    if not re.fullmatch(r"[a-z0-9-]+", identifier):
        _fail("invalid-policy-identifier")
    expected_bytes = canonicalize_policy(expected)
    actual_bytes = canonicalize_policy(actual)
    expected_hash = hashlib.sha256(expected_bytes).hexdigest()
    actual_hash = hashlib.sha256(actual_bytes).hexdigest()
    if expected_bytes != actual_bytes:
        _fail("policy-mismatch")
    return {
        "actual_sha256": actual_hash,
        "expected_sha256": expected_hash,
        "identifier": identifier,
        "status": "PASS",
    }


def _load_manifest_file() -> dict[str, Any]:
    if MANIFEST_PATH.is_symlink() or not MANIFEST_PATH.is_file():
        _fail("missing-manifest")
    if MANIFEST_PATH.stat().st_size > MAX_INPUT_BYTES:
        _fail("manifest-too-large")
    try:
        raw = MANIFEST_PATH.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise EvidenceError("unreadable-manifest") from exc
    return _expect_object(parse_json(raw), "manifest-root-not-object")


def _source_binding(manifest: Mapping[str, Any]) -> dict[str, str]:
    source = _expect_object(manifest.get("source_binding"), "invalid-source-binding")
    _expect_keys(
        source, {"infra_sha", "infra_tree", "website_sha", "website_tree"}, "invalid-source-binding"
    )
    for value in source.values():
        if not isinstance(value, str) or not SHA_PATTERN.fullmatch(value):
            _fail("invalid-source-binding")
    return source  # type: ignore[return-value]


def _validate_manifest_policies(manifest: Mapping[str, Any]) -> None:
    fixtures = _expect_object(manifest.get("policy_fixtures"), "invalid-policy-fixtures")
    expected = {
        "kms-key-policy",
        "publisher-inline",
        "publisher-trust",
        "deployer-inline",
        "deployer-trust",
    }
    if set(fixtures) != expected:
        _fail("invalid-policy-fixtures")
    for identifier, policy in fixtures.items():
        compare_policy(identifier, policy, policy)


def _resolve_template(value: Any, bindings: Mapping[str, str]) -> Any:
    if isinstance(value, str):
        match = TEMPLATE_PATTERN.fullmatch(value)
        if match:
            key = match.group(1)
            if key not in bindings:
                _fail("missing-dynamic-binding")
            return bindings[key]
        return value
    if isinstance(value, dict):
        return {key: _resolve_template(child, bindings) for key, child in value.items()}
    if isinstance(value, list):
        return [_resolve_template(child, bindings) for child in value]
    return value


def _flatten_bindings(payload: Mapping[str, Any]) -> dict[str, str]:
    cloudfront = _expect_object(payload["cloudfront"], "invalid-cloudfront-binding")
    target_group = _expect_object(payload["target_group"], "invalid-target-group-binding")
    task_definitions = _expect_object(
        payload["task_definitions"], "invalid-task-definition-binding"
    )
    secrets = _expect_object(payload["secret_arns"], "invalid-secret-binding")
    result = {
        "cloudfront_distribution_arn": cloudfront["distribution_arn"],
        "cloudfront_distribution_id": cloudfront["distribution_id"],
        "cloudfront_domain_name": cloudfront["domain_name"],
        "target_group_arn": target_group["arn"],
        "web_task_definition_arn": task_definitions["web"],
        "worker_task_definition_arn": task_definitions["worker"],
        "migration_task_definition_arn": task_definitions["migration"],
        "database_secret_arn": secrets["database_url"],
    }
    if not all(isinstance(value, str) for value in result.values()):
        _fail("invalid-dynamic-binding")
    return result


def validate_manifest(manifest: Mapping[str, Any]) -> dict[str, Any]:
    _expect_keys(
        manifest,
        {
            "contract_id",
            "dynamic_binding_schema",
            "policy_fixtures",
            "readback_manifest",
            "resource_policy_absence",
            "schema_version",
            "simulator_rows",
            "source_binding",
            "static",
        },
        "invalid-manifest-keys",
    )
    if (
        manifest["schema_version"] != SCHEMA_VERSION
        or manifest["contract_id"] != "website-sandbox-gate-b-v1"
    ):
        _fail("invalid-manifest-version")
    source = _source_binding(manifest)
    _validate_manifest_policies(manifest)
    static = _expect_object(manifest["static"], "invalid-static-contract")
    if static.get("account_id") != "817685572750" or static.get("region") != "eu-west-1":
        _fail("invalid-static-contract")
    roles = _expect_object(static.get("roles"), "invalid-role-contract")
    if set(roles) != {"publisher", "deployer"}:
        _fail("invalid-role-contract")
    absence = _expect_object(manifest["resource_policy_absence"], "invalid-policy-absence")
    if absence != {
        "ecr_registry": "RegistryPolicyNotFoundException",
        "ecr_repository": "RepositoryPolicyNotFoundException",
        "s3_bucket": "NoSuchBucketPolicy",
        "secrets_manager": "success-with-resource-policy-member-absent",
    }:
        _fail("invalid-policy-absence")
    schema = _expect_object(manifest["dynamic_binding_schema"], "invalid-binding-schema")
    required = _expect_list(schema.get("required"), "invalid-binding-schema")
    if len(required) != len(set(required)) or not all(isinstance(item, str) for item in required):
        _fail("invalid-binding-schema")
    _validate_simulator_rows(manifest, None)
    manifest_sha256 = hashlib.sha256(canonical_json_bytes(manifest)).hexdigest()
    if manifest_sha256 != EXPECTED_MANIFEST_SHA256:
        _fail("manifest-digest-mismatch")
    return {
        "contract_id": manifest["contract_id"],
        "manifest_sha256": manifest_sha256,
        "schema_version": SCHEMA_VERSION,
        "source_binding": source,
        "status": "PASS",
    }


def _validate_envelope(
    document: Mapping[str, Any], manifest: Mapping[str, Any], kind: str
) -> tuple[str, dict[str, Any]]:
    _expect_keys(
        document,
        {
            "capture_id",
            "infra_sha",
            "kind",
            "payload",
            "payload_sha256",
            "schema_version",
            "website_sha",
        },
        "invalid-envelope",
    )
    if document["schema_version"] != SCHEMA_VERSION or document["kind"] != kind:
        _fail("invalid-envelope")
    capture_id = document["capture_id"]
    if not isinstance(capture_id, str) or not CAPTURE_ID_PATTERN.fullmatch(capture_id):
        _fail("invalid-capture-id")
    source = _source_binding(manifest)
    if (
        document["website_sha"] != source["website_sha"]
        or document["infra_sha"] != source["infra_sha"]
    ):
        _fail("source-binding-mismatch")
    payload = _expect_object(document["payload"], "invalid-payload")
    payload_sha256 = document["payload_sha256"]
    if (
        not isinstance(payload_sha256, str)
        or not HEX_64_PATTERN.fullmatch(payload_sha256)
        or payload_sha256 != hashlib.sha256(canonical_json_bytes(payload)).hexdigest()
    ):
        _fail("payload-digest-mismatch")
    return capture_id, payload


def _expected_github_variables(
    manifest: Mapping[str, Any], payload: Mapping[str, Any]
) -> tuple[dict[str, str], dict[str, str]]:
    static = _expect_object(manifest["static"], "invalid-static-contract")
    roles = _expect_object(static["roles"], "invalid-role-contract")
    publisher = _expect_object(roles["publisher"], "invalid-role-contract")
    deployer = _expect_object(roles["deployer"], "invalid-role-contract")
    kms = _expect_object(static["kms"], "invalid-kms-contract")
    ecr = _expect_object(static["ecr"], "invalid-ecr-contract")
    ecs = _expect_object(static["ecs"], "invalid-ecs-contract")
    route53 = _expect_object(static["route53"], "invalid-route53-contract")
    network = _expect_object(payload["network"], "invalid-network-binding")
    target_group = _expect_object(payload["target_group"], "invalid-target-group-binding")
    repository = {
        "SANDBOX_AUTO_DEPLOY": "false",
        "SANDBOX_AWS_REGION": static["region"],
        "SANDBOX_ECR_REPOSITORY_NAME": ecr["name"],
        "SANDBOX_ECR_REPOSITORY_URI": ecr["uri"],
        "SANDBOX_KMS_KEY_ARN": kms["arn"],
        "SANDBOX_PUBLISHER_ROLE_ARN": publisher["arn"],
        "SANDBOX_ROUTE53_HOSTED_ZONE_ID": route53["zone_id"],
    }
    families = {
        name: ecs[f"{name}_family"].rsplit("/", 1)[1].removesuffix(":*")
        for name in ("web", "worker", "migration")
    }
    environment = {
        "SANDBOX_DEPLOYER_ROLE_ARN": deployer["arn"],
        "SANDBOX_ECS_ASSIGN_PUBLIC_IP": "true",
        "SANDBOX_ECS_CLUSTER_ARN": ecs["cluster_arn"],
        "SANDBOX_ECS_CONTAINER_NAMES": '{"migration":"migration","web":"web","worker":"worker"}',
        "SANDBOX_ECS_EXECUTION_ROLE_ARN": ecs["execution_role_arn"],
        "SANDBOX_ECS_MIGRATION_TASK_FAMILY": families["migration"],
        "SANDBOX_ECS_SECURITY_GROUP_IDS": canonical_json_bytes(
            network["security_group_ids"]
        ).decode(),
        "SANDBOX_ECS_SUBNET_IDS": canonical_json_bytes(network["subnet_ids"]).decode(),
        "SANDBOX_ECS_TASK_ROLE_ARN": ecs["task_role_arn"],
        "SANDBOX_ECS_WEB_SERVICE_NAME": "website-sandbox-web",
        "SANDBOX_ECS_WEB_TASK_FAMILY": families["web"],
        "SANDBOX_ECS_WORKER_SERVICE_NAME": "website-sandbox-worker",
        "SANDBOX_ECS_WORKER_TASK_FAMILY": families["worker"],
        "SANDBOX_RESOURCE_ENVIRONMENT_TAG": "sandbox",
        "SANDBOX_RESOURCE_PROJECT_TAG": "website",
        "SANDBOX_WEB_RELEASE_DESIRED_COUNT": "1",
        "SANDBOX_WEB_TARGET_GROUP_ARN": target_group["arn"],
        "SANDBOX_WORKER_RELEASE_DESIRED_COUNT": "1",
    }
    if not all(isinstance(value, str) for value in (*repository.values(), *environment.values())):
        _fail("invalid-static-contract")
    return repository, environment


def _validate_operator_identity(value: Any, manifest: Mapping[str, Any]) -> dict[str, Any]:
    operator = _expect_object(value, "invalid-operator-binding")
    _expect_keys(operator, {"account_id", "arn", "user_id"}, "invalid-operator-binding")
    static = _expect_object(manifest["static"], "invalid-static-contract")
    protected_role_names = {
        _expect_object(role, "invalid-role-contract")["name"]
        for role in _expect_object(static["roles"], "invalid-role-contract").values()
    }
    ecs = _expect_object(static["ecs"], "invalid-ecs-contract")
    for key in ("task_role_arn", "execution_role_arn"):
        role_arn = ecs[key]
        if not isinstance(role_arn, str):
            _fail("invalid-ecs-contract")
        protected_role_names.add(role_arn.rsplit("/", 1)[1])
    operator_arn = operator["arn"]
    protected_iam_arns = {f"arn:aws:iam::817685572750:role/{name}" for name in protected_role_names}
    protected_session = isinstance(operator_arn, str) and any(
        operator_arn.startswith(f"arn:aws:sts::817685572750:assumed-role/{name}/")
        for name in protected_role_names
    )
    if (
        operator["account_id"] != "817685572750"
        or not isinstance(operator_arn, str)
        or not IAM_CALLER_ARN_PATTERN.fullmatch(operator_arn)
        or operator_arn in protected_iam_arns
        or protected_session
        or not isinstance(operator["user_id"], str)
        or not operator["user_id"]
    ):
        _fail("invalid-operator-binding")
    return operator


def validate_bindings(document: Mapping[str, Any], manifest: Mapping[str, Any]) -> dict[str, Any]:
    validate_manifest(manifest)
    capture_id, payload = _validate_envelope(document, manifest, "bindings")
    required_sections = {
        "cloudfront",
        "dns_records",
        "github_environment_variables",
        "github_repository_variables",
        "network",
        "operator_identity",
        "secret_arns",
        "target_group",
        "task_definitions",
    }
    _expect_keys(payload, required_sections, "invalid-binding-sections")

    cloudfront = _expect_object(payload["cloudfront"], "invalid-cloudfront-binding")
    _expect_keys(
        cloudfront,
        {"distribution_arn", "distribution_id", "domain_name"},
        "invalid-cloudfront-binding",
    )
    distribution_id = cloudfront["distribution_id"]
    if not isinstance(distribution_id, str) or not CF_ID_PATTERN.fullmatch(distribution_id):
        _fail("invalid-cloudfront-binding")
    if (
        cloudfront["distribution_arn"]
        != f"arn:aws:cloudfront::817685572750:distribution/{distribution_id}"
    ):
        _fail("invalid-cloudfront-binding")
    if not isinstance(cloudfront["domain_name"], str) or not cloudfront["domain_name"].endswith(
        ".cloudfront.net"
    ):
        _fail("invalid-cloudfront-binding")

    target_group = _expect_object(payload["target_group"], "invalid-target-group-binding")
    _expect_keys(target_group, {"arn", "suffix"}, "invalid-target-group-binding")
    if not isinstance(target_group["arn"], str) or not TG_ARN_PATTERN.fullmatch(
        target_group["arn"]
    ):
        _fail("invalid-target-group-binding")
    if target_group["arn"].rsplit("/", 1)[1] != target_group["suffix"]:
        _fail("invalid-target-group-binding")

    task_definitions = _expect_object(
        payload["task_definitions"], "invalid-task-definition-binding"
    )
    _expect_keys(
        task_definitions, {"migration", "web", "worker"}, "invalid-task-definition-binding"
    )
    for family, arn in task_definitions.items():
        if not isinstance(arn, str):
            _fail("invalid-task-definition-binding")
        match = TASK_DEFINITION_PATTERN.fullmatch(arn)
        if not match or match.group(1) != family:
            _fail("invalid-task-definition-binding")

    static = _expect_object(manifest["static"], "invalid-static-contract")
    secret_names = _expect_object(static["secret_names"], "invalid-secret-contract")
    secret_arns = _expect_object(payload["secret_arns"], "invalid-secret-binding")
    if set(secret_arns) != set(secret_names):
        _fail("invalid-secret-binding")
    if len(set(secret_arns.values())) != len(secret_arns):
        _fail("invalid-secret-binding")
    for key, arn in secret_arns.items():
        if not isinstance(arn, str):
            _fail("invalid-secret-binding")
        match = SECRET_ARN_PATTERN.fullmatch(arn)
        if not match or match.group(1) != secret_names[key]:
            _fail("invalid-secret-binding")

    operator = _validate_operator_identity(payload["operator_identity"], manifest)

    repository_variables = _expect_object(
        payload["github_repository_variables"], "invalid-github-binding"
    )
    environment_variables = _expect_object(
        payload["github_environment_variables"], "invalid-github-binding"
    )
    expected_repository, expected_environment = _expected_github_variables(manifest, payload)
    if repository_variables != expected_repository or environment_variables != expected_environment:
        _fail("github-binding-mismatch")

    network = _expect_object(payload["network"], "invalid-network-binding")
    _expect_keys(
        network,
        {"alb_dns_name", "alb_zone_id", "security_group_ids", "subnet_ids", "vpc_id"},
        "invalid-network-binding",
    )
    for list_key, pattern, expected_count in (
        ("security_group_ids", SECURITY_GROUP_ID_PATTERN, 1),
        ("subnet_ids", SUBNET_ID_PATTERN, 2),
    ):
        values = _expect_list(network[list_key], "invalid-network-binding")
        if (
            len(values) != expected_count
            or len(values) != len(set(values))
            or not all(isinstance(item, str) and pattern.fullmatch(item) for item in values)
        ):
            _fail("invalid-network-binding")
    if (
        not isinstance(network["alb_dns_name"], str)
        or not network["alb_dns_name"].endswith(".eu-west-1.elb.amazonaws.com")
        or not isinstance(network["alb_zone_id"], str)
        or not re.fullmatch(r"Z[A-Z0-9]+", network["alb_zone_id"])
        or not isinstance(network["vpc_id"], str)
        or not VPC_ID_PATTERN.fullmatch(network["vpc_id"])
    ):
        _fail("invalid-network-binding")

    dns_records = _expect_list(payload["dns_records"], "invalid-dns-binding")
    if len(dns_records) != 6:
        _fail("invalid-dns-binding")
    record_keys: set[tuple[str, str]] = set()
    for record in dns_records:
        item = _expect_object(record, "invalid-dns-binding")
        _expect_keys(item, {"name", "type", "value"}, "invalid-dns-binding")
        record_key = (item["name"], item["type"])
        if (
            not all(isinstance(part, str) and part for part in record_key)
            or record_key in record_keys
        ):
            _fail("invalid-dns-binding")
        record_keys.add(record_key)
    for record_type in ("A", "AAAA"):
        matching = [
            item
            for item in dns_records
            if item["name"].rstrip(".") == "web.dtcdev.click" and item["type"] == record_type
        ]
        if len(matching) != 1 or matching[0]["value"].rstrip(".") != cloudfront[
            "domain_name"
        ].rstrip("."):
            _fail("cloudfront-dns-mismatch")
        origin = [
            item
            for item in dns_records
            if item["name"].rstrip(".") == "origin.web.dtcdev.click" and item["type"] == record_type
        ]
        if len(origin) != 1 or origin[0]["value"].rstrip(".") != network["alb_dns_name"].rstrip(
            "."
        ):
            _fail("origin-dns-mismatch")
    certificate_records = [item for item in dns_records if item["type"] == "CNAME"]
    if len(certificate_records) != 2 or not all(
        item["name"].startswith("_")
        and item["name"].rstrip(".").endswith((".web.dtcdev.click", ".origin.web.dtcdev.click"))
        and item["value"].endswith(".acm-validations.aws.")
        for item in certificate_records
    ):
        _fail("certificate-dns-mismatch")
    certificate_parents = {
        ("origin" if item["name"].rstrip(".").endswith(".origin.web.dtcdev.click") else "web")
        for item in certificate_records
    }
    if certificate_parents != {"origin", "web"}:
        _fail("certificate-dns-mismatch")

    bindings = _flatten_bindings(payload)
    binding_hash = hashlib.sha256(canonical_json_bytes(payload)).hexdigest()
    return {
        "binding_sha256": binding_hash,
        "capture_id": capture_id,
        "kind": "bindings",
        "operator_identity": operator,
        "resolved": bindings,
        "source_binding": _source_binding(manifest),
        "status": "PASS",
    }


def _validate_role_policy(
    role_class: str,
    actual: Mapping[str, Any],
    manifest: Mapping[str, Any],
) -> dict[str, dict[str, str]]:
    static = _expect_object(manifest["static"], "invalid-static-contract")
    expected_role = _expect_object(static["roles"], "invalid-role-contract")[role_class]
    role = _expect_object(expected_role, "invalid-role-contract")
    _expect_keys(
        actual,
        {
            "arn",
            "attached_policies",
            "inline_policies",
            "max_session_duration",
            "name",
            "path",
            "permissions_boundary",
            "trust_policy",
        },
        "invalid-role-readback",
    )
    for field in ("arn", "max_session_duration", "name", "path"):
        if actual[field] != role[field]:
            _fail("role-readback-mismatch")
    if actual["attached_policies"] != []:
        _fail("unexpected-attached-policy")
    if actual["permissions_boundary"] is not None:
        _fail("unexpected-permissions-boundary")
    inline = _expect_object(actual["inline_policies"], "invalid-inline-policies")
    if set(inline) != {role["name"]}:
        _fail("invalid-inline-policies")
    fixtures = _expect_object(manifest["policy_fixtures"], "invalid-policy-fixtures")
    return {
        "inline": compare_policy(
            f"{role_class}-inline", fixtures[f"{role_class}-inline"], inline[role["name"]]
        ),
        "trust": compare_policy(
            f"{role_class}-trust", fixtures[f"{role_class}-trust"], actual["trust_policy"]
        ),
    }


def _canonical_grant_inventory(
    grants: Any, application_arns: set[str]
) -> tuple[list[dict[str, Any]], str]:
    grant_list = _expect_list(grants, "invalid-grant-inventory")
    accepted_keys = {
        "Constraints",
        "CreationDate",
        "GrantId",
        "GranteePrincipal",
        "IssuingAccount",
        "Name",
        "Operations",
        "RetiringPrincipal",
    }
    normalized: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for grant in grant_list:
        item = _expect_object(grant, "invalid-grant-inventory")
        if set(item) - accepted_keys:
            _fail("invalid-grant-inventory")
        grant_id = item.get("GrantId")
        if not isinstance(grant_id, str) or not grant_id or grant_id in seen_ids:
            _fail("invalid-grant-inventory")
        seen_ids.add(grant_id)
        grantee = item.get("GranteePrincipal")
        operations = item.get("Operations")
        if (
            not isinstance(grantee, str)
            or not KMS_PRINCIPAL_ARN_PATTERN.fullmatch(grantee)
            or not isinstance(operations, list)
            or not operations
            or not all(
                isinstance(operation, str) and operation in KMS_GRANT_OPERATIONS
                for operation in operations
            )
            or len(operations) != len(set(operations))
        ):
            _fail("invalid-grant-inventory")
        retiring = item.get("RetiringPrincipal")
        if retiring is not None and (
            not isinstance(retiring, str) or not KMS_PRINCIPAL_ARN_PATTERN.fullmatch(retiring)
        ):
            _fail("invalid-grant-inventory")
        for string_field in ("CreationDate", "IssuingAccount", "Name"):
            field_value = item.get(string_field)
            if field_value is not None and (not isinstance(field_value, str) or not field_value):
                _fail("invalid-grant-inventory")
        constraints = item.get("Constraints")
        if constraints is not None:
            constraint_object = _expect_object(constraints, "invalid-grant-inventory")
            if set(constraint_object) - {
                "EncryptionContextEquals",
                "EncryptionContextSubset",
            }:
                _fail("invalid-grant-inventory")
            for constraint in constraint_object.values():
                values = _expect_object(constraint, "invalid-grant-inventory")
                if not all(
                    isinstance(key, str) and key and isinstance(value, str)
                    for key, value in values.items()
                ):
                    _fail("invalid-grant-inventory")
        if (
            item.get("GranteePrincipal") in application_arns
            or item.get("RetiringPrincipal") in application_arns
        ):
            _fail("application-role-kms-grant")
        canonical_item = dict(item)
        canonical_item["Operations"] = sorted(operations)
        normalized.append(_normalize_json(canonical_item))
    normalized.sort(key=canonical_json_bytes)
    return normalized, hashlib.sha256(canonical_json_bytes(normalized)).hexdigest()


def validate_policy_bundle(
    document: Mapping[str, Any], manifest: Mapping[str, Any], bindings_result: Mapping[str, Any]
) -> dict[str, Any]:
    validate_manifest(manifest)
    capture_id, payload = _validate_envelope(document, manifest, "policies")
    if capture_id != bindings_result.get("capture_id"):
        _fail("mixed-capture")
    _expect_keys(payload, {"kms", "roles"}, "invalid-policy-bundle")
    roles = _expect_object(payload["roles"], "invalid-policy-bundle")
    if set(roles) != {"publisher", "deployer"}:
        _fail("invalid-policy-bundle")
    policy_hashes: dict[str, dict[str, str]] = {}
    for role_class in ("publisher", "deployer"):
        role_hashes = _validate_role_policy(
            role_class,
            _expect_object(roles[role_class], "invalid-role-readback"),
            manifest,
        )
        policy_hashes[f"{role_class}-trust"] = role_hashes["trust"]
        policy_hashes[f"{role_class}-inline"] = role_hashes["inline"]

    static = _expect_object(manifest["static"], "invalid-static-contract")
    kms_static = _expect_object(static["kms"], "invalid-kms-contract")
    kms = _expect_object(payload["kms"], "invalid-kms-readback")
    _expect_keys(
        kms,
        {
            "alias_name",
            "alias_target_key_id",
            "arn",
            "enabled",
            "grant_inventory",
            "grant_inventory_truncated",
            "key_id",
            "key_manager",
            "key_policy",
            "key_state",
            "key_usage",
            "multi_region",
            "origin",
            "policy_name",
            "rotation_enabled",
            "spec",
        },
        "invalid-kms-readback",
    )
    for field in (
        "alias_name",
        "arn",
        "enabled",
        "key_id",
        "key_manager",
        "key_state",
        "key_usage",
        "multi_region",
        "origin",
        "rotation_enabled",
        "spec",
    ):
        if kms[field] != kms_static[field]:
            _fail("kms-readback-mismatch")
    if kms["alias_target_key_id"] != kms_static["key_id"] or kms["policy_name"] != "default":
        _fail("kms-readback-mismatch")
    if kms["grant_inventory_truncated"] is not False:
        _fail("truncated-grant-inventory")
    fixtures = _expect_object(manifest["policy_fixtures"], "invalid-policy-fixtures")
    kms_hash = compare_policy("kms-key-policy", fixtures["kms-key-policy"], kms["key_policy"])
    policy_hashes["kms-key-policy"] = kms_hash
    application_arns = {
        role["arn"] for role in _expect_object(static["roles"], "invalid-role-contract").values()
    }
    _normalized_grants, grant_hash = _canonical_grant_inventory(
        kms["grant_inventory"], application_arns
    )
    return {
        "capture_id": capture_id,
        "grant_baseline_sha256": grant_hash,
        "kind": "policies",
        "policy_hashes": policy_hashes,
        "source_binding": _source_binding(manifest),
        "status": "PASS",
    }


def _validate_s3(value: Any, manifest: Mapping[str, Any]) -> None:
    actual = _expect_object(value, "invalid-s3-readback")
    _expect_keys(
        actual,
        {
            "bucket",
            "bucket_location",
            "bucket_policy_error",
            "encryption_algorithm",
            "expected_bucket_owner",
            "key",
            "object_exists",
            "ownership",
            "public_access_block",
            "versioning",
        },
        "invalid-s3-readback",
    )
    static = _expect_object(manifest["static"], "invalid-static-contract")
    state = _expect_object(static["state"], "invalid-state-contract")
    expected_values = {
        "bucket": state["bucket"],
        "bucket_location": "us-east-1",
        "bucket_policy_error": "NoSuchBucketPolicy",
        "encryption_algorithm": "AES256",
        "expected_bucket_owner": "817685572750",
        "key": state["key"],
        "object_exists": True,
        "ownership": "BucketOwnerEnforced",
        "versioning": "Enabled",
    }
    for key, expected in expected_values.items():
        if actual[key] != expected:
            _fail("s3-readback-mismatch")
    public = _expect_object(actual["public_access_block"], "invalid-s3-readback")
    if public != {
        "BlockPublicAcls": True,
        "BlockPublicPolicy": True,
        "IgnorePublicAcls": True,
        "RestrictPublicBuckets": True,
    }:
        _fail("s3-readback-mismatch")


def _validate_ecr(value: Any, manifest: Mapping[str, Any]) -> None:
    actual = _expect_object(value, "invalid-ecr-readback")
    _expect_keys(
        actual,
        {
            "arn",
            "image_tag_mutability",
            "kms_key_arn",
            "name",
            "registry_id",
            "registry_policy_error",
            "repository_policy_error",
            "scan_on_push",
            "zero_digest_present",
            "zero_digest_error",
        },
        "invalid-ecr-readback",
    )
    static = _expect_object(manifest["static"], "invalid-static-contract")
    ecr = _expect_object(static["ecr"], "invalid-ecr-contract")
    expected = {
        "arn": ecr["arn"],
        "image_tag_mutability": "IMMUTABLE",
        "kms_key_arn": _expect_object(static["kms"], "invalid-kms-contract")["arn"],
        "name": ecr["name"],
        "registry_id": "817685572750",
        "registry_policy_error": "RegistryPolicyNotFoundException",
        "repository_policy_error": "RepositoryPolicyNotFoundException",
        "scan_on_push": True,
        "zero_digest_present": False,
        "zero_digest_error": "ImageNotFoundException",
    }
    if actual != expected:
        _fail("ecr-readback-mismatch")


def _validate_secrets(
    value: Any, manifest: Mapping[str, Any], binding_payload: Mapping[str, Any]
) -> None:
    actual = _expect_object(value, "invalid-secret-readback")
    static = _expect_object(manifest["static"], "invalid-static-contract")
    names = _expect_object(static["secret_names"], "invalid-secret-contract")
    bound_arns = _expect_object(binding_payload["secret_arns"], "invalid-secret-binding")
    if set(actual) != set(names):
        _fail("invalid-secret-readback")
    for key, expected_name in names.items():
        secret = _expect_object(actual[key], "invalid-secret-readback")
        _expect_keys(
            secret,
            {
                "arn",
                "deleted_date",
                "description",
                "kms_key_id",
                "name",
                "owning_service",
                "primary_region",
                "resource_policy_response",
                "rotation_enabled",
                "version_ids_to_stages",
            },
            "invalid-secret-readback",
        )
        description_key = expected_name.removeprefix("website-sandbox/").replace("-", " ")
        if (
            secret["arn"] != bound_arns[key]
            or secret["name"] != expected_name
            or secret["kms_key_id"] != _expect_object(static["kms"], "invalid-kms-contract")["arn"]
            or secret["version_ids_to_stages"] != {}
            or secret["description"]
            != f"Out-of-band value container for website-sandbox {description_key}"
            or secret["deleted_date"] is not None
            or secret["owning_service"] is not None
            or secret["primary_region"] is not None
            or secret["rotation_enabled"] is not False
        ):
            _fail("secret-readback-mismatch")
        response = _expect_object(
            secret["resource_policy_response"], "invalid-secret-policy-response"
        )
        if set(response) != {"ARN", "Name"}:
            _fail("secret-policy-present-or-ambiguous")
        if response["ARN"] != bound_arns[key] or response["Name"] != expected_name:
            _fail("secret-readback-mismatch")


def _validate_cloudfront(value: Any, binding_payload: Mapping[str, Any]) -> None:
    actual = _expect_object(value, "invalid-cloudfront-readback")
    _expect_keys(
        actual,
        {
            "aliases",
            "arn",
            "distribution_id",
            "domain_name",
            "enabled",
            "route53_web_targets",
            "status",
        },
        "invalid-cloudfront-readback",
    )
    bound = _expect_object(binding_payload["cloudfront"], "invalid-cloudfront-binding")
    if (
        actual["distribution_id"] != bound["distribution_id"]
        or actual["arn"] != bound["distribution_arn"]
        or actual["domain_name"] != bound["domain_name"]
        or actual["enabled"] is not True
        or actual["status"] != "Deployed"
        or actual["aliases"] != ["web.dtcdev.click"]
        or actual["route53_web_targets"]
        != {"A": bound["domain_name"], "AAAA": bound["domain_name"]}
    ):
        _fail("cloudfront-readback-mismatch")


def _validate_runtime(value: Any, binding_payload: Mapping[str, Any]) -> None:
    actual = _expect_object(value, "invalid-runtime-readback")
    _expect_keys(
        actual,
        {
            "database",
            "ecr_image_count",
            "ecs_cluster",
            "ecs_services",
            "pending_tasks",
            "running_tasks",
            "stopped_tasks",
            "target_group",
            "target_count",
            "task_definitions",
        },
        "invalid-runtime-readback",
    )
    for key in (
        "ecr_image_count",
        "pending_tasks",
        "running_tasks",
        "stopped_tasks",
        "target_count",
    ):
        if actual[key] != 0:
            _fail("runtime-not-zero")
    services = _expect_object(actual["ecs_services"], "invalid-runtime-readback")
    if set(services) != {"web", "worker"}:
        _fail("invalid-runtime-readback")
    task_definitions = _expect_object(
        binding_payload["task_definitions"], "invalid-task-definition-binding"
    )
    cluster = _expect_object(actual["ecs_cluster"], "invalid-runtime-readback")
    if cluster != {
        "active_services": 2,
        "arn": "arn:aws:ecs:eu-west-1:817685572750:cluster/website-sandbox",
        "name": "website-sandbox",
        "pending_tasks": 0,
        "registered_container_instances": 0,
        "running_tasks": 0,
        "status": "ACTIVE",
    }:
        _fail("ecs-cluster-readback-mismatch")
    for name, service in services.items():
        service_data = _expect_object(service, "invalid-runtime-readback")
        if service_data != {
            "arn": (
                f"arn:aws:ecs:eu-west-1:817685572750:service/website-sandbox/website-sandbox-{name}"
            ),
            "desired": 0,
            "name": f"website-sandbox-{name}",
            "pending": 0,
            "running": 0,
            "status": "ACTIVE",
            "task_definition": task_definitions[name],
        }:
            _fail("runtime-not-zero")
    definition_readbacks = _expect_object(actual["task_definitions"], "invalid-runtime-readback")
    if set(definition_readbacks) != {"web", "worker", "migration"}:
        _fail("invalid-runtime-readback")
    for name, bound_arn in task_definitions.items():
        match = TASK_DEFINITION_PATTERN.fullmatch(bound_arn)
        if match is None:
            _fail("invalid-task-definition-binding")
        definition = _expect_object(definition_readbacks[name], "invalid-runtime-readback")
        if definition != {
            "arn": bound_arn,
            "execution_role_arn": ("arn:aws:iam::817685572750:role/website-sandbox-task-execution"),
            "family": f"website-sandbox-{name}",
            "revision": int(match.group(2)),
            "status": "ACTIVE",
            "task_role_arn": ("arn:aws:iam::817685572750:role/website-sandbox-task-application"),
        }:
            _fail("task-definition-readback-mismatch")
    network = _expect_object(binding_payload["network"], "invalid-network-binding")
    target_group = _expect_object(binding_payload["target_group"], "invalid-target-group-binding")
    if actual["target_group"] != {
        "arn": target_group["arn"],
        "health_check_path": "/health/ready",
        "name": "website-sandbox-web",
        "port": 8000,
        "protocol": "HTTP",
        "target_type": "ip",
        "vpc_id": network["vpc_id"],
    }:
        _fail("target-group-readback-mismatch")
    if actual["database"] != {
        "arn": "arn:aws:rds:eu-west-1:817685572750:db:website-sandbox",
        "encrypted": True,
        "identifier": "website-sandbox",
        "kms_key_arn": (
            "arn:aws:kms:eu-west-1:817685572750:key/b9181223-d870-4bae-92d2-fc28b7813887"
        ),
        "publicly_accessible": False,
        "status": "available",
    }:
        _fail("database-readback-mismatch")


def validate_resource_bundle(
    document: Mapping[str, Any],
    manifest: Mapping[str, Any],
    bindings_document: Mapping[str, Any],
    bindings_result: Mapping[str, Any],
) -> dict[str, Any]:
    validate_manifest(manifest)
    capture_id, payload = _validate_envelope(document, manifest, "resources")
    if capture_id != bindings_result.get("capture_id"):
        _fail("mixed-capture")
    _expect_keys(
        payload,
        {
            "caller_identity",
            "cloudfront",
            "dns_records",
            "ecr",
            "github",
            "route53",
            "runtime",
            "s3",
            "secrets",
            "terraform",
        },
        "invalid-resource-bundle",
    )
    _assert_safe_data(payload)
    _capture, binding_payload = _validate_envelope(bindings_document, manifest, "bindings")
    if payload["caller_identity"] != binding_payload["operator_identity"]:
        _fail("caller-identity-mismatch")
    _validate_s3(payload["s3"], manifest)
    _validate_ecr(payload["ecr"], manifest)
    _validate_secrets(payload["secrets"], manifest, binding_payload)
    _validate_cloudfront(payload["cloudfront"], binding_payload)
    _validate_runtime(payload["runtime"], binding_payload)
    if payload["dns_records"] != binding_payload["dns_records"]:
        _fail("dns-readback-mismatch")
    route53 = _expect_object(payload["route53"], "invalid-route53-readback")
    static = _expect_object(manifest["static"], "invalid-static-contract")
    zone = _expect_object(static["route53"], "invalid-route53-contract")
    if route53 != {
        "record_count": 6,
        "records_sha256": hashlib.sha256(
            canonical_json_bytes(binding_payload["dns_records"])
        ).hexdigest(),
        "zone_id": zone["zone_id"],
    }:
        _fail("route53-readback-mismatch")
    github = _expect_object(payload["github"], "invalid-github-readback")
    if github != {
        "branch_policy": ["main"],
        "environment_variables": binding_payload["github_environment_variables"],
        "repository_variables": binding_payload["github_repository_variables"],
    }:
        _fail("github-readback-mismatch")
    terraform = _expect_object(payload["terraform"], "invalid-terraform-readback")
    _expect_keys(
        terraform,
        {"address_count", "locked", "state_metadata_sha256"},
        "invalid-terraform-readback",
    )
    if terraform["address_count"] != 98 or terraform["locked"] is not False:
        _fail("terraform-readback-mismatch")
    digest = terraform["state_metadata_sha256"]
    if not isinstance(digest, str) or not HEX_64_PATTERN.fullmatch(digest):
        _fail("terraform-readback-mismatch")
    return {
        "capture_id": capture_id,
        "kind": "resources",
        "resource_sha256": hashlib.sha256(canonical_json_bytes(payload)).hexdigest(),
        "source_binding": _source_binding(manifest),
        "status": "PASS",
    }


def _resolved_simulator_rows(
    manifest: Mapping[str, Any], binding_payload: Mapping[str, Any]
) -> list[dict[str, Any]]:
    flattened = _flatten_bindings(binding_payload)
    rows = _expect_list(manifest["simulator_rows"], "invalid-simulator-rows")
    return [
        _resolve_template(_expect_object(row, "invalid-simulator-row"), flattened) for row in rows
    ]


def _validate_simulator_rows(
    manifest: Mapping[str, Any], binding_payload: Mapping[str, Any] | None
) -> None:
    rows = _expect_list(manifest["simulator_rows"], "invalid-simulator-rows")
    ids: set[str] = set()
    by_id: dict[str, dict[str, Any]] = {}
    required = {
        "action",
        "changed_axis",
        "context",
        "expected",
        "id",
        "negative_of",
        "principal",
        "rationale",
        "resource",
    }
    for raw in rows:
        row = _expect_object(raw, "invalid-simulator-row")
        _expect_keys(row, required, "invalid-simulator-row")
        row_id = row["id"]
        if not isinstance(row_id, str) or not re.fullmatch(r"[a-z0-9-]+", row_id) or row_id in ids:
            _fail("invalid-simulator-row")
        ids.add(row_id)
        by_id[row_id] = row
        if row["principal"] not in {"publisher", "deployer"}:
            _fail("invalid-simulator-row")
        if not isinstance(row["action"], str) or not isinstance(row["resource"], str):
            _fail("non-atomic-simulator-row")
        if row["expected"] not in {"allowed", "implicitDeny"}:
            _fail("invalid-simulator-decision")
        if (
            not isinstance(row["context"], dict)
            or not all(
                isinstance(key, str) and isinstance(value, str)
                for key, value in row["context"].items()
            )
            or not isinstance(row["rationale"], str)
            or not row["rationale"]
        ):
            _fail("invalid-simulator-row")
    for row in rows:
        parent_id = row["negative_of"]
        axis = row["changed_axis"]
        if parent_id is None:
            if axis is not None:
                _fail("invalid-one-axis-row")
            continue
        if (
            not isinstance(parent_id, str)
            or parent_id not in by_id
            or row["expected"] != "implicitDeny"
        ):
            _fail("invalid-one-axis-row")
        parent = by_id[parent_id]
        if (
            parent["expected"] != "allowed"
            or parent["principal"] != row["principal"]
            or parent["action"] != row["action"]
        ):
            _fail("invalid-one-axis-row")
        if axis == "resource":
            if parent["resource"] == row["resource"] or parent["context"] != row["context"]:
                _fail("invalid-one-axis-row")
        elif isinstance(axis, str) and axis.startswith("context:"):
            context_key = axis.split(":", 1)[1]
            if parent["resource"] != row["resource"]:
                _fail("invalid-one-axis-row")
            parent_context = dict(parent["context"])
            child_context = dict(row["context"])
            differing = {
                key
                for key in set(parent_context) | set(child_context)
                if parent_context.get(key) != child_context.get(key)
            }
            if differing != {context_key}:
                _fail("invalid-one-axis-row")
        else:
            _fail("invalid-one-axis-row")
    if hashlib.sha256(canonical_json_bytes(rows)).hexdigest() != EXPECTED_SIMULATOR_MATRIX_SHA256:
        _fail("simulator-matrix-mismatch")
    if binding_payload is not None:
        _resolved_simulator_rows(manifest, binding_payload)


def validate_one_axis_pairs(
    manifest: Mapping[str, Any], binding_payload: Mapping[str, Any]
) -> None:
    validate_manifest(manifest)
    _validate_simulator_rows(manifest, binding_payload)


def _simulator_context_entries(context: Mapping[str, str]) -> list[dict[str, Any]]:
    return [
        {
            "ContextKeyName": key,
            "ContextKeyType": "arn" if value.startswith("arn:") else "string",
            "ContextKeyValues": [value],
        }
        for key, value in sorted(context.items())
    ]


def evaluate_simulator_result(
    expected_row: Mapping[str, Any], result: Mapping[str, Any], manifest: Mapping[str, Any]
) -> None:
    _expect_keys(
        result,
        {"EvaluationResults", "is_truncated", "request", "row_id"},
        "invalid-simulator-result",
    )
    if result["row_id"] != expected_row["id"]:
        _fail("simulator-row-mismatch")
    roles = _expect_object(
        _expect_object(manifest["static"], "invalid-static-contract")["roles"],
        "invalid-role-contract",
    )
    role = _expect_object(roles[expected_row["principal"]], "invalid-role-contract")
    request = _expect_object(result["request"], "invalid-simulator-request")
    if request != {
        "action_names": [expected_row["action"]],
        "context_entries": _simulator_context_entries(expected_row["context"]),
        "policy_source_arn": role["arn"],
        "resource_arns": [expected_row["resource"]],
    }:
        _fail("simulator-request-mismatch")
    if result["is_truncated"] is not False:
        _fail("truncated-simulator-result")
    evaluations = _expect_list(result["EvaluationResults"], "invalid-simulator-result")
    if len(evaluations) != 1:
        _fail("simulator-result-count")
    evaluation = _expect_object(evaluations[0], "invalid-simulator-result")
    _expect_keys(
        evaluation,
        {"EvalActionName", "EvalDecision", "EvalResourceName", "MissingContextValues"},
        "invalid-simulator-result",
    )
    if (
        evaluation["EvalActionName"] != expected_row["action"]
        or evaluation["EvalResourceName"] != expected_row["resource"]
        or evaluation["EvalDecision"] != expected_row["expected"]
        or evaluation["MissingContextValues"] != []
    ):
        _fail("simulator-evaluation-mismatch")


def validate_simulator_bundle(
    document: Mapping[str, Any],
    manifest: Mapping[str, Any],
    bindings_document: Mapping[str, Any],
    bindings_result: Mapping[str, Any],
) -> dict[str, Any]:
    validate_manifest(manifest)
    capture_id, payload = _validate_envelope(document, manifest, "simulator")
    if capture_id != bindings_result.get("capture_id"):
        _fail("mixed-capture")
    _expect_keys(payload, {"results"}, "invalid-simulator-bundle")
    _binding_capture, binding_payload = _validate_envelope(bindings_document, manifest, "bindings")
    validate_one_axis_pairs(manifest, binding_payload)
    expected_rows = _resolved_simulator_rows(manifest, binding_payload)
    results = _expect_list(payload["results"], "invalid-simulator-results")
    if len(results) != len(expected_rows):
        _fail("simulator-result-count")
    by_id: dict[str, dict[str, Any]] = {}
    for result in results:
        item = _expect_object(result, "invalid-simulator-result")
        row_id = item.get("row_id")
        if not isinstance(row_id, str) or row_id in by_id:
            _fail("duplicate-simulator-result")
        by_id[row_id] = item
    if set(by_id) != {row["id"] for row in expected_rows}:
        _fail("simulator-row-set-mismatch")
    decisions: dict[str, str] = {}
    for row in expected_rows:
        evaluate_simulator_result(row, by_id[row["id"]], manifest)
        decisions[row["id"]] = row["expected"]
    return {
        "capture_id": capture_id,
        "decision_sha256": hashlib.sha256(canonical_json_bytes(decisions)).hexdigest(),
        "kind": "simulator",
        "row_count": len(expected_rows),
        "source_binding": _source_binding(manifest),
        "status": "PASS",
    }


def build_final_summary(
    manifest: Mapping[str, Any], result_documents: Sequence[Mapping[str, Any]]
) -> dict[str, Any]:
    validate_manifest(manifest)
    expected_kinds = {"bindings", "policies", "resources", "simulator"}
    by_kind: dict[str, Mapping[str, Any]] = {}
    capture_ids: set[str] = set()
    for document in result_documents:
        kind = document.get("kind")
        if kind not in expected_kinds or kind in by_kind or document.get("status") != "PASS":
            _fail("invalid-summary-input")
        source = document.get("source_binding")
        if source != _source_binding(manifest):
            _fail("source-binding-mismatch")
        capture_id = document.get("capture_id")
        if not isinstance(capture_id, str) or not CAPTURE_ID_PATTERN.fullmatch(capture_id):
            _fail("invalid-summary-input")
        base_keys = {"capture_id", "kind", "source_binding", "status"}
        if kind == "bindings":
            expected_keys = base_keys | {
                "binding_sha256",
                "operator_identity",
                "resolved",
            }
            digest_fields = ("binding_sha256",)
            try:
                _validate_operator_identity(document.get("operator_identity"), manifest)
            except EvidenceError as exc:
                raise EvidenceError("invalid-summary-input") from exc
            _expect_object(document.get("resolved"), "invalid-summary-input")
        elif kind == "policies":
            expected_keys = base_keys | {
                "grant_baseline_sha256",
                "policy_hashes",
            }
            digest_fields = ("grant_baseline_sha256",)
            policy_hashes = _expect_object(document.get("policy_hashes"), "invalid-summary-input")
            if set(policy_hashes) != {
                "deployer-inline",
                "deployer-trust",
                "kms-key-policy",
                "publisher-inline",
                "publisher-trust",
            }:
                _fail("invalid-summary-input")
            for identifier, policy_result in policy_hashes.items():
                item = _expect_object(policy_result, "invalid-summary-input")
                if item != {
                    "actual_sha256": item.get("actual_sha256"),
                    "expected_sha256": item.get("expected_sha256"),
                    "identifier": identifier,
                    "status": "PASS",
                }:
                    _fail("invalid-summary-input")
                for field in ("actual_sha256", "expected_sha256"):
                    value = item[field]
                    if not isinstance(value, str) or not HEX_64_PATTERN.fullmatch(value):
                        _fail("invalid-summary-input")
                if item["actual_sha256"] != item["expected_sha256"]:
                    _fail("invalid-summary-input")
        elif kind == "resources":
            expected_keys = base_keys | {"resource_sha256"}
            digest_fields = ("resource_sha256",)
        else:
            expected_keys = base_keys | {"decision_sha256", "row_count"}
            digest_fields = ("decision_sha256",)
            if document.get("row_count") != len(manifest["simulator_rows"]):
                _fail("invalid-summary-input")
        if set(document) != expected_keys:
            _fail("invalid-summary-input")
        for field in digest_fields:
            digest = document.get(field)
            if not isinstance(digest, str) or not HEX_64_PATTERN.fullmatch(digest):
                _fail("invalid-summary-input")
        capture_ids.add(capture_id)
        by_kind[kind] = document
    if set(by_kind) != expected_kinds or len(capture_ids) != 1:
        _fail("mixed-capture")
    hashes = {
        kind: hashlib.sha256(canonical_json_bytes(by_kind[kind])).hexdigest()
        for kind in sorted(by_kind)
    }
    return {
        "capture_id": next(iter(capture_ids)),
        "contract_id": manifest["contract_id"],
        "caller_identity": by_kind["bindings"]["operator_identity"],
        "grant_baseline_sha256": by_kind["policies"]["grant_baseline_sha256"],
        "input_sha256": hashes,
        "manifest_sha256": hashlib.sha256(canonical_json_bytes(manifest)).hexdigest(),
        "schema_version": SCHEMA_VERSION,
        "source_binding": _source_binding(manifest),
        "status": "PASS",
    }


def _print_summary(value: Mapping[str, Any]) -> None:
    sys.stdout.buffer.write(canonical_json_bytes(value) + b"\n")


class _SafeArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> NoReturn:
        del message
        _fail("invalid-cli-arguments")


def _parser() -> argparse.ArgumentParser:
    parser = _SafeArgumentParser(description="Validate filtered #81 Gate B evidence offline")
    subparsers = parser.add_subparsers(dest="mode", required=True)
    subparsers.add_parser("manifest")
    for mode in ("bindings", "policies", "resources", "simulator"):
        child = subparsers.add_parser(mode)
        child.add_argument("--input", required=True)
        child.add_argument("--bindings")
        child.add_argument("--output", required=True)
    summary = subparsers.add_parser("summary")
    for kind in ("bindings", "policies", "resources", "simulator"):
        summary.add_argument(f"--{kind}", required=True)
    summary.add_argument("--output", required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    try:
        raw_arguments = list(argv if argv is not None else sys.argv[1:])
        for option in (
            "--bindings",
            "--input",
            "--output",
            "--policies",
            "--resources",
            "--simulator",
        ):
            if raw_arguments.count(option) > 1:
                _fail("repeated-path-option")
        arguments = _parser().parse_args(raw_arguments)
        manifest = _load_manifest_file()
        manifest_result = validate_manifest(manifest)
        if arguments.mode == "manifest":
            _print_summary(manifest_result)
            return 0
        if arguments.mode == "summary":
            results = [
                _read_tmp_json(getattr(arguments, kind))
                for kind in ("bindings", "policies", "resources", "simulator")
            ]
            summary = build_final_summary(manifest, results)
            write_safe_summary(arguments.output, summary)
            _print_summary(summary)
            return 0

        document = _read_tmp_json(arguments.input)
        if arguments.mode == "bindings":
            if arguments.bindings is not None:
                _fail("unexpected-bindings-input")
            result = validate_bindings(document, manifest)
        else:
            if not arguments.bindings:
                _fail("missing-bindings-input")
            binding_document = _read_tmp_json(arguments.bindings)
            binding_result = validate_bindings(binding_document, manifest)
            if arguments.mode == "policies":
                result = validate_policy_bundle(document, manifest, binding_result)
            elif arguments.mode == "resources":
                result = validate_resource_bundle(
                    document, manifest, binding_document, binding_result
                )
            else:
                result = validate_simulator_bundle(
                    document, manifest, binding_document, binding_result
                )
        write_safe_summary(arguments.output, result)
        _print_summary(result)
        return 0
    except EvidenceError as exc:
        _print_summary({"error": exc.code, "status": "STOP"})
        return 2
    except Exception:
        _print_summary({"error": "internal-validation-error", "status": "STOP"})
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
