"""Render the website-owned banners in the site design system.

Usage: ``uv run python scripts/banners/render.py [--out-dir DIR]``

Every banner in ``banners.json`` is rendered with headless Chromium from one HTML
template that uses the design-system tokens (``templates/core/_design_system.html``),
the self-hosted Quicksand cut, and the official DataTalks.Club logo file from the
brand assets.  The logo is always embedded from that file, never redrawn.
"""

from __future__ import annotations

import argparse
import base64
import html
import json
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[2]
SPECS = Path(__file__).with_name("banners.json")
FONT = ROOT / "core/static/core/fonts/quicksand-latin-var.woff2"
LOGO = ROOT / "content/docs_assets/images/brand-assets/svg/logo_main.svg"

# Design-system tokens (light theme) from templates/core/_design_system.html.
GROUNDS = {"cream": "#fdfaf3", "lavender": "#eff1fc", "mint": "#eff5ea"}
BADGES = {
    "bubble": ("#b9bff0", "#1e2136"),
    "gold": ("#e2c67a", "#1e2136"),
    "green": ("#2b7a35", "#ffffff"),
}
EYEBROWS = {"green": "#2b7a35", "indigo": "#5a62c4"}

CSS = """
@font-face { font-family: Quicksand; font-weight: 300 700;
  src: url(data:font/woff2;base64,%(font)s) format("woff2"); }
* { box-sizing: border-box; margin: 0; }
html, body { width: %(w)spx; height: %(h)spx; }
body { font-family: Quicksand, sans-serif; color: #1e2136; background: %(ground)s;
  padding: %(pad)spx; display: flex; flex-direction: column; position: relative;
  overflow: hidden; }
body::after { content: ""; position: absolute; inset: 0; border: 6px solid #1e2136;
  border-radius: 40px; pointer-events: none; }
.blob { position: absolute; right: -150px; top: -190px; width: 520px; height: 520px;
  border-radius: 50%% 44%% 55%% 46%%; background: %(blob)s; }
header { display: flex; justify-content: space-between; align-items: center; position: relative; }
.logo { height: %(logo_h)spx; }
.badge { font-size: 22px; font-weight: 700; letter-spacing: .16em; text-transform: uppercase;
  padding: 12px 28px; border: 3px solid #1e2136; border-radius: 999px;
  background: %(badge_bg)s; color: %(badge_fg)s; box-shadow: 5px 5px 0 rgba(30,33,54,.9); }
main { margin: auto 0; position: relative; }
.eyebrow { font-family: ui-monospace, "DejaVu Sans Mono", "Liberation Mono", monospace;
  font-size: 21px; font-weight: 700; letter-spacing: .14em; text-transform: uppercase;
  color: %(eyebrow)s; margin-bottom: 18px; }
h1 { font-size: %(title_px)spx; line-height: 1.05; font-weight: 700; letter-spacing: -.02em; }
.lede { font-size: 29px; line-height: 1.35; font-weight: 500; color: #4c5066;
  margin-top: 26px; max-width: 21em; }
footer { display: flex; justify-content: space-between; align-items: flex-end; position: relative; }
.person b { display: block; font-size: 36px; font-weight: 700; }
.person span { font-size: 24px; color: #5c6076; font-weight: 500; }
.when { background: #ffffff; border: 3px solid #1e2136; border-radius: 18px;
  padding: 18px 28px; box-shadow: 7px 7px 0 rgba(30,33,54,.9); }
.when i { font-style: normal; display: block; font-size: 20px; font-weight: 700;
  letter-spacing: .14em; text-transform: uppercase; color: #2b7a35;
  font-family: ui-monospace, "DejaVu Sans Mono", "Liberation Mono", monospace; }
.when b { display: block; font-size: 36px; }
.when span { font-size: 20px; color: #5c6076; font-weight: 500; }
"""


def page_html(spec: dict) -> str:
    width, height = spec["size"]
    square = width == height
    badge_bg, badge_fg = BADGES[spec["badge_tone"]]
    ground = GROUNDS[spec["ground"]]
    css = CSS % {
        "font": base64.b64encode(FONT.read_bytes()).decode(),
        "w": width,
        "h": height,
        "pad": 68 if square else 60,
        "ground": ground,
        "blob": "#eff1fc" if spec["ground"] != "lavender" else "#dfe2f7",
        "logo_h": 84 if square else 76,
        "badge_bg": badge_bg,
        "badge_fg": badge_fg,
        "eyebrow": EYEBROWS[spec["eyebrow_tone"]],
        "title_px": spec.get("title_px", 84 if square else 112),
    }
    logo = base64.b64encode(LOGO.read_bytes()).decode()
    esc = html.escape
    title = "<br>".join(esc(line) for line in spec["title"].split("<br>"))
    footer = ""
    if spec.get("person"):
        footer = (
            f'<footer><div class="person"><b>{esc(spec["person"])}</b>'
            f'<span>{esc(spec["role"])}</span></div>'
            f'<div class="when"><i>{esc(spec["date"])}</i><b>{esc(spec["time"])}</b>'
            f'<span>{esc(spec["note"])}</span></div></footer>'
        )
    badge = "" if spec["badge"] == spec["title"] else f'<div class="badge">{esc(spec["badge"])}</div>'
    return (
        f"<!doctype html><meta charset=utf-8><style>{css}</style><div class=blob></div>"
        f'<header><img class="logo" alt="DataTalks.Club" src="data:image/svg+xml;base64,{logo}">'
        f"{badge}</header>"
        f'<main><div class="eyebrow">{esc(spec["eyebrow"])}</div><h1>{title}</h1>'
        f'<p class="lede">{esc(spec["lede"])}</p></main>{footer}'
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out-dir", type=Path, help="write flat PNG copies here instead of the repo paths")
    args = parser.parse_args()
    specs = json.loads(SPECS.read_text())
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        for spec in specs:
            width, height = spec["size"]
            target = ROOT / spec["output"]
            if args.out_dir:
                args.out_dir.mkdir(parents=True, exist_ok=True)
                target = args.out_dir / Path(spec["output"]).name
            page = browser.new_page(viewport={"width": width, "height": height})
            page.set_content(page_html(spec))
            page.evaluate("document.fonts.ready")
            page.screenshot(path=str(target))
            page.close()
            print(target)
        browser.close()


if __name__ == "__main__":
    main()
