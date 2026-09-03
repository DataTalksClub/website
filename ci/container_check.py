from __future__ import annotations

import argparse
import json
import os
import platform
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from deploy.deployment_targets import CPU_ARCHITECTURES, CpuArchitecture


class ContainerCheckError(RuntimeError):
    """The production container failed its local verification contract."""


def host_cpu_architecture(machine: str | None = None) -> CpuArchitecture:
    """Return the reviewed architecture this host builds and runs natively.

    Local verification proves the Dockerfile and runtime contract on the
    developer's own machine.  It deliberately does not build the deployment
    target's architecture: doing that off-target needs emulation, under which
    the in-container checks take tens of minutes and the liveness smoke does not
    come up inside its budget, so the evidence would be slow and untrustworthy.
    The release image's architecture is proved natively by the CI container job,
    which runs on the target architecture's runner.
    """

    resolved = platform.machine() if machine is None else machine
    for architecture in CPU_ARCHITECTURES.values():
        if resolved in {architecture.runner_machine, architecture.image_architecture}:
            return architecture
    raise ContainerCheckError(f"no reviewed container architecture for host {resolved!r}")


def _run(
    arguments: list[str],
    *,
    cwd: Path,
    capture: bool = False,
    expected_success: bool = True,
) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        arguments,
        cwd=cwd,
        check=False,
        text=True,
        capture_output=capture,
    )
    if (result.returncode == 0) != expected_success:
        raise ContainerCheckError(f"container command had an unexpected result: {arguments[0]}")
    return result


def verify_container(repository: Path, revision: str) -> dict[str, Any]:
    image = f"dtc-website-verification:{revision}"
    container = f"dtc-website-verification-{revision[:12]}-{os.getpid()}"
    architecture = host_cpu_architecture()
    _run(
        [
            "docker",
            "buildx",
            "build",
            "--platform",
            architecture.build_platform,
            "--provenance=false",
            "--label",
            f"org.opencontainers.image.revision={revision}",
            "--tag",
            image,
            "--load",
            ".",
        ],
        cwd=repository,
    )
    inspected = _run(["docker", "image", "inspect", image], cwd=repository, capture=True).stdout
    try:
        metadata: Any = json.loads(inspected)[0]
    except (IndexError, KeyError, TypeError, json.JSONDecodeError) as exc:
        raise ContainerCheckError("Docker returned malformed image metadata") from exc
    if (
        metadata.get("Architecture") != architecture.image_architecture
        or metadata.get("Config", {}).get("User") != "10001:10001"
        or metadata.get("Config", {}).get("Labels", {}).get("org.opencontainers.image.revision")
        != revision
    ):
        raise ContainerCheckError("image provenance, architecture, or runtime user is invalid")
    for flag, expected in (("-u", "10001"), ("-g", "10001")):
        actual = _run(
            ["docker", "run", "--rm", "--entrypoint", "id", image, flag],
            cwd=repository,
            capture=True,
        ).stdout.strip()
        if actual != expected:
            raise ContainerCheckError("container process identity is invalid")

    fixtures = repository / ".tmp" / "verification-container" / revision
    absent = fixtures / "absent"
    absent.mkdir(parents=True, exist_ok=True)
    malformed = fixtures / "malformed.json"
    missing_entry = fixtures / "missing-entry.json"
    malformed.write_text("{\n", encoding="utf-8")
    missing_entry.write_text(
        '{"paths": {}, "version": "1.1", "hash": "fixture"}\n', encoding="utf-8"
    )

    static_command = [
        "docker",
        "run",
        "--rm",
        "--entrypoint",
        "sh",
        "--env",
        "DJANGO_SETTINGS_MODULE=website.settings.collectstatic",
    ]
    verifier = [image, "-c", "uv run --no-sync python -m scripts.verify_static_manifest"]
    _run([*static_command, *verifier], cwd=repository)
    for mount in (
        f"type=bind,source={absent},target=/app/staticfiles,readonly",
        f"type=bind,source={malformed},target=/app/staticfiles/staticfiles.json,readonly",
        f"type=bind,source={missing_entry},target=/app/staticfiles/staticfiles.json,readonly",
    ):
        _run(
            [*static_command, "--mount", mount, *verifier],
            cwd=repository,
            expected_success=False,
        )
    incompatible = _run(
        [
            *static_command,
            image,
            "-c",
            "uv run --no-sync python -m scripts.verify_static_manifest "
            "--incompatible-storage-fixture",
        ],
        cwd=repository,
        capture=True,
        expected_success=False,
    )
    diagnostic = (incompatible.stdout + incompatible.stderr).strip()
    if diagnostic != (
        "Static manifest verification failed: staticfiles storage does not use the runtime "
        "manifest backend"
    ):
        raise ContainerCheckError("incompatible static storage diagnostic is not exact")

    runtime = fixtures / f"runtime-{os.getpid()}"
    runtime.mkdir(parents=True, exist_ok=True)
    # The image runs as uid 10001. This process-scoped scratch mount must be writable by that
    # non-root identity so the local SQLite database exercises the real migration graph.
    runtime.chmod(0o777)
    runtime_mount = f"type=bind,source={runtime},target=/app/.tmp"
    _run(
        [
            "docker",
            "run",
            "--rm",
            "--env",
            "DJANGO_SETTINGS_MODULE=website.settings.local",
            "--mount",
            runtime_mount,
            "--entrypoint",
            "uv",
            image,
            "run",
            "--no-sync",
            "python",
            "manage.py",
            "migrate",
            "--noinput",
        ],
        cwd=repository,
    )

    try:
        _run(
            [
                "docker",
                "run",
                "--detach",
                "--name",
                container,
                "--env",
                "DJANGO_SETTINGS_MODULE=website.settings.local",
                "--mount",
                runtime_mount,
                "--publish",
                "127.0.0.1::8000",
                image,
                "web",
            ],
            cwd=repository,
        )
        address = _run(
            ["docker", "port", container, "8000/tcp"], cwd=repository, capture=True
        ).stdout.strip()
        if not address.startswith("127.0.0.1:"):
            raise ContainerCheckError("container did not publish a loopback liveness port")
        port = address.rsplit(":", 1)[1]
        expected = json.dumps(
            {
                "image_digest": None,
                "source_sha": None,
                "status": "ok",
                "version": "local-development-build-version-not-configured",
            }
        )
        for _ in range(30):
            try:
                with urllib.request.urlopen(  # noqa: S310
                    f"http://127.0.0.1:{port}/health/live", timeout=2
                ) as response:
                    body = response.read().decode("utf-8")
                if json.loads(body) == json.loads(expected):
                    with urllib.request.urlopen(  # noqa: S310
                        f"http://127.0.0.1:{port}/unified/", timeout=2
                    ) as response:
                        if response.status == 200:
                            return {
                                "assertions": [
                                    "image_architecture_matches_build_platform",
                                    "image_revision_label_matches_source",
                                    "image_runtime_user_configured_non_root",
                                    "runtime_uid_10001",
                                    "runtime_gid_10001",
                                    "static_manifest_valid",
                                    "static_manifest_absent_rejected",
                                    "static_manifest_malformed_rejected",
                                    "static_manifest_missing_entry_rejected",
                                    "static_manifest_incompatible_storage_rejected",
                                    "local_sqlite_migration_graph_applied",
                                    "health_live_local_identity",
                                    "unified_route_responds",
                                ],
                                "revision": revision,
                                "schema_version": 1,
                                "status": "pass",
                            }
            except (OSError, ValueError, urllib.error.URLError):
                pass
            time.sleep(1)
        raise ContainerCheckError("container liveness did not become ready")
    finally:
        subprocess.run(
            ["docker", "rm", "--force", container],
            cwd=repository,
            check=False,
            capture_output=True,
            text=True,
        )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repository", type=Path, default=Path.cwd())
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    revision = subprocess.run(
        ["git", "-C", os.fspath(args.repository), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    result = verify_container(args.repository, revision)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
