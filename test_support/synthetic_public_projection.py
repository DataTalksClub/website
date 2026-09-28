"""Synthetic stand-ins for the event-description bridge tests.

The reviewed public projection lives outside this repository, so those tests
cannot read it. They need two local substitutes: a handful of collection files
with public paths, which is all the link policy's route registry reads, and a
bridge artifact whose pins come from the bridge contract itself.

Nothing here touches the real reviewed tree.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

_ROUTE_COLLECTIONS = (
    "articles",
    "podcasts",
    "books",
    "people",
    "events",
    "wiki",
    "courses",
)

_RECORD_PREFIX = {
    "articles": "/blog",
    "podcasts": "/podcast",
    "books": "/books",
    "people": "/people",
    "events": "/events/archive",
    "wiki": "/wiki/pages",
    "courses": "/courses/catalogue",
}


def build_synthetic_projection(root: Path) -> Path:
    """Write the collection files the route registry reads and return ``root``."""

    root.mkdir(parents=True, exist_ok=True)
    for name in _ROUTE_COLLECTIONS:
        slug = f"synthetic-{name}"
        records = [
            {
                "slug": slug,
                "public_path": f"{_RECORD_PREFIX[name]}/{slug}.html",
                "title": f"Synthetic {name} {slug}",
                "blocks": [{"kind": "text", "text": f"A synthetic {name} record."}],
            }
        ]
        (root / f"{name}.json").write_text(
            json.dumps(records, ensure_ascii=False, indent=1) + "\n", encoding="utf-8"
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
