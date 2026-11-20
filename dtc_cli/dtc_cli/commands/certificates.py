"""``dtc certificates`` -- per-course certificate templates, previews and issuing.

The Studio "Certificates" page is the primary way to generate certificates; this is the secondary
path for staff.  ``preview`` publishes nothing.  ``issue`` only prints the plan unless ``--yes`` is
passed, which renders, publishes to S3 and records each graduate's certificate URL.
"""

from __future__ import annotations

import base64
from pathlib import Path

import click

from dtc_cli.client import get_client
from dtc_cli.output import emit

API = "/api/v1/admin"


@click.group()
def certificates() -> None:
    """Generate and inspect course certificates."""


@certificates.command("list")
def list_cmd() -> None:
    """Cohorts with a certificate template, their printed date and graduate count."""
    emit(get_client().get(f"{API}/certificates"), "json")


@certificates.command("preview")
@click.argument("cohort_slug")
@click.option("--name", required=True, help="Name to print, e.g. 'Rick Astley'.")
@click.option("--format", "fmt", type=click.Choice(["pdf", "png"]), default="pdf")
@click.option("--out", type=click.Path(path_type=Path), default=None, help="Output file path.")
def preview_cmd(cohort_slug: str, name: str, fmt: str, out: Path | None) -> None:
    """Render one throwaway certificate; nothing is published or recorded."""
    data = get_client().post(
        f"{API}/cohorts/{cohort_slug}/certificates/preview",
        json_body={"name": name, "format": fmt},
    )
    target = out or Path(data["filename"])
    target.write_bytes(base64.b64decode(data["content_base64"]))
    click.echo(f"Wrote {target} (certificate date: {data['date']})")


@certificates.command("issue")
@click.argument("cohort_slug")
@click.option("--yes", is_flag=True, help="Actually render, publish and record the certificates.")
@click.option("--enrollment-id", "enrollment_ids", type=int, multiple=True, help="Limit to these.")
def issue_cmd(cohort_slug: str, yes: bool, enrollment_ids: tuple[int, ...]) -> None:
    """Plan (default) or issue certificates for a cohort's graduates."""
    body: dict = {}
    if yes:
        body["confirm"] = cohort_slug
    if enrollment_ids:
        body["enrollment_ids"] = list(enrollment_ids)
    data = get_client().post(f"{API}/cohorts/{cohort_slug}/certificates/issue", json_body=body)
    emit(data, "json")
