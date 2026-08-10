# Pinned legacy compatibility inputs

These files contain the reproducible source inputs and the checked source-and-production URL
compatibility capture. A manifest row makes a production claim only when it contains a production
observation from the bounded shared crawler. Every classification remains a proposed preserve
decision until an owner explicitly reviews it.

## Rebuild the source inputs

Run the builder from this repository:

```console
uv run python scripts/build_pinned_legacy_sources.py
```

The default command prepares or verifies all five detached checkouts, rebuilds the four generated
sites, and atomically regenerates the six source-derived artifacts listed below. To regenerate from
already-built pinned trees without network access, or to compare them byte-for-byte without writing:

```console
uv run python scripts/build_pinned_legacy_sources.py --artifacts-only
make compatibility-source-artifacts-check
```

Both offline modes verify every checkout's origin, detached revision, and clean status first. They
bound file count, individual-file size, and total bytes; reject symlinks and root escapes; and use
only project-local `.tmp/` staging. `source-production-differences.json` is intentionally not
rewritten by this source builder: it is the production-crawl review ledger, updated by the shared
crawler/comparison workflow after source artifacts are established.

The builder creates detached, clean checkouts below `.tmp/legacy-compatibility-sources/sources/`
at these exact revisions:

| Source | Revision | Public mount |
| --- | --- | --- |
| `DataTalksClub/datatalksclub.github.io` | `ee43d3fa0929faf691178d79f19528e6f15a83e5` | `/` |
| `DataTalksClub/docs` | `3f23e006ffdaa498bbc69697408853b6f5eb37dc` | `/docs` |
| `DataTalksClub/faq` | `c8da1deea9e24945922702994de101dd90a5380a` | `/faq` |
| `DataTalksClub/podwiki` | `988b79d0d655bf4755945c3118544cb9e0dbead6` | `/podwiki` |
| `DataTalksClub/course-management-platform` | `98a235283904b4ef9ad29e196298540756cf1bcc` | `courses.datatalks.club` |

It never reads an adjacent developer checkout. An existing checkout is accepted only when its
origin and exact revision match, HEAD is detached, and both tracked and untracked status are
clean. All downloads and generated output stay below the project-local `.tmp/` directory.

Main and docs use the published Rustkyll v0.4.10 Linux AMD64 binary with its exact release URL and
SHA-256 digest. Podwiki uses the published Rustkyll 0.5.3 Linux AMD64 PyPI wheel with its exact
`files.pythonhosted.org` URL and SHA-256 digest; the wrapper downloads and verifies that wheel, then
verifies the packaged executable against the separately published v0.5.3 Linux binary digest and
passes its local canonical platform-tagged filename to `uvx`. It never resolves an unpinned
`rustkyll==version` requirement. FAQ uses the source repository's verified `website/uv.lock`
through `uv run --frozen`. The FAQ generator's only known wall-clock input,
its rendered `generation_time`, is frozen at `2000-01-01 00:00:00`; this deliberate difference
from production is retained in `source-production-differences.json`.

| Lane | Pinned public artifact | Artifact SHA-256 | Executed binary SHA-256 |
| --- | --- | --- | --- |
| Main and docs | [Rustkyll v0.4.10 Linux AMD64](https://github.com/alexeygrigorev/rustkyll/releases/download/v0.4.10/rustkyll-linux-amd64) | `ab96b800eb8427591841232ed2d0619f011b639200df6b4514ac9680caa6130e` | same as artifact |
| Podwiki | [Rustkyll 0.5.3 Linux AMD64 wheel](https://files.pythonhosted.org/packages/32/f4/9cae847680982c09346f8db66568a9ecb11d2e8de411c9829c7c8e2c4415/rustkyll-0.5.3-py3-none-manylinux_2_17_x86_64.manylinux2014_x86_64.whl) | `348c622cac08cdd2361c4300161b7da34b7f7162bf0ad3d9fd9a0cd053f54a8e` | `c8c2e6c732ecc224c28c170782114980b4707514835e7f587293f78bd38f2fba` |

Before each Rustkyll build, the wrapper derives `SOURCE_DATE_EPOCH` from the exact pinned commit and
normalizes every tracked regular source file to that value: main `1785872368`, docs `1786017922`,
and Podwiki `1785736104`. The same values are recorded as deterministic provenance overrides.

`source-build-provenance.json` records every repository revision, tool version, deterministic
override, output count, and generated-tree digest. The checked-in source build contains 2,937
generated-file rows:

| Source | Generated files |
| --- | ---: |
| Main | 2,301 |
| Docs | 174 |
| FAQ | 152 |
| Podwiki | 310 |

The main-site source remains the observation provenance for article, podcast, book, and adopted
media contracts after editorial ownership moves to `DataTalksClub/content`. Prepared records retain
these legacy contract IDs, source IDs, and revisions; they separately retain the new repository
commit and source checksum. The source move must not rewrite this artifact or imply that legacy
observations were generated from the new repository.

The accepted #105 source includes ten adopted public paths that this artifact never observed: two
podcast records and eight repaired media files. Their prepared database records intentionally have
no legacy contract triple; the checked #105 projection and immutable repair evidence bind them.
This absence is not permission to add arbitrary unobserved paths or synthesize contract IDs.

## Checked compatibility capture

`legacy-manifest.jsonl` is the canonical checked manifest. Its 2,965 rows combine all 2,937 source
URLs with the exact 2,965 anonymous production seeds: 2,937 URLs were observed in both captures,
28 are production-only, and none are source-only. It was generated at
`2026-08-08T05:55:00Z` with `dtc-legacy-manifest-crawler/3`; its SHA-256 digest is
`94a6469530a290147e94f825eb981836e1358b9d0a72c7f50f0eda6d638e1d7f`.

The production capture completed without capture errors. It recorded 2,976 HTTP responses and
258,215,736 transferred bytes across the committed seed set. Final status counts are 2,951 `200`,
four `401`, two `403`, seven `404`, and one `405`. Eleven rows contain HTTP redirect chains, two
contain observed client redirects, and nine are marked as soft 404s. The source and production
input digests are recorded in `source-production-differences.json`.

`legacy-manifest-differences.json` is the canonical comparison. It contains 4,918 differences
affecting all 2,965 URLs: 17 added assets, 19 removed assets, three canonical changes, 4,805 field
changes, 21 added fragments, 21 removed fragments, 28 added routes, and four removed routes. Its
SHA-256 digest is
`72b2a68694b17c091ba09e29f1e44e2c052cd6d244b7c750abde3f66ad0cd4ff`.
Every manifest row is still `preserve` with `review_state=proposed_preserve`; the comparison did
not automatically approve parity, a redirect, or a retirement.

## Django parity gate

Issue #35 adds a separate approved-target contract rather than changing this observation evidence.
`approved-expectation.schema.json` describes reviewed preserve, redirect, and retirement records
bound to the exact manifest, difference-ledger, and public-contract digests. A preserve requires
`approved_parity`; a redirect or retirement requires `approved_exception` plus an owner, reason,
and focused test. Target observations and reports use `target-observation.schema.json` and
`seo-parity-report.schema.json`.

The generic gate captures anonymous GET responses through Django without network access, retains
the exact input path/query spelling, compares server-rendered metadata/content/links/assets and
sitemap state, and emits only stable value-free findings. Its runtime registry exposes only
approved one-hop 301/308 and direct 410 decisions; an unknown path remains a normal 404. Local
captures suppress runtime monitoring through an internal context marker that an HTTP client cannot
set. Runtime events use known contract IDs or low-cardinality unknown/external groups and never
record raw queries, fragments, unknown paths, referrers, user IDs, or response bodies.

The checked real inputs intentionally have no approved expectation sidecar yet. This must remain a
nonzero `BLOCKED` result until adapter issues produce independently reviewed expectations:

```console
make check-links
make check-seo
make compatibility-real-gate-blocked-check
```

The last target succeeds only when the management command exits nonzero and its report says
`BLOCKED` with zero approved expectations. A fixture `PASS` is never whole-site or cutover approval.
WSGI routes on decoded `PATH_INFO`; `raw_network_reference` preserves collector evidence, while any
contract that depends on encoded separators still requires a deployed edge test. External URLs are
compared byte-for-byte but are not contacted by this activation gate.

CI parses both checked artifacts strictly, validates their schemas, re-encodes the manifest,
recomputes the comparison, verifies exact URL sets and digests, and checks source metadata
invariants. The same verification is available offline:

```console
make compatibility-artifacts-check
```

## Contract inventories

- `generated-path-baseline.jsonl` is the exact generated-file allowlist. It retains path case,
  trailing slashes, and a separately percent-encoded path. Assets are contracts too.
- `faq-fragment-contracts.jsonl` contains all 1,401 ten-character FAQ question anchors across six
  course pages.
- `podwiki-graph-fragment-contracts.jsonl` contains all 1,072 graph-node hash deep links and their
  source target URL/type.
- `machine-contract-samples.json` records 50 configured feed, sitemap, robots, JSON, query, filter,
  graph-hash, and course samples. Its 22 course samples cover every literal route plus
  `courses.datatalks.club/robots.txt`; five action endpoints are anonymous GET-denial probes, not
  mutation authorization. It explicitly marks configured contracts absent from a generated tree.
- `course-route-contracts.json` contains all 89 adopted Django route patterns: 9 accounts, 29 API,
  26 canonical Studio Courses, and 25 public-course routes. The Studio rows retain the pinned CMP
  source revision and callbacks while recording the target-owned `/studio/courses` mount and
  `studio_courses_*` names. The configured `/cadmin` machine samples remain the separately checked
  one-hop compatibility surface. Parameterized routes use illustrative paths only; they are not
  evidence that production objects or authenticated responses exist.
- `public-contracts.jsonl` is the canonical reconciliation of those inputs: 5,507 exact URL
  contracts with stable IDs, source provenance, expected status where known, and separate
  classification and parity-review state. Its per-row schema is
  `public-contracts.schema.json`. Generated/machine overlaps remain one row carrying the
  machine-contract marker; illustrative course paths retain their route identity.

The generated-path allowlist seeds the shared crawler. The crawler owns the complete strict JSONL
capture schema, including separate source and production observations, extracted SEO/link fields,
and comparison results. Do not turn these source-only rows into competing synthetic production
rows.

## Build and comparison workflow

All mutable checkouts, checkpoints, and work manifests stay below the project-local `.tmp/`
directory. The production command is anonymous and GET-only. It verifies each origin's
`robots.txt` through the same DNS-checked, IP-pinned transport before crawling, uses a static user
agent, waits at least 100 ms between requests to one origin, applies bounded retry backoff, and
crawls only the committed fragmentless seed set. It covers 22 anonymous literal course contracts
but does not recursively discover course URLs or probe any of the 68 parameterized authenticated
course examples.

```console
uv run python scripts/build_pinned_legacy_sources.py \
  --workspace .tmp/legacy-compatibility-sources

uv run python scripts/build_pinned_legacy_sources.py --check

uv run python scripts/build_legacy_manifest.py public-contracts --check

# Regenerate the checked-in artifact after an intentional source-contract update:
uv run python scripts/build_legacy_manifest.py public-contracts

uv run python scripts/build_legacy_manifest.py source \
  --workspace .tmp/legacy-compatibility-sources \
  --generated-at 2026-08-08T05:55:00Z \
  --output .tmp/compatibility/source.jsonl

uv run python scripts/build_legacy_manifest.py production \
  --generated-at 2026-08-08T05:55:00Z \
  --run-to-completion

uv run python scripts/build_legacy_manifest.py merge \
  --source .tmp/compatibility/source.jsonl \
  --production .tmp/compatibility/production.jsonl \
  --generated-at 2026-08-08T05:55:00Z \
  --output .tmp/compatibility/legacy-manifest.jsonl

uv run python scripts/build_legacy_manifest.py compare \
  .tmp/compatibility/legacy-manifest.jsonl \
  --output .tmp/compatibility/legacy-manifest-differences.json \
  --fail-on-difference

uv run python scripts/build_legacy_manifest.py validate \
  .tmp/compatibility/legacy-manifest.jsonl
```

The timestamp is explicit so repeated builds are byte-identical. Resume requires both the work
manifest and its policy/seed-bound checkpoint; tampered, oversized, symlinked, outside-`.tmp`, or
cross-policy checkpoints fail closed. The policy fingerprint includes the crawler/extractor
semantics version, so a semantic version bump cannot resume and mix observations captured by older
code. Per-capture response and byte counters bind restored work to the checkpoint totals. Manifest
replacement is atomic; if an interruption leaves the work file one
capture ahead of its checkpoint, resume discards and refetches that uncommitted row. Response reads
reserve a one-byte detection sentinel for unknown-length oversize responses; that byte is accounted
before the crawl terminates on the aggregate bound. Robots preflight is a separate control
transaction, and the verified policy is reapplied to every redirect destination. The record schema is
`_docs/compatibility/legacy-manifest.schema.json` (record schema v2); the Python loader additionally enforces JSONL
record order, canonical encoding, classification invariants, and source/production separation.
URL-valued social and structured metadata is normalized and redacted before storage; sensitive
query-shaped URL fragments and DOM fragment IDs are stored only as stable redacted digests.

## Review and classification rules

All newly merged rows default to `preserve` with `review_state=proposed_preserve`; production
observations never approve parity automatically. Parity approval is an explicit
`approved_parity` transition. A future `redirect` or `retire` classification requires
`approved_exception`, an owner and reason, and a focused test. Catch-all redirects to `/` or
`/index.html`, including many-to-one homepage-equivalent rules, are prohibited. An approved
redirect must be exactly one permanent `301` or `308` hop to its declared target, whose final
response status is successful (`200` through `299`).

`source-production-differences.json` is the review ledger. It records:

- the docs Makefile's historical v0.4.7 default, deployed workflow's v0.4.6 binary, and the
  compatibility rebuild's reproducibility-fix v0.4.10 binary;
- the FAQ deployment-time footer versus the frozen source-build time;
- the known browser-parity exception for the unescaped quoted meta description in
  `_site/blog/ml-deployment-lambda.html`; the deterministic extractor intentionally excludes the
  hidden title and malformed head-attribute tail from the main-content fingerprint;
- source-output absences for `/docs/robots.txt` and `/podwiki/robots.txt`;
- the completed anonymous production capture and all 4,918 unresolved comparison differences; and
- authenticated course HTML/API probes as not performed because no production credentials or
  learner/operator data were used.

The 68 parameterized course-route examples remain explicitly unprobed and were not added to the
production seed set. Production differences stay unresolved until an owner reviews them. Nothing
in this directory authorizes an authenticated production probe, redirect, retirement, or cutover.
