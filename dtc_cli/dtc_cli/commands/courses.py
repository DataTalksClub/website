"""``dtc courses`` -- peer-review status and assignment."""

from __future__ import annotations

import click

from dtc_cli.client import get_client
from dtc_cli.output import emit

API = "/api/v1/admin"


@click.group()
def courses() -> None:
    """Manage DataTalks.Club courses and peer reviews."""


@courses.command("peer-reviews")
@click.argument("course_slug")
@click.option("--cohort", default="", help="Optional cohort identifier or slug.")
@click.option("--project", default="", help="Optional project slug.")
@click.option("--json", "as_json", is_flag=True, help="Output raw JSON.")
def peer_reviews_cmd(course_slug: str, cohort: str, project: str, as_json: bool) -> None:
    """Read peer-review state, counts, and deadlines for a course."""
    params = {}
    if cohort:
        params["cohort"] = cohort
    if project:
        params["project"] = project
    data = get_client().get(f"{API}/courses/{course_slug}/peer-reviews", params=params)
    emit(data, "json")


@courses.command("assign-peer-reviews")
@click.argument("course_slug")
@click.option("--cohort", default="", help="Optional cohort identifier or slug.")
@click.option("--project", default="", help="Optional project slug.")
@click.option("--json", "as_json", is_flag=True, help="Output raw JSON.")
def assign_peer_reviews_cmd(course_slug: str, cohort: str, project: str, as_json: bool) -> None:
    """Create round-robin peer review assignments for a course."""
    body = {}
    if cohort:
        body["cohort"] = cohort
    if project:
        body["project"] = project
    data = get_client().post(
        f"{API}/courses/{course_slug}/peer-reviews/assignments",
        json_body=body,
    )
    emit(data, "json")
