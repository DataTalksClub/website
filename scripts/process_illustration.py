#!/usr/bin/env python3
"""Trim a transparent source image and export it as a WebP illustration.

The command uses ImageMagick because it is the same image pipeline used for the
homepage illustrations.  It accepts either a local source path or a public HTTP(S)
URL and writes the result atomically, so a failed conversion cannot leave a partial
asset in the static directory.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import Request, urlopen

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DOWNLOAD_DIRECTORY = REPOSITORY_ROOT / ".tmp" / "illustration-sources"
DEFAULT_FUZZ_PERCENT = 5.0


def _percentage(value: str) -> float:
    """Parse a fuzz percentage accepted by ImageMagick."""

    try:
        percentage = float(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("fuzz must be a number from 0 to 100") from error
    if not 0 <= percentage <= 100:
        raise argparse.ArgumentTypeError("fuzz must be a number from 0 to 100")
    return percentage


def _image_magick_command() -> str:
    """Return the installed ImageMagick executable."""

    for executable in ("magick", "convert"):
        if command := shutil.which(executable):
            return command
    raise RuntimeError("ImageMagick is required; install `magick` or `convert`")


def _download_source(source: str, download_directory: Path) -> Path:
    """Download a public source URL into the repository's temporary directory."""

    parsed = urlparse(source)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("source URL must use http or https")

    download_directory.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256(source.encode("utf-8")).hexdigest()[:16]
    suffix = Path(parsed.path).suffix or ".source"
    destination = download_directory / f"{digest}{suffix}"
    if destination.exists():
        return destination

    request = Request(source, headers={"User-Agent": "dtc-website-illustration-tool"})
    temporary = download_directory / f".{destination.name}.part"
    try:
        with urlopen(request, timeout=30) as response, temporary.open("wb") as output:
            shutil.copyfileobj(response, output)
        os.replace(temporary, destination)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise
    return destination


def _resolve_source(source: str, download_directory: Path) -> Path:
    """Resolve a local path or download an HTTP(S) source."""

    if urlparse(source).scheme:
        return _download_source(source, download_directory)

    path = Path(source).expanduser()
    if not path.is_file():
        raise FileNotFoundError(f"source image does not exist: {path}")
    return path


def process_illustration(
    source: Path, target: Path, *, fuzz_percent: float, image_magick: str
) -> None:
    """Trim transparent edge artifacts and atomically write a WebP target."""

    target.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{target.stem}.", suffix=".webp", dir=target.parent
    )
    os.close(descriptor)
    temporary = Path(temporary_name)
    command = [
        image_magick,
        str(source),
        "-fuzz",
        f"{fuzz_percent:g}%",
        "-trim",
        "+repage",
        str(temporary),
    ]
    try:
        subprocess.run(command, check=True)
        os.replace(temporary, target)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "source",
        help="local image path or public HTTP(S) URL; the source file is never modified",
    )
    parser.add_argument("target", type=Path, help="destination WebP path")
    parser.add_argument(
        "--fuzz",
        type=_percentage,
        default=DEFAULT_FUZZ_PERCENT,
        metavar="PERCENT",
        help=(
            "alpha-edge tolerance used only when finding the crop box "
            f"(default: {DEFAULT_FUZZ_PERCENT:g})"
        ),
    )
    parser.add_argument(
        "--download-directory",
        type=Path,
        default=DEFAULT_DOWNLOAD_DIRECTORY,
        help="temporary directory for URL sources",
    )
    arguments = parser.parse_args(argv)

    try:
        source = _resolve_source(arguments.source, arguments.download_directory)
        image_magick = _image_magick_command()
        process_illustration(
            source,
            arguments.target,
            fuzz_percent=arguments.fuzz,
            image_magick=image_magick,
        )
    except (OSError, RuntimeError, ValueError, subprocess.CalledProcessError) as error:
        print(f"Illustration processing failed: {error}", file=sys.stderr)
        return 1

    print(f"Wrote {arguments.target} from {source} using {arguments.fuzz:g}% fuzz")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
