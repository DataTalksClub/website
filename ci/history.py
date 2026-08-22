from __future__ import annotations

import argparse
import io
import json
import math
import re
import shutil
import stat
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path, PurePosixPath
from typing import Any

from ci.evidence import MAX_EVIDENCE_BYTES

GITHUB_API_VERSION = "2026-03-10"
MAX_RUNS = 20
MAX_ARCHIVE_BYTES = 64 * 1024 * 1024
MAX_ARCHIVE_FILES = 1000
MAX_REPORT_ARCHIVE_BYTES = 8 * 1024 * 1024
MAX_REPORT_JSON_BYTES = 512 * 1024
MAX_REPORT_COMPONENTS = 64
SELECTION_REPORT_SCHEMA_VERSION = 1
REPOSITORY_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
SHA_RE = re.compile(r"^[0-9a-f]{40}$")
REPORT_REASON_RE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
REPORT_PROFILES = ("focused", "full")
PLAN_PROFILES = frozenset({"documentation", "focused", "full"})
REPORT_DISPOSITIONS = ("not_applicable", "rerun", "reused")
REPORT_STATUSES = frozenset({"complete", "empty", "unavailable"})
CONCLUSIONS = frozenset(
    {
        "action_required",
        "cancelled",
        "failure",
        "neutral",
        "skipped",
        "stale",
        "startup_failure",
        "success",
        "timed_out",
    }
)


class EvidenceHistoryError(ValueError):
    """GitHub evidence history cannot be consumed safely."""


def _percentage(numerator: int, denominator: int) -> float:
    if denominator <= 0:
        return 0.0
    return round(numerator * 100 / denominator, 2)


def _empty_selection_report(
    *, status: str, runs_considered: int = 0, runs_unavailable: int = 0
) -> dict[str, Any]:
    if status not in REPORT_STATUSES:
        raise EvidenceHistoryError("selection report status is unsupported")
    report = {
        "evidence_reuse": {
            "component_counts": {item: 0 for item in REPORT_DISPOSITIONS},
            "reuse_rate_percent": 0.0,
            "run_rate_percent": 0.0,
            "runs_with_reuse": 0,
        },
        "profile": {
            "counts": {item: 0 for item in REPORT_PROFILES},
            "rates_percent": {item: 0.0 for item in REPORT_PROFILES},
        },
        "reason": {"counts": {}, "rates_percent": {}},
        "runs_considered": runs_considered,
        "runs_observed": 0,
        "runs_unavailable": runs_unavailable,
        "schema_version": SELECTION_REPORT_SCHEMA_VERSION,
        "status": status,
        "window_limit": MAX_RUNS,
    }
    return validate_selection_report(report)


def summarize_selection_history(
    records: Sequence[object],
    *,
    status: str = "complete",
    runs_considered: int | None = None,
) -> dict[str, Any]:
    """Build a bounded, aggregate-only report from validated selection facts.

    ``records`` deliberately contains only profile, reason, and component dispositions. It is
    not a raw GitHub response and must not contain run identities, source paths, or artifacts.
    """
    if status not in REPORT_STATUSES:
        raise EvidenceHistoryError("selection report status is unsupported")
    if len(records) > MAX_RUNS:
        raise EvidenceHistoryError("selection history exceeds the bounded report window")
    considered = len(records) if runs_considered is None else runs_considered
    if (
        not isinstance(considered, int)
        or isinstance(considered, bool)
        or considered < 0
        or considered > MAX_RUNS
        or considered < len(records)
    ):
        raise EvidenceHistoryError("selection history count is invalid")
    if status == "empty" and records:
        raise EvidenceHistoryError("empty selection history contains records")
    if status == "complete" and considered != len(records):
        raise EvidenceHistoryError("complete selection history is missing records")
    if status == "unavailable":
        return _empty_selection_report(
            status=status,
            runs_considered=considered,
            runs_unavailable=considered,
        )

    profile_counts = {item: 0 for item in REPORT_PROFILES}
    reason_counts: dict[str, int] = {}
    component_counts = {item: 0 for item in REPORT_DISPOSITIONS}
    runs_with_reuse = 0
    for record in records:
        if not isinstance(record, Mapping):
            raise EvidenceHistoryError("selection history record is malformed")
        profile = record.get("profile")
        reason = record.get("reason")
        dispositions = record.get("dispositions")
        if profile not in REPORT_PROFILES:
            raise EvidenceHistoryError("selection history profile is malformed")
        if (
            not isinstance(reason, str)
            or not REPORT_REASON_RE.fullmatch(reason)
            or not isinstance(dispositions, Mapping)
            or len(dispositions) > MAX_REPORT_COMPONENTS
        ):
            raise EvidenceHistoryError("selection history record is malformed")
        profile_counts[profile] += 1
        reason_counts[reason] = reason_counts.get(reason, 0) + 1
        reused = False
        for component, disposition in dispositions.items():
            if not isinstance(component, str) or not component:
                raise EvidenceHistoryError("selection history component is malformed")
            if disposition not in REPORT_DISPOSITIONS:
                raise EvidenceHistoryError("selection history disposition is malformed")
            component_counts[disposition] += 1
            reused = reused or disposition == "reused"
        if reused:
            runs_with_reuse += 1

    observed = len(records)
    applicable_components = component_counts["reused"] + component_counts["rerun"]
    report = {
        "evidence_reuse": {
            "component_counts": component_counts,
            "reuse_rate_percent": _percentage(component_counts["reused"], applicable_components),
            "run_rate_percent": _percentage(runs_with_reuse, observed),
            "runs_with_reuse": runs_with_reuse,
        },
        "profile": {
            "counts": profile_counts,
            "rates_percent": {
                item: _percentage(profile_counts[item], observed) for item in REPORT_PROFILES
            },
        },
        "reason": {
            "counts": dict(sorted(reason_counts.items())),
            "rates_percent": {
                item: _percentage(count, observed) for item, count in sorted(reason_counts.items())
            },
        },
        "runs_considered": considered,
        "runs_observed": observed,
        "runs_unavailable": considered - observed,
        "schema_version": SELECTION_REPORT_SCHEMA_VERSION,
        "status": "empty" if not records else status,
        "window_limit": MAX_RUNS,
    }
    return validate_selection_report(report)


def validate_selection_report(payload: object) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise EvidenceHistoryError("selection report must be an object")
    expected = {
        "evidence_reuse",
        "profile",
        "reason",
        "runs_considered",
        "runs_observed",
        "runs_unavailable",
        "schema_version",
        "status",
        "window_limit",
    }
    if set(payload) != expected or payload["schema_version"] != SELECTION_REPORT_SCHEMA_VERSION:
        raise EvidenceHistoryError("selection report has an unsupported shape")
    if payload["status"] not in REPORT_STATUSES:
        raise EvidenceHistoryError("selection report status is invalid")
    window_limit = payload["window_limit"]
    if (
        not isinstance(window_limit, int)
        or isinstance(window_limit, bool)
        or not 1 <= window_limit <= MAX_RUNS
    ):
        raise EvidenceHistoryError("selection report window is invalid")
    considered = payload["runs_considered"]
    observed = payload["runs_observed"]
    unavailable = payload["runs_unavailable"]
    if (
        any(
            not isinstance(value, int) or isinstance(value, bool) or value < 0
            for value in (considered, observed, unavailable)
        )
        or observed > considered
        or unavailable != considered - observed
        or considered > window_limit
    ):
        raise EvidenceHistoryError("selection report run counts are invalid")
    if payload["status"] == "complete" and unavailable:
        raise EvidenceHistoryError("complete selection report has unavailable runs")
    if payload["status"] == "empty" and (considered or observed or unavailable):
        raise EvidenceHistoryError("empty selection report has run counts")

    def validate_rates(rates: object, counts: Mapping[str, int], denominator: int) -> None:
        if not isinstance(rates, dict) or set(rates) != set(counts):
            raise EvidenceHistoryError("selection report rates are malformed")
        for key, value in rates.items():
            if (
                not isinstance(value, (int, float))
                or isinstance(value, bool)
                or not math.isfinite(value)
                or not 0 <= value <= 100
                or value != _percentage(counts[key], denominator)
            ):
                raise EvidenceHistoryError("selection report rate is invalid")

    profile = payload["profile"]
    if not isinstance(profile, dict) or set(profile) != {"counts", "rates_percent"}:
        raise EvidenceHistoryError("selection report profile is malformed")
    profile_counts = profile["counts"]
    if (
        not isinstance(profile_counts, dict)
        or set(profile_counts) != set(REPORT_PROFILES)
        or any(
            not isinstance(value, int) or isinstance(value, bool) or value < 0 or value > observed
            for value in profile_counts.values()
        )
        or sum(profile_counts.values()) != observed
    ):
        raise EvidenceHistoryError("selection report profile counts are malformed")
    validate_rates(profile["rates_percent"], profile_counts, observed)

    reason = payload["reason"]
    if not isinstance(reason, dict) or set(reason) != {"counts", "rates_percent"}:
        raise EvidenceHistoryError("selection report reason is malformed")
    reason_counts = reason["counts"]
    if (
        not isinstance(reason_counts, dict)
        or len(reason_counts) > MAX_RUNS
        or any(
            not isinstance(key, str)
            or not REPORT_REASON_RE.fullmatch(key)
            or not isinstance(value, int)
            or isinstance(value, bool)
            or value < 0
            or value > observed
            for key, value in reason_counts.items()
        )
        or sum(reason_counts.values()) != observed
        or list(reason_counts) != sorted(reason_counts)
    ):
        raise EvidenceHistoryError("selection report reason counts are malformed")
    validate_rates(reason["rates_percent"], reason_counts, observed)

    reuse = payload["evidence_reuse"]
    if not isinstance(reuse, dict) or set(reuse) != {
        "component_counts",
        "reuse_rate_percent",
        "run_rate_percent",
        "runs_with_reuse",
    }:
        raise EvidenceHistoryError("selection report evidence reuse is malformed")
    component_counts = reuse["component_counts"]
    if (
        not isinstance(component_counts, dict)
        or set(component_counts) != set(REPORT_DISPOSITIONS)
        or any(
            not isinstance(value, int)
            or isinstance(value, bool)
            or value < 0
            or value > MAX_REPORT_COMPONENTS * max(observed, 1)
            for value in component_counts.values()
        )
    ):
        raise EvidenceHistoryError("selection report component counts are malformed")
    applicable = component_counts["reused"] + component_counts["rerun"]
    if reuse["reuse_rate_percent"] != _percentage(component_counts["reused"], applicable):
        raise EvidenceHistoryError("selection report reuse rate is invalid")
    runs_with_reuse = reuse["runs_with_reuse"]
    if (
        not isinstance(runs_with_reuse, int)
        or isinstance(runs_with_reuse, bool)
        or not 0 <= runs_with_reuse <= observed
        or reuse["run_rate_percent"] != _percentage(runs_with_reuse, observed)
    ):
        raise EvidenceHistoryError("selection report run reuse rate is invalid")
    return payload


def dump_selection_report(payload: object, path: str | Path) -> None:
    validated = validate_selection_report(payload)
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(
        json.dumps(validated, ensure_ascii=True, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def selection_report_summary(payload: object) -> str:
    report = validate_selection_report(payload)
    lines = [
        "## Selective-CI history summary",
        "",
        f"- History status: `{report['status']}`",
        f"- Bounded window: at most `{report['window_limit']}` completed main push runs",
        f"- Records observed: `{report['runs_observed']}` of `{report['runs_considered']}`",
    ]
    if report["status"] == "unavailable":
        lines.extend(
            (
                "- Selection rates: `unavailable` (history was not used to alter CI selection)",
                "- Evidence reuse rates: `unavailable`",
            )
        )
    else:
        profiles = report["profile"]
        profile_text = ", ".join(
            f"`{item}`={profiles['counts'][item]} ({profiles['rates_percent'][item]:.2f}%)"
            for item in REPORT_PROFILES
        )
        reasons = report["reason"]
        reason_text = (
            ", ".join(
                f"`{item}`={reasons['counts'][item]} ({reasons['rates_percent'][item]:.2f}%)"
                for item in reasons["counts"]
            )
            or "none"
        )
        reuse = report["evidence_reuse"]
        lines.extend(
            (
                f"- Profiles: {profile_text}",
                f"- Reasons: {reason_text}",
                "- Evidence reuse: "
                f"{reuse['component_counts']['reused']} reused of "
                f"{reuse['component_counts']['reused'] + reuse['component_counts']['rerun']} "
                f"applicable components ({reuse['reuse_rate_percent']:.2f}%); "
                f"{reuse['runs_with_reuse']} runs with reuse "
                f"({reuse['run_rate_percent']:.2f}%)",
            )
        )
    lines.extend(
        (
            "- Redaction: aggregate counts/rates only; no run IDs, SHAs, paths, logs, secrets, "
            "or production data are emitted.",
            "",
        )
    )
    return "\n".join(lines)


class GitHubEvidenceClient:
    def __init__(
        self,
        *,
        api_url: str,
        repository: str,
        token: str,
        fetch_json: Callable[[str, Mapping[str, str]], object] | None = None,
        fetch_bytes: Callable[[str, Mapping[str, str]], bytes] | None = None,
    ) -> None:
        if not REPOSITORY_RE.fullmatch(repository) or not token:
            raise EvidenceHistoryError("GitHub evidence history credentials are unavailable")
        self.api_url = api_url.rstrip("/")
        self.repository = repository
        self.token = token
        self.fetch_json = fetch_json or _fetch_json
        self.fetch_bytes = fetch_bytes or _fetch_bytes

    def restore(
        self,
        *,
        workflow: str,
        current_run_id: int,
        output_directory: str | Path,
    ) -> list[dict[str, Any]]:
        if workflow not in {"ci.yml", "scheduled-full-regression.yml"}:
            raise EvidenceHistoryError("workflow is not allowlisted for evidence reuse")
        output = Path(output_directory)
        output.mkdir(parents=True, exist_ok=True)
        runs = self._runs(workflow, current_run_id=current_run_id)
        for run in runs:
            if run["conclusion"] != "success":
                continue
            artifact = self._evidence_artifact(run)
            if artifact is None:
                continue
            archive = self._download(
                artifact["archive_download_url"],
                expected_size=artifact["size_in_bytes"],
            )
            _extract_archive(archive, output / f"run-{run['id']}")
        return runs

    def selection_report(
        self,
        *,
        workflow: str,
        current_run_id: int,
    ) -> dict[str, Any]:
        """Read only bounded selection artifacts for an aggregate observability report."""
        if workflow not in {"ci.yml", "scheduled-full-regression.yml"}:
            raise EvidenceHistoryError("workflow is not allowlisted for selection reporting")
        runs = self._runs(workflow, current_run_id=current_run_id)
        if not runs:
            return _empty_selection_report(status="empty")
        records: list[dict[str, Any]] = []
        for run in runs:
            artifact = self._selection_artifact(run)
            if artifact is None:
                raise EvidenceHistoryError("selection history artifact is unavailable")
            if artifact["size_in_bytes"] > MAX_REPORT_ARCHIVE_BYTES:
                raise EvidenceHistoryError("selection history artifact is too large")
            archive = self._download(
                artifact["archive_download_url"],
                expected_size=artifact["size_in_bytes"],
                max_bytes=MAX_REPORT_ARCHIVE_BYTES,
            )
            records.append(_selection_record_from_archive(archive))
        return summarize_selection_history(records, runs_considered=len(runs))

    def _runs(self, workflow: str, *, current_run_id: int) -> list[dict[str, Any]]:
        quoted = urllib.parse.quote(workflow, safe="")
        payload = self._get_json(
            f"/repos/{self.repository}/actions/workflows/{quoted}/runs",
            {
                "branch": "main",
                "event": "push" if workflow == "ci.yml" else "schedule",
                "page": "1",
                "per_page": str(MAX_RUNS),
                "status": "completed",
            },
        )
        if not isinstance(payload, dict) or not isinstance(payload.get("workflow_runs"), list):
            raise EvidenceHistoryError("workflow history response is malformed")
        result: list[dict[str, Any]] = []
        seen: set[int] = set()
        for raw in payload["workflow_runs"][:MAX_RUNS]:
            if not isinstance(raw, dict):
                raise EvidenceHistoryError("workflow run is malformed")
            run_id = raw.get("id")
            attempt = raw.get("run_attempt")
            conclusion = raw.get("conclusion")
            head_sha = raw.get("head_sha")
            created_at = raw.get("created_at")
            if run_id == current_run_id:
                continue
            if (
                not isinstance(run_id, int)
                or isinstance(run_id, bool)
                or run_id <= 0
                or run_id in seen
                or not isinstance(attempt, int)
                or isinstance(attempt, bool)
                or attempt <= 0
                or raw.get("status") != "completed"
                or conclusion not in CONCLUSIONS
                or not isinstance(head_sha, str)
                or not SHA_RE.fullmatch(head_sha)
                or not isinstance(created_at, str)
                or not created_at
            ):
                raise EvidenceHistoryError("workflow run fields are malformed")
            seen.add(run_id)
            result.append(
                {
                    "conclusion": conclusion,
                    "created_at": created_at,
                    "head_sha": head_sha,
                    "id": run_id,
                    "run_attempt": attempt,
                }
            )
        return result

    def _evidence_artifact(self, run: Mapping[str, Any]) -> dict[str, Any] | None:
        return self._artifact(
            run,
            f"verification-evidence-{run['id']}-attempt-{run['run_attempt']}",
        )

    def _selection_artifact(self, run: Mapping[str, Any]) -> dict[str, Any] | None:
        return self._artifact(
            run,
            f"ci-selection-{run['id']}-attempt-{run['run_attempt']}",
        )

    def _artifact(self, run: Mapping[str, Any], expected_name: str) -> dict[str, Any] | None:
        payload = self._get_json(
            f"/repos/{self.repository}/actions/runs/{run['id']}/artifacts",
            {"per_page": "100"},
        )
        if not isinstance(payload, dict) or not isinstance(payload.get("artifacts"), list):
            raise EvidenceHistoryError("artifact history response is malformed")
        matches = [
            item
            for item in payload["artifacts"]
            if isinstance(item, dict)
            and item.get("name") == expected_name
            and not item.get("expired")
        ]
        if len(matches) > 1:
            raise EvidenceHistoryError("evidence artifact identity is ambiguous")
        if not matches:
            return None
        artifact = matches[0]
        url = artifact.get("archive_download_url")
        size = artifact.get("size_in_bytes")
        if (
            not isinstance(url, str)
            or not url.startswith("https://")
            or not isinstance(size, int)
            or isinstance(size, bool)
            or not 0 <= size <= MAX_ARCHIVE_BYTES
        ):
            raise EvidenceHistoryError("evidence artifact metadata is malformed")
        return {"archive_download_url": url, "size_in_bytes": size}

    def _get_json(self, endpoint: str, query: Mapping[str, str]) -> object:
        url = f"{self.api_url}{endpoint}?{urllib.parse.urlencode(query)}"
        try:
            return self.fetch_json(url, self._headers())
        except (OSError, ValueError, urllib.error.URLError) as exc:
            raise EvidenceHistoryError("GitHub evidence history request failed") from exc

    def _download(
        self,
        url: str,
        *,
        expected_size: int | None = None,
        max_bytes: int = MAX_ARCHIVE_BYTES,
    ) -> bytes:
        try:
            body = self.fetch_bytes(url, self._headers())
        except (OSError, ValueError, urllib.error.URLError) as exc:
            raise EvidenceHistoryError("GitHub evidence artifact download failed") from exc
        if len(body) > max_bytes or (expected_size is not None and len(body) != expected_size):
            raise EvidenceHistoryError("GitHub evidence artifact size is invalid")
        return body

    def _headers(self) -> dict[str, str]:
        return {
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {self.token}",
            "User-Agent": "DataTalksClub-website-verification-evidence",
            "X-GitHub-Api-Version": GITHUB_API_VERSION,
        }


def _selection_record_from_archive(body: bytes) -> dict[str, Any]:
    payloads = _read_report_archive_json(body)
    plan = payloads.get("verification-plan.json")
    selection = payloads.get("ci-selection.json")
    if not isinstance(plan, dict):
        raise EvidenceHistoryError("selection history plan is missing or malformed")
    plan_profile = plan.get("profile")
    plan_reason = plan.get("reason")
    if plan_profile not in PLAN_PROFILES or (
        not isinstance(plan_reason, str) or not REPORT_REASON_RE.fullmatch(plan_reason)
    ):
        raise EvidenceHistoryError("selection history plan metadata is malformed")
    if not isinstance(selection, dict):
        raise EvidenceHistoryError("selection history selection is missing or malformed")
    profile = selection.get("profile")
    reason = selection.get("reason")
    if profile not in REPORT_PROFILES or (
        not isinstance(reason, str) or not REPORT_REASON_RE.fullmatch(reason)
    ):
        raise EvidenceHistoryError("selection history selection identity is malformed")

    components = plan.get("components")
    if not isinstance(components, dict) or len(components) > MAX_REPORT_COMPONENTS:
        raise EvidenceHistoryError("selection history plan components are malformed")
    dispositions: dict[str, str] = {}
    for component, item in components.items():
        if not isinstance(component, str) or not component or not isinstance(item, dict):
            raise EvidenceHistoryError("selection history plan component is malformed")
        disposition = item.get("disposition")
        if disposition not in REPORT_DISPOSITIONS:
            raise EvidenceHistoryError("selection history plan disposition is malformed")
        dispositions[component] = disposition
    return {"dispositions": dispositions, "profile": profile, "reason": reason}


def _read_report_archive_json(body: bytes) -> dict[str, object]:
    wanted = {"ci-selection.json", "verification-plan.json"}
    try:
        archive = zipfile.ZipFile(io.BytesIO(body))
    except (zipfile.BadZipFile, OSError) as exc:
        raise EvidenceHistoryError("selection history artifact is not a valid ZIP") from exc
    with archive:
        try:
            infos = archive.infolist()
        except (OSError, RuntimeError, zipfile.BadZipFile) as exc:
            raise EvidenceHistoryError("selection history artifact is not a valid ZIP") from exc
        if len(infos) > MAX_ARCHIVE_FILES:
            raise EvidenceHistoryError("selection history artifact contains too many files")
        total = 0
        payloads: dict[str, object] = {}
        for info in infos:
            path = PurePosixPath(info.filename)
            if (
                path.is_absolute()
                or not path.parts
                or any(part in {"", ".", ".."} for part in path.parts)
            ):
                raise EvidenceHistoryError("selection history artifact contains an unsafe path")
            mode = info.external_attr >> 16
            if stat.S_ISLNK(mode) or info.is_dir():
                if info.is_dir():
                    continue
                raise EvidenceHistoryError("selection history artifact cannot contain symlinks")
            total += info.file_size
            if info.file_size > MAX_REPORT_JSON_BYTES or total > MAX_REPORT_ARCHIVE_BYTES:
                raise EvidenceHistoryError("selection history artifact is too large")
            name = path.name
            if name not in wanted:
                continue
            if name in payloads:
                raise EvidenceHistoryError("selection history JSON identity is ambiguous")
            try:
                raw = archive.read(info)
                payloads[name] = json.loads(raw)
            except (
                KeyError,
                OSError,
                RuntimeError,
                UnicodeError,
                json.JSONDecodeError,
                zipfile.BadZipFile,
            ) as exc:
                raise EvidenceHistoryError("selection history JSON is malformed") from exc
    return payloads


def selection_report_fail_closed(
    *,
    client: GitHubEvidenceClient,
    workflow: str,
    current_run_id: int,
    report_path: str | Path | None = None,
    summary_path: str | Path | None = None,
) -> tuple[dict[str, Any], str]:
    try:
        report = client.selection_report(
            workflow=workflow,
            current_run_id=current_run_id,
        )
        reason = "history_empty" if report["status"] == "empty" else "history_restored"
    except EvidenceHistoryError:
        report = _empty_selection_report(status="unavailable")
        reason = "history_unavailable"
    if report_path is not None:
        dump_selection_report(report, report_path)
    if summary_path is not None:
        destination = Path(summary_path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        with destination.open("a", encoding="utf-8") as output:
            output.write(selection_report_summary(report))
            output.write("\n")
    return report, reason


def restore_fail_closed(
    *,
    client: GitHubEvidenceClient,
    workflow: str,
    current_run_id: int,
    output_directory: str | Path,
    history_path: str | Path,
) -> tuple[list[dict[str, Any]], str]:
    try:
        history = client.restore(
            workflow=workflow,
            current_run_id=current_run_id,
            output_directory=output_directory,
        )
        reason = "history_restored"
    except EvidenceHistoryError:
        history = []
        reason = "history_unavailable_rerun"
    destination = Path(history_path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(
        json.dumps(history, ensure_ascii=True, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return history, reason


def _extract_archive(body: bytes, destination: Path) -> None:
    try:
        archive = zipfile.ZipFile(io.BytesIO(body))
    except (zipfile.BadZipFile, OSError) as exc:
        raise EvidenceHistoryError("evidence artifact is not a valid ZIP") from exc
    infos = archive.infolist()
    if len(infos) > MAX_ARCHIVE_FILES:
        raise EvidenceHistoryError("evidence artifact contains too many files")
    total = 0
    prepared: list[tuple[zipfile.ZipInfo, Path]] = []
    root = destination.resolve()
    for info in infos:
        path = PurePosixPath(info.filename)
        if (
            path.is_absolute()
            or not path.parts
            or any(part in {"", ".", ".."} for part in path.parts)
        ):
            raise EvidenceHistoryError("evidence artifact contains an unsafe path")
        mode = info.external_attr >> 16
        if stat.S_ISLNK(mode) or info.is_dir():
            if info.is_dir():
                continue
            raise EvidenceHistoryError("evidence artifact cannot contain symlinks")
        total += info.file_size
        if info.file_size > MAX_EVIDENCE_BYTES or total > MAX_ARCHIVE_BYTES:
            raise EvidenceHistoryError("evidence artifact expanded size is too large")
        target = (root / Path(*path.parts)).resolve()
        try:
            target.relative_to(root)
        except ValueError as exc:
            raise EvidenceHistoryError("evidence artifact escapes its destination") from exc
        prepared.append((info, target))
    if destination.exists():
        shutil.rmtree(destination)
    destination.mkdir(parents=True)
    for info, target in prepared:
        target.parent.mkdir(parents=True, exist_ok=True)
        with archive.open(info) as source, target.open("wb") as output:
            shutil.copyfileobj(source, output)


def _fetch_json(url: str, headers: Mapping[str, str]) -> object:
    return json.loads(_fetch_bytes(url, headers))


def _fetch_bytes(url: str, headers: Mapping[str, str]) -> bytes:
    request = urllib.request.Request(url, headers=dict(headers))
    with urllib.request.urlopen(request, timeout=30) as response:  # noqa: S310
        if response.status != 200:
            raise EvidenceHistoryError("GitHub API returned a non-success response")
        body = response.read(MAX_ARCHIVE_BYTES + 1)
    if len(body) > MAX_ARCHIVE_BYTES:
        raise EvidenceHistoryError("GitHub API response is too large")
    return body


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--api-url", default="https://api.github.com")
    parser.add_argument("--repository", required=True)
    parser.add_argument("--token", required=True)
    parser.add_argument("--workflow", default="ci.yml")
    parser.add_argument("--current-run-id", type=int, required=True)
    parser.add_argument("--output-directory")
    parser.add_argument("--history")
    parser.add_argument("--report")
    parser.add_argument("--summary")
    parser.add_argument("--github-output")
    args = parser.parse_args()
    client = GitHubEvidenceClient(
        api_url=args.api_url,
        repository=args.repository,
        token=args.token,
    )
    if args.report:
        _report, reason = selection_report_fail_closed(
            client=client,
            workflow=args.workflow,
            current_run_id=args.current_run_id,
            report_path=args.report,
            summary_path=args.summary,
        )
        if args.github_output:
            with Path(args.github_output).open("a", encoding="utf-8") as output:
                output.write(f"selection_history_reason={reason}\n")
        return
    if args.summary:
        parser.error("--summary requires --report")
    if not args.output_directory or not args.history:
        parser.error("--output-directory and --history are required without --report")
    _history, reason = restore_fail_closed(
        client=client,
        workflow=args.workflow,
        current_run_id=args.current_run_id,
        output_directory=args.output_directory,
        history_path=args.history,
    )
    if args.github_output:
        with Path(args.github_output).open("a", encoding="utf-8") as output:
            output.write(f"reason={reason}\n")


if __name__ == "__main__":
    main()
