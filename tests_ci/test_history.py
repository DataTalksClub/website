from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path
from urllib.parse import urlsplit

from ci.history import (
    GitHubEvidenceClient,
    restore_fail_closed,
    selection_report_fail_closed,
    selection_report_summary,
    summarize_selection_history,
)


def archive(name: str = "evidence.json") -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as output:
        output.writestr(name, '{"schema_version":1}\n')
    return buffer.getvalue()


def selection_archive(
    *,
    profile: str,
    reason: str,
    dispositions: dict[str, str],
    plan_profile: str | None = None,
    plan_reason: str | None = None,
) -> bytes:
    buffer = io.BytesIO()
    selection = {"profile": profile, "reason": reason}
    plan = {
        "components": {
            component: {"disposition": disposition}
            for component, disposition in dispositions.items()
        },
        "profile": plan_profile or profile,
        "reason": plan_reason or reason,
    }
    with zipfile.ZipFile(buffer, "w") as output:
        output.writestr("ci-selection.json", json.dumps(selection))
        output.writestr("verification-plan.json", json.dumps(plan))
    return buffer.getvalue()


def test_empty_selection_history_is_bounded_and_zeroed() -> None:
    report = summarize_selection_history([])

    assert report["status"] == "empty"
    assert report["window_limit"] == 20
    assert report["runs_observed"] == 0
    assert report["profile"]["counts"] == {"focused": 0, "full": 0}
    assert report["reason"]["counts"] == {}
    assert report["evidence_reuse"]["reuse_rate_percent"] == 0.0
    summary = selection_report_summary(report)
    assert "empty" in summary
    assert "run IDs" in summary


def test_normal_selection_history_reports_profile_reason_and_reuse_rates() -> None:
    report = summarize_selection_history(
        [
            {
                "dispositions": {
                    "django": "rerun",
                    "playwright": "reused",
                    "screenshots": "not_applicable",
                },
                "profile": "focused",
                "reason": "single_application",
            },
            {
                "dispositions": {
                    "django": "rerun",
                    "playwright": "rerun",
                    "screenshots": "not_applicable",
                },
                "profile": "full",
                "reason": "unknown_path",
            },
        ]
    )

    assert report["profile"]["counts"] == {"focused": 1, "full": 1}
    assert report["profile"]["rates_percent"] == {"focused": 50.0, "full": 50.0}
    assert report["reason"]["counts"] == {
        "single_application": 1,
        "unknown_path": 1,
    }
    assert report["evidence_reuse"]["component_counts"] == {
        "not_applicable": 2,
        "rerun": 3,
        "reused": 1,
    }
    assert report["evidence_reuse"]["reuse_rate_percent"] == 25.0
    assert report["evidence_reuse"]["run_rate_percent"] == 50.0


def test_selection_report_reads_at_most_twenty_main_push_runs() -> None:
    body_by_run = {
        10: selection_archive(
            profile="focused",
            reason="single_application",
            dispositions={"django": "reused"},
            plan_profile="documentation",
            plan_reason="documentation_only",
        ),
        11: selection_archive(
            profile="full", reason="unknown_path", dispositions={"django": "rerun"}
        ),
    }
    run_history = [
        {
            "conclusion": "success",
            "created_at": f"2026-08-09T12:{index:02d}:00Z",
            "head_sha": f"{index:040x}",
            "id": index,
            "run_attempt": 1,
            "status": "completed",
        }
        for index in range(1, 26)
    ]
    requests: list[str] = []

    def fetch_json(url, _headers):
        requests.append(url)
        path = urlsplit(url).path
        if path.endswith("/runs"):
            return {"workflow_runs": run_history}
        run_id = int(path.split("/runs/")[1].split("/")[0])
        body = body_by_run.get(run_id, body_by_run[10])
        return {
            "artifacts": [
                {
                    "archive_download_url": f"https://api.github.invalid/{run_id}.zip",
                    "expired": False,
                    "name": f"ci-selection-{run_id}-attempt-1",
                    "size_in_bytes": len(body),
                }
            ]
        }

    client = GitHubEvidenceClient(
        api_url="https://api.github.invalid",
        repository="DataTalksClub/website",
        token="not-logged",
        fetch_json=fetch_json,
        fetch_bytes=lambda url, _headers: body_by_run.get(
            int(url.rsplit("/", 1)[-1].removesuffix(".zip")), body_by_run[10]
        ),
    )
    report = client.selection_report(workflow="ci.yml", current_run_id=999)

    assert report["runs_considered"] == 20
    assert report["runs_observed"] == 20
    assert report["profile"]["counts"] == {"focused": 19, "full": 1}
    assert len(requests) == 21
    assert "per_page=20" in requests[0]
    assert "page=1" in requests[0]
    assert "event=push" in requests[0]


def test_normal_selection_report_writes_redacted_scheduled_output(tmp_path: Path) -> None:
    body = selection_archive(
        profile="focused",
        reason="single_application",
        dispositions={"django": "reused", "playwright": "rerun"},
    )

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
                    "name": "ci-selection-10-attempt-1",
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
    report_path = tmp_path / "selection-observability.json"
    summary_path = tmp_path / "summary.md"
    report, reason = selection_report_fail_closed(
        client=client,
        workflow="ci.yml",
        current_run_id=11,
        report_path=report_path,
        summary_path=summary_path,
    )

    assert reason == "history_restored"
    assert report["status"] == "complete"
    assert json.loads(report_path.read_text(encoding="utf-8"))["profile"]["counts"] == {
        "focused": 1,
        "full": 0,
    }
    summary = summary_path.read_text(encoding="utf-8")
    assert "Profiles:" in summary
    assert "single_application" in summary
    assert "artifact.zip" not in summary
    assert "a" * 40 not in summary


def test_malformed_selection_history_fails_closed_without_leaking_error_data(
    tmp_path: Path,
) -> None:
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
                    "name": "ci-selection-10-attempt-1",
                    "size_in_bytes": len(archive()),
                }
            ]
        }

    client = GitHubEvidenceClient(
        api_url="https://api.github.invalid",
        repository="DataTalksClub/website",
        token="not-logged",
        fetch_json=fetch_json,
        fetch_bytes=lambda _url, _headers: archive(),
    )
    report_path = tmp_path / "selection-report.json"
    summary_path = tmp_path / "summary.md"
    report, reason = selection_report_fail_closed(
        client=client,
        workflow="ci.yml",
        current_run_id=11,
        report_path=report_path,
        summary_path=summary_path,
    )

    assert reason == "history_unavailable"
    assert report["status"] == "unavailable"
    assert report["runs_observed"] == 0
    output = report_path.read_text(encoding="utf-8") + summary_path.read_text(encoding="utf-8")
    assert "artifact.zip" not in output
    assert "a" * 40 not in output


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
