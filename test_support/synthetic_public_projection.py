"""A small, synthetic stand-in for the reviewed public projection.

The reviewed projection lives outside this repository
(``~/prod/dtc-data/content-staging/`` -- see
``_docs/architecture/database-only-content.md``), so CI and a fresh checkout
cannot reach it.  The tests that still exercise the projection file contract --
the tree digest, the manifest scope declaration, the editorial route manifest,
and the Markdown/link policies the description bridge renders under -- generate
the tree here instead: a handful of records whose provenance names the accepted
revisions, whose digests and counts are derived exactly the way
``scripts/prod/public_projection_source`` and ``scripts/build_public_projection``
derive them, and whose media references resolve.

Everything the loaders check is code-owned, so a generated tree can satisfy
every pin while staying synthetic: the accepted revisions, the marker canary
counts and the route manifest shape are constants of the checking modules, and
this factory reads those same constants rather than re-stating them.

Nothing here touches the real reviewed tree, and no module under ``scripts/``
changes: production keeps reading the corpus it has always read.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from scripts.prod.public_projection_source import (
    COLLECTION_NAMES,
    EDITORIAL_ROUTE_COLLECTIONS,
    EDITORIAL_ROUTE_MIGRATION_SCHEMA,
    EXPECTED_PODCAST_PLATFORM_PROVIDERS,
    EXPECTED_RECORD_SOURCES,
    EXPECTED_REVISIONS,
    EXPECTED_TREE_DIGEST_SCOPE,
    _editorial_route_counts,
    _editorial_route_manifest_digest,
    _expected_editorial_routes,
    _sha256,
    _tree_sha256,
)

#: Kramdown inline target metadata, the canary the projection build pins per
#: collection (``REVIEWED_TARGET_MARKER_COUNTS``).  The synthetic people carry
#: exactly the accepted count; the synthetic articles carry none.
TARGET_MARKER = '{: target="blank" }'

_SINGULAR = {
    "articles": "article",
    "podcasts": "podcast",
    "books": "book",
    "people": "profile",
    "events": "event",
    "wiki": "wiki page",
    "courses": "course",
    "media": "media record",
}

_RECORD_PREFIX = {
    "articles": "/blog",
    "podcasts": "/podcast",
    "books": "/books",
    "people": "/people",
    "events": "/events/archive",
    "wiki": "/wiki/pages",
    "courses": "/courses/catalogue",
    "media": "/media",
}


def _record(collection: str, slug: str, *, provenance_pair: int = 0) -> dict[str, Any]:
    """One synthetic catalogue record the loaders accept.

    Each collection names the accepted (repository, revision) pairs it is
    built from; ``provenance_pair`` picks among them when there is more than
    one, mirroring the real projection whose records arrive from several
    accepted sources.
    """

    pairs = sorted(EXPECTED_RECORD_SOURCES[collection])
    repository, revision = pairs[min(provenance_pair, len(pairs) - 1)]
    return {
        "slug": slug,
        "public_path": f"{_RECORD_PREFIX[collection]}/{slug}.html",
        "title": f"Synthetic {_SINGULAR[collection]} {slug}",
        "provenance": {
            "repository": repository,
            "revision": revision,
            "source_path": f"synthetic/{collection}/{slug}.md",
            "source_key": slug,
            "checksum": hashlib.sha256(f"{collection}/{slug}".encode()).hexdigest(),
        },
        "blocks": [{"kind": "text", "text": f"A synthetic {_SINGULAR[collection]} record."}],
    }


def _people_records() -> list[dict[str, Any]]:
    """Two profiles carrying exactly the accepted target-marker canary count."""

    accepted_markers = 10
    rich = _record("people", "synthetic-projection-rich")
    sparse = _record("people", "synthetic-projection-sparse", provenance_pair=1)
    per_profile = accepted_markers // 2
    for record in (rich, sparse):
        record["blocks"] = [
            {
                "kind": "text",
                "text": (f"A synthetic profile biography. {TARGET_MARKER} " * per_profile).strip(),
            }
        ]
    return [rich, sparse]


def _podcast_records() -> list[dict[str, Any]]:
    # Positive season/episode integers and a published date are what the
    # listing order reads; transcripts stay absent, so the manifest declares
    # none.
    records = [
        _record("podcasts", f"synthetic-episode-{index}", provenance_pair=index % 2)
        for index in range(1, 4)
    ]
    for index, record in enumerate(records, start=1):
        record["season"] = index
        record["episode"] = 100 - index
        record["published"] = f"2026-08-{index:02d}"
        record["transcript"] = ""
    return records


def build_synthetic_projection(root: Path) -> Path:
    """Write the whole synthetic projection tree under ``root`` and return it."""

    root.mkdir(parents=True, exist_ok=True)
    counts: dict[str, int] = {}
    for name in COLLECTION_NAMES:
        if name == "people":
            records = _people_records()
        elif name == "podcasts":
            records = _podcast_records()
        elif name == "media":
            records = [
                {
                    "slug": f"synthetic-media-{index}",
                    "record_key": f"images/synthetic/{index}.png",
                    "public_path": f"/media/synthetic/{index}.png",
                    "provenance": {
                        "repository": sorted(EXPECTED_RECORD_SOURCES["media"])[index % 2][0],
                        "revision": sorted(EXPECTED_RECORD_SOURCES["media"])[index % 2][1],
                        "checksum": hashlib.sha256(f"synthetic-media-{index}".encode()).hexdigest(),
                    },
                }
                for index in range(1, 4)
            ]
        else:
            records = [
                _record(name, f"synthetic-{name}-{index}", provenance_pair=index % 2)
                for index in range(1, 3)
            ]
        counts[name] = len(records)
        (root / f"{name}.json").write_text(
            json.dumps(records, ensure_ascii=False, indent=1) + "\n", encoding="utf-8"
        )

    providers = list(EXPECTED_PODCAST_PLATFORM_PROVIDERS)
    (root / "podcast_platforms.json").write_text(
        json.dumps(
            [
                {
                    "key": provider,
                    "provider": provider,
                    "label": f"Synthetic {provider}",
                    "title": f"Synthetic {provider}",
                    "url": f"https://{provider}.example/subscription",
                    "dot": provider[0],
                }
                for provider in providers
            ],
            ensure_ascii=False,
            indent=1,
        )
        + "\n",
        encoding="utf-8",
    )
    counts["transcripts"] = 0

    graph = {
        "nodes": [
            {
                "id": "synthetic-node-a",
                "label": "Synthetic node A",
                "title": "Synthetic node A",
                "type": "page",
                "url": "/wiki/pages/synthetic-wiki-1.html",
            },
            {
                "id": "synthetic-node-b",
                "label": "Synthetic node B",
                "title": "Synthetic node B",
                "type": "page",
                "url": "",
            },
        ],
        "links": [
            {
                "source": "synthetic-node-a",
                "target": "synthetic-node-b",
                "kind": "related",
                "weight": 1,
            }
        ],
    }
    (root / "wiki_graph.json").write_text(
        json.dumps(graph, ensure_ascii=False, indent=1) + "\n", encoding="utf-8"
    )
    (root / "wiki_search.json").write_text(
        json.dumps({"documents": []}, ensure_ascii=False, indent=1) + "\n", encoding="utf-8"
    )

    artifacts = {path.name: _sha256(path) for path in sorted(root.glob("*.json"))}

    projection: dict[str, Any] = {
        "manifest": {"selection_mode": "preferred"},
        **{
            name: tuple(json.loads((root / f"{name}.json").read_text(encoding="utf-8")))
            for name in EDITORIAL_ROUTE_COLLECTIONS
        },
    }
    finals, aliases = _expected_editorial_routes(projection)
    finals_count, aliases_count = _editorial_route_counts(projection)
    source_revisions = sorted(
        {
            (record["provenance"]["repository"], record["provenance"]["revision"])
            for name in EDITORIAL_ROUTE_COLLECTIONS
            for record in projection[name]
        }
    )
    route_manifest = {
        "schema_version": 1,
        "schema": {
            "path": "_docs/compatibility/editorial-route-migration.schema.json",
            "sha256": _sha256(EDITORIAL_ROUTE_MIGRATION_SCHEMA),
        },
        "provenance": {
            "builder": "scripts/build_public_projection.py",
            "projection_schema_version": 1,
            "projection_selection_mode": "preferred",
            "source_artifacts": {
                f"{name}.json": artifacts[f"{name}.json"] for name in EDITORIAL_ROUTE_COLLECTIONS
            },
            "source_revisions": [
                {"repository": repository, "revision": revision}
                for repository, revision in source_revisions
            ],
        },
        "counts": {"finals": finals_count, "aliases": aliases_count},
        "finals": finals,
        "aliases": aliases,
    }
    route_manifest["content_sha256"] = _editorial_route_manifest_digest(route_manifest)
    (root / "editorial_route_migration.json").write_text(
        json.dumps(route_manifest, ensure_ascii=False, indent=1) + "\n", encoding="utf-8"
    )
    artifacts["editorial_route_migration.json"] = _sha256(root / "editorial_route_migration.json")

    manifest = {
        "schema_version": 1,
        "selection_mode": "preferred",
        "counts": counts,
        "tree_digest_scope": EXPECTED_TREE_DIGEST_SCOPE,
        "media_storage": {
            "location": "object-store",
            "records": "media.json",
            "integrity": "per-record provenance.checksum",
            "count": counts["media"],
        },
        "tree_sha256": _tree_sha256(root),
        "sources": {
            name: {
                "revision": revision,
                "accepted": name == "preferred_content",
            }
            for name, revision in EXPECTED_REVISIONS.items()
        },
        "artifacts": artifacts,
    }
    (root / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=1) + "\n", encoding="utf-8"
    )
    return root


def build_synthetic_bridge() -> dict[str, Any]:
    """Build the synthetic event-description bridge the validators accept.

    Every pin the bridge contract checks is a code-owned constant: the audit
    anchors, the reconciliation counts, the link-review inventory.  The
    synthetic entries fill the pinned match and gap totals with generated
    identities and one sanitized paragraph each, so the whole contract --
    schema binding, per-entry digests, sorting, public safety -- is exercised
    without the reviewed corpus's 660 KB of real speaker text.
    """

    from scripts.staging import event_description_bridge as bridge_contract

    matches: list[dict[str, Any]] = []
    for index in range(1, bridge_contract.EXPECTED_MATCH_COUNT + 1):
        # The provider's name is a forbidden token inside a stored bridge, so
        # the synthetic source keys never spell it.
        source_key = f"synthetic-provider-event-{index:04d}"
        description_html = (
            f'<p class="mt-4 leading-7">A synthetic reviewed event description, entry {index}.</p>'
        )
        entry: dict[str, Any] = {
            "target": {
                "repository": bridge_contract.LEGACY_REPOSITORY,
                "revision": bridge_contract.LEGACY_REVISION,
                "source_path": bridge_contract.LEGACY_SOURCE_PATH,
                "source_key": source_key,
                "checksum": bridge_contract.LEGACY_SOURCE_CHECKSUM,
            },
            "source_identity_sha256": hashlib.sha256(f"identity/{source_key}".encode()).hexdigest(),
            "source_description_sha256": hashlib.sha256(
                f"description/{source_key}".encode()
            ).hexdigest(),
            "match_basis": "exact_normalized_provider_path",
            "description_html": description_html,
            "description_text": bridge_contract.description_plain_text(description_html),
        }
        entry["entry_sha256"] = bridge_contract.canonical_json_sha256(
            {key: value for key, value in entry.items() if key != "entry_sha256"}
        )
        matches.append(entry)

    gaps = sorted(
        (
            {
                "source_identity_sha256": hashlib.sha256(
                    f"gap/synthetic-{index:04d}".encode()
                ).hexdigest(),
                "reason": "no_exact_projection_luma_path",
            }
            for index in range(1, bridge_contract.EXPECTED_GAP_COUNT + 1)
        ),
        key=lambda gap: gap["source_identity_sha256"],
    )

    bridge: dict[str, Any] = {
        "schema_version": bridge_contract.BRIDGE_SCHEMA_VERSION,
        "schema": {
            "path": bridge_contract.BRIDGE_SCHEMA_PUBLIC_PATH,
            "sha256": bridge_contract._schema_sha256(),
        },
        "source": {
            "exporter_revision": bridge_contract.EXPORTER_REVISION,
            "public_event_allowlist_sha256": bridge_contract.PUBLIC_EVENT_ALLOWLIST_SHA256,
            "description_manifest_sha256": bridge_contract.DESCRIPTION_MANIFEST_SHA256,
            "safe_source_sha256": bridge_contract.SAFE_SOURCE_SHA256,
        },
        "projection": {
            "events_sha256": bridge_contract.BASELINE_EVENTS_SHA256,
            "event_count": bridge_contract.EXPECTED_EVENT_COUNT,
            "repository": bridge_contract.LEGACY_REPOSITORY,
            "revision": bridge_contract.LEGACY_REVISION,
            "source_path": bridge_contract.LEGACY_SOURCE_PATH,
            "source_checksum": bridge_contract.LEGACY_SOURCE_CHECKSUM,
        },
        "policies": {
            "matching": bridge_contract.MATCHING_POLICY_VERSION,
            "markdown": bridge_contract.MARKDOWN_POLICY_VERSION,
            "links": bridge_contract.LINK_POLICY_VERSION,
        },
        "counts": {
            "source_pairs": bridge_contract.EXPECTED_PAIR_COUNT,
            "matches": bridge_contract.EXPECTED_MATCH_COUNT,
            "gaps": bridge_contract.EXPECTED_GAP_COUNT,
            "described_events": bridge_contract.EXPECTED_MATCH_COUNT,
            "undescribed_events": bridge_contract.EXPECTED_UNDESCRIBED_COUNT,
            "title_and_instant_equal": 36,
            "title_equal_instant_different": 113,
            "title_different_instant_equal": 2,
            "title_and_instant_different": 8,
        },
        "matches": matches,
        "gaps": gaps,
        "link_review": {
            "url_occurrences": bridge_contract.EXPECTED_URL_OCCURRENCES,
            "distinct_url_literals": bridge_contract.EXPECTED_DISTINCT_URLS,
            "decision_counts": dict(bridge_contract.EXPECTED_LINK_DECISION_COUNTS),
            "decision_inventory_sha256": bridge_contract.LINK_REVIEW_INVENTORY_SHA256,
            "remote_images_omitted": bridge_contract.EXPECTED_REMOTE_IMAGES_OMITTED,
        },
    }
    bridge["content_sha256"] = bridge_contract.canonical_json_sha256(
        {key: value for key, value in bridge.items() if key != "content_sha256"}
    )
    return bridge


def write_synthetic_bridge(path: Path) -> Path:
    """Write the synthetic bridge artifact and return its path."""

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(build_synthetic_bridge(), ensure_ascii=False, indent=1) + "\n",
        encoding="utf-8",
    )
    return path


class _RegistryBoundRenderer:
    """The builder's renderer with every registry read bound to the synthetic tree.

    Rendering validates the generated HTML against the reviewed route registry
    again at call time, so the synthetic registry has to be in place for the
    life of the renderer, not only while it is built.
    """

    def __init__(self, renderer: Any, registry: tuple[set[str], dict[str, set[str]]]):
        self._renderer = renderer
        self._registry = registry

    def classify(self, destination: str) -> Any:
        return self._renderer.classify(destination)

    def render(self, markdown: str) -> Any:
        from scripts.staging import event_description_bridge as bridge_contract

        with patch_object(bridge_contract, "_reviewed_route_registry", lambda: self._registry):
            return self._renderer.render(markdown)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._renderer, name)


def synthetic_renderer(tree_root: Path) -> Any:
    """The bridge builder's own renderer, over the synthetic route registry.

    The link policy reads the projection's collections for the public route
    registry; pointing it at the synthetic tree gives the real Markdown and
    link policies without the real corpus.
    """

    from scripts.build_event_description_bridge import (
        DescriptionRenderer,
        _projection_routes_and_fragments,
    )
    from scripts.staging import event_description_link_policy as link_policy

    with patch_object(link_policy, "PROJECTION_ROOT", tree_root):
        registry = _projection_routes_and_fragments()
    public_paths, fragments = registry
    return _RegistryBoundRenderer(DescriptionRenderer(public_paths, fragments), registry)


def patch_object(target: Any, name: str, value: Any):
    """A tiny local alias so the factory needs no unittest import at module load."""

    from unittest.mock import patch

    return patch.object(target, name, value)
