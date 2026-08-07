import argparse
from pathlib import Path
from urllib.parse import urlparse

from playwright.sync_api import sync_playwright


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Capture local DTC pages for tester inspection")
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--urls", nargs="+", required=True)
    parser.add_argument("--output", type=Path, default=Path(".tmp/screenshots"))
    parser.add_argument("--viewport", default="1280x720")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    width, height = (int(value) for value in args.viewport.lower().split("x", maxsplit=1))
    args.output.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page(viewport={"width": width, "height": height})
        for path in args.urls:
            response = page.goto(f"{args.base_url.rstrip('/')}/{path.lstrip('/')}")
            if response is None or response.status >= 400:
                raise RuntimeError(
                    f"Could not capture {path}: HTTP {response.status if response else 'none'}"
                )
            parsed = urlparse(path)
            stem = parsed.path.strip("/").replace("/", "-") or "home"
            output_path = args.output / f"{stem}-{width}x{height}.png"
            page.screenshot(path=output_path, full_page=True)
            print(output_path)
        browser.close()


if __name__ == "__main__":
    main()
