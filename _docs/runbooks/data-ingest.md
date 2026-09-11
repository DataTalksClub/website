# Data ingest, by source

Every place data enters this repository, what brings it in, and what happens to it
next. Written to be run from at 2am without asking anyone a question.

Issue #310 (*Consolidate every data ingest into one clear set of prod scripts*) is
the work this describes. `scripts/prod/` and the explicit runners
`scripts/content.py` and `scripts/production_data.py` own the operational entry points;
the root Makefile contains only local development lifecycle commands.

Counts were measured on `main` on 2026-09-03 and re-checked on 2026-09-05. The
2026-09-05 pass re-measured the CMP export, the `aisl` export, event identity,
content and new-event discovery, and the registration aggregates; a figure it did
not re-measure says so where it appears rather than implying a fresh count.
Where a number is asserted by code, the assertion is cited so you can re-check it.

---

## 1. How to read this

### The three places data can be

Every question in this document is really "which of these three is it in?", so
they are named once here and used with these meanings throughout.

| Term | What it is |
| --- | --- |
| **Source** | Where the data actually lives and is authored, upstream of us: a Luma export, the CMP production export, `DataTalksClub/content`, a course repository, the retired legacy site. We do not serve it and mostly cannot write to it. |
| **Staging** | `temporary/content/`. Source data after review and processing, already in the shape the production database wants. Nothing serves it, nobody authors in it, and it is deleted once production is ingested. Its only purpose is to be pumped into prod. |
| **Production database** | The target. Every public page reads it and nothing else. |

Work moves in one direction: **source → staging → production database.** A thing
that reads staging on a public request is a bug, and a thing that reaches past
staging into a source at request time is a worse one.

> **A naming leftover.** `temporary/content/public_projection/` is staging. It is
> called a *projection* because it once was one: the site read those JSON files
> on every request, and "projection" named a read model served directly. Nothing
> has served them since the database cutover. Read "projection" as "staging"
> wherever it appears as a *noun for the files*; `scripts/projection_build/` and
> `scripts/build_public_projection.py` keep the name honestly, because building
> that artifact is what they do.

### Three things that decide how a source behaves

Three things decide how a source behaves, and they are independent of each other.
Confusing them is the main way this goes wrong.

**Sync model** — how the data gets here.

| Model | Meaning |
| --- | --- |
| **Continuous** | Upstream keeps changing; a push or a scheduled pull re-ingests it. Re-running is the normal case. |
| **Pinned build** | Upstream is read by an offline build script at a *pinned revision*. The output is committed. Re-running requires the pinned checkout. |
| **One-time** | Frozen history, read once at migration. Re-running is safe but nothing upstream moves. |

**Serving path** — how a visitor's page is produced.

| Path | Meaning |
| --- | --- |
| **Database** | A view queries Django models. Every public content page is this. |
| **Object store** | A database row names the bytes; the bytes come from the public media store. |
| **Unused** | Ingested into the database, but no view resolves anything through it. |

A fourth value, **JSON projection** — a view reading a checked-in JSON file with
no database involved — was removed from this table. No source has been served
that way since the database cutover, so a row in §2 never carries it.

**Fate** — where the owner has ruled it should end up.

| Fate | Meaning |
| --- | --- |
| **Push-synced** | Moves to `DataTalksClub/content`; a push there syncs it into our database. |
| **One-off export** | Extracted once, frozen into our database, never re-synced. |
| **Already fine** | Correct as it stands. |
| **Undecided** | No ruling yet. Listed so it cannot be missed. |

> **The single most important fact in this document.** Every public content page is
> served today from the **database**. The `content/` app's pipeline —
> `ContentSource`, `ContentRelease`, `ContentDocument`, `ContentRelation`,
> `ContentAsset` — is live at both ends: the `scripts/prod/` importers write it and
> a public request reads it. `content/public_data.py`, the module that used to
> reassemble those rows into the old projection dictionary, is deleted;
> `content/catalogue.py`, `content/docs_projection.py`, `content/faq_data.py`,
> `content/article_faq.py` and `events/queries.py` are the readers now. §9 lists
> every surface.
>
> **So an importer is the right tool for every source below**, and the missing piece
> is never the *read* side. Where a source is still a gap, the gap is either a
> *reproducer* — something that rebuilds its staging file from upstream — or
> continuous re-ingest. It is never a page that cannot read a database row.
>
> *(Until the database cutover this paragraph said the exact opposite, and was
> right. If you are holding a quotation of it, this is the correction.)*

---

## 2. Source index

| # | Source | Sync model | Serving path | Fate |
| --- | --- | --- | --- | --- |
| 1 | Course repositories (3) | **Continuous** | Database | Already fine |
| 2 | `DataTalksClub/content` | Pinned build | Database | **Push-synced** |
| 3 | `DataTalksClub/podwiki` | Pinned build | Database | Stays at podwiki |
| 4 | `DataTalksClub/faq` | Pinned build (no builder) | Database | Stays at faq |
| 5 | `DataTalksClub/docs` | Pinned build (no builder) | Database | Undecided |
| 6 | `DataTalksClub/course-management-platform` (specs) | Pinned build | Unused | Undecided |
| 7 | Legacy site — people | Pinned build | Database | **Push-synced** |
| 8 | Legacy site — events | Pinned build | Database | **One-off export** (done) |
| 9 | Legacy site — author images | Pinned build | Object store | **One-off export** → CDN |
| 10 | Legacy site — article FAQ | Pinned build | Database | **One-off export** (done) |
| 11 | CMP export — course content | One-time | Database | One-off export |
| 12 | CMP export — learner data | One-time | Database | One-off export |
| 13 | `zoomcamp-scoring` (pre-2024) | One-time | Database | One-off export |
| 14 | Event identity manifest | One-time | Database | One-off export |
| 15 | Event description bridge | One-time | Database | One-off export |
| 16 | Luma registration aggregates | One-time | Database | One-off export |
| 17 | Eventbrite registration aggregates | One-time | Database | One-off export |
| 18 | Public media objects | Hydrate/publish | Object store | Already fine → CDN |
| 19 | Sponsors | **No source** | Database | Undecided |
| 20 | Testimonials | Migration seed only | Database | Undecided |
| 21 | `rds-aisl_prod` — second production database | **Not addressed** | — | **Undecided** |

Sources 3–6 are separate upstream repositories that the owner's original list
folded into "`DataTalksClub/content`". They are not in that repository and never
were. Sources 19–21 have no ingest at all.

---

## 3. Continuous: course repositories

**What it is.** The curriculum — courses, cohorts, modules, units, homework,
questions — authored in the course repositories themselves.

| | |
| --- | --- |
| **Upstream** | `DataTalksClub/ai-dev-tools-zoomcamp`, `llm-zoomcamp`, `machine-learning-zoomcamp`, each with a `course.yaml` at its root |
| **Repository list** | Registered `ContentSource` rows. `content_sync/course_repository_sources.json` is only how a fresh database gets its first rows |
| **Ingest** | `content_sync/course_repository_ingest.py` — one implementation, two transports |
| **Transport A** | Signed GitHub push webhook → `api/views/course_repository_webhooks.py` → durable job `content_sync.course_repository_sync.import_commit` |
| **Transport B** | `scripts/prod/sync_course_repositories.py` (`--from-disk` for offline) |
| **Writes** | `courses` app: `Course`, `Cohort`, `Module`, `Unit`, `Homework`, `Question`, plus `CourseCurriculumImportRun` bookkeeping, via `courses/services/curriculum_import.py` |
| **Volume** | 20 modules / 181 units across the three repositories |
| **Idempotency** | Safe. Transactional projection keyed on `source_content_id`; an import run is recorded |
| **Bootstrap** | Needs `migrate` and registered `ContentSource` rows first; then yes |
| **Serving** | **Database.** `/courses` and the lesson/unit pages query `courses` models |

`mlops-zoomcamp` and `stock-markets-analytics-zoomcamp` are deliberately absent —
they have no `course.yaml`, so neither transport can ingest them.

**This is the only genuinely continuous content ingest in the codebase, and it is
the model every other source should be measured against.** Both transports
converge on one function, the repository list is data rather than a hardcoded
list, and the ingest is transactional. See §10.

Ordering: `migrate` → `uv run --frozen python scripts/content.py sources` →
`uv run --frozen python scripts/content.py checkouts` (the only networked step) →
`uv run --frozen python scripts/content.py pull`.

---

## 4. The projection: sources 2–10

Nine sources feed one build script and one set of committed JSON files. They are
described together because they share a failure mode: **the build is a single
offline script that requires five pinned checkouts simultaneously, and nothing
runs it automatically.**

### The builder

`scripts/build_public_projection.py` (~3,200 lines). Requires three roots:

```
uv run --frozen python scripts/build_public_projection.py \
    --content-root     <DataTalksClub/content     @ 1375c506…> \
    --legacy-main-root <datatalksclub.github.io   @ ee43d3fa…> \
    --wiki-root        <DataTalksClub/podwiki     @ 988b79d0…> \
    --output temporary/content/public_projection
```

Each root is verified at `scripts/build_public_projection.py:187-196` (`_verify_checkout`):
`.git` present, HEAD exactly the pinned revision, matching `origin`, clean tree.
A missing or moved checkout is a hard failure, not a warning.

`--mode preferred` (the default and the accepted mode) takes articles, podcasts and
books from `--content-root`. `--mode fallback` takes them from the legacy repo
instead; it is **not accepted** (`manifest.json` → `selection_rule.fallback_promoted: false`).

**No general runner invokes this script.** It is referenced from
`.github/workflows/content-update.yml` and the runbooks only. A full rebuild is
currently **not reproducible** — see issue #253 and §11.

### What it writes

`temporary/content/public_projection/`, all committed, ~37 MB excluding media:

| Artifact | Records | Source |
| --- | ---: | --- |
| `articles.json` | 55 | `content` repo `articles/*.md` |
| `podcasts.json` | 203 (201 transcripts) | `content` repo `podcasts/*.yaml` |
| `books.json` | 98 | `content` repo `books/*.yaml` |
| `people.json` | 438 | **legacy** `_people/*.md` |
| `events.json` | 421 | **legacy** `_data/events.yaml` |
| `media.json` | 1,253 | `content` repo images + **legacy** `images/authors/` |
| `wiki.json` | 282 | `podwiki` `_wiki/*.md` |
| `wiki_graph.json` | nodes/links | `podwiki` `graph/graph.json` |
| `wiki_search.json` | corpus | `podwiki` `search/search-corpus.json` |
| `courses.json` | 12 | CMP `scripts/production_like_course_specs.json` |
| `editorial_route_migration.json` | 7 keys | derived — legacy URL → canonical redirects |
| `podcast_platforms.json` | 4 | `scripts/podcast_platforms.json` (in-repo) |
| `manifest.json` | — | counts + per-artifact SHA-256 + `tree_sha256` |

`courses.json` is imported into `ContentDocument` with the rest of the catalogue
but **no view reads it** — `/courses` is database-served. It is ballast (§12 item
13).

### Integrity at runtime

**The startup digest check is gone, and nothing replaced it.** `content.E002` no
longer exists: the catalogue is database rows, so there is no file for a system
check to digest. `content/public_data.py` itself is deleted; `content/catalogue.py`
reads the active editorial release and caches each kind on the release id
(`_records`, `lru_cache(maxsize=64)`), so the cached answer follows an import. A
database with no active release returns an empty collection rather than refusing
to boot, because an absent catalogue is a normal state. `content/apps.py`'s remaining checks
(`content.E003`–`E005`, `content.W001`) cover only the media store.

The consequence for an operator: a hand-edit under `temporary/content/` is not
caught at boot. It is caught by `uv run --frozen python scripts/ci.py content-update-check`, and by the importers
themselves — each refuses a reviewed file whose declared counts, digests or pinned
revision do not match. Somebody has to run them.

### Per-source detail

**2 — `DataTalksClub/content`** @ `1375c506…`, tree `1537664d…`, CI run pinned.
Supplies articles (55), podcasts (203) and their transcripts (201), books (98), and
post/podcast/book images. Path allowlist and accepted counts are declared in
`content_sync/dtc_content/contract.py`. Fate: **push-synced** — this is the
repository everything editorial is moving to. §10 says how.

**3 — `DataTalksClub/podwiki`** @ `988b79d0…`. Supplies `wiki.json` (282),
`wiki_graph.json`, `wiki_search.json`, and 14 wiki assets. Read at
`scripts/build_public_projection.py:2154, 2163, 2164` and `_write_wiki_assets` (2725).

Note the direction, which is easy to get backwards: **`/podwiki` is a deliberate
404 and the legacy `/podwiki/` path redirects away, but `/wiki` is served by this
application** from that projection — `content/public_urls.py:213-228` routes the
hub, detail, search corpus, graph, special pages, feed, sitemap, robots and assets.
Owner ruling: **the wiki source stays in podwiki.** The serving stays ours.

**4 — `DataTalksClub/faq`** @ `c8da1dee…` → `temporary/content/faq_projection.json`
+ `content/faq_assets/`, imported into `ContentDocument` by
`scripts/prod/import_faq.py`. 6 courses, 70 sections, 1,401 questions, 99 assets.
Served at `/faq/` by `content/review_views.py` via `content/faq_data.py`, from the
database. Owner ruling: **FAQ stays where it is.** **Gap: there is no builder for
the reviewed file in this repository.** It is reviewed in by hand and only
*checked* by `ci/content_update.py`. Every other family has a reproducer; this one
does not.

**5 — `DataTalksClub/docs`** @ `3f23e006…` →
`temporary/content/docs_projection.json` + `content/docs_assets/`, imported into
`ContentDocument` and `ContentAsset` by `scripts/prod/import_docs.py`. 106 pages,
39 images. Served at `/docs/` by `content/review_views.py`, from the database.
**Same gap: no builder for the reviewed file in this repository.**

**6 — `DataTalksClub/course-management-platform`** @ `98a23528…`. Two unrelated uses:
`scripts/production_like_course_specs.json` is pinned from it and produces
`courses.json` (unused, above) and seeds the local catalogue; and
`scripts/sync_course_platform.py` syncs *application code* from it against the
768-row ledger `_docs/adoption/course-platform/copied-files.tsv`. The code sync is
not data ingest and is out of scope here.

**7 — legacy `_people`** → `people.json` (438). Read **unconditionally in both
modes** at `scripts/build_public_projection.py:1721`. This is the largest remaining
editorial dependency on the legacy repository. Fate: **push-synced** — `_people`
moves to `DataTalksClub/content`. It has not moved yet.

**8 — legacy `_data/events.yaml`** → `events.json` (421), with description, speakers
and links, built at `scripts/build_public_projection.py:1799`. Fate: **one-off
export, and it is done** — `scripts/prod/import_events.py`'s `import_content()`
writes those records into `EventContent` and the event pages read the database
(§14.2). The built file is ingest input now; nothing serves it.

**9 — legacy `images/authors/`** → 438 of the 1,253 `media.json` records, copied by
`_copy_people_media()` (`scripts/build_public_projection.py:2837-2846`). Fate:
**one-off export to the CDN**; see §7.

**10 — legacy `_data/faqs/*.yml`** → the article catalogue in `ContentDocument`,
read by `content/article_faq.py`. 10 blog articles ended with an FAQ accordion
whose pairs lived in the legacy site's data files, not in the article Markdown;
159 Q/A pairs. The former `content/article_faq.json` capture and its builder
`scripts/build_article_faq.py` are both deleted.
Fate: **one-off export, and it is already done** — the capture is committed and the
pages render from it.

> This article FAQ is **not** the same thing as source 4. Source 4 is the course
> FAQ at `/faq/` from `DataTalksClub/faq`. Source 10 is ten accordions at the
> bottom of ten blog posts. They share a word and nothing else.

---

## 5. The `datatalksclub.github.io` dependency, exhaustively

The owner's position, in their words: *"for now it's okay to take some content from
there. but some is one-off export and some (articles etc) goes to the content
repository and synchronized from there on push."* Taking content from the legacy
repo in the interim is acceptable. **A source with no declared fate is not.**

### 5.1 The headline

**The legacy repository is never read at Django request time.** Every page is
served from the database. There is exactly **one** non-offline read path, and it
is a script an operator runs, not a view.

**If `datatalksclub.github.io` disappeared right now, no visitor would see any
change.** What breaks is *rebuilding*, and one specific operational task.

### 5.2 Every read, classified

Reads that **take content** (fate required):

| # | Location | Reads | Supplies | If the repo vanishes today |
| --- | --- | --- | --- | --- |
| a1 | `scripts/build_public_projection.py:1721` | `_people/*.md` | `people.json` (438) | Pages fine (committed). **Rebuild fails** at `:2941` |
| a2 | `scripts/build_public_projection.py:1799` | `_data/events.yaml` | `events.json` (421) | Pages fine. **Rebuild fails** |
| a3 | `scripts/build_public_projection.py:2846` | `images/authors/*` | 438 `media.json` records | Pages fine *if already hydrated*. See a5 |
| a4 | *(retired)* | `_data/faqs/*.yml` | the article FAQ, 10 articles / 159 pairs | Nothing. `scripts/build_article_faq.py` and the `article_faq.json` capture are both deleted; the pairs live in the article catalogue and are read from the database |
| a5 | `content/media_tooling.py` `_read_from_github` | `raw.githubusercontent.com/.../images/authors/*` over HTTPS | 438 author images | **Only under an explicit `--source github`**, which then breaks. See below |
| a6 | `build_public_projection.py:1385, 1541, 1556, 1641, 3078` | `_posts`, `_podcast`, `_books`, `images/**` | articles, podcasts, books, media — **`--mode fallback` only** | Nothing. Fallback is not accepted |

**a5 is the only read that still wants the legacy repository at run time**, and
`temporary/content/public_projection/media/` is gitignored (`.gitignore:23`, `git ls-files`
returns 0 files). 438 of the 997 media records carry
`"repository": "DataTalksClub/datatalksclub.github.io"` in their provenance.

`sync_public_media_hydrate.py` no longer defaults to `--source github`: the flag is
required, and with no source the command refuses (exit `2`) and names what each source
needs instead of downloading 997 objects, 438 of them from the legacy repository. So:

- Production with `PUBLIC_MEDIA_STORE_BACKEND=s3` — **unaffected**, images come from the bucket.
- CI with `PUBLIC_MEDIA_STORE_BACKEND=memory` — **unaffected**.
- A fresh developer clone, or a re-hydration of the S3 bucket, must name its source:
  `--source checkout` against local clones (`content/media_tooling.py` parses
  `--checkout DataTalksClub/datatalksclub.github.io=/path`), or `--source store` from
  an already-hydrated peer or the bucket. `--source github` still exists and still
  reaches the legacy repository; it is the only network route to those 438 objects
  while the bucket is not yet the origin of record.

Closing this the rest of the way means the CDN bucket becomes the origin of record —
§7 — after which `--source github` has no remaining purpose.

Reads that are **provenance assertions only** — they compare strings inside reviewed
JSON and never touch the repository or the network. They still *pin* us to the legacy
repo's name, so rebuilding any of these records from a different upstream requires
editing these constants first. They are build- and import-time checks now, not the
boot-time refusal this table used to describe:

| # | Location | Asserts |
| --- | --- | --- |
| a7 | *(retired)* | the startup claim check ran as `content.E002`, which no longer exists — see **Integrity at runtime** above. The provenance still travels on each record; nothing verifies it at boot |
| a8 | `content/article_faq.py` | the article FAQ is built from published documents now; what survives is the legacy Slack-URL rewrite (`_LEGACY_SLACK`, `:45`) |
| a9 | `scripts/projection_build/event_description_bridge.py` | `LEGACY_REPOSITORY`, `LEGACY_REVISION`, `LEGACY_SOURCE_PATH = "_data/events.yaml"`, source checksum. A build-time pin now, not a runtime one |
| a10 | `scripts/projection_build/event_speaker_bio_normalization.py` | `people_repository` in the committed normalization plan, which also pins the event count at exactly 421 (§12 item 8) |
| a11 | `temporary/content/event_identity_manifest.json` | the legacy repo as per-event provenance. Read by `scripts/prod/import_events.py`; **no migration reads it any more** |
| a12 | `content_sync/dtc_content/adapter.py:151, 879, 1203-1205`; `contract.py:13` | the `migration.yaml` **inside a `DataTalksClub/content` checkout** — not a legacy read |

Dead data, no code path:

| # | Location | Note |
| --- | --- | --- |
| a13 | `content/review_projection.json` (6 hits) | `edit_url`s pointing at `.../edit/main/_posts/...`. Loaded at startup, but **no template renders `edit_url`** — dead links in data, not on a page |

Already deleted on this branch — **verify they stay gone**: `scripts/build_pinned_legacy_sources.py`
(cloned the legacy repo into `.tmp/`), `compatibility/source_config.py`
(`PINNED_LEGACY_SOURCES`), `scripts/build_legacy_manifest.py`,
`scripts/validate_github_editorial_source_projection_inventory.py`,
`_docs/compatibility/legacy-manifest.jsonl`. Nothing live imports them. Stale on-disk
residue that is not tracked: `scripts/__pycache__/build_pinned_legacy_sources.cpython-313.pyc`,
`.tmp/legacy-compatibility-sources/`, `.tmp/legacy-main-pinned/`.

### 5.3 Redirects — not ingestion, not violations

These send a visitor to the legacy site for pages we deliberately do not host. The
owner asked for them. **Do not confuse them with the reads above.**

| Path | Status | Configured in |
| --- | --- | --- |
| `/mediakit/`, `/mediakit` | **301** | This repo — `website/urls.py:16, 46-56`. Tested by `core/tests/test_mediakit.py` |
| `/docs/`, `/faq/`, `/podwiki/` | **302** | **Not in this repo.** A CloudFront viewer-request function in `DataTalksClub/aws-infra` (`modules/django-website/edge.tf`), referenced from `deploy/development_seo_policy.py:19-32` |

There are zero `.tf` files and zero CloudFront function sources in this worktree.
The redirect behaviour is documented in
`_docs/runbooks/production-hosting-and-dns-migration.md:451-455, 798-848` — 636 URLs,
21.7% of the contract, now 302.

> **Live contradiction, flagged for decision.** Django *also* serves `/docs/` and
> `/faq/` itself (`website/urls.py:62-84`) out of the database, filled by
> `scripts/prod/import_docs.py` and `import_faq.py` — sourced from
> `DataTalksClub/docs` and
> `DataTalksClub/faq`, **not** from the legacy repo. In production the CloudFront
> 302 fires first, so those Django routes are shadowed. Both facts are true and
> they point in opposite directions: either the projections are dead weight and
> should go, or the redirects should. Nobody should discover this mid-migration.
> `/podwiki` is a deliberate 404 (`conftest.py:82`).

---

## 6. Folder mapping: legacy repo → destination

Measured against `/home/alexey/git/datatalksclub.github.io`. **Two columns, and the
difference between them is the whole story**: `HEAD` is what you see if you `ls` the
checkout today; `@pin` is what the build actually reads, at
`LEGACY_MAIN_REVISION = ee43d3fa…` (`scripts/build_public_projection.py:76`),
hard-verified at `:2941`.

| Folder | HEAD | @pin | Holds | Feeds | Fate | Destination |
| --- | ---: | ---: | --- | --- | --- | --- |
| `_posts` | 55 | 55 | Article Markdown | `articles.json` (55) — **fallback only** | Push-synced | **Already in `content`** as `articles/*.md` |
| `_podcast` | 209 | 206 | Episode Markdown | `podcasts.json` (203) — **fallback only** | Push-synced | **Already in `content`** |
| `_books` | 100 | 99 | Book Markdown | `books.json` (98) — **fallback only** | Push-synced | **Already in `content`** as `books/*.yaml` |
| `_people` | 443 | **439** | Author/speaker profiles | `people.json` (438) — **read in BOTH modes** | **Push-synced** | **No destination. This is the work.** |
| `_data/events.yaml` | 429 rows | **421 rows** | Events + descriptions | `events.json` (421) — **ingest input** | **One-off export — done** | Database |
| `_data/faqs/` | 10 | 10 | Blog article FAQ pairs | `article_faq.json` | One-off export — **done** | Already captured |
| `_data/sponsors.yaml` | 1 | 1 | Sponsor list | **Nothing reads it** | Undecided — §12.7 | — |
| `_data/*` (rest) | 3 | 3 | `header`, `navigation`, `events_extra` | Nothing | Stays behind | — |
| `_conferences` | 2 | 2 | Nested multi-track agendas | **Nothing** — see below | **Undecided** | — |
| `_tools` | 2 | 2 | Open-source spotlight cards | Nothing | Stays behind | — |
| `images/` | 1,281 | 1,281 | Article/podcast/book/author media | `media.json` | **One-off export → CDN** | `dtc-website-media` (§7) |
| `assets/` | 4 | 4 | `accordion.js`, `styles.css`, `syntax.css`, `theme.js` | Nothing — pure Jekyll theme | Stays behind | — |
| `_docx` | 1 | 1 | **Only `.gitkeep`** — the folder is empty | Nothing | Drop it | — |
| `_includes` / `_layouts` / `_site` | 22 / 6 / 2,318 | — | Jekyll machinery and build output | Nothing | Stays behind | — |
| `articles.md`, `books.md`, `courses.md`, `events.md`, `people.md`, `podcast.md`, `index.md`, `slack.md`, `support.md`, `tools.md` | 10 | — | Jekyll index pages | Nothing | Stays behind | — |

`articles/`, `podcasts/`, `books/` and `images/` **already exist** in
`DataTalksClub/content` and are declared in its ingest contract
(`content_sync/dtc_content/contract.py`). **Do not re-copy them.**

**The folders with no destination anywhere are `_people`, `_data`, `_conferences`
and `_tools`.** `_people` and `_data/events.yaml` are the live ones — they are the
only reason the build still needs a legacy checkout at all.

**`_conferences` reaches no page.** Nothing constructs a `_conferences` path;
`legacy_main_root` is joined only with `_people` (`:1721`) and `_data/events.yaml`
(`:1799`). The manifest rule `conference_links_outside_slice: "omitted"` means
something narrower than it sounds: at `:1841-1849`, when an event row carries a
`link`/`youtube`/`anchor` starting with `/conferences/`, **the link is dropped** and
the event survives without it. The count is asserted at `:1891`
(`conference_links_omitted != 6`). So **6 event rows point at conference pages that
do not exist on our site**, and the two conference programme documents — nested
multi-track agendas with talks, speakers and abstracts, much richer than an
`events.yaml` row — have no destination. This needs a decision.

### 6.1 Count deltas — there is not a single silent drop

Every apparent shortfall is one of exactly three things. **None of them is a bug.**

| Pair | HEAD | @pin | Post-pin drift | `_template.md` | Editorial removal | Result |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| `_posts` → articles | 55 | 55 | 0 | 0 | 0 | **55** |
| `_people` → people | 443 | 439 | −4 | −1 | 0 | **438** |
| `_books` → books | 100 | 99 | −1 | −1 | 0 | **98** |
| `_podcast` → podcasts | 209 | 206 | −3 | −1 | **−2** | **203** |
| podwiki `_wiki` → wiki | 283 | 282 | −1 | 0 | 0 | **282** |
| `events.yaml` → events | 429 | 421 | −8 | — | — | **421** |

**1. Pin drift (the largest cause).** The pinned revisions are behind their upstream
`HEAD`s by 4 people, 3 podcast episodes, 1 book, 8 event rows and 1 wiki page. Every
one is an **addition after the pin; there are zero deletions**. This is the pin doing
its job — `_verify_checkout` (`:2941-2942`) refuses to build against anything else.
It also means **the site is missing ~17 records that exist upstream today**, which is
the strongest practical argument for push-sync.

**2. `_template.md` scaffolds — deliberate.** Five across four collections. For
`_people` the filter is explicit and doubly enforced:

- `scripts/build_public_projection.py:1722-1725` — inventory assertion: raises
  `"people: public/private source inventory mismatch"` unless there are exactly 439
  `*.md` files **and** the only underscore-prefixed one is `_template.md`.
- `scripts/build_public_projection.py:1739-1740` — the exclusion itself:
  `if path.name.startswith("_"): continue`.

There is exactly **one** underscore-prefixed file, `_people/_template.md`, and it is a
Liquid scaffold (`short: {{id}}`, `picture: "images/authors/{{id}}.jpg"`) — not a
draft, not an example, a template. For `_books` and `_podcast` the exclusion happened
at *migration* time, declared in `content/migration/migration.yaml`
(`rules.templates: "legacy _template.md files excluded"`), not in the builder.

`_people` carries a second, stricter filter worth knowing: a **field allowlist**
(`bio_short, github, layout, linkedin, picture, short, title, twitter, web`) that
hard-fails on anything outside it (`:1726-1745`), plus `short == filename stem`
(`:1746-1748`) and a `picture` path pattern (`:1749-1756`). **A `_people` file with a
new front-matter field will fail the build.** Whoever moves this folder to
`DataTalksClub/content` must carry that allowlist across or the constraint is lost.

**3. Two podcast episodes removed — deliberate, under a signed manifest.**
`content/migration/podcast-removals.yaml` (`kind: podcast_guest_removal`,
`created: 2026-09-01`) records `205/203 → 203/201`, naming `_podcast/_s12e08.md` and
`_podcast/_theme-park-crowd-modeling-to-tesla-full-stack-data-engineering.md`. Both
were **already underscore-prefixed in legacy at the pin**, and the theme-park file has
no YAML front matter at all — neither was a live episode on the Jekyll site either.
They were deleted from the content repo by commit `1375c50`, which *is*
`PREFERRED_CONTENT_REVISION`. Enforced by absence in the source repo, not by a
builder filter; the builder would fail loudly at `:2470` if they reappeared.

**No `published`, `draft`, `hidden` or `status` key exists anywhere in `_posts`,
`_podcast`, `_books` or `_people`.** There is no draft mechanism to misread.

#### The contract is one migration-generation stale

`content_sync/dtc_content/contract.py` `ACCEPTED_COUNTS` says `podcasts: 205,
podcast_transcripts: 203`. `manifest.json` says `podcasts: 203, transcripts: 201`.
**Both are correct about different commits:** `ACCEPTED_CONTENT_COMMIT = e29f56ce…`
is pre-removal; `PREFERRED_CONTENT_REVISION = 1375c506…` is the commit that performed
the removals.

A second staleness in the same dataclass, and this one will bite an ingest:
`path_allowlist` declares `podcasts/*.yaml` and `podcasts/transcripts/*.yaml` (flat),
but at `1375c506…` the layout is **season-hierarchical** — `podcasts/s12/e08.yaml`,
`podcasts/s21/e01-transcript.yaml`, 24 season directories. The builder already handles
both shapes (`rglob` in preferred mode, flat `glob` in fallback — `:1440-1448`,
`:1369-1373`, `:1622-1626`); **the ingest contract's allowlist does not.** Any
push-sync built against that allowlist today would match nothing. See §12.

### 6.2 The podwiki overlap — no dual ownership

`/home/alexey/git/podwiki` also carries `_people` (441), `_books` (99) and
`_podcast_summaries` (207), which looks alarmingly like a second copy of the same
records. **It is not, and nothing needs to be reconciled.**

There are exactly four `wiki_root` joins in the builder:

- `:2154` `(wiki_root / "_wiki").glob("*.md")`
- `:2163` `wiki_root / "graph" / "graph.json"`
- `:2164` `wiki_root / "search" / "search-corpus.json"`
- `:2731` `_write_wiki_assets`, over `WIKI_PUBLIC_ASSETS = ("assets/og-default.png",)` (`:143`) — one file

**podwiki's `_people`, `_books`, `_podcast_summaries`, `_data`, `docs` and
`_extensions` are never opened.** No code constructs those paths. They are inputs to
podwiki's own Jekyll build.

The `podcast_paths`, `book_paths` and `people_paths` arguments passed into `_wiki()`
at `:3019` are **`dict[str, str]` of slug → public_path built from records we already
produced** (`:2947`, `:3011-3018`), not directory reads — the type signature at
`:2148-2150` settles it. They are used only for cross-link resolution
(`:2074-2083`), URL canonicalisation (`:2126-2134`), rejecting a search document with
an unknown `episode_slug` (`:2241`, `:2253`), and asserting the graph references
exactly 203 podcasts / 98 books / 438 people (`:2270-2293`).

**Conclusion: after the migration, `DataTalksClub/content` owns articles, podcasts,
books and people; `DataTalksClub/podwiki` owns the wiki, its graph and its search
corpus. Neither claims the other's records. podwiki's own copies stay podwiki's
business and we keep ignoring them.** No action, but the graph assertions at
`:2270-2293` mean **a change to our people/book/podcast counts requires a matching
podwiki graph rebuild**, or the projection build fails. That coupling is real and easy
to trip over.

---

## 7. Media

Settled by the owner: **images go to the CDN**, not into the content repository.

The mechanism already exists and is landed — issue #301,
`_docs/runbooks/public-media-objects.md`. `temporary/content/public_projection/media/` is
gitignored; Django resolves `/images/<path>` against `temporary/content/public_projection/media.json`
and reads the object through a pluggable store selected by
`PUBLIC_MEDIA_STORE_BACKEND`:

| Backend | Use |
| --- | --- |
| `local` | Default. On-disk tree. What a developer or tester uses |
| `memory` | Deterministic offline fixture derived from `media.json`. CI. Refuses to activate under production settings |
| `s3` | Published objects from the bucket, via the ambient credential chain. No static key in the repo |

Commands: `scripts/prod/sync_public_media_hydrate.py` (materialise),
`sync_public_media_publish.py` (upload), `sync_public_media_verify.py` (check store
against `media.json`).

Bucket: `dtc-website-media`, 1,253 objects, ~154 MB, named in
`main/website-static/terraform.tfvars.example` in `aws-infra`. Object keys are
path-mirrored `<PUBLIC_MEDIA_S3_PREFIX>/<record_key>`, derived from the **matched
record only**, never from the request path.

**The content migration does not duplicate this work — it inherits it.** Media never
enters `DataTalksClub/content`; the 1,253 objects are already modelled as records
with per-record `provenance.checksum`.

**The reference-rewriting problem, stated plainly.** If an article moves to the
content repository while its images live in the CDN, something must rewrite the
image references. Today that something is `_article_blocks(body, media_root=...)`
(`scripts/build_public_projection.py:1409`) and `_copy_media()` (`:2743`), inside the
projection builder — it rewrites references *at build time* and records provenance
per asset. **There is no equivalent on the database ingest path.** Whatever replaces
the builder has to do this job too, and it is the least obvious part of the work.

### The rewriting surface, measured

~1,201 distinct local media paths across four flat subtrees, and it is unusually
clean:

| Collection | Distinct media paths referenced | Subtree | Ratio |
| --- | ---: | --- | --- |
| `_posts` (55) | 362 | `images/posts/` | ~6.6 per article |
| `_podcast` (206) | 205 | `images/podcast/` | 1:1 |
| `_books` (99) | 196 | `images/books/` | ~2 per book (`cover` + `preview`) |
| `_people` (439) | 438 | `images/authors/` | exactly 1 per profile |

How the 1,253 `media.json` records are composed: **815** from the content repo
(`images/{posts,podcast,books}`, `_copy_media` `:2743-2834`) **+ 438** author images
from the legacy repo (`_copy_people_media` `:2837-2884`, gated by
`EXPECTED_PEOPLE_MEDIA_COUNT = 438` at `:2882`).

The content repo already holds every legacy image for posts/podcast/books —
`legacy-only == 0` for all three — plus **8 extra** restored by
`repairs/2026-08-09-missing-media.yaml`. That is exactly
`EXPECTED_PREFERRED_CONTENT_MEDIA_COUNT = 815` against
`EXPECTED_FALLBACK_CONTENT_MEDIA_COUNT = 807` (`:118-119`).

Things that will surprise you:

- **`images/{partners,landing,other,courses}` (31 files) are not in the projection at
  all.** `_copy_media` hard-fails on anything outside `images/{posts,podcast,books}/`
  (`:2775-2778`). They are Jekyll site chrome.
- **52 carried-but-unreferenced files** (45 under `images/posts/`, 7 under
  `images/podcast/`) are shipped without being named in any front matter or body —
  probably referenced from event descriptions, possibly orphaned.
- **2 of the 440 legacy author images are unused** (440 present, 438 consumed).
- `_copy_media` **content-sniffs every file** — JPEG SOI/EOI, PNG and GIF magic, and
  an SVG sanitizer rejecting `<script>`, `<style>`, event handlers and remote
  `href`/`src`/`url()` (`:2780-2800`). **Any CDN move must preserve byte identity**;
  every object is verified against `provenance.checksum`.

---

## 8. One-time imports into the database

### 11 — CMP export, course content

| | |
| --- | --- |
| **Upstream** | A CMP production SQLite export, e.g. `/data/tmp/rds-export/cmp/rds-prod-20260905-182754.db` (235–250 MB). The exports are filed per product now: `cmp/` is ours, `aisl/` is not (§12 item 9) |
| **Script** | `scripts/prod/import_cmp_content.py` → `courses/services/cmp_content_import.py` |
| **Reads** | `courses_course`, `courses_homework`, `courses_question`, `courses_project`, `courses_reviewcriteria`, `courses_registrationcampaign` — enforced by `_assert_content_only()` at `cmp_content_import.py:312-340` |
| **Refuses to read** | `courses_enrollment`, `courses_submission`, `courses_answer`, `courses_projectsubmission`, `courses_peerreview`, `courses_criteriaresponse`, `courses_courseregistration` |
| **Writes** | ~991 rows: cohort content and registration campaign definitions |
| **Idempotency** | Safe. Every write keyed on a natural key; prints a JSON summary |
| **Bootstrap** | **Yes.** `BOOTSTRAPS_EMPTY_DATABASE = True` — it mints its own cohort and family from the reviewed catalogue, so no placeholder seeder is needed. It still *reconciles* against whatever the repository pull wrote, which is why it runs last in `COURSE_CATALOGUE_ORDER` (§11) |

```
uv run --frozen python scripts/prod/import_cmp_content.py \
    --database .tmp/production-prep-current.sqlite3 \
    --source /data/tmp/rds-export/cmp/rds-prod-20260905-182754.db
```

> **The export is not frozen.** `/data/tmp/rds-export/` receives a **new dump
> every day**. Treating this as "one-time" is a *decision about cutover*, not a
> property of the source: CMP is still live and still being written to. Pick the
> export deliberately and record which one you used.

### 12 — CMP export, learner data — **two importers: accounts, then history**

The other 31 tables in the same export. Measured on
`/data/tmp/rds-export/cmp/rds-prod-20260905-182754.db` (2026-09-05): 38 tables,
**673,449 rows total**. The eleven learner-bearing ones hold **513,625** rows:

| Table | Rows | Imported by |
| --- | ---: | --- |
| `courses_answer` | 218,577 | `import_cmp_learner_history.py` |
| `courses_criteriaresponse` | 107,691 | `import_cmp_learner_history.py` |
| `courses_projectevaluationscore` | 38,026 | `import_cmp_learner_history.py` |
| `courses_submission` | 36,617 | `import_cmp_learner_history.py` |
| `courses_courseregistration` | 28,831 | `import_cmp_learner_history.py` |
| `courses_enrollment` | 21,409 | `import_cmp_learner_history.py` |
| `accounts_customuser` | 20,469 | `import_cmp_learners.py` |
| `account_emailaddress` | 20,466 | `import_cmp_learners.py` |
| `courses_peerreview` | 13,041 | `import_cmp_learner_history.py` |
| `courses_projectsubmission` | 4,279 | `import_cmp_learner_history.py` |
| `courses_userwrappedstatistics` | 4,219 | `import_cmp_learner_history.py` |

`socialaccount_socialaccount` (21,761 on the 2026-09-02 export) is in the same file and
is on the **never import** list — see `production-data-migration.md` step 4 for the fate
of all 38 tables.

**`scripts/prod/import_cmp_learners.py` imports the two account tables and nothing
else.** It is resumable, tracks progress per table in `CmpLearnerImportProgress`, and
creates no account with a usable password, staff or superuser rights, or a
`SocialAccount` row. Its own docstring says enrollments, submissions, answers, reviews
and course registrations "belong to a separate importer that reconciles against the
cohorts and homework `import_cmp_content` writes".

**That separate importer is `scripts/prod/import_cmp_learner_history.py`**
(`BOOTSTRAPS_EMPTY_DATABASE = False`, `:61`) over
`courses/services/cmp_learner_history_import.py`, whose `TABLE_ORDER` (`:171-181`) is
the dependency order of the remaining nine tables: course registrations, enrollments,
submissions, answers, project submissions, peer reviews, criteria responses, project
evaluation scores and per-user Wrapped statistics. It **reconciles and never invents** —
cohorts, homework, questions, projects and criteria come from `import_cmp_content`,
accounts from `import_cmp_learners`, and a row whose parent is missing is counted under
a named bucket and skipped rather than given a placeholder parent. It is resumable,
tracks progress per table in `CmpHistoryImportProgress`, and reports counts and bounded
codes only, never payload. `scripts/load_rds_export.py`, the broad loader that used to
look like the candidate, is deleted — `scripts/tests/test_retired_broad_loader.py`
asserts its absence. `review_import/` imports a *sanitized* subset for local review and
deliberately leaves the learner tables empty.

So the run order is `import_cmp_content`, then `import_cmp_learners`, then
`import_cmp_learner_history` — the third reads the account claims the second wrote
into the target database (`accounts.CmpLearnerClaim`), so point it at the same
database. It still
carries PII, so it stays under `production-data-migration.md` step 4's transforms and
never-import list; that document's §8.3 step 4 records the verified end-to-end run
(20,469 accounts and 414,768 history rows, replay a no-op, SIGKILL-and-resume
identical).

### 13 — `zoomcamp-scoring`, pre-2024 history

| | |
| --- | --- |
| **Upstream** | `DataTalksClub/zoomcamp-scoring`, a **separate repository**, cloned locally. Never vendored |
| **Script** | `scripts/prod/import_legacy_zoomcamp.py` + `scripts/prod/legacy_zoomcamp/` |
| **Command** | `uv run --frozen python scripts/prod/import_legacy_zoomcamp.py --database … --source-repo ~/git/zoomcamp-scoring` |
| **Writes** | Cohorts, homeworks, projects, submissions, enrollments, certificates |
| **Idempotency** | Safe. Every write keyed on a natural key; a replay reports the same counts and creates no duplicate |
| **Bootstrap** | **Yes**, and it is the only source for the pre-2024 editions. `Cohort.save()` resolves the course family from the slug, so no catalogue need exist. Ten other modules bootstrap too (§11) |

The owner's list said "pre-2023"; the code says **pre-2024** — the CMP carries data
from 2024 onward (`scripts/prod/import_legacy_zoomcamp.py:9-13`).

Identity handling, which matters: every learner's real email is recovered from the
weekly raw exports and graduate lists and used to *pick the account*, so a historical
cohort attaches to the account a returning learner already has. Every **displayed**
identity — leaderboard name, certificate name — is a freshly generated placeholder
from the platform's own anonymous-name generator. See `legacy_zoomcamp/identity.py`
and `email_recovery.py`. Only the graded `processed/` exports are read for scores,
never the free-text `raw/` GitHub links.

```
git clone https://github.com/DataTalksClub/zoomcamp-scoring ~/git/zoomcamp-scoring
uv run --frozen python scripts/prod/import_legacy_zoomcamp.py \
    --database .tmp/local.sqlite3 --source-repo ~/git/zoomcamp-scoring --list
```

`--list` reports the discovered editions and writes nothing. Use it first.

### 14 — Event identity manifest

`temporary/content/event_identity_manifest.json` — schema version 4, **421 events**
(no alias mechanism is retained). Imported by `scripts/prod/import_events.py`'s `import_identities()`
(dry-run by default when called with `apply=False`; atomic), which reads that path
as `IDENTITY_MANIFEST_PATH`. **No migration seeds it any more** — the ones that used
to are gone, and `migrate` publishes nothing. `test_support/reference_data.py` loads
the same file into every test database, and the event content beside it.

The former `manage.py import_event_identities` command (and its
`import_event_identity_manifest` alias) are retired: they wrapped the exact
same `scripts.prod.identity_manifest.import_identity_manifest` call `import_events.py` already
made, so once `scripts/prepare_local_data.py` was repointed at that function
directly, nothing called the command anymore.

This assigns the stable public event IDs. **Everything about events depends on it** —
run it before any event registration import.

### 14.2 — Event content

`temporary/content/public_projection/events.json` — 421 records, 159 of them
carrying a description. Imported by `scripts/prod/import_events.py`'s
`import_content()`, which runs immediately after `import_identities()` in the
same `run()`, into `EventContent` with its `EventSpeaker` and `EventLink` rows.
The public event pages read those rows through `events/queries.py`; nothing on a
request path opens the file.

That file is a **staging layer**, not a projection anybody serves. It was built
offline from the legacy `_data/events.yaml` (§8) and then reviewed and
rewritten: the event description bridge (§15) matched 159 events to their Luma
descriptions, removed the "about the speaker" biography and the platform
boilerplate from each one, and bound every surviving link to a reviewed
destination. Rebuilding it needs a Luma exporter checkout an authorized operator
holds locally, so the reviewed form exists nowhere else — which is why it is
checked in and why it is the source.

`events/content_import.py` validates the whole candidate before writing a row
and resolves each record against the identity by its exact legacy tuple, title
and slug, refusing everything on any mismatch. It **reconciles only**: a record
naming an identity the database does not hold is a refusal, never a new event.
Run §14 first — this writes nothing without it. A description arriving without
the bridge's provenance behind it is also refused.

Replaying is safe. Speakers and links are an ordered set the record owns
outright, so a re-run replaces them wholesale; an unchanged record is reported
under `unchanged` and writes nothing.

### 14.3 — New-event identity discovery

Until this leg existed, the manifest above (§14) was the **only** way an `Event` row
could exist. `events.models.create_event_identity()` — the atomic, allocator-safe
function that actually inserts one — had zero callers outside tests. A genuinely new
event named in a fresh Luma export (one the manifest has never described, because it
postdates the export the manifest was built from) had no path into the database at all.

**The mechanism**: `scripts/prod/import_events.py`'s
`discover_new_luma_event_identities()` (orchestration) calls
`scripts.prod.registration_sources.luma.discover_luma_events()` (the read) and
`scripts.prod.registrant_import.create_provider_event_identity()` (the write, itself a thin wrapper
around `create_event_identity()`; it does not reimplement allocation or path
construction). Run it two ways:

```
uv run --frozen python scripts/prod/import_events.py \
    --database .tmp/local.sqlite3 \
    --luma-source /path/to/a/fresh/luma-export \
    --discover-new-events-only
```

`--discover-new-events-only` runs the identity-manifest import, this leg and the staged
content of §14.4, and deliberately **does not** require `--eventbrite-source` or a Luma
export matching the pinned checksum in `event-registration-sources.json` — that pin
exists to protect registration *counts* from silent drift, and this leg writes no count.
Without the flag, a full `run()` (§16/17) also calls it once, right after the identity
and content imports and before registration-aggregate derivation, reporting it under the
`new_event_identities` key — a distinct top-level key, deliberately never merged into
`identities` (the manifest replay) or `activation_coverage` (the registration-count
gate), so an automatic creation can never be mistaken for either.

**What counts as "new," and the duplicate bug that taught us the rule.** The first
version of this leg asked only whether *we* had already minted an identity for the
provider's own event id, guarded by a check for an existing registration aggregate
revision. On a database built in production order that guard is always empty when
discovery runs — identities import first, aggregates stage last — so every export event
the reviewed manifest already described under its legacy `_data/events.yaml` source key
got a **second** `Event`, with its own public id and a provisioned Q&A session. Measured
against the pinned 166-event export: **164 identities created, 144 of them duplicates.**

An export event now matches an event we already have when **exactly one** existing event
shares its calendar date and, case/whitespace-normalized, its exact title — the same
rule `events.services.resolve_unmatched_aggregates` already applies to the same problem,
exact on both axes, so a merely similar title or a neighbouring date is not a match at
all (`scripts.prod.registrant_import.ExistingEventIndex`). Several events sharing both is reported
under `ambiguous_total` and guessed at by nobody, because folding two real events into
one is worse than a duplicate. Anything matching nothing is genuinely new and still gets
an identity. Nothing is *attached* by title or date: a recognised event keeps its own
source key and gains no alias, no provider id and no registration count — discovery
simply declines to create.

**Reconciling a database that ran the unguarded version.** Guarding discovery stops new
duplicates and does nothing about rows already written, so
`--report-duplicate-identities` names each one — its public id and canonical path, the
event it duplicates, and the rows a delete would take with it — and changes nothing.
`--remove-duplicate-identities` removes only a duplicate that is provably inert: no
alias, no registration, no aggregate revision, and either no Q&A session or the
untouched draft one `create_event_identity` provisions. A duplicate carrying real
dependent data is reported and kept. There is no force flag, because merging one is a
decision this script may not guess at.

**Report shape**, per provider: `candidate_total` (events read from the export),
`existing_event_total` (recognised as an event we already hold), `already_tracked_total`
(we minted this one on an earlier run), `ambiguous_total`, `undated_total`,
`no_metadata_total` (a Luma event with zero registrations has no CSV row to read a title
from at all — reported separately, not silently dropped and not treated as an error),
`created_total` and `created_events`. Each created entry carries `title`, `start_at`,
`eligible_count`, `public_id`, `canonical_path` and a `reason` string, so a human
reading the run log sees *why* each row exists without re-deriving it.

**Measured 2026-09-05** against the pinned export
(`.local/migration-data/events/luma-aggregate-v1.backup-20260902`, 166 events) into a
scratch SQLite database built by `manage.py migrate`, with only the reviewed manifest
imported: **166 candidates → 144 recognised as events already held, 20 created, 2 with
no metadata, 0 ambiguous, 0 undated.** A second run against the same database creates
nothing (`already_tracked_total: 20`). Against the drifted 174-event directory the same
run reads 174 candidates and creates 27 — see §12.1, was item 10, for why that
directory did not validate in a full run.

**Three of the twenty are worth a human's eye, and the report says so.** A created event
that shares its exact normalized title with an existing event on a *different* date is
flagged under `existing_event_dates_with_this_title` — it does not change the decision,
because a different date is a different event until a person says otherwise, but a
recurring series or a rescheduled event looks exactly like this. On the pinned export
the three are "Open-Source LLM Zoomcamp 2025 Pre-Course Live Q&A" (7 days apart), "From
RAG to AI Agents: Function Calling and Tool Use" (5 days) and "RAG and Agents
Evaluation: Measuring Retrieval and LLM Answers" (3 days).

**The mapping-review posture, stated explicitly.** Creating an identity and activating a
registration count are independent gates, deliberately. Minting an identity is safe,
reviewable plumbing — title and a canonical `/events/<public_id>/<slug>` path, nothing a
visitor's registration count depends on — so it is fine to automate. This leg never
creates or touches a registration mapping and never activates a count; that stays
exactly as gated as it is (§12 item 2).

### 14.4 — Staged content for discovered events

§14.3 gives a genuinely new event an identity: a title and a canonical path. §14.2
cannot then give it a page, because the 421-record corpus it imports is frozen and
its descriptions come through the bridge (§15), which matches on the legacy
`_data/events.yaml` tuple a discovered event does not have. Without this stage such
an event reaches the database with a URL that renders no schedule and no
description. This is that stage.

**Two files, and which of them a person owns.**

| | What it is | Who writes it |
| --- | --- | --- |
| `_docs/migration-data/local-event-type-input.json` | The reviewed `type` per description file — `webinar`, `workshop`, `podcast` or `conference` — with a `reason` | **A person.** Ships empty. |
| `temporary/content/luma_event_descriptions.json` | The built staging artifact: finished descriptions with their schedule and provenance | `scripts/build_luma_event_descriptions.py --write` |

**Build.** `scripts/build_luma_event_descriptions.py` reads a description export root
holding `descriptions/*.md` beside `_json/*.json`, one pair per event, named alike.
Reporting is the default; it writes nothing without `--write`.

```
uv run --frozen python scripts/build_luma_event_descriptions.py \
    --database .tmp/local.sqlite3 \
    --source-root .local/migration-data/events/luma
```

Per pair it resolves the event by the **provider's own event id**, read from the
checkpoint, against the source identity §14.3 minted the row under. Not by slug: 128
of the 166 export slugs match more than one `Event` row in a database that has both
the manifest and the discovered identities, so a slug lookup silently picks one of
them. An export whose event has no identity yet is reported under `no_identity_yet`
and skipped — run §14.3 first.

`starts_at` comes from that same checkpoint's `event.start_at`, which is genuinely
there. `ends_at` deliberately does not: Luma derives `end_at` from a nominal
`duration_interval`, so importing it would publish a guessed duration as a stated
end, which `events.models.EventContent` says never to do. `type` comes only from the
reviewed input file above — nothing anywhere in a Luma export says whether an event
is a webinar or a workshop, and nothing here infers it from a title. An export the
reviewed file does not name is reported under `no_reviewed_type`, run after run,
until somebody decides.

The description itself is rendered through the bridge's own Markdown and link
policies and then put through the same `normalize_description_html` that removed the
"about the speaker" block and the platform footer from the 421.

The checkpoint carries the registration list beside the event fields. The three
fields are read through the bridge's span reader, which decodes only the spans it is
given, so the `guests` array is never parsed. No attendee value reaches the artifact,
the report or the database.

**The link gate stays human.** A destination with no reviewed decision stops that
event, and the builder reports it **by URL** under `needs_link_review`. Approving one
is an edit to `scripts/projection_build/event_description_link_policy.py` by a
person: host approval alone is deliberately not enough, and nothing here infers or
auto-approves. Measured against the real export on 2026-09-05: **4 events, 6 distinct
destinations** — `http://bol.com`, `https://Fly.io`, two GitHub URLs and one YouTube
URL on already-approved hosts that are not in `REVIEWED_RENDERED_LINKS`, and
`https://pythoninvest.com/` (the reviewed literal is the no-slash spelling).

The build is deterministic: the same export, identities and reviewed input produce a
byte-identical artifact.

**Import.** `scripts/prod/import_events.py`'s `import_new_content()` runs straight
after `discover_new_luma_event_identities()` in the same `run()`, and again under
`--discover-new-events-only`, into `EventContent` with its `EventSpeaker` and
`EventLink` rows. It is reported under its own top-level `new_event_content` key,
never merged into `event_content`: the two artifacts have different provenance and
merging their counts would hide which one moved.

`events/content_import.py`'s `import_new_event_content()` is the same shape as
`import_event_content()` beside it. It validates the whole candidate — envelope,
declared counts and content digest recomputed, then every record — before writing a
row. It reconciles against the identity's **own source triple** rather than the
legacy tuple, which is what keeps it off the 421: their triples name the legacy
repository and can never equal a provider one. A record naming an identity the
database does not hold is a refusal, never a new event. A description arriving
without the review that decided its type is refused too.

**A missing artifact is a normal state**, reported as `{"present": false}`. It exists
only while there is content waiting to land.

Replaying is safe. Speakers and links are an ordered set the record owns outright, so
a re-run replaces them wholesale; an unchanged record reports `unchanged` and writes
nothing.

**Measured end to end on 2026-09-05**, against a scratch SQLite database built from
`manage.py migrate` plus a full `import_events.py` run: 166 description pairs read,
**164 resolved** to an identity by exact provider event id (the other 2 are the
zero-registration events §14.3 could not name, so they have no identity to resolve
to), **4 stopped for link review**, and the rest waiting on a type. With a scratch
type file covering the 160 that pass both gates, the builder prepared 160 records —
154 speaker bios removed, 134 platform-boilerplate blocks removed, 154 internal links
rewritten — and the import created 160 `EventContent` rows. A second run of both
reported `replayed: true`, `created: 0`, `unchanged: 160`. With the checked-in
reviewed file, which is empty, the same run prepares **0**: nobody has decided a type
yet, and that is the honest state.

### 15 — Event description bridge

`temporary/content/event_description_bridge.json`, built by
`scripts/build_event_description_bridge.py`. 159 described events matched, 262
undescribed, 9 gaps, from 168 source pairs.

Its inputs are `--exporter-root` (a Luma exporter checkout) and
`temporary/content/public_projection/events.json` — **it does not read the legacy repository**,
despite naming it in `LEGACY_REPOSITORY`. That constant is provenance stamping only
(§5.2 a9).

The bridge is applied into the event records at projection-build time, and those
records are what §14.2 imports. No runtime code reads the bridge.

### 16 / 17 — Luma and Eventbrite registration aggregates

The pinned facts, from `_docs/migration-data/event-registration-sources.json`. **A run
validates against these**, so they are the numbers that matter, not whatever a directory
on disk currently holds.

| | Luma | Eventbrite |
| --- | --- | --- |
| **Events** | 166 | 209 |
| **Rows** | 51,924 (51,873 approved + 51 declined) | 24,001 (all `attending`) |
| **Schema** | `luma_v1` | three CSV schema versions, fingerprint-checked |
| **Adapter** | `scripts/prod/registration_sources/luma.py` | `scripts/prod/registration_sources/eventbrite.py` |
| **Prep** | `scripts/prepare_event_registration_sources.py` | same |
| **Facts** | `_docs/migration-data/event-registration-sources.json` | same |
| **Activation** | `mapping_review_required` | `mapping_review_required` |

**Aggregate-only. No attendee row is ever read into the database** — the adapters
return counts, checksums and provider IDs. An unsupported schema fingerprint refuses
to parse rows at all (see `derive_eventbrite`'s unsupported-schema branch); one Eventbrite `.xlsx` is
recorded as `unsupported_xlsx_total: 1`.

Both are `activation_state: mapping_review_required` — **staged but not activated.**
Prepared bundles land in a gitignored `.local/migration-data`, never in the worktree.
The durable protected copy a real run should point at lives outside any worktree, at
`~/prod/dtc-data/luma-eventbrite-export/luma-aggregate-v1/`.

> **The default `--luma-source` no longer validates.**
> `.local/migration-data/events/luma-aggregate-v1` has grown to 174 events against the
> 166 the facts file pins, so a full `import_events.py run()` against it exits 1 with
> `registration_source_validation_failed` (verified 2026-09-05). The sibling
> `luma-aggregate-v1.backup-20260902` holds the pinned 166 and runs clean. Somebody has
> to decide whether the pin moves or the directory is discarded — §12.1, was item 10.
>
> **It should move.** Counted 2026-09-05: the 174-event directory is a later capture of
> the same account — 52,467 rows, `tree_sha256 2e18d184…`, the eight extra events dated
> 2026-08-31 to 2026-09-15 — not a corrupted one. Moving a pin is a reviewed commit;
> [`event-registration-pull.md`](event-registration-pull.md) §4.3 is what that review is.

**Luma is not frozen history, and this table is a snapshot rather than a fact.**
New events keep appearing and people keep registering for events we already have:
between the two prepared exports on this machine, 8 events are new and **99 of the
166 they share have different registrant rows — 5 grew, 13 shrank**. A provider
export is therefore not append-only, and neither the aggregate leg nor the
attendee-level leg picks any of that up on a plain re-run. The recurring
procedure, the last-synchronised record, and what is lost when Luma access
expires are in [`event-registration-pull.md`](event-registration-pull.md).

**What a clean run actually resolves.** Measured 2026-09-05 against the pinned export
and the Eventbrite archive, into a scratch database: 375 provider events stage, the
`activation_coverage` line reports `0 of 375 provider events resolved` because no
`--current-registration-input` file named any exact pair, the automatic exact
date-and-title pass then resolves 99 Luma aggregates, and 276 stay unresolved and render
no count. Both sources finish `activated: false`. See §12 item 2 for the breakdown of
why each one is unresolved.

`uv run --frozen python scripts/prod/import_events.py` runs with
`--current-registration-input` pointed at
`_docs/migration-data/local-current-registration-input.json`; set that variable empty to
leave every mapping review-required.

### 18 — Mailchimp export, course-cohort registration backfill

| | |
| --- | --- |
| **Upstream** | The same Mailchimp subscribed-audience export `import_mailchimp_event_tags.py`/`import_mailchimp_subscriptions.py` read, e.g. `~/prod/dtc-data/mailchimp-export/` |
| **Script** | `scripts/prod/import_mailchimp_course_tags.py` → `courses/services/mailchimp_course_tag_import.py` |
| **Writes** | `courses.models.Enrollment` rows only — never `CourseRegistration`, never `CourseInterest` (that concept is gated by issue #286, a different thing: these tags name cohorts that already happened, not pre-cohort interest) |
| **Reads** | Only rows carrying a reviewed course-cohort tag (`TAG_COHORT_MAP` in the service module) — the settled owner-approved edition→(family, year) table from issue #15, e.g. `de-zoomcamp-1`/`de-zoomcamp` → 2022, `ml-zoomcamp-2` → 2022; see that module's docstring for the full table |
| **Idempotency** | Safe. `Enrollment`'s `(student, course)` unique constraint backs a `get_or_create` write; a second run changes nothing |
| **Bootstrap** | **No.** Every tag names a cohort that must already exist (from `import_legacy_zoomcamp` and `import_cmp_content`, run first). A missing cohort is skipped and reported (`tags_missing_cohort`), never guessed at or created |

```
uv run --frozen python scripts/prod/import_mailchimp_course_tags.py \
    --database .tmp/local.sqlite3 \
    --export-dir ~/prod/dtc-data/mailchimp-export
```

`--dry-run` computes cohort-match, account-match and enrollment counts through
read-only queries only. A row whose email matches no existing account is skipped and
counted; this importer never creates a `CustomUser`.

---

## 8.1 Rebuilding a local dev database end to end

The order that actually matters, all commands assuming
`DTC_ENVIRONMENT=local DTC_SQLITE_PATH=<path> DJANGO_SETTINGS_MODULE=website.settings.local`
and an ambient `DTC_SQLITE_PATH`/`DTC_ENVIRONMENT`/`DJANGO_SETTINGS_MODULE` unset first
(a stale ambient value silently points a command at the wrong database):

1. `manage.py migrate`
2. Course catalogue, in order: `import_legacy_zoomcamp.py --source-repo ~/git/zoomcamp-scoring`
   (§13), then `scripts/prod/sync_course_repositories.py` against each registered course
   repository checkout (§3), then `scripts/prod/import_cmp_content.py --source <rds export>`
   (§11). CMP runs last because it reconciles.
3. The five independent, order-free reviewed one-time imports (§4, §11 items 4): `import_public_content.py`, `import_faq.py`, `import_docs.py`, `import_sponsors.py`, `import_testimonials.py`.
4. `import_events.py` (§14-17) — one call, six legs internally sequenced.
5. `import_mailchimp_course_tags.py` (§18) — after step 2, since it needs the course
   catalogue's cohorts to already exist.

**Deliberately not part of a routine rebuild** (§11 "Not in the bootstrap order, and
why"): `import_cmp_learners.py` + `import_cmp_learner_history.py` (real accounts and
learner activity — run only when you actually need them, and record which export you
used), `import_event_registrants.py` and the remaining Mailchimp importers (attendee-level
personal data, need provider credentials), and `import_account_reconciliation.py` (merges
real people, no rollback).

## 9. What is file-backed and what is database-backed

The owner decided "all the content" moves into the database. **That has happened for
the serving path**: no public request reads a file from the source tree. What survives
of the old arrangement is naming, not behaviour. `content/public_data.py` — the
compatibility layer that kept returning the dict shape the files had — is deleted;
`content/catalogue.py` is a query function per kind, `content/public_routes.py`
holds the route inventory and `content/public_graph.py` the graph safety contract.
The name still shows on `content/docs_projection.py` and on
`content/media_store.py`'s `PROJECTION_ROOT`. Describe those as "database-backed,
still wearing the projection's name", not as unfinished ingest.

| Area | Serving path | Source |
| --- | --- | --- |
| Wiki (hub, detail, search, graph, feed, sitemap) | **Database** | `ContentDocument`, via `content/catalogue.py` |
| Podcasts (hub, episodes, guests, transcripts, resources) | **Database** | as above |
| Articles (hub, detail) | **Database** | as above |
| Article FAQ accordions | **Database** | `content/article_faq.py` over `ContentDocument` |
| People / authors | **Database** | `ContentDocument` |
| Books | **Database** | `ContentDocument` |
| FAQ (`/faq/`) | **Database** | `content/faq_data.py` over `ContentDocument` |
| Docs (`/docs/`) | **Database** | `content/docs_projection.py` over `ContentDocument` / `ContentAsset` |
| Editorial redirects | **Database** | `ContentDocument` (the route manifest is one document) |
| Media (`/images/…`) | **Database record + object store** | record from `ContentDocument`, bytes from the store (`content/media_store.py`) |
| Event listing, descriptions and links | **Database** | `events.EventContent` / `EventLink` |
| Event speakers | **Database** ⋈ **database** | `events.EventSpeaker`, biography joined from the person's catalogue record at request time (`content/event_speakers.py`) |
| Event detail | **Database** | `Event` identity, `EventContent`, `EventQnaSession`, registration totals |
| Sitemaps | **Database** | catalogue sections plus `Cohort` for courses |
| Courses / curriculum | **Database** | `courses` models |
| Sponsors | **Database** | `core.models.Sponsor` |
| Testimonials | **Database** | `courses.models.Testimonial` |
| Certificates | **Database** | `Enrollment.certificate_url` |

The one file a public request still touches is the wiki's default social card,
`content/wiki_assets/og-default.png` — a design asset that ships with the app, and the
route serving it still checks the published manifest first. `content/media_store.py`'s
`media_records()` also reads the staged `media.json`, but only for operator tooling and
the offline fixture store; the `/images/…` view resolves its record from the database.

### The pipeline, and what is still wired to nothing

`content/models.py` defines `ContentSource`, `ContentRelease`, `ActiveContentPath`,
`ContentDocument`, `ContentRelation`, `ContentAsset`.

- **`ContentSource`** is the repository registry — written by
  `content_sync/course_repository_registration.py`, read by
  `scripts/prod/sync_course_repositories.py` and the webhook view. It is not a content
  store and never was.
- **`ContentDocument`, `ContentRelease`, `ActiveContentPath` and `ContentAsset` are
  live at both ends.** `scripts/prod/import_public_content.py` writes the editorial
  catalogue, `import_faq.py` the FAQ, `import_docs.py` the documentation and its
  assets; every table in the row above is read on a public request.
  `content/queries.py`'s `resolve_public_document` is called from
  `content/public_views.py:1272` and `content/review_views.py:359`.
- **What is still wired to nothing is the *push-sync* half.**
  `prepare_dtc_content_candidate` (`content_sync/dtc_content/preparation.py`) — the
  function that would turn a pushed `DataTalksClub/content` revision into a release —
  is referenced only from `content_sync/dtc_content/__init__.py`'s `__all__` and from
  tests. No command, view, webhook or job invokes it.

So the database is the serving path, and the remaining gap is *continuous* ingest: a
push to `DataTalksClub/content` changes nothing here today. The one-time importers filled
the tables; nothing keeps them current. `uv run --frozen python scripts/content.py drift` (§10.3) now measures that
gap on demand — it reports, it does not close it.

---

## 10. How we check that content is synchronised

Once content is push-synchronised the operational question is *"is what we are
serving actually what the content repository says?"* — at any moment, not only at
import time. For `DataTalksClub/content` that check now exists and is
`uv run --frozen python scripts/content.py drift` (§10.3). **The course repositories still have none**: nothing
compares a served curriculum against its repository out of band, and §10.3's second
half is still a design rather than a tool.

### 10.1 What already exists

Six mechanisms, each solving part of the problem for a different source.

| Mechanism | Compares | Runs | Catches |
| --- | --- | --- | --- |
| `scripts/prod/sync_content_verify.py` | the **served editorial catalogue** against a `DataTalksClub/content` checkout at a named revision, **both directions** | `scripts/content.py drift`, by hand / ops | missing, extra and byte-mismatched records per family; a served revision that is behind upstream or not on it at all |
| `CourseCurriculumImportRun` + `replayed` | this commit against previously applied runs | during ingest | re-applying a commit; a commit that produces a different manifest checksum |
| `manage.py verify_dtc_content` | a **checkout** against pinned contract constants | CI / by hand | wrong repo, wrong commit, dirty tree, content that fails the adapter |
| `content_sync/dtc_content/parity.py` | adapter bundle against the committed projection | inside `verify_dtc_content`, **only at one frozen commit** | projection and content repo disagreeing |
| `scripts/prod/sync_public_media_verify.py` | `media.json` against the object store, **both directions** | by hand / ops | missing, unreadable, mismatched and **orphaned** objects |
| `ci/content_update.py` | on-disk artifact digests against `manifest.json`, per family | CI | a hand-edited projection artifact |

Detail worth knowing at 2am:

**`sync_content_verify.py`** is the only one of the six that compares what a **request
actually serves** against **today's upstream**. It reads `ContentDocument` rows through
the active release of the enabled `ContentSource` for `DataTalksClub/content`, keys each
record on `provenance.source_key` (`transcript_provenance.source_key` for a transcript,
`provenance.source_path` for a media object) and compares `provenance.checksum` — which
is the raw SHA-256 of the upstream file bytes — against the blob at the revision under
comparison. It needs no adapter, no bundle and no `parity.py`, because a served record
already declares which repository owns it, which record it is, and what its source bytes
hashed to. It writes nothing and makes no call over a network of its own: every git
invocation is a local object-database read, and the comparison revision is resolved from
the checkout's own remote-tracking branch. Refreshing that checkout is a separate,
explicitly networked step (`uv run --frozen python scripts/content.py checkout`), which is why the report always
states the remote-tracking head it resolved — that is how stale the answer is.

**`replayed`** (`courses/services/curriculum_import.py:876`) means *this exact commit
and parser version was already applied; nothing was written*. It is reported by
`scripts/prod/sync_course_repositories.py` as `" (replayed)"` and in the JSON summary. It also
re-asserts the projection still exists — a missing Course or Cohort raises
`idempotent_projection_missing` (`:833, :841`). Guards `source_commit_checksum_conflict`
and `source_import_identity_conflict` (`:882, :885`) fire when the same commit
produces a different manifest checksum or a different repository identity. **This is
the closest thing in the tree to a real drift assertion.**

**`verify_dtc_content`** (`content_sync/dtc_content/repository.py:290-355`) asserts:
absolute non-symlink checkout, `git rev-parse --show-toplevel` matches,
**`HEAD == expected_commit`**, `origin` in the allowlist, **`git status --porcelain`
empty**, then materialises the commit tree into a temp dir and re-parses everything
against `DTC_CONTENT_CONTRACT`. Fully offline, no database. But it needs git, a full
clone on the box, and scratch space under `<repo>/.tmp/content-verification` — it is a
**CI gate, not an operator health check**, and it compares against *hardcoded* pins,
never against upstream HEAD.

**`parity.py:311`** is already 90% of the editorial drift check. It proves the adapter
bundle equals what the projection serves, modulo eight declared transforms, down to
per-record provenance, route contract identity, media content type/size/checksum, and
two canonical digests. Its two limits: it is **pinned to `ACCEPTED_CONTENT_COMMIT`**
(`parity.py:318`, gated at `repository.py:353-355`), so it verifies *a historical
acceptance* rather than today's upstream; and it only iterates projection → bundle
(`:410-415`, `projection_document_missing`), never bundle → projection.

**`sync_public_media_verify.py`** (`content/media_tooling.py:311-353`) is **the only true
bidirectional set-diff in the codebase** — `missing` / `unreadable` / `mismatched`
from the record side, `extra` from the store side, `clean` overall, non-zero exit.
Copy its report shape.

### 10.2 What a drift check must assert, and what is closest today

The third column is the state **before** `sync_content_verify.py` landed. It is still the
state for every source except `DataTalksClub/content`; what answers each class for the
editorial repository today is the table below it.

| Question | Assert | Closest existing (pre-#323; still current for every other source) |
| --- | --- | --- |
| **(a)** Upstream document never arrived | for every upstream path admitted by `path_allowlist`, a served record exists at its identity; report `upstream − served` | `parity.py:410-415` — but **inverted** and frozen-commit gated. Counts-only: `EXPECTED_COUNTS` + `_validate_collection` (`build_public_projection.py:2469`). Shape to copy: `verify_media` `report.missing` |
| **(b)** We serve what upstream deleted | the reverse diff `served − upstream` | `curriculum_import.py:583-588, 697-699, 751-777` reconciles deletions **during** ingest (with `protected_question_removal` when submissions exist) but never detects out of band. `media_tooling.py:344-352` `report.extra` is the only orphan detector |
| **(c)** Upstream changed, we did not re-ingest | recompute per-record digest from upstream, compare to stored | Course rows store `source_checksum` (`curriculum_import.py:257`) — but it digests the **parsed dataclass**, not file bytes, so comparison requires re-parsing. `manifest_checksum` conflict (`:876-877`) fires only inside an ingest of the same commit. Editorial: `provenance.checksum` is stored in every projection record and **nothing recomputes it from upstream** |
| **(d)** Our commit is behind upstream HEAD | resolve `refs/heads/<branch>` upstream, compare to ingested commit, assert ancestry not fork | **Nothing does this.** No code path resolves an upstream ref. Closest: `course_repository_checkout.py:77` `commit_is_public` answers "is our commit on the public remote?" — one `git rev-parse <remote>/<branch>` from answering "is it the tip?". `scripts/content.py checkouts` already prints upstream HEAD per source and compares it to nothing |

**What answers each class today, for `DataTalksClub/content`.** The "closest existing"
column above is the state before `sync_content_verify.py` landed; it is kept because it
is still the state for every other source. For the editorial repository the four classes
now map onto named report buckets:

| Class | Bucket | Sets the exit code |
| --- | --- | --- |
| **(a)** upstream document never arrived | `families.<family>.missing` | yes |
| **(b)** we serve what upstream deleted | `families.<family>.extra` | yes |
| **(c)** upstream changed, we did not re-ingest | `families.<family>.mismatched` | yes |
| **(d)** our commit is behind upstream HEAD | `revision_status.state` — `clean` / `behind` with `commits_behind` / `unreachable` | yes |

Media is the one deliberate asymmetry. The served media set is the **referenced** subset
by construction — an image no article, podcast or book points at was never published — so
upstream-only images are reported as `families.media.upstream_unreferenced`, counted and
listed but **not** part of the exit code, while `extra` and `mismatched` for media stay
drift. Media's `missing` bucket is therefore always empty; it exists so that every family
reads the same way.

**The schema is still waiting, and the check deliberately does not touch it.**
`ContentSource` declares `last_webhook_at` (`content/models.py:70`),
`last_reconciled_at` (`:71`), `pending_follow_up` (`:73`) and
`freshness_target_minutes` (`:74`). All four still have **zero writers and zero readers**
anywhere in the tree — `sync_content_verify.py` did not become the first writer, on
purpose. `last_successful_commit` (`:65`) is the "what we hold" pointer and is written
only by the unused release pipeline — the course-repository route never sets it.

### 10.3 How an operator runs it

**For `DataTalksClub/content`: two commands, and only the first touches the network.**

```bash
uv run --frozen python scripts/content.py checkout    # clone or refresh the editorial checkout; prints the resolved HEAD
uv run --frozen python scripts/content.py drift       # offline, read-only; one JSON report on stdout
```

`scripts/content.py checkouts` — plural, §3 — does **not** cover this repository: it selects
only sources whose `adapter_type` is `course_repository_v1`
(`sync_course_repositories.py:73-80`), and the editorial source is not one. The singular
`scripts/content.py checkout` is the editorial one. Do not reach for it expecting the
course-repository `checkouts` command.

Neither target names a repository. Both read the registered enabled `ContentSource` whose
repository is `DataTalksClub/content` — `dtc-public-content` on any database built by
`scripts/prod/import_public_content.py` — and
`sync_content_verify.py --checkout-plan` prints that selection, which is what
`scripts/content.py checkout` consumes. A database with no such enabled source is refused rather
than falling back to a name written into a command wrapper.

| Override | Default | Means |
| --- | --- | --- |
| `CONTENT_DATABASE` | `.tmp/local.sqlite3` | the database whose served catalogue is read |
| `CONTENT_CHECKOUT` | `.tmp/content-checkout` | the editorial checkout; absolutised for you |
| `CONTENT_DRIFT_REVISION` | the checkout's `refs/remotes/origin/<branch>` | compare against a different revision |

**Exit codes, and why 1 and 2 are not the same thing.**

| Code | Means |
| --- | --- |
| 0 | clean — every family matched and the served revision is upstream's tip |
| 1 | drift — the report is on stdout and something is not clean |
| 2 | refusal — the run could not produce an answer; `{"condition": …, "error": …}` on stderr and **nothing on stdout** |

One is reserved for a *report* so that "we are behind" is distinguishable from "I could
not look" — which matters the day this becomes a scheduled job. The wrapper preserves the
script's real exit code, so `scripts/content.py drift` echoes it to
stderr; anything that has to tell the two apart runs
`uv run --frozen python scripts/prod/sync_content_verify.py --database … --checkout …`
directly.

Exit 2 covers: an argparse refusal, a checkout that is not an absolute non-symlink
directory, an `origin` outside `DataTalksClub/content` (refused before a byte of it is
parsed), an unresolvable `--revision`, no enabled editorial source, no active release,
an unreadable upstream file and an upstream tree over the contract's ceilings. **An
un-ingested database is a refusal, not "everything is missing".**

**What it deliberately does not do — this corrects the step 7 this section used to
carry.** The shipped check performs **no** `INSERT`, `UPDATE` or `DELETE`, no service
call and no release transition, and in particular it does **not** write
`last_reconciled_at`, `pending_follow_up` or `last_successful_commit`. The design sketch
below used to instruct an operator to record the outcome in those fields; do not. That
schema slice belongs to **#273**, those four fields still have zero writers (§10.2), and
a read-only report that writes is no longer a read-only report. `freshness_target_minutes`
is likewise not consulted: the report states the remote-tracking head it resolved and
lets the reader judge staleness, because the answer is only ever as fresh as the last
`uv run --frozen python scripts/content.py checkout`.

A checkout with uncommitted changes, or with HEAD parked on some other branch, still
produces a report. The answer comes from the named revision's tree read out of the object
database, so the working copy is simply not part of it — deliberately unlike
`verify_dtc_content`, which is a CI gate on a checkout and does refuse both.

**What it answers today.** Against the current dataset it exits **1** with
`revision_status.state = "unreachable"`: every content-owned served record names
`1375c506…`, which is not on `DataTalksClub/content` (§12 item 1, issue #326). That is
the finding, not a malfunction, and this check is what makes it visible without counting
by hand. Repairing it is #326's; reporting it is this tool's.

**For the course repositories there is still no such check.** What follows is the design
it would be built from, unchanged except that its step 7 is struck for the reason above:

1. **Enumerate** sources exactly as `sync_course_repositories.select_sources` does
   (`:77-111`) — `ContentSource.objects.filter(enabled=True, adapter_type=…)`.
2. **Resolve upstream HEAD** with the same offline git primitives as
   `course_repository_checkout._git`, plus `git rev-parse <remote>/<branch>`; reuse
   `commit_is_public` for ancestry. Compare to the ingested commit → **(d)**.
3. **Read the snapshot** through the already-shared reader path
   (`read_course_repository_checkout` / `fetch_course_repository_snapshot` →
   `read_course_repository_archive`). Same tar, same ceilings, no second
   implementation.
4. **Parse and digest** with `parse_course_repository` and `_checksum`. **Never write.**
5. **Diff** against the served side by stable identity (absent-in-served → **a**,
   absent-in-upstream → **b**) and by digest (→ **c**).
6. **Emit** one line of sorted JSON; exit non-zero unless clean — the house style of
   `verify_dtc_content.py:59`, `sync_public_media_verify.py:52-53`,
   `sync_course_repositories.py:449`, and now `sync_content_verify.py`.
7. ~~**Record** the outcome in `last_reconciled_at` and `pending_follow_up`, using
   `freshness_target_minutes` as the staleness threshold for (d).~~ **Struck.** That is
   a write, it belongs to #273, and the editorial check that shipped does not do it. A
   course-repository check must not either until #273 says what those fields mean.

One thing the editorial check settled that this sketch got wrong: the served side is the
**database**, not `temporary/content/public_projection/*.json`, and `parity.py` is not
the way in. `parity.py` is pinned to `ACCEPTED_CONTENT_COMMIT` and only iterates
projection → bundle; the shipped check reads `ContentDocument` rows and diffs both
directions, which is why it needed neither the adapter nor the bundle.

### 10.2 Should the content repo reuse the course-repository machinery?

**Yes, and it is not a close call.**

The course-repository ingest already solves exactly this problem: one implementation
(`content_sync/course_repository_ingest.py`) shared by a signed push webhook and
`scripts/prod/sync_course_repositories.py`, with the repository list coming from registered
`ContentSource` rows rather than a hardcoded list. The owner was explicit when that
was built that the two transports must use the same code, and the reason holds here:
a second entry point is a second set of bugs and a second answer to "what is
registered".

`DataTalksClub/content` is the same shape of problem — a GitHub repository whose
pushes should land in our database — and it is *already* modelled as a `ContentSource`
with a declared contract (`content_sync/dtc_content/contract.py`, adapter type
`dtc-content-v4`). `scripts/prod/__init__.py` states the intent directly: the
course-repository path *"is the shape the rest of this package is being gathered
into; adding a second entry point for it would undo it."*

What genuinely differs, and needs care rather than a separate pipeline:

- **Content kinds.** The course ingest writes curriculum models; the content adapter
  produces `ContentDocument`/`ContentRelation`/`ContentAsset`. That is an adapter
  difference, which `ContentSource.adapter_type` already exists to express.
- **Media.** Course repositories carry no 154 MB of images. Content ingest has to
  hand assets to the object store and rewrite references (§7). This is the real new
  work.
- **The read side is dead** (§9). Ingest without switching the views over changes
  nothing a visitor sees.

---

## 11. Ordering, and what to run

### Bootstrap order

1. `manage.py migrate` — **required first, and it seeds no content.** Exactly one
   data-bearing migration survives repo-wide (`courses/0002_simplify_registration_counts.py`)
   and it publishes nothing. Event identities, homepage testimonials and the sponsor
   directory used to arrive this way; they are explicit imports now.
2. **A bootstrapping importer, in the order below.** An importer either *bootstraps* —
   it can populate a database holding no prior rows of its own domain — or it
   *reconciles*, matching upstream rows against rows already present and writing
   nothing where it finds no match. Running a reconciler first is not an error, it is a
   silent no-op, which is the trap. A module that bootstraps declares
   `BOOTSTRAPS_EMPTY_DATABASE = True`; the rest either declare it `False` or, like
   `import_account_reconciliation`, declare nothing and are read as not bootstrapping.
   `scripts/prod/__init__.py` lists the bootstrapping set in
   `BOOTSTRAPPING_ENTRY_POINTS`, and `scripts/tests/test_prod_conventions.py` checks the
   two agree. Eleven modules bootstrap today and nine do not: `import_events`,
   `import_event_registrants`, `import_mailchimp_event_tags`,
   `import_mailchimp_subscriptions`, `import_cmp_learner_history`,
   `import_account_reconciliation` and the three media `sync_public_media_*` scripts.
   `import_cmp_learner_history` is the sharpest case — on a database without
   `import_cmp_content` and `import_cmp_learners` it resolves nothing, writes nothing
   and reports success.
3. **Course catalogue, in the declared order** (`COURSE_CATALOGUE_ORDER`, same module):
   `import_legacy_zoomcamp` (the frozen pre-2024 editions, which nothing else has), then
   `scripts/content.py sources` / `checkouts` / `pull`
   (`sync_course_repositories` — the git-synchronized upstream, which owns module and
   unit curricula, and the only networked step), then
   `scripts/prod/import_cmp_content.py`, which is run directly with
   `--database <database> --source <export>`. **CMP runs last because it
   reconciles.** It no longer needs a placeholder seeder to reconcile against — it
   mints its own cohort and family from the reviewed catalogue — but the reverse
   order still refuses on a homework slug collision the first time one cohort is
   described by both CMP and a repository.
   `scripts/tests/test_prepare_local_data_order.py` holds the orchestrator to it.
4. `scripts/prod/import_public_content.py`, `import_faq.py`, `import_docs.py`,
   `import_sponsors.py`, `import_testimonials.py` — the reviewed one-time inputs under
   `temporary/content/`. All bootstrap; none depends on another.
5. `scripts/prod/import_events.py`, whose own `run()` performs six legs in a fixed
   order because each reconciles against the one before it: identity import (§14),
   content import (§14.2), the Eventbrite description-precedence overlay (landed
   2026-09-11 — overwrites the row content import just wrote, for every event whose
   Eventbrite id resolves against `~/prod/dtc-data/eventbrite-event-identities.json`;
   see `events.eventbrite_content`), new-event identity discovery (§14.3), staged
   content for those events (§14.4), then registration-aggregate derivation and
   staging (§16/17). Run it before anything else event-related.
6. `scripts/prod/import_event_registrants.py` — both providers now: `--luma-source`
   (always run) and, since 2026-09-11, `--eventbrite-source`/`--eventbrite-identities`
   (opt-in; a one-time backfill against Eventbrite's frozen export, not a recurring
   pull — see `_docs/runbooks/event-registration-pull.md` §4.5b) — and the Mailchimp
   importers, which reconcile against the events step 5 wrote. They have no Make
   target on purpose — see "Not in the bootstrap order, and why" below.

`scripts/production_data.py dataset` runs stages 1–3 plus `scripts/prepare_local_data.py` and
`scripts/verify_local_dataset.py`; that orchestrator runs the whole of step 5 by
composing `import_events.run()` itself — one call, all six legs, in the fixed order,
under its single transaction, after the editorial block — and `verify_local_dataset.py`
reports `database_event_identities` and `database_event_content` separately, because an
identity alone publishes no page. `scripts/production_data.py bootstrap` therefore has no
`import-events` call of its own: the rehearsal already ran the pipeline, and a second
pass would re-parse both provider exports and re-run every leg to compensate for drift
the first pass had not produced. Read
`_docs/runbooks/local-course-modules-preparation.md` for prerequisites.

### Not in the bootstrap order, and why

`scripts/production_data.py bootstrap` runs steps 1–5 and stops. Three groups of entry
points are deliberately outside it, and the exclusions are checked by
`scripts/tests/test_prod_conventions.py` rather than left to prose.

- **Step 6** — `import_event_registrants`, `import_mailchimp_event_tags` and
  `import_mailchimp_subscriptions`. They read attendee-level personal data and need
  provider credentials, so they stay separately invoked runs.
- **The CMP learner importers** — `scripts/prod/import_cmp_learners.py`,
  `scripts/prod/import_cmp_learner_history.py`, and the explicit ordered runner for the pair
  in order. Between them they write 20,009 real accounts and 472,690 learner rows. A
  local dataset rebuild is a routine developer action and must never pull real
  accounts into a dev SQLite file on its way past. The export is not frozen either —
  a new dump lands daily and there is no `latest` symlink — so an unattended rebuild
  could neither choose one deliberately nor record the choice in the run log, which
  `_docs/runbooks/production-data-migration.md` §4 step 4 requires. Use
  the same importer scripts with `--status` to
  read a resumable run's progress without opening the export at all.
- **Account reconciliation** — `scripts/prod/import_account_reconciliation.py` (the dry run)
  and its `--rollback-check` mode. It merges real people, and
  `_docs/runbooks/account-reconciliation.md` §4 calls the apply out as the one step of
  the migration with no rollback. **There is no `--apply` target and there must never
  be one**: the apply stays a consciously typed command against a human-reviewed
  mapping document.

`scripts/prod/import_cmp_content.py` is the exception that proves the shape. It reads content
only, so it is safe in a rebuild — and it is still not added to
`production_data.py bootstrap`, because that runner already runs it through
`production_data.py local` → `prepare_local_data.py --cmp-source-db`, and a second call
would import it twice.

`--source` and `--database` have no default value for these production importers. Every
command refuses with exit 2 before any Python starts when its source or destination is
missing; a missing value is never a skip, a warning or a guess.

### Where the consolidation got to

As of 2026-09-05, `scripts/prod/` is the single set of production entry points and the
explicit script commands resolve:

- `import_legacy_zoomcamp.py` and `import_events.py` both run, against
  `scripts/prod/import_legacy_zoomcamp.py` and `scripts/prod/import_events.py`. The
  window in which `import-events` pointed at a script that did not exist is over.
- The provider export readers have left the `events` domain.
  `scripts/prod/registration_sources/` holds the Luma and Eventbrite parsers, the Luma
  registrant CSV reader and `safe_source_facts`; `events/importers.py` is a port
  (registry, result types, `SourceReader`) that the ingest entry point registers readers
  into. The domain no longer hard-codes provider names, row counts or CSV header
  digests.
- The projection build helpers live in `scripts/projection_build/`, and the staging
  files they produce live under `temporary/content/`.
- The four CMP entry points — `import_cmp_content`, `import_cmp_learners`,
  `import_cmp_learner_history` and `import_account_reconciliation` — have explicit script
  commands, listed under "Not in the bootstrap order, and why" above. They were runbook-only
  prose that nothing executed and nothing held to an order. `scripts/prod/__init__.py`
  declares `CMP_LEARNER_ORDER` for the run order and `SCRIPT_COMMAND_EXCLUSIONS` for the
  entry points that deliberately have no general runner, and
  `scripts/tests/test_prod_conventions.py` closes the set: a new `scripts/prod` module
  either has a script command or carries a written reason. `sync_content` and the
  three `sync_public_media_*` scripts are excluded pending #310, which is where that
  decision belongs.
- `_docs/design/specs/script-inventory.md` is a point-in-time analysis pinned to an old
  commit — it still describes `scripts/load_rds_export.py`, which is deleted. **Any
  survey based on it will be wrong.** `_docs/runbooks/ingest-script-inventory.md` is the
  maintained map.

---

## 12. Known defects and gaps

Ordered by how much they will hurt. Every entry's **structural** claims — a file exists
or does not, a symbol is present, a repo-wide grep returns nothing, a cited line range
still holds what it is cited for — were re-checked against the code on **2026-09-07** at
`6899c616`. Figures measured over data outside this repository were **not** re-measured
that day: the CMP and AISL exports under `/data/tmp/rds-export/`, anything from a full
`import_events.py` run, the directories under `.local/migration-data/`, and the legacy
upstream drift in §6.1. Each such figure keeps the date it was taken and says so, so a
freshly checked line number never implies a freshly taken number. §12.1 records what
closed, so an old item number still leads somewhere.

1. **The revision the served editorial catalogue was built from does not exist
   upstream** — issue #326. `scripts/build_public_projection.py:92` pins
   `PREFERRED_CONTENT_REVISION = "1375c506dbce85c7c0e5e61f83c753128c5a48d1"`, and
   `gh api repos/DataTalksClub/content/commits/1375c506…` answers **HTTP 422, "No commit
   found for SHA"** (checked 2026-09-07; upstream `main` is `8be8587c`). All **1,116**
   content-owned served records carry that revision — 55 articles, 203 podcasts, 201
   transcripts (in `transcript_provenance`), 98 books and 559 media objects, counted
   2026-09-07 over `temporary/content/public_projection/*.json`. So what we serve was
   built from a source nobody else can fetch, and its provenance resolves only on the
   machine holding the unpushed clone. **This is the most serious entry in the list**,
   and two others are pieces of it: it makes item 4 unfixable rather than merely un-run,
   because a rebuild cannot obtain its inputs at all; and it makes item 13 an
   understatement for this source, because the content pin is not behind upstream, it is
   off the upstream graph. It is no longer invisible: `scripts/content.py drift` reports it as
   `revision_status.state = "unreachable"` and exits 1 (§10.3, §12.1 was item 10).

2. **The mapping backlog: registration aggregates stage, mostly do not resolve, and
   none activate.** Measured on 2026-09-05 by a full
   `scripts/prod/import_events.py` run against the then-pinned 166-event Luma export and
   the Eventbrite archive, into a scratch SQLite database built by `manage.py migrate`:
   **375 provider events** stage (166 Luma, 209 Eventbrite). With no
   `--current-registration-input` file supplied, `activation_coverage` reports **0 of
   375 resolved**; the narrower automatic pass then resolves **99** Luma aggregates on
   exact date-and-title equality, leaving **276 unresolved** (67 Luma — 48 ambiguous
   dates, 11 title mismatches, 6 with no canonical event on the date, 2 with no
   provider metadata — and all 209 Eventbrite, whose export carries no event-level
   title or date to match on). Both sources finish `activated: false`,
   `activation_state: unresolved`. Resolution and public-display activation are two
   separate gates and neither has been passed. The adapters are not the gap; the
   mapping review is. *Every figure here is against the 166-event capture and was not
   re-measured on 2026-09-07; the Luma pin has since moved to the 174-event export
   (`f100d16d`, 2026-09-06 — §12.1, was item 10), so the staged totals will differ on
   the next run.* The code path is unchanged: `--current-registration-input`
   (`import_events.py:1129`) and `activation_coverage` (`:954`, reported at `:1100`).

3. **The only remaining route to 438 of the media objects is the legacy repository**
   (§5.2 a5), and the media tree is gitignored. The default is closed —
   `sync_public_media_hydrate.py` requires `--source` and refuses rather than reach for
   `DataTalksClub/datatalksclub.github.io` — but `--source github` is still the only way
   to fetch those bytes for anyone without a legacy checkout or a populated bucket. It
   stops mattering when the bucket becomes the origin of record.

4. **A full staging rebuild is not reproducible** — issue #253. Contract digests
   `PROJECTION_MANIFEST_SHA256` / `PROJECTION_TREE_SHA256`
   (`content_sync/dtc_content/contract.py:42-43`) were already stale before #301
   changed the digest scope, and the comment above them still says they were
   deliberately left untouched. Whichever of #253/#301 lands second must regenerate
   them. **Regenerating them is not enough on its own:** item 1 means the content
   revision a rebuild would have to read cannot be fetched, so #253 cannot be closed
   before #326 is.

5. **The FAQ and docs staging files have no builder in this repository.**
   `temporary/content/faq_projection.json` and `temporary/content/docs_projection.json`
   are reviewed in by hand and only shape-checked (`ci/content_update.py:47-48`).
   `scripts/prod/import_faq.py` and `scripts/prod/import_docs.py` now read them into
   the database, so the *import* side is closed — what is missing is a reproducer that
   rebuilds either file from `DataTalksClub/faq` and `DataTalksClub/docs`. Every other
   family has one.

6. **`/faq/` and `/docs/` are both served by Django and 302'd away by CloudFront**
   (§5.3). Django still routes them (`website/urls.py:62-84` → `content/review_views.py`),
   now out of the database rather than a file; the CloudFront viewer-request function
   still fires first in production. Two mechanisms disagree; one should go.

7. **The event description bridge structurally cannot describe a discovered event.**
   `apply_bridge_to_events` (`scripts/projection_build/event_description_bridge.py:557-592`)
   matches on the legacy `_data/events.yaml` tuple and **blanks the description of any
   event it has no entry for**. An event minted from a provider export has no such
   tuple, so a rebuild of the 421-record corpus would erase it. This is why discovered
   events get their own staging artifact (§14.4) rather than a bigger bridge; it is
   recorded here because anyone reaching for "just add it to the bridge" needs to know
   it does not work.

8. **`_docs/migrations/event-speaker-bio-normalization.json` pins exactly 421 events and
   has no generator in this repository.** A 422nd event fails the build with `event
   speaker-bio projection count mismatch`
   (`scripts/projection_build/event_speaker_bio_normalization.py:483-485`). Since §14.3
   now mints identities for genuinely new events, the count this file pins and the
   number of events we hold are no longer the same thing by construction.

9. **The second production database is out of scope — decided 2026-09-05.**
   `/data/tmp/rds-export/aisl/rds-aisl_prod-*.db` (the loose
   `rds-aisl_prod-*.db` files at the top of `/data/tmp/rds-export/` are the
   same export before it was filed into `aisl/`) is the database of **AI Shipping
   Labs, a different product**. Measured on the 2026-09-04 export: 108 tables,
   **121,265 rows**, with its own `events`, `content`, `payments`, `plans`,
   `questionnaires`, `bookclub`, `crm` and `analytics` apps; the row count moves
   every day (151,402 on 2026-09-02), so treat any single figure as a snapshot.
   **Owner ruling: it is not migrated here.** No script in this repository reads
   it and none should — re-verified 2026-09-07: a repo-wide grep for `aisl`
   matches no Python file. The entry stays so that the next person to find
   a second `.db` under `/data/tmp/rds-export/` learns it was excluded
   deliberately rather than rediscovering it. Full statement:
   `production-data-migration.md` §14. *The row figures are carried forward from
   2026-09-05 and were not re-measured.*

10. **`backfill_event_qna` and `retry_event_qna` have zero callers.** Repo-wide grep for
    either name returns no reference outside the command modules themselves. The
    *service* `retry_event_qna_provision` is reachable from Studio
    (`events/qna/studio_views.py:121`), the admin API (`management_api/views.py:1046`)
    and its capability (`events/qna/capabilities.py:151`); only the two CLI wrappers are
    dead.

11. **`_conferences` (2 records) has no declared fate**, reaches no page, and **6 event
    rows carry links to conference pages that do not exist on our site** — the links are
    dropped at `scripts/build_public_projection.py:1878-1881` and the drop is
    count-asserted at `:1923-1924`, so it is deliberate and visible rather than silent
    (§6).

12. **The projection's `courses` collection (12 records) is imported and read by no
    view.** `scripts/prod/import_public_content.py` writes it into `ContentDocument`
    and `scripts/projection_build/public_projection_source.py` lists `courses` in
    `COLLECTION_NAMES`, but `/courses`
    is served from `courses.models.Cohort`. Nothing resolves a page through those
    documents.

13. **The pinned revisions are behind upstream, so the site is missing records that
    exist today** — 4 people, 3 podcast episodes, 1 book, 8 event rows, 1 wiki page
    (§6.1). *Not re-measured in either pass (2026-09-05, 2026-09-07): it needs a fresh
    clone of the upstream repositories, and the gap can only have grown.* Those figures
    are against the legacy `datatalksclub.github.io` and podwiki pins; for
    `DataTalksClub/content` the problem is not drift but item 1. Not a defect in itself;
    it is the strongest practical argument for push-sync. **The editorial half is no
    longer invisible** — `scripts/content.py drift` counts it per family (§10.3) — but the
    legacy and podwiki pins in these figures still have no drift check of their own, so
    they stay hand-counted.

14. **The `_people` front-matter field allowlist is enforced only in the projection
    builder** (`scripts/build_public_projection.py:1757-1793`), along with `short == stem`
    and the `picture` path pattern. Move the folder to `DataTalksClub/content` without
    carrying those rules across and the constraint is silently lost.

15. **The podwiki graph asserts our record counts.**
    `scripts/build_public_projection.py:2315-2326` fails unless the graph holds 1,070
    nodes and 12,987 links and references exactly 203 podcasts, 98 books and 438 people.
    **Changing our counts requires a matching podwiki graph rebuild**, across a
    repository boundary, or the projection build fails (§6.2).

16. **Four importer docstrings and one service still name files that moved to
    `temporary/content/`.** `scripts/prod/import_sponsors.py`,
    `scripts/prod/import_testimonials.py`, `scripts/prod/import_faq.py`,
    `scripts/prod/import_docs.py` and `core/sponsors.py` name
    `core/sponsor_directory.json`, `courses/homepage_testimonials.json`,
    `content/faq_projection.json` and `content/docs_projection.json`. Each script's
    `REVIEWED_PATH` is correct, so only the prose misleads. Recorded, not fixed.

    The `--identity-manifest` half of this item is closed:
    `scripts/prepare_local_data.py` imports `IDENTITY_MANIFEST_PATH` from
    `scripts/prod/import_events.py` instead of re-deriving the path, which is the
    fix that stops it drifting again.

### 12.1 Closed since the last revision

Recorded with the old item number so a reader arriving with a stale reference finds
the answer rather than re-investigating. **A `Was item` number is the number the entry
held in the revision that closed it** — the numbering a stale reference was written
against, not a number that survives later revisions. §12 renumbers contiguously
whenever an entry leaves it or a new one is ranked above it, so the same `Was item`
number can appear twice in this table for two unrelated defects.

| Was item | Was claimed | Closed by |
| --- | --- | --- |
| 1 | "CMP learner data beyond accounts has no importer — that importer does not exist, and it is the largest remaining gap in the migration" | `scripts/prod/import_cmp_learner_history.py` over `courses/services/cmp_learner_history_import.py`, whose `TABLE_ORDER` (`:171-181`) covers all nine remaining learner tables — course registrations, enrollments, submissions, answers, project submissions, peer reviews, criteria responses, project evaluation scores and Wrapped statistics. Rehearsed end to end on 2026-09-05: 20,469 accounts and 414,768 history rows, replay a no-op, SIGKILL-and-resume identical (`production-data-migration.md` §8.3 step 4). §8 source 12 |
| 2 | "The content database pipeline is dead at both ends — nothing writes them and nothing reads them" | `scripts/prod/import_public_content.py` writes `ContentDocument` rows; `content/catalogue.py`, `content/article_faq.py`, `content/faq_data.py` and `content/docs_projection.py` read them on every public request. §9 |
| 7 | "Sponsors have no ingest at all" | `scripts/prod/import_sponsors.py`, reading `temporary/content/sponsor_directory.json` through `core.sponsors`' shared services. `core/sponsor_history.py` and its hardcoded `FEATURED_SUPPORTERS` tuple are deleted |
| 8 | "Testimonials arrive only through a data migration" | `scripts/prod/import_testimonials.py`, reading `temporary/content/homepage_testimonials.json`. The seeding migration is gone; the two `RunPython` migrations left repo-wide (`courses/0002_simplify_registration_counts.py` and `data/0002_redact_datamailer_audit_pii.py`) seed no content |
| 10 | "`.local/migration-data/events/luma-aggregate-v1` has drifted off the pin — it holds 174 events against the 166 `_docs/migration-data/event-registration-sources.json` pins, so the default `--luma-source` fails, and somebody has to decide whether the pin moves or the directory is discarded" | The decision was taken and the pin moved: `f100d16d` (2026-09-06) re-pins `luma.event_total` to **174** and `luma.tree_sha256` to `2e18d184…`, the digest of that directory, with the row, registration and status totals moved together. Reviewed per [`event-registration-pull.md`](event-registration-pull.md) §4.3. *Closed on the pin file alone; no import was re-run for this pass* |
| 10 | "No drift check exists for `DataTalksClub/content` — nothing in the repository compares what we serve against what the content repository says, at any moment" | `scripts/prod/sync_content_verify.py`, run as `scripts/content.py drift` over a checkout refreshed by `scripts/content.py checkout` (§10.1, §10.3) — issue #323. Read-only in both directions and offline: it diffs the five families `DataTalksClub/content` owns — articles, podcasts, podcast transcripts, books, media — keyed on `provenance.source_key` and digested against `provenance.checksum`, and reports `missing` / `extra` / `mismatched` per family plus `revision_status` for drift class (d). Exit 0 clean, 1 drift, 2 refusal. It writes nothing, including the `last_reconciled_at` / `pending_follow_up` this section's step 7 used to ask for — that slice is #273's. Two things it does **not** close: the course repositories still have no equivalent (§10.3), and no CI or scheduled job runs it, because against today's data it exits 1 by design (item 1) and `_docs/PROCESS.md` does not permit carrying a red required job |
| 15 | "The ingest contract's `path_allowlist` declares flat `podcasts/*.yaml` and `podcasts/transcripts/*.yaml` while the content repository's layout is season-hierarchical, so any push-sync built against it would match nothing" | The premise was wrong. `DataTalksClub/content` `main` is `8be8587c` and its layout is flat — checked 2026-09-07 through the API: `podcasts/` holds **205** `*.yaml` files plus one directory, `podcasts/transcripts/` holds **203**, which is exactly what `content_sync/dtc_content/contract.py:121-124` declares, what `ACCEPTED_SOURCE_COUNTS` records and what [`content-authoring.md`](../content-authoring.md) `:26-32` requires normatively. The season-hierarchical tree the entry described is what the unobtainable pinned revision `1375c506…` holds in an unpushed local clone (§6.1, and item 1) — not what a push-sync would be built against. The count half was already closed: `ACCEPTED_SOURCE_COUNTS` (205/203) and `ACCEPTED_COUNTS` (203/201) are two named constants with a comment explaining that they describe different commits |
| 20 | "`content_sync`'s test suite fails wholesale on `main` — 104 failures and 8 errors, every one `DatabaseOperationForbidden`" | `a218361d` and `abc3890c` (both 2026-09-06): the adapter and repository suites moved from `SimpleTestCase` to `TestCase`, and the preparation suite stopped needing the one-time `dtc-public-content` source. Re-run on 2026-09-07 at `6899c616`: `manage.py test content_sync` → **`Ran 101 tests … OK (skipped=11)`**, exit 0. The two figures this entry carried alongside it were also wrong: `content.tests.test_editorial_route_migration_contract` is **not** failing (`Ran 5 tests … OK (expected failures=1)` — its human gate is encoded as an `expectedFailure`), and `content.tests.test_public_media_view` passes under the repository default `PUBLIC_MEDIA_STORE_BACKEND=memory`; forcing `local` against an unhydrated media root gives 6 failures and 3 errors, which is the condition `manage.py check` already reports as `content.W001` (`content/apps.py:59`, issue #301). What is left for **#324** is narrow: `content_sync/tests/test_dtc_content_accepted_checkout.py:95-96` is still a `SimpleTestCase` that reads the catalogue, but it is `@skipUnless(ACCEPTED_CHECKOUT, …)` and `DTC_CONTENT_ACCEPTED_CHECKOUT` is unset in every default environment, so the wall has never been observed |
| — | "Event content has no importer" | `scripts/prod/import_events.py`'s `import_content()` over `events/content_import.py`. Measured 2026-09-05: 421 events, 159 described, 456 speakers, 682 links (§14.2) |
| — | "New-event discovery mints a duplicate identity for events we already have" | Fixed. Against the pinned 166-event export it created **164** identities, **144** of them duplicates; it now creates **20**, recognises **144** as events already held, and reports 2 it cannot name (§14.3) |

---

## 13. Quick reference

| I need to… | Run |
| --- | --- |
| Register **course** repositories | `scripts/content.py sources` |
| Fetch the **course** checkouts, one per registered course repository (network) | `scripts/content.py checkouts` — plural. Course repositories only; it does **not** cover `DataTalksClub/content` |
| Ingest curriculum from those checkouts (offline) | `scripts/content.py pull` |
| Refresh the **editorial** checkout, `DataTalksClub/content` alone (network) | `scripts/content.py checkout` — singular. The only networked step of the drift check |
| Ask whether we still serve what `DataTalksClub/content` says (offline, read-only) | `scripts/content.py drift` — exit 0 clean, 1 drift, 2 refusal (§10.3) |
| Verify a content checkout | `uv run --frozen python scripts/ci.py verify-dtc-content` |
| Check the committed projections | `uv run --frozen python scripts/ci.py content-update-check` |
| Rebuild the whole local dataset | `uv run --frozen python scripts/production_data.py dataset` |
| Verify that dataset | `uv run --frozen python scripts/production_data.py verify --database …` |
| Import pre-2024 Zoomcamp history | `uv run --frozen python scripts/prod/import_legacy_zoomcamp.py` |
| Import events | `uv run --frozen python scripts/prod/import_events.py` |
| Import CMP course content | `uv run --frozen python scripts/prod/import_cmp_content.py --database … --source …` |
| Import CMP learner accounts | `uv run --frozen python scripts/prod/import_cmp_learners.py --database … --source …` (the account layer of step 4 — §8) |
| Import CMP learner history | `uv run --frozen python scripts/prod/import_cmp_learner_history.py --database … --source …` (the other nine tables; run it after the two above, against the same database — §8) |
| Import the editorial catalogue | `uv run --frozen python scripts/prod/import_public_content.py --database …` |
| Import the FAQ and the docs | `uv run --frozen python scripts/prod/import_faq.py --database …`, then `import_docs.py` |
| Import sponsors and testimonials | `uv run --frozen python scripts/prod/import_sponsors.py --database …`, then `import_testimonials.py` |
| Import event identities and content | `uv run --frozen python scripts/prod/import_events.py --database … --luma-source … --eventbrite-source …` (identity import is always the first step; content follows it in the same run) |
| Create identities for new events in a fresh Luma export (§14.3) | `uv run --frozen python scripts/prod/import_events.py --database … --luma-source … --discover-new-events-only` |
| See what a description export still needs from a person (§14.4) | `uv run --frozen python scripts/prod/import_events.py --database … --luma-source … --discover-new-events-only`, then `uv run --frozen python scripts/build_luma_event_descriptions.py --database … --source-root …` |
| Build the staged descriptions once that report is clean (§14.4) | the same command with `--write`; then re-run `import_events.py` |
| Find the duplicate identities an unguarded discovery run left behind (§14.3) | `uv run --frozen python scripts/prod/import_events.py --database … --luma-source … --report-duplicate-identities`; add `--remove-duplicate-identities` to delete only the provably inert ones |
| Materialise media | `uv run --frozen python scripts/prod/sync_public_media_hydrate.py --source checkout --checkout …` (`--source` is required; run it bare to see the options) |
| Publish media to the store | `uv run --frozen python scripts/prod/sync_public_media_publish.py` |
| Verify media against `media.json` | `uv run --frozen python scripts/prod/sync_public_media_verify.py` |

### Never do these

- Do not hand-edit anything in `temporary/content/`. Nothing checks it at startup any
  more — `content.E002` is gone and the app's remaining checks (`content/apps.py`,
  `content.E003`–`E005`, `content.W001`) only cover the media store — so an edit here
  is silently carried into the database by the next import instead of refusing to boot.
- Do not copy `/data/tmp/rds-export/` into the worktree. Read it in place,
  read-only. (This is the stable, protected location; the old `/data/tmp/rds-export/`
  scratch copy still exists too, but `~/prod/dtc-data/` is what scripts should be
  pointed at now.)
- Do not add a second entry point for course-repository ingest. There is one, and
  both transports share it deliberately.
- Do not trust `_docs/design/specs/script-inventory.md`. It is stale (§11).
