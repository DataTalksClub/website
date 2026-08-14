"""Capture deterministic, synthetic visual evidence for a reviewed verification plan.

The normal CI screenshot job runs this module against a local Django process.  It
only visits routes and states selected by the plan, never submits forms, and stores
the PNGs next to a small digest-bound inspection manifest for ``ci.verification``.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any
from urllib.parse import urljoin

from playwright.sync_api import Page, sync_playwright

from ci.evidence import isoformat, utc_now
from ci.verification import load_plan

DEBUG_MARKERS = (
    "traceback (most recent call last)",
    "django debug toolbar",
    "exception type:",
    "internal server error",
)
SAFE_FILENAME = re.compile(r"[^a-z0-9]+")
PAGE_BODY_READY_TIMEOUT_MS = 10_000


def _capture_name(capture: Mapping[str, Any]) -> str:
    route = str(capture["route"]).strip("/") or "home"
    route_part = SAFE_FILENAME.sub("-", route.lower()).strip("-") or "home"
    state_part = SAFE_FILENAME.sub("-", str(capture["route_state"]).lower()).strip("-")
    return (
        f"{route_part}-{state_part}-{capture['viewport']}-"
        f"{capture['width']}x{capture['height']}.png"
    )


def _assert_page_is_safe(page: Page, *, route_state: str, status: int | None) -> None:
    if status is None:
        raise RuntimeError("screenshot route did not return an HTTP response")
    if route_state == "not-found":
        if status != 404:
            raise RuntimeError(f"not-found route returned unexpected status {status}")
    elif not 200 <= status < 400:
        raise RuntimeError(f"application route returned unexpected status {status}")

    body_text = page.locator("body").inner_text(timeout=PAGE_BODY_READY_TIMEOUT_MS).strip()
    if not body_text:
        raise RuntimeError("screenshot route rendered an empty document")
    lowered = body_text.lower()
    marker = next((item for item in DEBUG_MARKERS if item in lowered), None)
    if marker:
        raise RuntimeError(f"screenshot route exposed a debug error marker: {marker}")

    fits_viewport = page.evaluate(
        """() => document.documentElement.scrollWidth <= document.documentElement.clientWidth + 1"""
    )
    if fits_viewport is not True:
        raise RuntimeError("screenshot route has horizontal overflow")


def _capture_route(
    page: Page,
    *,
    base_url: str,
    capture: Mapping[str, Any],
    output_directory: Path,
    render_sha256: str,
) -> dict[str, Any]:
    width = int(capture["width"])
    height = int(capture["height"])
    page.set_viewport_size({"width": width, "height": height})
    route = str(capture["route"])
    response = page.goto(urljoin(f"{base_url.rstrip('/')}/", route.lstrip("/")), wait_until="load")
    _assert_page_is_safe(
        page,
        route_state=str(capture["route_state"]),
        status=response.status if response else None,
    )

    artifact_path = Path("screenshots") / _capture_name(capture)
    image_path = output_directory / artifact_path
    image_path.parent.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(image_path), full_page=True, animations="disabled")
    image_sha256 = hashlib.sha256(image_path.read_bytes()).hexdigest()
    return {
        **capture,
        "artifact_path": artifact_path.as_posix(),
        "captured_at": isoformat(utc_now()),
        "image_sha256": image_sha256,
        "independent_inspection": True,
        "render_sha256": render_sha256,
        "verdict": "pass",
    }


def capture_plan(
    plan: Mapping[str, Any], *, base_url: str, output_directory: str | Path
) -> dict[str, Any]:
    """Capture every exact route/viewport identity selected by ``plan``."""

    if plan["components"]["screenshots"]["disposition"] != "rerun":
        raise RuntimeError("screenshot capture requires a rerun disposition")
    captures = plan["render"]["required_captures"]
    if not isinstance(captures, list) or not captures:
        raise RuntimeError("screenshot plan has no required captures")
    output = Path(output_directory)
    output.mkdir(parents=True, exist_ok=True)
    results: list[dict[str, Any]] = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        try:
            page = browser.new_page()
            for capture in captures:
                if not isinstance(capture, Mapping):
                    raise RuntimeError("screenshot plan contains an invalid capture")
                results.append(
                    _capture_route(
                        page,
                        base_url=base_url,
                        capture=capture,
                        output_directory=output,
                        render_sha256=str(plan["render"]["sha256"]),
                    )
                )
        finally:
            browser.close()
    return {"captures": results, "reviewer": "normal-ci-render-inspection"}


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", required=True)
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--output", required=True)
    args = parser.parse_args(argv)
    payload = capture_plan(
        load_plan(args.plan), base_url=args.base_url, output_directory=args.output
    )
    output_path = Path(args.output) / "screenshots.json"
    output_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"Captured {len(payload['captures'])} screenshot views")


if __name__ == "__main__":
    main()
