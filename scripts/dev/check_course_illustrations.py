"""Check and capture course illustrations on a running website without DB writes.

    uv run python scripts/dev/check_course_illustrations.py \
        --base-url http://localhost:8000 --base-url http://127.0.0.1:8091

By default discover family routes from the live catalogue. Use repeated --family
or --route options to select a smaller set. Captures and report.json stay in .tmp.
This is browser evidence for review, not a replacement for visual inspection.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path
from urllib.parse import urlsplit

from playwright.sync_api import Page, sync_playwright

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_VIEWPORTS = ((1440, 900), (768, 1024), (390, 844), (320, 740))
COURSE_ROUTE = re.compile(r"/courses(?:/[a-z0-9]+(?:-[a-z0-9]+)*)?\Z")
NON_FAMILY_ROUTES = {"/courses/register", "/courses/projects"}
CATALOGUE_MEDIA = ".catalog-card-media"


def base_url(value: str) -> str:
    parsed = urlsplit(value)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or parsed.path not in {"", "/"}
    ):
        raise argparse.ArgumentTypeError("Use an HTTP(S) origin without credentials or a path.")
    return value.rstrip("/")


def course_route(value: str) -> str:
    if not COURSE_ROUTE.fullmatch(value) or value in NON_FAMILY_ROUTES:
        raise argparse.ArgumentTypeError("Use /courses or a /courses/<family> landing route.")
    return value


def viewport(value: str) -> tuple[int, int]:
    if not re.fullmatch(r"[1-9][0-9]{2,3}x[1-9][0-9]{2,3}", value):
        raise argparse.ArgumentTypeError("Use WIDTHxHEIGHT, for example 390x844.")
    width, height = value.split("x")
    return int(width), int(height)


def output_directory(value: str) -> Path:
    path = Path(value)
    resolved = (path if path.is_absolute() else ROOT / path).resolve()
    scratch = (ROOT / ".tmp").resolve()
    if not resolved.is_relative_to(scratch) or resolved == scratch:
        raise argparse.ArgumentTypeError("Output must be a subdirectory of this project's .tmp.")
    return resolved


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", action="append", type=base_url)
    parser.add_argument("--family", action="append", default=[])
    parser.add_argument("--route", action="append", type=course_route, default=[])
    parser.add_argument("--viewport", action="append", type=viewport)
    parser.add_argument("--theme", action="append", choices=["light", "dark"])
    parser.add_argument(
        "--output-dir",
        type=output_directory,
        default=output_directory(".tmp/screenshots/course-illustration-check"),
    )
    args = parser.parse_args(argv)
    try:
        args.route.extend(course_route(f"/courses/{family}") for family in args.family)
    except argparse.ArgumentTypeError as error:
        parser.error(str(error))
    args.base_url = args.base_url or ["http://localhost:8000"]
    args.viewport = args.viewport or DEFAULT_VIEWPORTS
    args.theme = args.theme or ["light", "dark"]
    return args


def discover_routes(page: Page, origin: str) -> list[str]:
    response = page.goto(f"{origin}/courses", wait_until="networkidle")
    if response is None or response.status != 200:
        raise RuntimeError("The course catalogue did not return HTTP 200.")
    paths = page.locator("main a[href]").evaluate_all(
        "links => links.filter(link => new URL(link.href).origin === location.origin)"
        ".map(link => new URL(link.href).pathname)"
    )
    return [
        "/courses",
        *sorted(
            {
                path
                for path in paths
                if COURSE_ROUTE.fullmatch(path) and path not in {"/courses", *NON_FAMILY_ROUTES}
            }
        ),
    ]


def inspect_page(page: Page, *, route: str, theme: str, width: int) -> dict:
    catalogue = route == "/courses"
    selector = f".hero-collage-card, {CATALOGUE_MEDIA}" if catalogue else ".family-hero-art"
    slots = page.locator(selector)
    failures = []
    if slots.count() == 0:
        failures.append("No illustration slot was rendered.")
    images = slots.locator("img")
    # Full-page capture does not itself enter the viewport or start lazy images.
    # Visit every media slot first; hidden theme companions may remain deferred.
    for slot in slots.all():
        if slot.is_visible():
            slot.scroll_into_view_if_needed()
            slot.locator("img:visible").evaluate_all(
                "imgs => Promise.all(imgs.map(img => img.decode().catch(() => {})))"
            )
    page.evaluate("window.scrollTo({top: 0, left: 0, behavior: 'instant'})")
    image_records = []
    geometry = []
    for index, slot in enumerate(slots.all()):
        slot_images = slot.locator("img")
        visible = slot.locator("img:visible")
        expected_visible = True
        themed = slot.locator(".doodle-light, .doodle-dark").count() > 0
        if slot_images.count() == 0:
            failures.append(f"Slot {index}: missing illustration; placeholder or empty slot.")
        if visible.count() != int(expected_visible):
            failures.append(f"Slot {index}: wrong number of visible images.")
        if themed and (
            slot.locator(".doodle-light").count() != 1 or slot.locator(".doodle-dark").count() != 1
        ):
            failures.append(f"Slot {index}: incomplete light/dark illustration pair.")
        if (
            (themed or not catalogue)
            and expected_visible
            and (slot.locator(f".doodle-{theme}:visible").count() != 1)
        ):
            failures.append(f"Slot {index}: expected {theme} artwork is not visible.")
        placeholder = slot.evaluate(
            """(el, mediaSelector) => ({
                classPresent: el.matches('[class*="placeholder"]') ||
                    Boolean(el.querySelector('[class*="placeholder"]')),
                textPresent: el.matches(mediaSelector) && Boolean(el.textContent.trim()),
            })""",
            CATALOGUE_MEDIA,
        )
        if placeholder["classPresent"] or placeholder["textPresent"]:
            failures.append(f"Slot {index}: placeholder class or media text remains.")
        geometry.append(slot.bounding_box())
    for image in images.all():
        record = image.evaluate("""img => ({
            path: new URL(img.src).pathname,
            natural_width: img.naturalWidth, natural_height: img.naturalHeight,
            complete: img.complete, alt: img.getAttribute('alt'),
            decoding: img.decoding,
        })""")
        record["visible"] = image.is_visible()
        if record["visible"] and (not record["complete"] or not record["natural_width"]):
            failures.append("An illustration failed to decode.")
        if record["alt"] != "" or record["decoding"] != "async":
            failures.append(
                "An illustration lost its decorative or asynchronous decoding semantics."
            )
        image_records.append(record)
    overflow = page.evaluate("document.documentElement.scrollWidth > innerWidth")
    if overflow:
        failures.append("The page overflows horizontally.")
    clipped = page.locator(
        ".family-hero h1, .family-status > *, .family-hero-actions a, "
        ".family-skills h3, .family-proof a"
    ).evaluate_all("""els => els.filter(el => {
        const r = el.getBoundingClientRect();
        return r.width && (r.x < -1 || r.right > innerWidth + 1);
    }).map(el => ({tag:el.tagName, class:el.className}))""")
    if clipped:
        failures.append("A hero, skill, or proof element is clipped by the viewport.")
    return {
        "failures": failures,
        "geometry": geometry,
        "images": image_records,
        "overflow": overflow,
        "clipped": clipped,
    }


def run(args: argparse.Namespace) -> dict:
    args.output_dir.mkdir(parents=True, exist_ok=True)
    records = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        for origin in args.base_url:
            discovery = browser.new_page()
            try:
                routes = list(dict.fromkeys(args.route)) or discover_routes(discovery, origin)
            finally:
                discovery.close()
            host_key = hashlib.sha256(origin.encode()).hexdigest()[:10]
            for route in routes:
                for width, height in args.viewport:
                    for theme in args.theme:
                        page = browser.new_page(
                            viewport={"width": width, "height": height},
                            color_scheme=theme,
                            reduced_motion="reduce",
                        )
                        page.add_init_script(
                            f"localStorage.setItem('darkMode', '{str(theme == 'dark').lower()}')"
                        )
                        record = {
                            "origin": origin,
                            "route": route,
                            "theme": theme,
                            "viewport": [width, height],
                            "failures": [],
                        }
                        stem = f"{host_key}-{route.strip('/').replace('/', '-')}-{width}-{theme}"
                        try:
                            response = page.goto(f"{origin}{route}", wait_until="networkidle")
                            record["status"] = response.status if response else None
                            if record["status"] != 200:
                                # Never capture a development error page with debug settings.
                                raise RuntimeError("The requested page did not return HTTP 200.")
                            page.evaluate("document.fonts.ready")
                            record.update(inspect_page(page, route=route, theme=theme, width=width))
                            page.screenshot(
                                path=str(args.output_dir / f"{stem}.png"), full_page=True
                            )
                            page.screenshot(path=str(args.output_dir / f"{stem}-opening.png"))
                            record["screenshot"] = f"{stem}.png"
                        except Exception as error:
                            record["failures"].append(
                                f"Browser check failed: {type(error).__name__}"
                            )
                        finally:
                            page.close()
                        records.append(record)
        browser.close()
    report = {
        "checks": len(records),
        "failed": sum(bool(r["failures"]) for r in records),
        "records": records,
    }
    (args.output_dir / "report.json").write_text(json.dumps(report, indent=2) + "\n")
    return report


def main() -> int:
    args = parse_args()
    report = run(args)
    print(
        json.dumps(
            {
                "checks": report["checks"],
                "failed": report["failed"],
                "report": str(args.output_dir / "report.json"),
            }
        )
    )
    return int(report["failed"] > 0)


if __name__ == "__main__":
    raise SystemExit(main())
