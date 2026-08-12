from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path
from urllib.parse import urlsplit

from ci.history import GitHubEvidenceClient, restore_fail_closed


def archive(name: str = "evidence.json") -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as output:
        output.writestr(name, '{"schema_version":1}\n')
    return buffer.getvalue()


def test_bounded_actions_history_restores_exact_success_artifact(tmp_path: Path) -> None:
    body = archive()

    def fetch_json(url, _headers):
        path = urlsplit(url).path
        if path.endswith("/runs"):
            return {
                "workflow_runs": [
                    {
                        "conclusion": "success",
                        "created_at": "2026-08-09T12:00:00Z",
                        "head_sha": "a" * 40,
                        "id": 10,
                        "run_attempt": 2,
                        "status": "completed",
                    }
                ]
            }
        return {
            "artifacts": [
                {
                    "archive_download_url": "https://api.github.invalid/artifact.zip",
                    "expired": False,
                    "name": "verification-evidence-10-attempt-2",
                    "size_in_bytes": len(body),
                }
            ]
        }

    client = GitHubEvidenceClient(
        api_url="https://api.github.invalid",
        repository="DataTalksClub/website",
        token="not-logged",
        fetch_json=fetch_json,
        fetch_bytes=lambda _url, _headers: body,
    )
    history = client.restore(workflow="ci.yml", current_run_id=11, output_directory=tmp_path)
    assert history[0]["id"] == 10
    assert (tmp_path / "run-10/evidence.json").is_file()


def test_history_failure_or_unsafe_archive_returns_empty_fail_closed(tmp_path: Path) -> None:
    unsafe = archive("../escape.json")

    def fetch_json(url, _headers):
        if urlsplit(url).path.endswith("/runs"):
            return {
                "workflow_runs": [
                    {
                        "conclusion": "success",
                        "created_at": "2026-08-09T12:00:00Z",
                        "head_sha": "a" * 40,
                        "id": 10,
                        "run_attempt": 1,
                        "status": "completed",
                    }
                ]
            }
        return {
            "artifacts": [
                {
                    "archive_download_url": "https://api.github.invalid/artifact.zip",
                    "expired": False,
                    "name": "verification-evidence-10-attempt-1",
                    "size_in_bytes": len(unsafe),
                }
            ]
        }

    client = GitHubEvidenceClient(
        api_url="https://api.github.invalid",
        repository="DataTalksClub/website",
        token="not-logged",
        fetch_json=fetch_json,
        fetch_bytes=lambda _url, _headers: unsafe,
    )
    history_path = tmp_path / "history.json"
    history, reason = restore_fail_closed(
        client=client,
        workflow="ci.yml",
        current_run_id=11,
        output_directory=tmp_path / "evidence",
        history_path=history_path,
    )
    assert history == []
    assert reason == "history_unavailable_rerun"
    assert json.loads(history_path.read_text(encoding="utf-8")) == []
    assert not (tmp_path / "escape.json").exists()
