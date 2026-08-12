from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import urlsplit

import yaml

from ci.ownership import load_graph, sha256_json
from ci.verification import dump_json, load_plan

SCHEMA_VERSION = 1
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


class ContentInvariantError(ValueError):
    """A changed structured content artifact cannot prove its invariants."""


def build_invariant_artifact(*, repository: str | Path, plan: Mapping[str, Any]) -> dict[str, Any]:
    repository = Path(repository).resolve()
    paths = plan["large_content"]["paths"]
    rules = load_graph()["large_content"]
    files = [_file_invariants(repository, path, rules) for path in paths]
    payload = {
        "files": files,
        "input_sha256": plan["large_content"]["sha256"],
        "path_count": len(files),
        "record_count": sum(item["record_count"] for item in files),
        "schema_version": SCHEMA_VERSION,
        "status": "pass",
    }
    payload["invariant_sha256"] = sha256_json(payload)
    return validate_invariant_artifact(payload, plan=plan)


def validate_invariant_artifact(payload: object, *, plan: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(payload, dict) or set(payload) != {
        "files",
        "input_sha256",
        "invariant_sha256",
        "path_count",
        "record_count",
        "schema_version",
        "status",
    }:
        raise ContentInvariantError("content invariant artifact has an invalid shape")
    if payload["schema_version"] != SCHEMA_VERSION or payload["status"] != "pass":
        raise ContentInvariantError("content invariant artifact is not a passing supported record")
    identity = dict(payload)
    digest = identity.pop("invariant_sha256")
    if not isinstance(digest, str) or digest != sha256_json(identity):
        raise ContentInvariantError("content invariant artifact digest does not match")
    if payload["input_sha256"] != plan["large_content"]["sha256"]:
        raise ContentInvariantError("content invariant inputs do not match the plan")
    files = payload["files"]
    if not isinstance(files, list) or payload["path_count"] != len(files):
        raise ContentInvariantError("content invariant file count does not match")
    if [item.get("path") for item in files] != plan["large_content"]["paths"]:
        raise ContentInvariantError("content invariant paths do not match the plan")
    if payload["record_count"] != sum(item.get("record_count", -1) for item in files):
        raise ContentInvariantError("content invariant record count does not match")
    for item in files:
        _validate_file_invariants(item)
    return payload


def _file_invariants(
    repository: Path, relative_path: str, rules: Mapping[str, Sequence[str]]
) -> dict[str, Any]:
    path = PurePosixPath(relative_path)
    if path.is_absolute() or not path.parts or any(part in {"", ".", ".."} for part in path.parts):
        raise ContentInvariantError("content invariant path is unsafe")
    source = (repository / Path(*path.parts)).resolve()
    try:
        source.relative_to(repository)
    except ValueError as exc:
        raise ContentInvariantError("content invariant path escapes the repository") from exc
    if not source.is_file() or source.is_symlink():
        raise ContentInvariantError("changed structured content file is missing")
    body = source.read_bytes()
    # Use the repository-relative path for synthetic control records; the absolute filesystem
    # path is an implementation detail and is not a valid public URL.
    records = _records(Path(*path.parts), body)
    if not records:
        raise ContentInvariantError("structured content must contain at least one record")
    identities: list[str] = []
    identity_fields: list[str] = []
    invalid_urls: list[str] = []
    canonical_urls: list[str] = []
    records_with_url = 0
    metadata_complete = 0
    metadata_identities: list[str] = []
    for record in records:
        if not isinstance(record, dict):
            raise ContentInvariantError("structured content records must be objects")
        identity_field, identity = _identity(record, rules["identity_fields"])
        identity_fields.append(identity_field)
        identities.append(identity)
        metadata = {
            field: record[field]
            for field in rules["metadata_fields"]
            if field in record and isinstance(record[field], str) and record[field].strip()
        }
        metadata_identities.append(sha256_json(metadata))
        if metadata:
            metadata_complete += 1
        record_has_url = False
        for field in rules["url_fields"]:
            if field not in record:
                continue
            value = record[field]
            if not isinstance(value, str) or (canonical := _canonical_url(value)) is None:
                invalid_urls.append(f"{identity}:{field}")
            else:
                canonical_urls.append(canonical)
                record_has_url = True
        records_with_url += int(record_has_url)
        if not record_has_url:
            raise ContentInvariantError("every structured content record requires a canonical URL")
    if len(set(identities)) != len(identities):
        raise ContentInvariantError("structured content identities are not unique")
    if invalid_urls:
        raise ContentInvariantError("structured content contains invalid URLs")
    if records and metadata_complete != len(records):
        raise ContentInvariantError("structured content metadata is incomplete")
    return {
        "byte_sha256": hashlib.sha256(body).hexdigest(),
        "canonical_url_order_sha256": sha256_json(canonical_urls),
        "identity_fields": identity_fields,
        "identity_order_sha256": sha256_json(identities),
        "identity_unique": True,
        "invalid_urls": invalid_urls,
        "metadata_complete_count": metadata_complete,
        "metadata_order_sha256": sha256_json(metadata_identities),
        "metadata_total_count": len(records),
        "path": relative_path,
        "record_count": len(records),
        "records_with_url": records_with_url,
        "url_complete_count": records_with_url,
        "url_total_count": len(records),
    }


def _validate_file_invariants(value: object) -> None:
    expected = {
        "byte_sha256",
        "canonical_url_order_sha256",
        "identity_fields",
        "identity_order_sha256",
        "identity_unique",
        "invalid_urls",
        "metadata_complete_count",
        "metadata_order_sha256",
        "metadata_total_count",
        "path",
        "record_count",
        "records_with_url",
        "url_complete_count",
        "url_total_count",
    }
    if not isinstance(value, dict) or set(value) != expected:
        raise ContentInvariantError("content invariant file result has an invalid shape")
    path = value["path"]
    if not isinstance(path, str):
        raise ContentInvariantError("content invariant file path is invalid")
    pure_path = PurePosixPath(path)
    if (
        pure_path.is_absolute()
        or not pure_path.parts
        or any(part in {"", ".", ".."} for part in pure_path.parts)
    ):
        raise ContentInvariantError("content invariant file path is unsafe")
    if any(
        not isinstance(value[field], str) or not SHA256_RE.fullmatch(value[field])
        for field in (
            "byte_sha256",
            "canonical_url_order_sha256",
            "identity_order_sha256",
            "metadata_order_sha256",
        )
    ):
        raise ContentInvariantError("content invariant file digest is invalid")
    record_count = value["record_count"]
    counts = (
        value["metadata_complete_count"],
        value["metadata_total_count"],
        value["records_with_url"],
        value["url_complete_count"],
        value["url_total_count"],
    )
    if (
        not isinstance(record_count, int)
        or isinstance(record_count, bool)
        or record_count < 0
        or any(not isinstance(item, int) or isinstance(item, bool) or item < 0 for item in counts)
    ):
        raise ContentInvariantError("content invariant counts are invalid")
    if (
        value["identity_unique"] is not True
        or value["metadata_complete_count"] != record_count
        or value["metadata_total_count"] != record_count
        or value["url_complete_count"] != record_count
        or value["url_total_count"] != record_count
        or value["records_with_url"] != record_count
        or value["invalid_urls"] != []
    ):
        raise ContentInvariantError("content invariant completeness checks did not pass")
    identity_fields = value["identity_fields"]
    if (
        not isinstance(identity_fields, list)
        or len(identity_fields) != record_count
        or any(not isinstance(item, str) or not item for item in identity_fields)
    ):
        raise ContentInvariantError("content invariant identities are invalid")


_RECORD_COLLECTION_KEYS = ("records", "items", "pages", "courses", "aliases", "finals")


def _records(path: Path, body: bytes) -> list[dict[str, Any]]:
    try:
        text = body.decode("utf-8")
    except UnicodeError as exc:
        raise ContentInvariantError("structured content must be UTF-8") from exc
    suffix = path.suffix.lower()
    try:
        if suffix == ".csv":
            return [dict(item) for item in csv.DictReader(text.splitlines())]
        if suffix == ".jsonl":
            return [json.loads(line) for line in text.splitlines() if line.strip()]
        parsed = json.loads(text) if suffix == ".json" else yaml.safe_load(text)
    except (csv.Error, json.JSONDecodeError, yaml.YAMLError) as exc:
        raise ContentInvariantError("structured content cannot be parsed") from exc
    if isinstance(parsed, list):
        return parsed
    if isinstance(parsed, dict):
        for collection_key in _RECORD_COLLECTION_KEYS:
            collection = parsed.get(collection_key)
            if isinstance(collection, list) and all(isinstance(item, dict) for item in collection):
                return [dict(item) for item in collection]
        if parsed and all(isinstance(value, dict) for value in parsed.values()):
            return [dict(value) | {"key": str(key)} for key, value in parsed.items()]
        # Control-plane JSON/YAML artifacts (manifests, source bindings, and count indexes)
        # are still covered by the invariant gate. They do not contain a record collection,
        # so bind one synthetic record to the file itself instead of weakening the gate or
        # requiring product-facing metadata solely for CI bookkeeping.
        return [
            {
                "key": path.as_posix(),
                "title": path.name,
                "path": f"/{path.as_posix()}",
            }
        ]
    raise ContentInvariantError("structured content must contain records")


def _identity(record: Mapping[str, Any], fields: Sequence[str]) -> tuple[str, str]:
    for field in fields:
        value = record.get(field)
        if isinstance(value, (str, int)) and not isinstance(value, bool) and str(value).strip():
            return field, str(value)
    raise ContentInvariantError("structured content record has no declared stable identity")


def _canonical_url(value: str) -> str | None:
    if not value or any(character in value for character in "\r\n\t"):
        return None
    try:
        parsed = urlsplit(value)
    except ValueError:
        return None
    if value.startswith("/") and not value.startswith("//"):
        return parsed.geturl()
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
    ):
        return None
    hostname = parsed.hostname.lower()
    try:
        port_value = parsed.port
    except ValueError:
        return None
    port = f":{port_value}" if port_value is not None else ""
    return parsed._replace(scheme=parsed.scheme.lower(), netloc=f"{hostname}{port}").geturl()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repository", type=Path, default=Path.cwd())
    parser.add_argument("--plan", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    plan = load_plan(args.plan)
    if not plan["large_content"]["impact"]:
        raise SystemExit("verification plan has no large-content impact")
    dump_json(build_invariant_artifact(repository=args.repository, plan=plan), args.output)


if __name__ == "__main__":
    main()
