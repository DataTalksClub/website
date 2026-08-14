# GitHub editorial source and public projection inventory

This is the Milestone-0 evidence slice for [#153](https://github.com/DataTalksClub/website/issues/153).
It records checked-in source and projection evidence only. It does not complete [#72](https://github.com/DataTalksClub/website/issues/72), approve an owner decision, perform a content cutover, or replace the legacy URL manifest, the Milestone-0 readiness matrix, or the CMP schema inventory.

- Snapshot repository: `DataTalksClub/website`
- Snapshot ref: `refs/heads/main`
- Snapshot SHA: `539bd8c6ff73661e174af7183f6f49d181efa1fa`
- Captured at (UTC): `2026-08-14T03:14:33Z`
- Validator: [`scripts/validate_github_editorial_source_projection_inventory.py`](../../scripts/validate_github_editorial_source_projection_inventory.py)

## Selected source pins

The selected editorial source list is copied from [`_docs/planning/sources/index.yaml`](../planning/sources/index.yaml). The four rows below are the complete selected editorial set for this inventory; other planning inputs in that index are not editorial collection sources for this slice.

| Source key | Repository | Full immutable revision | Authority | Source page |
| --- | --- | --- | --- | --- |
| `dtc-main-site` | `DataTalksClub/datatalksclub.github.io` | `ee43d3fa0929faf691178d79f19528e6f15a83e5` | `primary` | [`wiki/sources/dtc-main-site.md`](../planning/wiki/sources/dtc-main-site.md) |
| `dtc-docs` | `DataTalksClub/docs` | `3f23e006ffdaa498bbc69697408853b6f5eb37dc` | `primary` | [`wiki/sources/dtc-docs.md`](../planning/wiki/sources/dtc-docs.md) |
| `dtc-faq` | `DataTalksClub/faq` | `c8da1deea9e24945922702994de101dd90a5380a` | `primary` | [`wiki/sources/dtc-faq.md`](../planning/wiki/sources/dtc-faq.md) |
| `dtc-podwiki` | `DataTalksClub/podwiki` | `988b79d0d655bf4755945c3118544cb9e0dbead6` | `primary` | [`wiki/sources/dtc-podwiki.md`](../planning/wiki/sources/dtc-podwiki.md) |

## DataTalksClub/content contract

The accepted content contract is [`_docs/content-authoring.md`](../content-authoring.md), not a new source declaration. Its immutable source and tree are recorded here so drift fails closed:

- Content source revision: `e29f56ce70bd997171a78a9f0facc9354797f421`
- Content source tree: `c82b0c6ff462dcdd7140f03f2e7d884ed10ff8fa`
- Content source counts: `articles=55; podcasts=205; separate_transcripts=203; books=98; source_owned_media=815`
- Editorial overlay SHA-256: `63969508134e8b2ef3c8471e9c8dbccc96842fcfc25225fe02e1ed5a4f5926f6`
- Repair manifest SHA-256: `80d3014c47bf57de792473fc1da8f7569daeb55107688c3485153f773948d3aa`
- Transcript provenance boundary: `separate transcript identity with its own source checksum and edit/provenance links; no public transcript route`
- Source-owned edit/provenance boundary: `the operator edit action opens the one authoritative file in DataTalksClub/content; immutable releases retain source path and checksum`

The adapter's exact allowed patterns are preserved below. The transcript path is deliberately separate from episode YAML, and `migration.yaml`, the repair manifest, and the editorial overlay remain evidence inputs rather than new editorial authorities.

| Pattern | Contract role | Evidence |
| --- | --- | --- |
| `articles/*.md` | Article Markdown and front matter | [`content-authoring.md`](../content-authoring.md) |
| `podcasts/*.yaml` | Podcast episode metadata | [`content-authoring.md`](../content-authoring.md) |
| `podcasts/transcripts/*.yaml` | Separate transcript mappings | [`content-authoring.md`](../content-authoring.md) |
| `books/*.yaml` | Book mappings and discussion archives | [`content-authoring.md`](../content-authoring.md) |
| `images/posts/**` | Article media | [`content-authoring.md`](../content-authoring.md) |
| `images/podcast/**` | Podcast media | [`content-authoring.md`](../content-authoring.md) |
| `images/books/**` | Book media | [`content-authoring.md`](../content-authoring.md) |
| `migration.yaml` | Source migration identity | [`content-authoring.md`](../content-authoring.md) |
| `repairs/2026-08-09-missing-media.yaml` | Repaired-baseline evidence | [`content-authoring.md`](../content-authoring.md) |
| `editorial-overlays/2026-08-10-podcast-descriptions.yaml` | 19-record editorial overlay evidence | [`content-authoring.md`](../content-authoring.md) |

## Projection artifact inventory

These are the exact 13 JSON projections accepted for the snapshot, plus their checked-in manifest. JSON and manifest checksums are SHA-256 of the checked-in bytes. Counts are observed fields, not a claim that a projection is an approval or a cutover.

| Artifact key | Checked-in path | Source revision / owner | Observed count fields | SHA-256 | Schema / evidence | Unresolved hand-off |
| --- | --- | --- | --- | --- | --- | --- |
| `articles` | `content/public_projection/articles.json` | `e29f56ce70bd997171a78a9f0facc9354797f421 / DataTalksClub/content` | `items=55` | `3eb3127a3615e3ff21817e4cf43fb3798303fe3f7098632c6d659c7b9538309d` | [`articles.json`](../../content/public_projection/articles.json); [`#105`](https://github.com/DataTalksClub/website/issues/105) | `None recorded` |
| `podcasts/transcripts` | `content/public_projection/podcasts.json` | `e29f56ce70bd997171a78a9f0facc9354797f421 / DataTalksClub/content` | `episodes=205; separate_transcripts=203` | `33409b09c184a02ff6b685d805d9ad05d74bb15d5b34ea86f34c5a10b4cb0c8d` | [`podcasts.json`](../../content/public_projection/podcasts.json); [`#119`](https://github.com/DataTalksClub/website/issues/119); [`#132`](https://github.com/DataTalksClub/website/issues/132) | `None recorded` |
| `books` | `content/public_projection/books.json` | `e29f56ce70bd997171a78a9f0facc9354797f421 / DataTalksClub/content` | `items=98` | `64f14434dee15dd12e12ae510554a80dfe5d635022f431ee552a05c0e0511c5f` | [`books.json`](../../content/public_projection/books.json); [`#105`](https://github.com/DataTalksClub/website/issues/105) | `None recorded` |
| `people` | `content/public_projection/people.json` | `ee43d3fa0929faf691178d79f19528e6f15a83e5 / DataTalksClub/datatalksclub.github.io` | `person_details=438; public_catalogue=absent` | `f1bc223aee48ff614bcc24351f3253897459b1b7e75ea70ecd5dec98ff1b0a44` | [`people.json`](../../content/public_projection/people.json); [`#105`](https://github.com/DataTalksClub/website/issues/105) | `None recorded; no public /people catalogue is implied` |
| `events` | `content/public_projection/events.json` | `ee43d3fa0929faf691178d79f19528e6f15a83e5 / DataTalksClub/datatalksclub.github.io` | `items=421` | `260eeeb2974a436b80621d87df30bfea743273b3d38a6dfe9532dfb7d99f00ec` | [`events.json`](../../content/public_projection/events.json); [`#105`](https://github.com/DataTalksClub/website/issues/105) | [`#16`](https://github.com/DataTalksClub/website/issues/16) remains OPEN for legacy-host consumer inventory |
| `courses` | `content/public_projection/courses.json` | `98a235283904b4ef9ad29e196298540756cf1bcc / DataTalksClub/course-management-platform` | `items=12` | `318d7cb156cdcf74f346695d3db2b526e81c3321426c33365e91d3f471211c4d` | [`courses.json`](../../content/public_projection/courses.json); [`#105`](https://github.com/DataTalksClub/website/issues/105) | [`#12`](https://github.com/DataTalksClub/website/issues/12) remains OPEN for editorial activation ownership |
| `media` | `content/public_projection/media.json` | `e29f56ce70bd997171a78a9f0facc9354797f421 + ee43d3fa0929faf691178d79f19528e6f15a83e5 / DataTalksClub/content + DataTalksClub/datatalksclub.github.io` | `total=1253; content=815; legacy_main_portraits=438` | `6b6670d01407c72649f89a7671e240d7c75d9653b9bb25b30f362e14b0325aea` | [`media.json`](../../content/public_projection/media.json); [`#105`](https://github.com/DataTalksClub/website/issues/105) | `None recorded` |
| `FAQ` | `content/faq_projection.json` | `c8da1deea9e24945922702994de101dd90a5380a / DataTalksClub/faq` | `courses=6; sections=70; questions=1401; assets=99` | `7b6e5723b2ab0cf453254c10fb06a08175ca2bee5b9c65d98cc0534acfe8f209` | [`faq_projection.json`](../../content/faq_projection.json); [`#42`](https://github.com/DataTalksClub/website/issues/42) | `None recorded` |
| `docs` | `content/docs_projection.json` | `3f23e006ffdaa498bbc69697408853b6f5eb37dc / DataTalksClub/docs` | `pages=106; assets=39` | `1abd84ab397e5ce70dd570e1f7c4d2d9a753b0fcaf82d9a74a6cfa571b153dd2` | [`docs_projection.json`](../../content/docs_projection.json); [`#41`](https://github.com/DataTalksClub/website/issues/41) | `None recorded` |
| `wiki` | `content/public_projection/wiki.json` | `988b79d0d655bf4755945c3118544cb9e0dbead6 / DataTalksClub/podwiki` | `pages=282` | `5f64f4d4a7a7436830d5a5c039e081d82fbf47ac01b7fb891fa6c5816650ce68` | [`wiki.json`](../../content/public_projection/wiki.json); [`#43`](https://github.com/DataTalksClub/website/issues/43) | `None recorded` |
| `wiki graph` | `content/public_projection/wiki_graph.json` | `988b79d0d655bf4755945c3118544cb9e0dbead6 / DataTalksClub/podwiki` | `nodes=1072; links=13006; counts_map={"articles":80,"books":98,"comparisons":24,"guides":25,"how_tos":4,"links":13006,"nodes":1072,"persons":439,"podcasts":205,"roadmaps":12,"topics":48,"transitions":15,"wikis":202}` | `07f433eab8c818abf4a2d270c1f9a582bc450c16b7ffbbee344b8998eeb4ebb8` | [`wiki_graph.json`](../../content/public_projection/wiki_graph.json); [`#43`](https://github.com/DataTalksClub/website/issues/43) | `None recorded` |
| `wiki search` | `content/public_projection/wiki_search.json` | `988b79d0d655bf4755945c3118544cb9e0dbead6 / DataTalksClub/podwiki` | `documents=2998` | `e8f82b7471ce9152f994f2dfc3ef370b8d2a98384834051985dd45c5269f7307` | [`wiki_search.json`](../../content/public_projection/wiki_search.json); [`#43`](https://github.com/DataTalksClub/website/issues/43) | `None recorded` |
| `editorial route migration` | `content/public_projection/editorial_route_migration.json` | `e29f56ce70bd997171a78a9f0facc9354797f421 + ee43d3fa0929faf691178d79f19528e6f15a83e5 / DataTalksClub/content + DataTalksClub/datatalksclub.github.io` | `finals=796; aliases=1592` | `7d8111eca8f2bdb8927cc48da449ac36624f69f9c45e99961fbe0243ecbac531` | [`editorial-route-migration.schema.json`](../compatibility/editorial-route-migration.schema.json); [`editorial_route_migration.json`](../../content/public_projection/editorial_route_migration.json); [`#34`](https://github.com/DataTalksClub/website/issues/34) | [`#16`](https://github.com/DataTalksClub/website/issues/16) remains OPEN; no redirect approval is inferred |
| `manifest` | `content/public_projection/manifest.json` | `preferred checked-in projection evidence / compatibility-evidence` | `members=11; aggregate_counts=9; selection_mode=preferred` | `b9ad483c9f3fb16de526d34f4e5ad7d776c4084099e659725af500620923b9cc` | [`manifest.json`](../../content/public_projection/manifest.json); [`#105`](https://github.com/DataTalksClub/website/issues/105) | `None recorded` |

- Manifest members (exact): `articles.json, books.json, courses.json, editorial_route_migration.json, events.json, media.json, people.json, podcasts.json, wiki.json, wiki_graph.json, wiki_search.json`

FAQ, docs, and editorial-route migration are checked-in projections but are intentionally outside this legacy public-projection manifest's member map.

## Ownership and provenance boundaries

Each row has exactly one controlled ownership value. `github-editorial-read` means a GitHub-backed editorial read model; `database-operational` means application-owned operational state; `studio-admin-api` means a management adapter over shared services; and `compatibility-evidence` means a checked projection or compatibility observation, not a new authority.

| Collection or domain | Ownership | Explicit boundary | Owner / evidence |
| --- | --- | --- | --- |
| `articles` | `github-editorial-read` | `DataTalksClub/content@e29f56...` owns source Markdown; public reads use the accepted projection and retain edit/provenance identity. | [`content-authoring.md`](../content-authoring.md); [`#103`](https://github.com/DataTalksClub/website/issues/103) |
| `podcasts/transcripts` | `github-editorial-read` | Episode YAML and transcript YAML are separate authoritative files; transcripts have no public route or independent index. | [`content-authoring.md`](../content-authoring.md); [`#119`](https://github.com/DataTalksClub/website/issues/119) |
| `books` | `github-editorial-read` | Book YAML and discussion archives remain source-owned; no database override is implied. | [`content-authoring.md`](../content-authoring.md); [`#103`](https://github.com/DataTalksClub/website/issues/103) |
| `source-owned content media` | `github-editorial-read` | Content media remains byte-for-byte below the adopted `images/` roots and retains source checksum/provenance. | [`content-authoring.md`](../content-authoring.md); [`#105`](https://github.com/DataTalksClub/website/issues/105) |
| `people` | `github-editorial-read` | Legacy main-site people remain editorial source data; linked Person details do not create a public `/people` catalogue. | [`people.json`](../../content/public_projection/people.json); [`#105`](https://github.com/DataTalksClub/website/issues/105) |
| `docs` | `github-editorial-read` | Docs pages and navigation remain sourced from the pinned Docs repository. | [`docs_projection.json`](../../content/docs_projection.json); [`#41`](https://github.com/DataTalksClub/website/issues/41) |
| `FAQ` | `github-editorial-read` | FAQ courses, sections, questions, assets, and JSON contract remain sourced from the pinned FAQ repository. | [`faq_projection.json`](../../content/faq_projection.json); [`#42`](https://github.com/DataTalksClub/website/issues/42) |
| `wiki` | `github-editorial-read` | Podwiki pages, graph, and search evidence remain source-backed; the public route is `/wiki`. | [`wiki.json`](../../content/public_projection/wiki.json); [`#43`](https://github.com/DataTalksClub/website/issues/43) |
| `events projection` | `compatibility-evidence` | The 421-row legacy-main projection is read/migration evidence; database Event rows become authoritative only after the owning cutover work. | [`events.json`](../../content/public_projection/events.json); [`#105`](https://github.com/DataTalksClub/website/issues/105) |
| `courses projection` | `compatibility-evidence` | The 12-row CMP projection is read evidence; Course/Cohort, registration, enrollment, and learner operations remain application-owned. | [`courses.json`](../../content/public_projection/courses.json); [`#150`](https://github.com/DataTalksClub/website/issues/150) |
| `database operational courses/cohorts/events/registrations` | `database-operational` | Operational Course, Cohort, Event, registration, enrollment, and learner mutations belong to application services and durable jobs. | [`04-courses-and-cohorts.md`](../specs/04-courses-and-cohorts.md); [`#30`](https://github.com/DataTalksClub/website/issues/30) |
| `Studio` | `studio-admin-api` | Studio validates, previews, activates, rolls back, and opens edits through shared services; it does not become editorial source authority. | [`06-studio-and-admin-api.md`](../specs/06-studio-and-admin-api.md); [`#103`](https://github.com/DataTalksClub/website/issues/103) |
| `/api/v1/admin/` | `studio-admin-api` | The admin API is a management adapter over shared application services with the same authorization, audit, and redaction boundary. | [`06-studio-and-admin-api.md`](../specs/06-studio-and-admin-api.md); [`#103`](https://github.com/DataTalksClub/website/issues/103) |
| `legacy manifest and route migration` | `compatibility-evidence` | URL/link/asset observations and route aliases are compatibility evidence, not a new redirect approval or editorial owner. | [`README.md`](../compatibility/README.md); [`#34`](https://github.com/DataTalksClub/website/issues/34) |

## Related evidence and issue ownership

This inventory links the owning evidence and issue; it does not duplicate their contracts or silently take their implementation scope.

| Issue | Owning scope | Existing evidence |
| --- | --- | --- |
| [`#34`](https://github.com/DataTalksClub/website/issues/34) | Legacy URL, link, fragment, asset, and SEO manifest | [`compatibility/README.md`](../compatibility/README.md) |
| [`#39`](https://github.com/DataTalksClub/website/issues/39) | Main-site source adapter | [`03-github-content-and-people.md`](../specs/03-github-content-and-people.md) |
| [`#41`](https://github.com/DataTalksClub/website/issues/41) | Docs source adapter and path contract | [`dtc-docs.md`](../planning/wiki/sources/dtc-docs.md) |
| [`#42`](https://github.com/DataTalksClub/website/issues/42) | FAQ source adapter and JSON contract | [`dtc-faq.md`](../planning/wiki/sources/dtc-faq.md) |
| [`#43`](https://github.com/DataTalksClub/website/issues/43) | Podwiki source, graph, and search adapter | [`dtc-podwiki.md`](../planning/wiki/sources/dtc-podwiki.md) |
| [`#103`](https://github.com/DataTalksClub/website/issues/103) | Runtime content release adapter | [`content-authoring.md`](../content-authoring.md) |
| [`#105`](https://github.com/DataTalksClub/website/issues/105) | Accepted baked public projection and route behavior | [`manifest.json`](../../content/public_projection/manifest.json) |
| [`#119`](https://github.com/DataTalksClub/website/issues/119) | Podcast source and transcript identity contract | [`03-github-content-and-people.md`](../specs/03-github-content-and-people.md) |
| [`#132`](https://github.com/DataTalksClub/website/issues/132) | Podcast season/order and owner credentials contract | [`03-github-content-and-people.md`](../specs/03-github-content-and-people.md) |
| [`#150`](https://github.com/DataTalksClub/website/issues/150) | Milestone-0 readiness matrix | [`milestone-0-readiness.md`](2026-08-14-milestone-0-readiness.md) |
| [`#152`](https://github.com/DataTalksClub/website/issues/152) | CMP schema and data inventory | [`course-platform-schema-data-inventory.md`](2026-08-14-course-platform-schema-data-inventory.md) |

## Unresolved decision hand-offs

These five decision hand-offs are deliberately recorded as `OPEN`. No approval, owner assignment, redirect, search backend, analytics policy, privacy policy, source sync, production cutover, or #72 completion is inferred here.

| Issue | Status | Open contract text | Decision evidence |
| --- | --- | --- | --- |
| [`#12`](https://github.com/DataTalksClub/website/issues/12) | `OPEN` | GitHub-backed editorial workflow and activation ownership remain an owner decision. | [`open-decisions.md`](../specs/open-decisions.md) |
| [`#16`](https://github.com/DataTalksClub/website/issues/16) | `OPEN` | Authenticated course API-consumer inventory and legacy-host redirect inventory remain an owner decision. | [`open-decisions.md`](../specs/open-decisions.md) |
| [`#23`](https://github.com/DataTalksClub/website/issues/23) | `OPEN` | Privacy ownership, retention, minors, and public-profile policy remain an owner decision. | [`open-decisions.md`](../specs/open-decisions.md) |
| [`#24`](https://github.com/DataTalksClub/website/issues/24) | `OPEN` | PostgreSQL search and public-search contract remain an owner decision. | [`open-decisions.md`](../specs/open-decisions.md) |
| [`#27`](https://github.com/DataTalksClub/website/issues/27) | `OPEN` | Analytics and tracking preservation remain an owner decision. | [`open-decisions.md`](../specs/open-decisions.md) |

The validator is local and stdlib-only. It reads this audit, the checked-in source index and content contract, and the listed JSON artifacts; it performs no network, database, source checkout, projection write, or deployment mutation.
