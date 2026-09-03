# Pinned route-contract inputs

What is left here is not a compatibility corpus. It is the pinned provenance the content pipeline
still reads: the exact public paths, source repositories, revisions, and source paths of the four
generated sites the editorial content came from. `content/route_contracts.py` decodes these files,
`content/inventory.py` filters them to base-path route contracts, and `content/services.py` and
`content_sync/dtc_content/` bind each ingested document and asset to one of those contracts.

| File | What it holds |
| --- | --- |
| `source-build-provenance.json` | Repository, revision, tool version, and generated-tree digest per source |
| `generated-path-baseline.jsonl` | Generated public paths for the main site, docs, FAQ, and Podwiki |
| `faq-fragment-contracts.jsonl` | FAQ question fragments under `/faq/` |
| `podwiki-graph-fragment-contracts.jsonl` | Podwiki graph fragments under `/podwiki/graph/` |
| `course-route-contracts.json` | Route contracts derived from the adopted course-platform URLconfs |
| `machine-contract-samples.json` | Machine-readable endpoints (feeds, sitemaps, OpenAPI, calendars) |

The rows join into one canonical inventory whose SHA-256 is
`31f505350566bfcde0a30109dadcfb3565042fd395b4c1bd151966f94d361332`. That digest is
`content.models.PUBLIC_CONTRACT_DIGEST` and is stored on every `ContentRelease`. It is computed
from these files by `content.route_contracts.public_contract_inventory_sha256`; there is no longer
a second checked-in copy of the joined rows.

`editorial-route-migration.schema.json` and `event-description-bridge.schema.json` are unrelated to
the above. They are live schemas for `scripts/build_public_projection.py`, `content/public_data.py`,
and `content/event_description_bridge.py`.

`development-legacy-identifiers.md` is also unrelated: it records the retired
`website-sandbox` deployment identifiers that `deploy/` and `.github/workflows/ci.yml` still name.

## What was removed

The frozen legacy-compatibility corpus and the machinery that compared this site against it were
deleted: the 23 MB crawl manifest, its difference ledger and schemas, the derived public-contract
artifact, the approved-expectation, target-observation, and SEO-parity schemas, the crawler,
differ, parity gate, target collector, link and expectation checkers, the compatibility monitoring
middleware, the manifest and pinned-source builders, the whole `compatibility/` package, and the
CI content-invariant and compatibility components. The one piece the content pipeline needs — the
route-contract loader — moved to `content/route_contracts.py`, unchanged, so every pinned content
digest still holds.

Nothing mechanically compares this application against the old site any more. The old site is
served from S3, and `/faq/`, `/docs/`, and `/podwiki/` redirect to GitHub Pages, so most of the
paths that corpus pinned are not served by this application at all. URL behaviour that real people
still reach — the redirects and alias views — is ordinary product behaviour with its own tests.
