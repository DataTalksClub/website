from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import zipfile
from pathlib import Path
from unittest.mock import patch

import pytest
from django.core.mail import EmailMessage

from test_support.email_backend import SyntheticCaptureEmailBackend, reset_capture_mailbox
from test_support.messaging import (
    CaptureMailbox,
    CaptureSafetyError,
    SimulatedOutcome,
    artifact_canaries,
    owned_publication_roots,
    scan_artifacts,
)
from test_support.network import (
    ExternalNetworkDenied,
    NetworkGuard,
    _assert_guarded_subprocess,
)
from test_support.provenance import (
    FixtureProvenanceError,
    validate_private_fixture,
    validate_public_fixture,
)
from test_support.safety import (
    TestSafetyError,
    authorize_from_environment,
    django_test_safety,
    django_test_safety_marker,
)

TEST_SUPPORT_ROOT = Path(__file__).resolve().parents[1]
PUBLIC_FIXTURE = TEST_SUPPORT_ROOT / "fixtures" / "public" / "crawler-index-v1.html"
PUBLIC_PROVENANCE = TEST_SUPPORT_ROOT / "fixtures" / "public" / "crawler-index-v1.provenance.json"
PRIVATE_FIXTURE = TEST_SUPPORT_ROOT / "fixtures" / "private" / "provider-cases-v1.json"
TAILWIND_FIXTURE = TEST_SUPPORT_ROOT / "fixtures" / "public" / "tailwindcss-3.4.17.js"
TAILWIND_PROVENANCE = (
    TEST_SUPPORT_ROOT / "fixtures" / "public" / "tailwindcss-3.4.17.provenance.json"
)


def test_public_fixture_has_exact_offline_provenance() -> None:
    provenance = validate_public_fixture(PUBLIC_FIXTURE, PUBLIC_PROVENANCE)
    assert provenance.commit == "5b6a8418f105f7cbdf7d3821d5a3c4f0a4f4c613"
    assert provenance.source_path == "compatibility/tests/fixtures/crawler/index.html"
    tailwind = validate_public_fixture(TAILWIND_FIXTURE, TAILWIND_PROVENANCE)
    assert tailwind.commit == "4f9f603e12b51cc53b8a09c7739b8f88c8eb87eb"
    assert tailwind.source_kind == "release_build"
    assert tailwind.artifact_url == "https://cdn.tailwindcss.com/3.4.17"
    assert tailwind.byte_sha256 == (
        "176e894661aa9cdc9a5cba6c720044cbbf7b8bd80d1c9a142a7c24b1b6c50d15"
    )


def test_deployed_missing_page_expectation_is_exact_and_cdn_masking_is_removed() -> None:
    from conftest import (
        OFFLINE_ROUTE_FIXTURES,
        _expected_local_response,
    )

    test_name = "test_deployed_public_and_studio_html_are_exact_and_read_only"
    assert OFFLINE_ROUTE_FIXTURES == {}
    assert _expected_local_response(test_name, 404, "/__dtc_deployed_smoke_missing__")
    assert not _expected_local_response(test_name, 200, "/__dtc_deployed_smoke_missing__")
    assert not _expected_local_response(test_name, 404, "/arbitrary-missing")
    assert not _expected_local_response("another_test", 404, "/__dtc_deployed_smoke_missing__")


def test_public_provenance_rejects_changed_missing_extra_malformed_and_traversal(
    tmp_path: Path,
) -> None:
    fixture = tmp_path / "fixture.html"
    provenance = tmp_path / "fixture.provenance.json"
    fixture.write_bytes(PUBLIC_FIXTURE.read_bytes())
    original = json.loads(PUBLIC_PROVENANCE.read_text(encoding="utf-8"))

    cases = (
        {**original, "byte_sha256": "0" * 64},
        {key: value for key, value in original.items() if key != "commit"},
        {**original, "extra": True},
        {**original, "commit": "not-a-revision"},
        {**original, "source_path": "../private.csv"},
        {**original, "artifact_url": "https://raw.githubusercontent.com/unversioned.html"},
    )
    for index, payload in enumerate(cases):
        provenance.write_text(json.dumps(payload), encoding="utf-8")
        with pytest.raises(FixtureProvenanceError):
            validate_public_fixture(fixture, provenance)
        assert index >= 0

    provenance.write_text("not-json", encoding="utf-8")
    with pytest.raises(FixtureProvenanceError):
        validate_public_fixture(fixture, provenance)
    provenance.unlink()
    with pytest.raises(FixtureProvenanceError):
        validate_public_fixture(fixture, provenance)


def test_public_provenance_cannot_match_bytes_while_naming_a_different_source(
    tmp_path: Path,
) -> None:
    fixture = tmp_path / "fixture.html"
    provenance = tmp_path / "fixture.provenance.json"
    fixture.write_bytes(PUBLIC_FIXTURE.read_bytes())
    payload = json.loads(PUBLIC_PROVENANCE.read_text(encoding="utf-8"))
    payload["source_path"] = "compatibility/tests/fixtures/crawler/different.html"
    provenance.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(FixtureProvenanceError, match="exact commit and source path"):
        validate_public_fixture(fixture, provenance)


def test_public_provenance_rejects_symlinks(tmp_path: Path) -> None:
    fixture = tmp_path / "fixture.html"
    fixture.symlink_to(PUBLIC_FIXTURE)
    provenance = tmp_path / "fixture.provenance.json"
    provenance.write_bytes(PUBLIC_PROVENANCE.read_bytes())
    with pytest.raises(FixtureProvenanceError, match="symlink"):
        validate_public_fixture(fixture, provenance)
    fixture.unlink()


def test_private_fixture_is_synthetic_and_rejects_historical_identity_fields(
    tmp_path: Path,
) -> None:
    fixture = validate_private_fixture(PRIVATE_FIXTURE)
    assert fixture["generator_version"] == "synthetic-provider-cases-v1"
    unsafe = tmp_path / "unsafe.json"
    unsafe.write_text(
        json.dumps(
            {
                "generator_version": "synthetic-v1",
                "records": [{"attendee": "private"}],
                "schema_family": "provider-neutral-v1",
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(FixtureProvenanceError, match="forbidden"):
        validate_private_fixture(unsafe)


def test_private_fixture_rejects_every_prohibited_field_recursively(tmp_path: Path) -> None:
    prohibited = (
        "answer",
        "email",
        "event_name",
        "filename",
        "name",
        "payload",
        "provider_digest",
        "provider_id",
        "source_digest",
        "token",
    )
    for field in prohibited:
        unsafe = tmp_path / f"unsafe-{field}.json"
        unsafe.write_text(
            json.dumps(
                {
                    "generator_version": "synthetic-v1",
                    "records": [{"nested": {field: "synthetic-private-value"}}],
                    "schema_family": "provider-neutral-v1",
                }
            ),
            encoding="utf-8",
        )
        with pytest.raises(FixtureProvenanceError, match="forbidden"):
            validate_private_fixture(unsafe)

    unsafe_email = tmp_path / "unsafe-email-value.json"
    unsafe_email.write_text(
        json.dumps(
            {
                "generator_version": "synthetic-v1",
                "records": [{"nested": {"value": "synthetic@example.invalid"}}],
                "schema_family": "provider-neutral-v1",
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(FixtureProvenanceError, match="forbidden"):
        validate_private_fixture(unsafe_email)


@pytest.mark.parametrize(
    "field",
    (
        "attendee_name",
        "attendee-name",
        "attendeeName",
        "providerId",
        "provider-ID",
        "providerIdentifier",
        "sourceDigest",
        "source-path",
        "registrationRow",
        "eventName",
    ),
)
def test_private_fixture_rejects_compound_field_styles_at_any_depth(
    tmp_path: Path,
    field: str,
) -> None:
    unsafe = tmp_path / "unsafe-compound.json"
    unsafe.write_text(
        json.dumps(
            {
                "generator_version": "synthetic-v1",
                "records": [{"safe": [{"nested": {field: "synthetic"}}]}],
                "schema_family": "provider-neutral-v1",
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(FixtureProvenanceError, match="forbidden"):
        validate_private_fixture(unsafe)


def test_capture_mailbox_is_reserved_dry_run_and_simulates_every_outcome() -> None:
    mailbox = CaptureMailbox()
    for outcome in SimulatedOutcome:
        message = mailbox.send(
            purpose=f"synthetic-{outcome.value}",
            recipient=f"synthetic-{outcome.value}@example.invalid",
            subject="Synthetic subject",
            body="Synthetic body",
            outcome=outcome,
        )
        assert message.redacted_metadata()["recipient"] == "[REDACTED]"
        assert "@" not in repr(message)
    with pytest.raises(CaptureSafetyError, match="reserved synthetic"):
        mailbox.send(
            purpose="unsafe",
            recipient="uncontrolled@example.com",
            subject="Unsafe",
            body="Unsafe",
        )


def test_django_email_backend_enforces_reserved_recipient_at_send_boundary() -> None:
    reset_capture_mailbox()
    accepted = EmailMessage(
        "Synthetic subject",
        "Synthetic body",
        "noreply@example.invalid",
        ["synthetic-recipient@example.invalid"],
    )
    assert accepted.send() == 1
    captured = SyntheticCaptureEmailBackend.mailbox.messages
    assert len(captured) == 1
    assert captured[0].recipient == "[REDACTED]"
    assert "synthetic-recipient" not in repr(captured[0])

    rejected = EmailMessage(
        "Synthetic subject",
        "Synthetic body",
        "noreply@example.invalid",
        ["qa-uncontrolled@example.com"],
    )
    with pytest.raises(CaptureSafetyError, match="reserved synthetic"):
        rejected.send()
    assert len(captured) == 1


def test_artifact_scan_fails_closed_for_every_protected_canary(tmp_path: Path) -> None:
    canaries = (
        "person@example.com",
        "token-canary",
        "credential-canary",
        "private-profile-canary",
        "registration-canary",
        "attendee-canary",
        "provider-payload-canary",
    )
    safe = tmp_path / "safe.json"
    safe.write_text('{"recipient":"[REDACTED]"}', encoding="utf-8")
    assert scan_artifacts(tmp_path, canaries=canaries) == ()
    for index, canary in enumerate(canaries):
        unsafe = tmp_path / f"unsafe-{index}.trace"
        unsafe.write_text(canary, encoding="utf-8")
        with pytest.raises(CaptureSafetyError, match="protected canary"):
            scan_artifacts(tmp_path, canaries=canaries)
        unsafe.unlink()


def test_artifact_scan_checks_compressed_playwright_trace_members(tmp_path: Path) -> None:
    trace = tmp_path / "synthetic.trace.zip"
    with zipfile.ZipFile(trace, "w") as archive:
        archive.writestr("trace.network", "synthetic-token-canary")
    with pytest.raises(CaptureSafetyError, match="protected canary"):
        scan_artifacts(tmp_path, canaries=artifact_canaries())
    trace.unlink()


def test_artifact_scan_rejects_any_email_shape_in_plain_and_compressed_files(
    tmp_path: Path,
) -> None:
    plain = tmp_path / "screenshot-handoff.txt"
    plain.write_text("new-synthetic-value@example.invalid", encoding="utf-8")
    with pytest.raises(CaptureSafetyError, match="protected"):
        scan_artifacts(tmp_path, canaries=())
    plain.unlink()

    trace = tmp_path / "trace.zip"
    with zipfile.ZipFile(trace, "w") as archive:
        archive.writestr("network", "another-synthetic-value@example.test")
    with pytest.raises(CaptureSafetyError, match="protected"):
        scan_artifacts(tmp_path, canaries=())


def test_publication_roots_include_runtime_and_screenshot_handoff(tmp_path: Path) -> None:
    repository = tmp_path / "repository"
    artifacts = repository / ".tmp" / "tests" / "run" / "artifacts"
    screenshots = repository / ".tmp" / "screenshots" / "issue-75"
    artifacts.mkdir(parents=True)
    screenshots.mkdir(parents=True)
    assert owned_publication_roots(repository, artifacts) == (
        artifacts,
        repository / ".tmp" / "screenshots",
    )


def test_network_guard_denies_dns_http_clients_and_network_subprocesses() -> None:
    with NetworkGuard():
        with pytest.raises(ExternalNetworkDenied, match="DNS"):
            socket.getaddrinfo("github.com", 443)
        with pytest.raises(ExternalNetworkDenied, match="subprocess"):
            subprocess.run(["curl", "https://github.com"], check=False)


def test_network_guard_denies_udp_and_every_dns_entry_point_before_system_use() -> None:
    reached: list[object] = []

    def fake_sendto(_socket: socket.socket, _payload: bytes, address: object) -> int:
        reached.append(address)
        return 1

    with patch.object(socket.socket, "sendto", fake_sendto):
        with NetworkGuard():
            udp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            try:
                with pytest.raises(ExternalNetworkDenied, match="socket send"):
                    udp.sendto(b"synthetic", ("203.0.113.1", 9))
            finally:
                udp.close()
    assert reached == []

    dns_calls = (
        ("gethostbyname", ("github.com",)),
        ("gethostbyname_ex", ("github.com",)),
        ("gethostbyaddr", ("203.0.113.1",)),
        ("getnameinfo", (("203.0.113.1", 443), 0)),
    )
    for name, args in dns_calls:
        with patch.object(socket, name, return_value="system-result"):
            with NetworkGuard():
                with pytest.raises(ExternalNetworkDenied, match="DNS"):
                    getattr(socket, name)(*args)


def test_python_subprocess_inherits_network_denial() -> None:
    script = "import socket; socket.create_connection(('203.0.113.1', 443), timeout=0.01)"
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=Path(__file__).resolve().parents[2],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode != 0
    assert "denied by test runtime" in result.stderr


def test_subprocess_injects_guard_into_isolated_environment_and_rejects_override() -> None:
    script = "import os; print(os.environ['DTC_TEST_NETWORK_DENY'])"
    with NetworkGuard():
        injected = subprocess.run(
            [sys.executable, "-c", script],
            cwd=Path(__file__).resolve().parents[2],
            env={"PATH": os.environ["PATH"]},
            capture_output=True,
            text=True,
            check=False,
        )
        with pytest.raises(ExternalNetworkDenied, match="cannot remove"):
            subprocess.run(
                [sys.executable, "-c", script],
                env={**os.environ, "DTC_TEST_NETWORK_DENY": "0"},
                check=False,
            )
    assert injected.returncode == 0
    assert injected.stdout.strip() == "1"


@pytest.mark.parametrize(
    "argv",
    [
        ["git", "clone", "https://github.com/DataTalksClub/website.git"],
        ["git", "fetch", "origin"],
        ["git", "push", "origin", "main"],
        ["git", "ls-remote", "git@github.com:DataTalksClub/website.git"],
        ["git", "submodule", "update", "--init"],
        ["git", "remote", "update"],
        ["git", "remote", "prune", "origin"],
        ["git", "remote", "show", "origin"],
        ["git", "remote", "set-head", "origin", "--auto"],
        ["git", "archive", "--remote=https://github.com/example/repo", "HEAD"],
        ["bash", "-c", "curl https://github.com"],
        ["sh", "-c", "git fetch origin"],
        ["sh", "-c", f"{sys.executable} -S -c 'print(1)'"],
        [sys.executable, "-S", "-c", "print('unguarded')"],
        [sys.executable, "-I", "-c", "print('unguarded')"],
        [sys.executable, "-E", "-c", "print('unguarded')"],
        ["env", "PYTHONPATH=", sys.executable, "-c", "print('unguarded')"],
        ["env", "-i", sys.executable, "-c", "print('unguarded')"],
        ["env", "-u", "PYTHONPATH", sys.executable, "-c", "print('unguarded')"],
        [
            "sh",
            "-c",
            "unset DTC_TEST_NETWORK_DENY PYTHONPATH; cd /; python3 -c 'print(1)'",
        ],
    ],
)
def test_subprocess_guard_rejects_network_wrappers_and_guard_bypasses(
    argv: list[str],
) -> None:
    with pytest.raises(ExternalNetworkDenied):
        subprocess.run(argv, check=False)


def test_subprocess_guard_preserves_local_git_and_benign_shell_commands() -> None:
    repository = Path(__file__).resolve().parents[2]
    git = subprocess.run(
        ["git", "-C", os.fspath(repository), "rev-parse", "--show-toplevel"],
        capture_output=True,
        text=True,
        check=False,
    )
    shell = subprocess.run(
        ["sh", "-c", "printf local-only"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert git.returncode == 0
    assert Path(git.stdout.strip()).resolve() == repository.resolve()
    assert shell.returncode == 0
    assert shell.stdout == "local-only"
    _assert_guarded_subprocess(["git", "remote", "get-url", "origin"])


@pytest.mark.skipif(not hasattr(os, "posix_spawn"), reason="POSIX spawn is unavailable")
@pytest.mark.parametrize(
    ("spawn_name", "python_flag"),
    [("posix_spawn", "-S"), ("posix_spawnp", "-I")],
)
def test_posix_spawn_entry_points_reject_python_guard_suppression(
    spawn_name: str,
    python_flag: str,
) -> None:
    spawn = getattr(os, spawn_name)
    with NetworkGuard():
        with pytest.raises(ExternalNetworkDenied, match="Python subprocess"):
            spawn(
                sys.executable,
                [sys.executable, python_flag, "-c", "print('unguarded')"],
                {"PATH": os.environ["PATH"]},
            )


def test_shell_true_cannot_hide_a_network_command() -> None:
    with pytest.raises(ExternalNetworkDenied, match="shell-wrapped"):
        subprocess.run("printf local-only; curl https://github.com", shell=True, check=False)


def _safe_environment(marker: str) -> dict[str, str]:
    return {
        "DTC_TEST_BASE_URL": "https://web.dtcdev.click",
        "DTC_TEST_REMOTE_NAMESPACE": "synthetic-run-75",
        "DTC_TEST_SAFETY_COMMAND": marker,
        "DTC_TEST_TARGET_CLASS": "isolated_development",
    }


@pytest.mark.parametrize(
    "marker",
    ("remote_readonly", "remote_mutation", "live_email", "live_provider"),
)
def test_every_safety_class_requires_its_own_exact_command_before_connection(
    marker: str,
) -> None:
    environment = _safe_environment("not_selected")
    with (
        patch.object(socket, "getaddrinfo") as resolve,
        patch.object(socket, "create_connection") as connect,
        patch.dict(os.environ, environment, clear=True),
        pytest.raises(TestSafetyError, match="exact opt-in command"),
    ):
        authorize_from_environment(marker)
    resolve.assert_not_called()
    connect.assert_not_called()


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("DTC_TEST_SAFETY_COMMAND", None),
        ("DTC_TEST_TARGET_CLASS", None),
        ("DTC_TEST_REMOTE_NAMESPACE", None),
        ("DTC_TEST_BASE_URL", None),
        ("DTC_TEST_SAFETY_COMMAND", "remote_mutation"),
        ("DTC_TEST_TARGET_CLASS", "production"),
        ("DTC_TEST_REMOTE_NAMESPACE", "bad"),
        ("DTC_TEST_BASE_URL", "http://web.dtcdev.click"),
        ("DTC_TEST_BASE_URL", "https://127.0.0.1"),
        ("DTC_TEST_BASE_URL", "https://datatalks.club"),
    ],
)
def test_remote_readonly_fails_before_connection_for_every_wrong_authority(
    name: str,
    value: str | None,
) -> None:
    environment = _safe_environment("remote_readonly")
    if value is None:
        environment.pop(name)
    else:
        environment[name] = value
    with (
        patch.object(socket, "getaddrinfo") as resolve,
        patch.object(socket, "create_connection") as connect,
        patch.dict(os.environ, environment, clear=True),
        pytest.raises(TestSafetyError),
    ):
        authorize_from_environment("remote_readonly")
    resolve.assert_not_called()
    connect.assert_not_called()


def test_remote_readonly_allows_only_safe_methods_on_exact_origin() -> None:
    with patch.dict(os.environ, _safe_environment("remote_readonly"), clear=True):
        authorization = authorize_from_environment("remote_readonly")
    authorization.authorize_request("GET", "https://web.dtcdev.click/health/live")
    with pytest.raises(TestSafetyError, match="state-changing"):
        authorization.authorize_request("POST", "https://web.dtcdev.click/synthetic")
    with pytest.raises(TestSafetyError, match="allowlist|exact approved origin"):
        authorization.authorize_request("GET", "https://other.example.invalid/")


def test_live_email_requires_secret_recipient_reference_without_printing_it() -> None:
    environment = {
        **_safe_environment("live_email"),
        "DTC_TEST_SMOKE_RECIPIENT_REFERENCE": "DTC_SMOKE_RECIPIENT_75",
        "DTC_SMOKE_RECIPIENT_75": "controlled-smoke@example.org",
    }
    with patch.dict(os.environ, environment, clear=True):
        authorization = authorize_from_environment("live_email")
    assert authorization.recipient_reference == "DTC_SMOKE_RECIPIENT_75"
    assert "controlled-smoke" not in repr(authorization)


@pytest.mark.parametrize(
    ("reference", "recipient"),
    [
        (None, None),
        ("bad", None),
        ("DTC_SMOKE_RECIPIENT_75", None),
        ("DTC_SMOKE_RECIPIENT_75", "synthetic@example.invalid"),
        ("DTC_SMOKE_RECIPIENT_75", "malformed-recipient"),
    ],
)
def test_live_email_missing_or_malformed_authority_fails_before_connection(
    reference: str | None,
    recipient: str | None,
) -> None:
    environment = _safe_environment("live_email")
    if reference is not None:
        environment["DTC_TEST_SMOKE_RECIPIENT_REFERENCE"] = reference
    if reference is not None and recipient is not None:
        environment[reference] = recipient
    with (
        patch.object(socket, "getaddrinfo") as resolve,
        patch.object(socket, "create_connection") as connect,
        patch.dict(os.environ, environment, clear=True),
        pytest.raises(TestSafetyError),
    ):
        authorize_from_environment("live_email")
    resolve.assert_not_called()
    connect.assert_not_called()


def test_django_service_safety_decorator_declares_and_enforces_exact_marker() -> None:
    @django_test_safety("remote_readonly")
    def synthetic_service_test() -> str:
        return "ran"

    assert django_test_safety_marker(synthetic_service_test) == "remote_readonly"
    with patch.dict(os.environ, {}, clear=True):
        with pytest.raises(TestSafetyError, match="exact opt-in command"):
            synthetic_service_test()
    with patch.dict(os.environ, _safe_environment("remote_readonly"), clear=True):
        assert synthetic_service_test() == "ran"

    with pytest.raises(ValueError, match="unknown"):
        django_test_safety("local")
