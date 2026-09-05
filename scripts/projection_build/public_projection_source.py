"""Load and fully check the built public projection from its files.

This is the build and ingest side of the public projection: it reads the
manifest, verifies every artifact digest and the complete-tree digest, checks
the declared counts against the records actually present, validates the wiki
graph and the editorial route manifest, and returns the whole checked
catalogue.

Nothing a public request touches calls it. The site reads articles, podcasts,
books, people, wiki pages and their assets from the database; this module is
what ``scripts/prod/import_public_content.py`` uses to decide the files are
worth importing, and what the parity tooling compares against.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from django.core.exceptions import ImproperlyConfigured

from content.podcast_routes import PODCAST_HIERARCHICAL_ONLY_SLUGS, podcast_canonical_path
from content.public_data import _validate_wiki_graph, ordered_podcasts, safe_public_graph_url

EXPECTED_SELECTION = "preferred"
#: The collections the built files carry. Events are among them: the files were
#: built before events became database rows, and this module reads the files as
#: they are. The import skips that collection.
COLLECTION_NAMES = (
    "articles",
    "podcasts",
    "books",
    "people",
    "events",
    "wiki",
    "courses",
    "media",
)
REQUIRED_COUNT_KEYS = frozenset({*COLLECTION_NAMES, "transcripts"})
from content.public_text import strip_leaked_target_attributes, target_attribute_count

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_PROJECTION_ROOT = REPOSITORY_ROOT / "temporary" / "content" / "public_projection"

PODCAST_PLATFORM_FILENAME = "podcast_platforms.json"
EXPECTED_PODCAST_PLATFORM_PROVIDERS = (
    "apple",
    "spotify",
    "youtube",
    "spotify_for_creators",
)
EDITORIAL_ROUTE_MIGRATION_FILENAME = "editorial_route_migration.json"
EDITORIAL_ROUTE_MIGRATION_SCHEMA = (
    REPOSITORY_ROOT / "_docs" / "compatibility" / "editorial-route-migration.schema.json"
)
EXPECTED_LEAKED_TARGET_MARKERS = {"articles": 0, "people": 10}
MEDIA_TREE_PREFIX = "media/"
EXPECTED_TREE_DIGEST_SCOPE = (
    "projection artifacts and wiki assets; excludes manifest.json and media/"
)
EXPECTED_MEDIA_STORAGE_FIELDS = {
    "location": "object-store",
    "records": "media.json",
    "integrity": "per-record provenance.checksum",
}
EDITORIAL_ROUTE_COLLECTIONS = {
    "articles": "/blog",
    "podcasts": "/podcast",
    "books": "/books",
    "people": "/people",
}
EXPECTED_REVISIONS = {
    "preferred_content": "1375c506dbce85c7c0e5e61f83c753128c5a48d1",
    "fallback_selection": "373bef2912342ece1d2a2d2a9395aa3417243283",
    "legacy_main": "ee43d3fa0929faf691178d79f19528e6f15a83e5",
    "wiki": "988b79d0d655bf4755945c3118544cb9e0dbead6",
    "courses": "98a235283904b4ef9ad29e196298540756cf1bcc",
}
EXPECTED_RECORD_SOURCES = {
    "articles": {("DataTalksClub/content", EXPECTED_REVISIONS["preferred_content"])},
    "podcasts": {("DataTalksClub/content", EXPECTED_REVISIONS["preferred_content"])},
    "books": {("DataTalksClub/content", EXPECTED_REVISIONS["preferred_content"])},
    "people": {("DataTalksClub/datatalksclub.github.io", EXPECTED_REVISIONS["legacy_main"])},
    "events": {("DataTalksClub/datatalksclub.github.io", EXPECTED_REVISIONS["legacy_main"])},
    "wiki": {("DataTalksClub/podwiki", EXPECTED_REVISIONS["wiki"])},
    "courses": {("DataTalksClub/course-management-platform", EXPECTED_REVISIONS["courses"])},
    "media": {
        ("DataTalksClub/content", EXPECTED_REVISIONS["preferred_content"]),
        ("DataTalksClub/datatalksclub.github.io", EXPECTED_REVISIONS["legacy_main"]),
    },
}


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ImproperlyConfigured(f"Public projection cannot load {path.name}.") from exc


def _sha256(path: Path) -> str:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError as exc:
        raise ImproperlyConfigured(f"Public projection cannot read {path.name}.") from exc


def _tree_sha256(root: Path) -> str:
    """Digest the projection artifacts and wiki assets, excluding the media objects.

    Media objects live in an object store and are verified per record against
    ``provenance.checksum``, so they are outside the complete-tree digest.  The symlink
    rejection deliberately still covers ``media/``: a symlink anywhere below the root is
    a hard failure whether or not its bytes contribute to the digest.
    """

    digest = hashlib.sha256()
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        if path.is_symlink():
            raise ImproperlyConfigured("Public projection tree contains a symlink.")
        relative_path = path.relative_to(root).as_posix()
        if path.name == "manifest.json" or relative_path.startswith(MEDIA_TREE_PREFIX):
            continue
        relative = relative_path.encode()
        payload = path.read_bytes()
        digest.update(len(relative).to_bytes(8, "big"))
        digest.update(relative)
        digest.update(len(payload).to_bytes(8, "big"))
        digest.update(payload)
    return digest.hexdigest()


def _canonical_json_sha256(payload: Any) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    return hashlib.sha256(encoded).hexdigest()


def _editorial_route_manifest_digest(manifest: dict[str, Any]) -> str:
    return _canonical_json_sha256(
        {key: value for key, value in manifest.items() if key != "content_sha256"}
    )


def _expected_editorial_routes(
    projection: dict[str, Any],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    finals: list[dict[str, Any]] = []
    aliases: list[dict[str, Any]] = []
    for collection, prefix in EDITORIAL_ROUTE_COLLECTIONS.items():
        for record in projection[collection]:
            final_path = record["public_path"]
            clean_path = f"{prefix}/{record['slug']}"
            expected_path = (
                podcast_canonical_path(record["slug"])
                if collection == "podcasts"
                else f"{clean_path}.html"
            )
            if final_path != expected_path:
                raise ImproperlyConfigured("Public projection editorial final mismatch.")
            finals.append(
                {
                    "collection": collection,
                    "record_key": record["slug"],
                    "final_path": final_path,
                    "source": dict(record["provenance"]),
                }
            )
            if collection == "podcasts" and record["slug"] in PODCAST_HIERARCHICAL_ONLY_SLUGS:
                continue
            for source_path in (clean_path, f"{clean_path}/"):
                aliases.append(
                    {
                        "collection": collection,
                        "record_key": record["slug"],
                        "source_path": source_path,
                        "final_path": final_path,
                        "status_code": 301,
                        "query_policy": "preserve_raw",
                    }
                )
    finals.sort(key=lambda item: item["final_path"])
    aliases.sort(key=lambda item: item["source_path"])
    return finals, aliases


def _editorial_route_counts(projection: dict[str, Any]) -> tuple[int, int]:
    """Return the (finals, aliases) counts the loaded projection implies.

    Derived from the collections actually present, so publishing or retiring
    content never needs a matching hand-edit here.
    """

    finals_count = sum(len(projection[name]) for name in EDITORIAL_ROUTE_COLLECTIONS)
    hierarchical_only = sum(
        1
        for record in projection.get("podcasts", ())
        if record["slug"] in PODCAST_HIERARCHICAL_ONLY_SLUGS
    )
    aliases_count = 2 * (finals_count - hierarchical_only)
    return finals_count, aliases_count


def _validate_editorial_route_manifest(
    route_manifest: Any,
    projection: dict[str, Any],
    artifact_digests: dict[str, str],
) -> None:
    if not isinstance(route_manifest, dict) or set(route_manifest) != {
        "schema_version",
        "schema",
        "provenance",
        "counts",
        "finals",
        "aliases",
        "content_sha256",
    }:
        raise ImproperlyConfigured("Public projection editorial route manifest shape mismatch.")
    expected_schema = {
        "path": "_docs/compatibility/editorial-route-migration.schema.json",
        "sha256": _sha256(EDITORIAL_ROUTE_MIGRATION_SCHEMA),
    }
    if route_manifest["schema_version"] != 1 or route_manifest["schema"] != expected_schema:
        raise ImproperlyConfigured("Public projection editorial route schema mismatch.")

    expected_source_artifacts = {
        f"{name}.json": artifact_digests[f"{name}.json"] for name in EDITORIAL_ROUTE_COLLECTIONS
    }
    source_revisions = sorted(
        {
            (record["provenance"]["repository"], record["provenance"]["revision"])
            for name in EDITORIAL_ROUTE_COLLECTIONS
            for record in projection[name]
        }
    )
    expected_provenance = {
        "builder": "scripts/build_public_projection.py",
        "projection_schema_version": 1,
        "projection_selection_mode": projection["manifest"]["selection_mode"],
        "source_artifacts": expected_source_artifacts,
        "source_revisions": [
            {"repository": repository, "revision": revision}
            for repository, revision in source_revisions
        ],
    }
    if route_manifest["provenance"] != expected_provenance:
        raise ImproperlyConfigured("Public projection editorial route provenance mismatch.")
    expected_finals_count, expected_aliases_count = _editorial_route_counts(projection)
    if route_manifest["counts"] != {
        "finals": expected_finals_count,
        "aliases": expected_aliases_count,
    }:
        raise ImproperlyConfigured("Public projection editorial route count mismatch.")

    finals = route_manifest["finals"]
    aliases = route_manifest["aliases"]
    if not isinstance(finals, list) or len(finals) != expected_finals_count:
        raise ImproperlyConfigured("Public projection editorial final count mismatch.")
    if not isinstance(aliases, list) or len(aliases) != expected_aliases_count:
        raise ImproperlyConfigured("Public projection editorial alias count mismatch.")
    try:
        final_paths = [item["final_path"] for item in finals]
        alias_paths = [item["source_path"] for item in aliases]
        alias_graph = {item["source_path"]: item["final_path"] for item in aliases}
    except (KeyError, TypeError) as exc:
        raise ImproperlyConfigured(
            "Public projection editorial route record shape mismatch."
        ) from exc
    if len(final_paths) != len(set(final_paths)) or len(alias_paths) != len(set(alias_paths)):
        raise ImproperlyConfigured("Public projection editorial route duplicate.")
    if set(final_paths) & set(alias_paths):
        raise ImproperlyConfigured("Public projection editorial route collision.")
    for source_path, target_path in alias_graph.items():
        visited = {source_path}
        cursor = target_path
        followed_alias = False
        while cursor in alias_graph:
            followed_alias = True
            if cursor in visited:
                raise ImproperlyConfigured("Public projection editorial redirect loop.")
            visited.add(cursor)
            cursor = alias_graph[cursor]
        if followed_alias:
            raise ImproperlyConfigured("Public projection editorial redirect chain.")
    final_path_set = set(final_paths)
    if any(target not in final_path_set for target in alias_graph.values()):
        raise ImproperlyConfigured("Public projection editorial redirect target mismatch.")

    expected_finals, expected_aliases = _expected_editorial_routes(projection)
    if finals != expected_finals or aliases != expected_aliases:
        raise ImproperlyConfigured("Public projection editorial route inventory is incomplete.")
    if route_manifest["content_sha256"] != _editorial_route_manifest_digest(route_manifest):
        raise ImproperlyConfigured("Public projection editorial route content digest mismatch.")


def load_checked_projection(root: Path | None = None) -> dict[str, Any]:
    root = root or DEFAULT_PROJECTION_ROOT
    try:
        manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ImproperlyConfigured(
            "Public projection is absent (no manifest.json; default catalogues are empty)."
        ) from exc
    except (OSError, json.JSONDecodeError) as exc:
        raise ImproperlyConfigured("Public projection cannot load manifest.json.") from exc
    if manifest.get("schema_version") != 1 or manifest.get("selection_mode") != EXPECTED_SELECTION:
        raise ImproperlyConfigured("Unsupported public projection selection.")
    # The manifest declares its own counts for this build; they are not pinned to a fixed
    # inventory because publishing or retiring content legitimately moves every one of
    # them.  What must hold, regardless of size, is that the declaration is well-formed
    # and that every artifact and derived count actually matches what is declared -
    # checked below, collection by collection, as each artifact is loaded.
    counts = manifest.get("counts")
    if (
        not isinstance(counts, dict)
        or set(counts) != REQUIRED_COUNT_KEYS
        or any(
            isinstance(value, bool) or not isinstance(value, int) or value < 0
            for value in counts.values()
        )
    ):
        raise ImproperlyConfigured("Public projection count declarations are malformed.")
    if manifest.get("tree_digest_scope") != EXPECTED_TREE_DIGEST_SCOPE:
        raise ImproperlyConfigured("Public projection tree digest scope is not declared.")
    media_storage = manifest.get("media_storage")
    if (
        not isinstance(media_storage, dict)
        or set(media_storage) != {"location", "records", "count", "integrity"}
        or {key: media_storage[key] for key in EXPECTED_MEDIA_STORAGE_FIELDS}
        != EXPECTED_MEDIA_STORAGE_FIELDS
        or media_storage["count"] != counts["media"]
    ):
        raise ImproperlyConfigured("Public projection media storage declaration mismatch.")
    if manifest.get("tree_sha256") != _tree_sha256(root):
        raise ImproperlyConfigured("Public projection complete-tree digest mismatch.")
    sources = manifest.get("sources", {})
    if {key: value.get("revision") for key, value in sources.items()} != EXPECTED_REVISIONS:
        raise ImproperlyConfigured(
            "Public projection revisions do not match the accepted inventory."
        )
    if sources["preferred_content"].get("accepted") is not True:
        raise ImproperlyConfigured("Preferred projection source is not marked accepted.")
    if sources["fallback_selection"].get("accepted") is not False:
        raise ImproperlyConfigured("Fallback selection must remain explicitly unaccepted.")

    projection: dict[str, Any] = {"manifest": manifest}
    artifacts = manifest.get("artifacts", {})
    for name in COLLECTION_NAMES:
        filename = f"{name}.json"
        path = root / filename
        if _sha256(path) != artifacts.get(filename):
            raise ImproperlyConfigured(f"Public projection digest mismatch: {filename}.")
        records = _read_json(path)
        if not isinstance(records, list) or not records:
            raise ImproperlyConfigured(f"Public projection collection mismatch: {name}.")
        if len(records) != counts[name]:
            raise ImproperlyConfigured(f"Public projection declared count mismatch: {name}.")
        projection[name] = tuple(records)

    # Every media reference a content record carries must resolve to a real media
    # object.  This is the referential check that a fixed media-object total only ever
    # gestured at: it catches a broken or stale reference regardless of how many media
    # objects the projection happens to contain today.
    media_paths = {item["public_path"] for item in projection["media"]}
    for name in ("articles", "podcasts", "books", "people"):
        for record in projection[name]:
            image_path = record.get("image_path")
            if image_path and image_path not in media_paths:
                raise ImproperlyConfigured(
                    f"Public projection media reference does not resolve: {name}."
                )
            for block in record.get("blocks", ()) or ():
                if isinstance(block, dict) and block.get("kind") == "image":
                    if block.get("src") not in media_paths:
                        raise ImproperlyConfigured(
                            f"Public projection media reference does not resolve: {name}."
                        )

    platform_path = root / PODCAST_PLATFORM_FILENAME
    if _sha256(platform_path) != artifacts.get(PODCAST_PLATFORM_FILENAME):
        raise ImproperlyConfigured("Public podcast platform artifact digest mismatch.")
    platforms = _read_json(platform_path)
    if (
        not isinstance(platforms, list)
        or tuple(item.get("provider") for item in platforms if isinstance(item, dict))
        != EXPECTED_PODCAST_PLATFORM_PROVIDERS
    ):
        raise ImproperlyConfigured("Public podcast platform inventory mismatch.")
    if any(
        not isinstance(item, dict)
        or set(item) != {"key", "provider", "label", "title", "url", "dot"}
        or item.get("key") != item.get("provider")
        or not isinstance(item.get("label"), str)
        or not item["label"].strip()
        or item.get("title") != item.get("label")
        or not isinstance(item.get("url"), str)
        or not item["url"].startswith("https://")
        or not isinstance(item.get("dot"), str)
        for item in platforms
    ):
        raise ImproperlyConfigured("Public podcast platform record mismatch.")
    # What each platform is called and where it points is editorial content, so it
    # is not pinned here. The checks above are the code-owned part: every record
    # names a known provider, carries a non-empty label, and links over https.
    projection["podcast_platforms"] = tuple(platforms)

    transcript_count = sum(bool(item.get("transcript")) for item in projection["podcasts"])
    if transcript_count != counts["transcripts"]:
        raise ImproperlyConfigured("Public projection transcript count mismatch.")
    ordered_podcasts(projection["podcasts"])

    for name in ("wiki_graph", "wiki_search"):
        filename = f"{name}.json"
        path = root / filename
        if _sha256(path) != artifacts.get(filename):
            raise ImproperlyConfigured(f"Public projection digest mismatch: {filename}.")
        projection[name] = _read_json(path)
    _validate_wiki_graph(projection["wiki_graph"])

    for name in COLLECTION_NAMES:
        records = projection[name]
        if any(
            (
                record.get("provenance", {}).get("repository"),
                record.get("provenance", {}).get("revision"),
            )
            not in EXPECTED_RECORD_SOURCES[name]
            for record in records
        ):
            raise ImproperlyConfigured(f"Public projection record provenance mismatch: {name}.")
        paths = [item["public_path"] for item in records]
        slugs = [item["slug"] for item in records]
        if len(paths) != len(set(paths)) or len(slugs) != len(set(slugs)):
            raise ImproperlyConfigured(f"Public projection duplicate key: {name}.")
        projection[f"{name}_by_slug"] = {item["slug"]: item for item in records}
        projection[f"{name}_by_path"] = {item["public_path"]: item for item in records}
    if any(
        podcast["transcript"]
        and (
            podcast.get("transcript_provenance", {}).get("repository") != "DataTalksClub/content"
            or podcast.get("transcript_provenance", {}).get("revision")
            != EXPECTED_REVISIONS["preferred_content"]
        )
        for podcast in projection["podcasts"]
    ):
        raise ImproperlyConfigured("Public projection transcript provenance mismatch.")
    route_path = root / EDITORIAL_ROUTE_MIGRATION_FILENAME
    if _sha256(route_path) != artifacts.get(EDITORIAL_ROUTE_MIGRATION_FILENAME):
        raise ImproperlyConfigured("Public projection editorial route artifact digest mismatch.")
    route_manifest = _read_json(route_path)
    _validate_editorial_route_manifest(route_manifest, projection, artifacts)
    projection["editorial_route_migration"] = route_manifest
    projection["editorial_route_aliases_by_path"] = {
        item["source_path"]: item for item in route_manifest["aliases"]
    }
    return projection


