from __future__ import annotations

import argparse
import io
import json
import re
import shutil
import stat
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from collections.abc import Callable, Mapping
from pathlib import Path, PurePosixPath
from typing import Any

from ci.evidence import MAX_EVIDENCE_BYTES

GITHUB_API_VERSION = "2026-03-10"
MAX_RUNS = 20
MAX_ARCHIVE_BYTES = 64 * 1024 * 1024
MAX_ARCHIVE_FILES = 1000
REPOSITORY_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
SHA_RE = re.compile(r"^[0-9a-f]{40}$")
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
            archive = self._download(artifact["archive_download_url"])
            _extract_archive(archive, output / f"run-{run['id']}")
        return runs

    def _runs(self, workflow: str, *, current_run_id: int) -> list[dict[str, Any]]:
        quoted = urllib.parse.quote(workflow, safe="")
        payload = self._get_json(
            f"/repos/{self.repository}/actions/workflows/{quoted}/runs",
            {
                "branch": "main",
                "event": "push" if workflow == "ci.yml" else "schedule",
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
        payload = self._get_json(
            f"/repos/{self.repository}/actions/runs/{run['id']}/artifacts",
            {"per_page": "100"},
        )
        if not isinstance(payload, dict) or not isinstance(payload.get("artifacts"), list):
            raise EvidenceHistoryError("artifact history response is malformed")
        expected_name = f"verification-evidence-{run['id']}-attempt-{run['run_attempt']}"
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

    def _download(self, url: str) -> bytes:
        try:
            body = self.fetch_bytes(url, self._headers())
        except (OSError, ValueError, urllib.error.URLError) as exc:
            raise EvidenceHistoryError("GitHub evidence artifact download failed") from exc
        if len(body) > MAX_ARCHIVE_BYTES:
            raise EvidenceHistoryError("GitHub evidence artifact is too large")
        return body

    def _headers(self) -> dict[str, str]:
        return {
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {self.token}",
            "User-Agent": "DataTalksClub-website-verification-evidence",
            "X-GitHub-Api-Version": GITHUB_API_VERSION,
        }


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
    parser.add_argument("--output-directory", required=True)
    parser.add_argument("--history", required=True)
    parser.add_argument("--github-output")
    args = parser.parse_args()
    client = GitHubEvidenceClient(
        api_url=args.api_url,
        repository=args.repository,
        token=args.token,
    )
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
