from __future__ import annotations

import csv
import hashlib
import io
import json
import tempfile
from datetime import datetime
from pathlib import Path
from zipfile import ZipFile

import pytest
from django.conf import settings
from django.db.models import F
from django.test import Client, override_settings
from playwright.sync_api import Page, expect

from accounts.studio_sessions import SESSION_REFERENCE_KEY
from accounts.studio_test_support import make_studio_user
from content.public_data import event_groups, public_projection
from events.models import (
    HistoricalEventMapping,
    HistoricalRegistrationAggregateRevision,
    HistoricalRegistrationAggregateSlot,
    HistoricalRegistrationSourceRun,
    HistoricalRegistrationTotalState,
)

pytestmark = [pytest.mark.full, pytest.mark.django_db(transaction=True)]

VIEWPORTS = (
    ({"width": 1440, "height": 900}, "desktop"),
    ({"width": 390, "height": 844}, "mobile"),
)
SCREENSHOTS = Path(".tmp/screenshots/issue-112")
PUBLIC_CANARY = "synthetic-protected-event-112"


def tree_checksum(root: Path) -> str:
    digest = hashlib.sha256(b"dtc-protected-tree-v1\0")
    for path in sorted(root.iterdir(), key=lambda item: item.name):
        relative = path.relative_to(root).as_posix().encode()
        payload = path.read_bytes()
        digest.update(len(relative).to_bytes(8, "big"))
        digest.update(relative)
        digest.update(len(payload).to_bytes(8, "big"))
        digest.update(payload)
    return digest.hexdigest()


def screenshot(page: Page, name: str, suffix: str) -> None:
    SCREENSHOTS.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=SCREENSHOTS / f"{name}-{suffix}.png", full_page=True)


def authenticated_cookie(page: Page, live_server, user) -> None:
    client = Client()
    client.force_login(user)
    assert client.session.get(SESSION_REFERENCE_KEY)
    page.context.add_cookies(
        [
            {
                "name": settings.SESSION_COOKIE_NAME,
                "value": client.cookies[settings.SESSION_COOKIE_NAME].value,
                "url": live_server.url,
            }
        ]
    )


def assert_private(response) -> None:
    assert response is not None and response.status == 200
    assert response.headers["x-robots-tag"] == "noindex, nofollow"
    cache = {part.strip().casefold() for part in response.headers["cache-control"].split(",")}
    assert {"private", "no-store"}.issubset(cache)


def seed_total(event: dict, *, count: int, complete: bool) -> None:
    provenance = event["provenance"]
    suffix = hashlib.sha256(event["slug"].encode()).hexdigest()
    run_state = (
        HistoricalRegistrationSourceRun.State.ACTIVE
        if complete
        else HistoricalRegistrationSourceRun.State.QUARANTINED
    )
    aggregate_state = (
        HistoricalRegistrationAggregateRevision.State.ACTIVE
        if complete
        else HistoricalRegistrationAggregateRevision.State.QUARANTINED
    )
    run = HistoricalRegistrationSourceRun.objects.create(
        provider=HistoricalRegistrationSourceRun.Provider.LUMA,
        adapter_version="synthetic-browser-v1",
        schema_version="synthetic-browser-v1",
        whole_source_checksum=suffix,
        source_reference_digest=hashlib.sha256(f"reference-{suffix}".encode()).hexdigest(),
        manifest_entry_total=2,
        manifest_event_total=1,
        parsed_row_total=count,
        eligible_row_total=count,
        excluded_row_total=0,
        quarantined_event_total=0 if complete else 1,
        status_totals={"approved": count},
        state_totals={aggregate_state: 1},
        reason_codes=[] if complete else ["unsupported_schema"],
        mapping_set_revision=1,
        policy_version="historical-registration-v1",
        state=run_state,
        actor_ref="synthetic-browser-actor",
    )
    mapping = HistoricalEventMapping.objects.create(
        provider=HistoricalRegistrationSourceRun.Provider.LUMA,
        external_event_identifier=f"{PUBLIC_CANARY}-{suffix[:12]}",
        event_id=event["identity_id"],
        canonical_repository=provenance["repository"],
        canonical_revision=provenance["revision"],
        canonical_source_key=provenance["source_key"],
        canonical_slug_snapshot=event["slug"],
        state=HistoricalEventMapping.State.MAPPED,
        mapping_set_revision=1,
        reviewer_ref="synthetic-browser-reviewer",
        reason_code="synthetic_mapping",
    )
    aggregate = HistoricalRegistrationAggregateRevision.objects.create(
        source_run=run,
        mapping=mapping,
        eligible_count=count,
        excluded_count=0,
        quarantined_count=0 if complete else count,
        coverage_boundary="historical",
        status_policy_version="historical-status-v1",
        combination_policy=HistoricalRegistrationAggregateRevision.CombinationPolicy.REPLACEMENT,
        aggregate_checksum=hashlib.sha256(f"aggregate-{suffix}".encode()).hexdigest(),
        state=aggregate_state,
        reason_code="" if complete else "unsupported_schema",
    )
    if complete:
        HistoricalRegistrationAggregateSlot.objects.create(
            canonical_repository=provenance["repository"],
            canonical_revision=provenance["revision"],
            canonical_source_key=provenance["source_key"],
            canonical_slug_snapshot=event["slug"],
            provider=HistoricalRegistrationSourceRun.Provider.LUMA,
            coverage_boundary="historical",
            active_revision=aggregate,
        )
    HistoricalRegistrationTotalState.objects.create(
        canonical_repository=provenance["repository"],
        canonical_revision=provenance["revision"],
        canonical_source_key=provenance["source_key"],
        canonical_slug_snapshot=event["slug"],
        complete=complete,
    )


def seed_validated_overlap(event: dict, *, suffix: str) -> HistoricalRegistrationSourceRun:
    provenance = event["provenance"]
    checksum = hashlib.sha256(f"overlap-{suffix}".encode()).hexdigest()
    run = HistoricalRegistrationSourceRun.objects.create(
        provider=HistoricalRegistrationSourceRun.Provider.EVENTBRITE,
        adapter_version="synthetic-browser-v1",
        schema_version="synthetic-browser-v1",
        whole_source_checksum=checksum,
        source_reference_digest=hashlib.sha256(f"reference-{checksum}".encode()).hexdigest(),
        manifest_entry_total=1,
        manifest_event_total=1,
        parsed_row_total=2,
        eligible_row_total=2,
        excluded_row_total=0,
        quarantined_event_total=0,
        status_totals={"attending": 2},
        state_totals={HistoricalRegistrationAggregateRevision.State.VALIDATED: 1},
        reason_codes=[],
        mapping_set_revision=1,
        policy_version="historical-registration-v1",
        state=HistoricalRegistrationSourceRun.State.VALIDATED,
        actor_ref="synthetic-browser-overlap-actor",
    )
    mapping = HistoricalEventMapping.objects.create(
        provider=HistoricalRegistrationSourceRun.Provider.EVENTBRITE,
        external_event_identifier=f"synthetic-overlap-{suffix}",
        event_id=event["identity_id"],
        canonical_repository=provenance["repository"],
        canonical_revision=provenance["revision"],
        canonical_source_key=provenance["source_key"],
        canonical_slug_snapshot=event["slug"],
        state=HistoricalEventMapping.State.MAPPED,
        mapping_set_revision=1,
        reviewer_ref="synthetic-browser-overlap-reviewer",
        reason_code="synthetic_mapping",
    )
    HistoricalRegistrationAggregateRevision.objects.create(
        source_run=run,
        mapping=mapping,
        eligible_count=2,
        excluded_count=0,
        quarantined_count=0,
        coverage_boundary="historical",
        status_policy_version="historical-status-v1",
        combination_policy=(
            HistoricalRegistrationAggregateRevision.CombinationPolicy.ADDITIVE_DISJOINT
        ),
        aggregate_checksum=hashlib.sha256(f"aggregate-{checksum}".encode()).hexdigest(),
        state=HistoricalRegistrationAggregateRevision.State.VALIDATED,
    )
    return run


def mapping_card(page: Page, external_id: str):
    # Design 5a (issue #179): a mapping proposal is a .list-row record carrying
    # data-mapping-id, which is the stable hook rather than the old card class.
    return page.locator("article[data-mapping-id]").filter(has_text=external_id)


@pytest.mark.parametrize(("viewport", "suffix"), VIEWPORTS)
def test_public_zero_one_plural_and_omitted_states_are_private_and_responsive(
    page: Page,
    live_server,
    monkeypatch,
    viewport: dict[str, int],
    suffix: str,
) -> None:
    page.set_viewport_size(viewport)
    event_times = sorted(
        {datetime.fromisoformat(event["starts_at"]) for event in public_projection()["events"]},
        reverse=True,
    )
    assert len(event_times) >= 2
    synthetic_now = event_times[1] + (event_times[0] - event_times[1]) / 2
    grouped = event_groups(now=synthetic_now)
    assert grouped.upcoming and len(grouped.recent) >= 3
    monkeypatch.setattr("content.public_views.event_groups", lambda: grouped)
    events = (grouped.upcoming[0], *grouped.recent[:3])
    for event, count, complete in zip(
        events, (0, 1, 12, 0), (True, True, True, False), strict=True
    ):
        seed_total(event, count=count, complete=complete)

    request_urls: list[str] = []
    console_messages: list[str] = []
    page.on("request", lambda request: request_urls.append(request.url))
    page.on("console", lambda message: console_messages.append(message.text))

    hub = page.goto(f"{live_server.url}/events")
    assert hub is not None and hub.status == 200
    expect(page.locator("[data-registration-total-revision]")).to_have_count(0)
    page.get_by_role("link", name=events[0]["title"], exact=True).first.click()
    expect(page).to_have_url(f"{live_server.url}{events[0]['public_path']}")

    for event, expected, state in zip(
        events,
        ("0 registered", "1 registered", "12 registered", None),
        ("upcoming-zero", "past-one", "past-plural", "past-omitted"),
        strict=True,
    ):
        response = page.goto(f"{live_server.url}{event['public_path']}")
        assert response is not None and response.status == 200
        html = page.content()
        aria = page.locator("body").aria_snapshot()
        if expected is None:
            expect(page.locator("[data-registration-total-revision]")).to_have_count(0)
            assert "x-event-registration-total-revision" not in response.headers
        else:
            expect(page.get_by_text(expected, exact=True)).to_be_visible()
            expect(page.locator("[data-registration-total-revision]")).to_have_count(1)
            assert response.headers["cache-control"] == "no-store, max-age=0, s-maxage=0"
            assert int(response.headers["x-event-registration-total-revision"]) >= 1
        for protected in (
            PUBLIC_CANARY,
            "attendee-card",
            "attendee-avatar",
            "synthetic-browser-reviewer",
            "synthetic-browser-actor",
        ):
            assert protected not in html
            assert protected not in aria
        assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth + 1")
        screenshot(page, f"public-{state}", suffix)

    browser_evidence = "\n".join((*request_urls, *console_messages))
    assert PUBLIC_CANARY not in browser_evidence
    assert "traceback" not in browser_evidence.casefold()


@pytest.mark.parametrize(("viewport", "suffix"), VIEWPORTS)
def test_studio_stage_replay_map_validate_activate_preview_rollback_and_denial(
    page: Page,
    live_server,
    viewport: dict[str, int],
    suffix: str,
) -> None:
    page.set_viewport_size(viewport)
    event = public_projection()["events"][0]
    provenance = event["provenance"]
    scratch = Path(settings.BASE_DIR) / ".tmp"
    scratch.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(dir=scratch) as temporary:
        source = Path(temporary) / "synthetic-source"
        source.mkdir()
        external_id = f"synthetic-studio-provider-{suffix}"
        external_url = f"https://example.test/{external_id}"
        excluded_id = f"synthetic-studio-excluded-{suffix}"
        excluded_url = f"https://example.test/{excluded_id}"
        source_missing_id = f"synthetic-studio-source-missing-{suffix}"
        (source / "synthetic.json").write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "event_id": external_id,
                    "event_url": external_url,
                }
            ),
            encoding="utf-8",
        )
        with (source / "synthetic.csv").open("w", encoding="utf-8", newline="") as stream:
            writer = csv.DictWriter(
                stream,
                fieldnames=("event_id", "guest_id", "approval_status", "ignored_email"),
            )
            writer.writeheader()
            writer.writerow(
                {
                    "event_id": external_id,
                    "guest_id": "synthetic-browser-registration",
                    "approval_status": "approved",
                    "ignored_email": "synthetic-private-canary@example.test",
                }
            )
        (source / "synthetic-excluded.json").write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "event_id": excluded_id,
                    "event_url": excluded_url,
                }
            ),
            encoding="utf-8",
        )
        with (source / "synthetic-excluded.csv").open("w", encoding="utf-8", newline="") as stream:
            writer = csv.DictWriter(
                stream,
                fieldnames=("event_id", "guest_id", "approval_status"),
            )
            writer.writeheader()
            writer.writerow(
                {
                    "event_id": excluded_id,
                    "guest_id": "synthetic-browser-excluded-registration",
                    "approval_status": "approved",
                }
            )
        unsupported_csv = io.StringIO(newline="")
        unsupported_writer = csv.DictWriter(
            unsupported_csv,
            fieldnames=("Unexpected order", "Unexpected attendee", "Unexpected status"),
        )
        unsupported_writer.writeheader()
        unsupported_writer.writerow(
            {
                "Unexpected order": "synthetic-order",
                "Unexpected attendee": "synthetic-attendee",
                "Unexpected status": "Attending",
            }
        )
        unsupported_archive = Path(temporary) / "synthetic-unsupported.zip"
        with ZipFile(unsupported_archive, "w") as archive:
            archive.writestr("900000001.csv", unsupported_csv.getvalue())
        registry = {
            "synthetic-studio-luma": {
                "provider": "luma",
                "reconciliation_profile": "synthetic",
                "path": str(source),
                "sha256": tree_checksum(source),
                "mapping_bridge": {
                    external_url: {
                        "repository": provenance["repository"],
                        "revision": provenance["revision"],
                        "source_key": provenance["source_key"],
                        "slug": event["slug"],
                    }
                },
                "source_missing": {
                    source_missing_id: {
                        "repository": provenance["repository"],
                        "revision": provenance["revision"],
                        "source_key": provenance["source_key"],
                        "slug": event["slug"],
                    }
                },
            },
            "synthetic-studio-unsupported": {
                "provider": "eventbrite",
                "reconciliation_profile": "synthetic",
                "path": str(unsupported_archive),
                "sha256": hashlib.sha256(unsupported_archive.read_bytes()).hexdigest(),
            },
        }
        with override_settings(HISTORICAL_REGISTRATION_SOURCES=registry):
            event_operator = make_studio_user(
                username=f"historical-browser-{suffix}",
                roles=("event_operator",),
            )
            authenticated_cookie(page, live_server, event_operator)
            response = page.goto(f"{live_server.url}/studio/events/historical-registration-totals/")
            assert_private(response)
            expect(
                page.get_by_role("heading", name="Historical registration totals", exact=True)
            ).to_be_visible()
            screenshot(page, "studio-import-list", suffix)

            page.get_by_label("Provider").select_option("luma")
            page.get_by_label("Registered source reference").select_option(
                label="Luma historical registration source"
            )
            assert "synthetic-studio-luma" not in page.content()
            page.get_by_label("Confirm aggregate-only staging").check()
            page.get_by_role("button", name="Stage source", exact=True).click()
            expect(
                page.get_by_role("heading", name="luma reconciliation", exact=True)
            ).to_be_visible()
            staged_url = page.url
            assert external_id not in page.content()
            screenshot(page, "studio-import-detail-staged", suffix)

            page.get_by_label("Confirm validate").check()
            page.get_by_role("button", name="Validate", exact=True).click()
            expect(page.get_by_role("alert")).to_contain_text(
                "The aggregate state changed or is not ready for this action."
            )
            screenshot(page, "studio-validation-conflict", suffix)

            page.get_by_label("Confirm dry-run").check()
            page.get_by_role("button", name="Dry-Run", exact=True).click()
            expect(page).to_have_url(staged_url)

            response = page.goto(f"{live_server.url}/studio/events/historical-registration-totals/")
            assert_private(response)
            page.get_by_label("Confirm aggregate-only staging").check()
            page.get_by_role("button", name="Stage source", exact=True).click()
            expect(page).to_have_url(staged_url)
            assert HistoricalRegistrationSourceRun.objects.count() == 1

            response = page.goto(
                f"{live_server.url}/studio/events/historical-registration-totals/mappings/"
            )
            assert_private(response)
            expect(
                page.get_by_role("heading", name="Historical event mappings", exact=True)
            ).to_be_visible()
            expect(page.get_by_text(external_id, exact=True)).to_be_visible()
            source_missing_card = mapping_card(page, source_missing_id)
            expect(
                source_missing_card.get_by_role("heading", name="luma · source_missing", exact=True)
            ).to_be_visible()
            screenshot(page, "studio-source-missing", suffix)

            mapping = HistoricalEventMapping.objects.get(
                provider=HistoricalRegistrationSourceRun.Provider.LUMA,
                external_event_identifier=external_id,
            )
            HistoricalEventMapping.objects.filter(pk=mapping.pk).update(revision=F("revision") + 1)
            main_card = mapping_card(page, external_id)
            main_card.get_by_label("Decision").select_option("mapped")
            main_card.get_by_label("Exact Event identity").select_option(event["identity_id"])
            main_card.get_by_label("Combination policy").select_option("replacement")
            main_card.get_by_label("Private review note").fill("Synthetic stale browser review.")
            with page.expect_response(
                lambda candidate: (
                    candidate.url.endswith("/mappings/") and candidate.request.method == "POST"
                )
            ) as stale_response:
                main_card.get_by_role("button", name="Save reviewed decision", exact=True).click()
            assert stale_response.value.status == 409
            expect(page.get_by_role("alert")).to_contain_text(
                "The aggregate state changed or is not ready for this action."
            )
            mapping.refresh_from_db()
            assert mapping.state == HistoricalEventMapping.State.REVIEW_REQUIRED
            screenshot(page, "studio-stale-revision", suffix)

            main_card = mapping_card(page, external_id)
            main_card.get_by_label("Decision").select_option("mapped")
            main_card.get_by_label("Exact Event identity").select_option(event["identity_id"])
            main_card.get_by_label("Combination policy").select_option("replacement")
            main_card.get_by_label("Private review note").fill("Synthetic exact browser review.")
            screenshot(page, "studio-mapping-review", suffix)
            main_card.get_by_role("button", name="Save reviewed decision", exact=True).click()
            assert "/mappings/?updated=" in page.url

            excluded_card = mapping_card(page, excluded_id)
            excluded_card.get_by_label("Decision").select_option("excluded")
            excluded_card.get_by_label("Combination policy").select_option("exclude")
            excluded_card.get_by_label("Reason code").fill("reviewed_exclusion")
            excluded_card.get_by_label("Private review note").fill("Synthetic browser exclusion.")
            excluded_card.get_by_role("button", name="Save reviewed decision", exact=True).click()
            assert "/mappings/?updated=" in page.url
            excluded_card = mapping_card(page, excluded_id)
            expect(
                excluded_card.get_by_role("heading", name="luma · excluded", exact=True)
            ).to_be_visible()
            screenshot(page, "studio-exclusion-state", suffix)

            response = page.goto(staged_url)
            assert_private(response)
            page.get_by_label("Confirm validate").check()
            page.get_by_role("button", name="Validate", exact=True).click()
            expect(page.get_by_text("validated", exact=True).first).to_be_visible()
            page.get_by_label("Confirm activate").check()
            page.get_by_role("button", name="Activate", exact=True).click()
            expect(page.get_by_text("active", exact=True).first).to_be_visible()
            assert external_id not in page.content()
            assert "synthetic-private-canary@example.test" not in page.content()
            screenshot(page, "studio-import-detail-active", suffix)

            preview_url = (
                f"{live_server.url}/studio/events/{event['identity_id']}/registration-total/"
            )
            preview = page.goto(preview_url)
            assert_private(preview)
            expect(page.get_by_role("heading", name="Registration total preview")).to_be_visible()
            expect(page.get_by_text("1", exact=True).first).to_be_visible()
            assert external_id not in page.content()
            screenshot(page, "studio-total-preview", suffix)

            page.context.clear_cookies()
            public = page.goto(f"{live_server.url}{event['public_path']}")
            assert public is not None and public.status == 200
            expect(page.get_by_text("1 registered", exact=True)).to_be_visible()
            assert public.headers["cache-control"] == "no-store, max-age=0, s-maxage=0"
            for protected in (external_id, "synthetic-private-canary@example.test"):
                assert protected not in page.content()

            authenticated_cookie(page, live_server, event_operator)
            overlap_run = seed_validated_overlap(event, suffix=suffix)
            overlap_url = (
                f"{live_server.url}/studio/events/historical-registration-totals/{overlap_run.id}/"
            )
            overlap_detail = page.goto(overlap_url)
            assert_private(overlap_detail)
            page.get_by_label("Confirm activate").check()
            with page.expect_response(
                lambda candidate: (
                    candidate.url.endswith(f"/{overlap_run.id}/activate/")
                    and candidate.request.method == "POST"
                )
            ) as overlap_response:
                page.get_by_role("button", name="Activate", exact=True).click()
            assert overlap_response.value.status == 409
            expect(page.get_by_role("alert")).to_contain_text(
                "The aggregate state changed or is not ready for this action."
            )
            overlap_run.refresh_from_db()
            assert overlap_run.state == HistoricalRegistrationSourceRun.State.VALIDATED
            screenshot(page, "studio-overlap-conflict", suffix)

            page.goto(staged_url)
            page.get_by_label("Confirm rollback").check()
            page.get_by_role("button", name="Rollback", exact=True).click()
            expect(page.get_by_text("rolled_back", exact=True).first).to_be_visible()
            page.context.clear_cookies()
            rolled_back = page.goto(f"{live_server.url}{event['public_path']}")
            assert rolled_back is not None and rolled_back.status == 200
            expect(page.locator("[data-registration-total-revision]")).to_have_count(0)
            screenshot(page, "public-after-studio-rollback", suffix)

            authenticated_cookie(page, live_server, event_operator)
            unsupported_list = page.goto(
                f"{live_server.url}/studio/events/historical-registration-totals/"
            )
            assert_private(unsupported_list)
            page.get_by_label("Provider").select_option("eventbrite")
            page.get_by_label("Registered source reference").select_option(
                label="Eventbrite historical registration source"
            )
            assert "synthetic-studio-unsupported" not in page.content()
            page.get_by_label("Confirm aggregate-only staging").check()
            page.get_by_role("button", name="Stage source", exact=True).click()
            expect(
                page.get_by_role("heading", name="eventbrite reconciliation", exact=True)
            ).to_be_visible()
            expect(page.get_by_text("quarantined", exact=True).first).to_be_visible()
            expect(page.get_by_text("unsupported", exact=True).first).to_be_visible()
            assert "synthetic-attendee" not in page.content()
            screenshot(page, "studio-unsupported-schema", suffix)

            page.context.clear_cookies()
            denied_user = make_studio_user(
                username=f"historical-denied-{suffix}",
                roles=("content_operator",),
            )
            authenticated_cookie(page, live_server, denied_user)
            denied = page.goto(f"{live_server.url}/studio/events/historical-registration-totals/")
            assert denied is not None and denied.status == 403
            expect(page.get_by_text("Studio access denied", exact=True)).to_be_visible()
            assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth + 1")
