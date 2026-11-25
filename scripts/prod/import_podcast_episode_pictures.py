#!/usr/bin/env python3
"""Import the generated podcast episode pictures into a database.

The pictures are drawn with banner-generator's ``dtc-event-speaker`` template and
kept outside the repository, in ``~/prod/dtc-data/episode-pictures-<date>/``, one
set per episode named by the episode's source slug:

* ``<slug>.png`` (1000 x 1000) -- the card picture, only for an episode whose
  recording has no event banner;
* ``<slug>-yt.png`` (1280 x 720) -- the video poster (also the YouTube thumbnail);
* ``<slug>-og.png`` (1200 x 630) -- the social preview.

Each lands in ``content.PodcastEpisodePicture`` keyed on ``(slug, variant)``, so a
re-run replaces the bytes and creates nothing new.  A file whose slug is not a
published episode is refused before anything is written.

    uv run --frozen python scripts/prod/import_podcast_episode_pictures.py \\
        --database .tmp/local.sqlite3 \\
        --reviewed-file ~/prod/dtc-data/episode-pictures-2026-11-25
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.prod.target import configure_target, reviewed_file_parser  # noqa: E402

SYNC_MODEL = "one-time"
BOOTSTRAPS_EMPTY_DATABASE = False

PICTURES_ROOT = Path.home() / "prod" / "dtc-data" / "episode-pictures-2026-11-25"
_FILE = re.compile(r"(?P<slug>[-a-zA-Z0-9_]+?)(?P<suffix>-yt|-og)?\.png\Z")
_VARIANT_BY_SUFFIX = {None: "card", "-yt": "poster", "-og": "social"}
_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


class EpisodePictureImportFailure(RuntimeError):
    """A safe refusal naming the condition, never a picture's bytes."""


def _pictures(root: Path) -> list[tuple[str, str, Path]]:
    if not root.is_dir():
        raise EpisodePictureImportFailure(f"not a directory: {root}")
    found = []
    for path in sorted(root.iterdir()):
        match = _FILE.fullmatch(path.name)
        if match is None:
            continue
        found.append((match["slug"], _VARIANT_BY_SUFFIX[match["suffix"]], path))
    return found


def run(*, path: Path | None = None, apply: bool = True) -> dict[str, Any]:
    from content import catalogue
    from content.models import PodcastEpisodePicture

    pictures = _pictures(path or PICTURES_ROOT)
    published = {record["slug"] for record in catalogue.podcasts()}
    unknown = sorted({slug for slug, _, _ in pictures} - published)
    if unknown:
        raise EpisodePictureImportFailure(f"not published episodes: {', '.join(unknown)}")
    for _, _, file in pictures:
        if not file.read_bytes().startswith(_PNG_SIGNATURE):
            raise EpisodePictureImportFailure(f"not a PNG: {file.name}")
    counts = {variant: 0 for variant in _VARIANT_BY_SUFFIX.values()}
    created = 0
    for slug, variant, file in pictures:
        counts[variant] += 1
        if not apply:
            continue
        _, was_created = PodcastEpisodePicture.objects.update_or_create(
            episode_slug=slug,
            variant=variant,
            defaults={"image": file.read_bytes(), "content_type": "image/png"},
        )
        created += was_created
    return {
        "total": len(pictures),
        "by_variant": counts,
        "created": created if apply else 0,
        "updated": len(pictures) - created if apply else 0,
        "applied": apply,
    }


def main(argv: list[str] | None = None) -> int:
    parser = reviewed_file_parser(__doc__, PICTURES_ROOT)
    args = parser.parse_args(argv)
    try:
        configure_target(parser, args)
        report = run(path=args.reviewed_file.expanduser().resolve(), apply=not args.dry_run)
    except EpisodePictureImportFailure as error:
        print(json.dumps({"error": str(error)}, indent=2))
        return 1
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
