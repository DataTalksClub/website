# /// script
# requires-python = ">=3.13"
# dependencies = ["pillow==12.3.0"]
# ///
"""Check native PNG/WebP pixel equality and alpha; visual review is still required."""

import argparse
import hashlib
import json
from pathlib import Path

from PIL import Image


def verify_pair(source: Path, target: Path) -> dict:
    with Image.open(source) as png, Image.open(target) as webp:
        if png.format != "PNG" or webp.format != "WEBP":
            raise ValueError("Expected a PNG source and a WebP destination")
        original, installed = png.convert("RGBA"), webp.convert("RGBA")
        if original.size != installed.size or original.tobytes() != installed.tobytes():
            raise ValueError(f"Native decoded RGBA differs: {target}")
        alpha = installed.getchannel("A")
        if alpha.getextrema() != (0, 255):
            raise ValueError(f"Expected transparent exterior and opaque artwork: {target}")
        width, height = installed.size
        borders = (
            (0, 0, width, 1),
            (0, height - 1, width, height),
            (0, 0, 1, height),
            (width - 1, 0, width, height),
        )
        if any(alpha.crop(border).getextrema() != (0, 0) for border in borders):
            raise ValueError(f"Visible pixels touch the canvas border: {target}")
        dimensions = installed.size
    return {
        "source_png": str(source),
        "installed_webp": str(target),
        "source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
        "installed_sha256": hashlib.sha256(target.read_bytes()).hexdigest(),
        "dimensions": dimensions,
        "decoded_rgba_equal": True,
        "all_four_borders_transparent": True,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source_dir", type=Path, help="Directory containing native PNGs")
    parser.add_argument("installed_dir", type=Path, help="Directory containing corresponding WebPs")
    parser.add_argument(
        "--prefix", default="", help="Prefix before each PNG stem in WebP filenames"
    )
    args = parser.parse_args()
    sources = sorted(args.source_dir.glob("*.png"))
    if not sources:
        parser.error("No PNG sources found")
    if "/" in args.prefix or "\\" in args.prefix:
        parser.error("Prefix must be a filename prefix, not a path")
    records = [
        verify_pair(source, args.installed_dir / f"{args.prefix}{source.stem}.webp")
        for source in sources
    ]
    print(json.dumps(records, indent=2))


if __name__ == "__main__":
    main()
