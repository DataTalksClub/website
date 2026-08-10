# 03 - GitHub content and people

Status: draft

## Source ownership

The following repositories remain editorial sources of truth:

- `DataTalksClub/content`: articles, podcast episode metadata, separate podcast transcripts,
  books, and media below `images/posts/`, `images/podcast/`, and `images/books/`;
- `DataTalksClub/datatalksclub.github.io`: main pages, people, tools, conferences, events, other
  legacy editorial data, and migration/compatibility provenance for collections moved to
  `DataTalksClub/content`;
- `DataTalksClub/docs`: docs pages and navigation hierarchy;
- `DataTalksClub/faq`: FAQ courses, sections, records, and JSON source;
- `DataTalksClub/podwiki`: wiki pages, typed links/citations, graph, and search source.

Studio never creates a conflicting published database override. In the MVP it provides source status, candidate preview, validation diagnostics, activation/rollback, and edit-on-GitHub links.
Public pages do not render repository names, revisions, checksums, source paths, or source/edit links;
that provenance remains available only to the build, audit, and Studio workflows.
The exact `DataTalksClub/content` folder, schema, and edit-link rules are defined in
[`content-authoring.md`](../content-authoring.md). Edit links use its moving `main` branch;
immutable release provenance uses the exact source commit.

## Core read models

### ContentSource

- stable identifier and display name;
- repository owner/name, allowed branch, and path allowlist;
- adapter type and mount path;
- enabled state and maximum file/byte limits;
- active release, last successful commit, last webhook/reconciliation time;
- sync lock, pending follow-up flag, and freshness target;
- secret reference, never the webhook or GitHub secret itself.

### ContentRelease

- source, immutable commit SHA, parser/rendering version, and status;
- requested, fetched, validated, activated, superseded, and failed timestamps;
- initiator, webhook delivery/request ID, counts, warnings, and structured errors;
- search/graph build identifiers, asset manifest checksum, and the reviewed public route/invalidation
  manifest computed for activation.

States are `queued`, `fetching`, `validating`, `ready`, `active`, `superseded`, `invalid`, and `failed`. Only a complete `ready` release can become active.

### ContentDocument

- release, content kind, stable key, source path, checksum, and source timestamps;
- exact public path, slug, title, summary, canonical URL, and SEO fields;
- raw frontmatter JSON, raw Markdown/YAML body, and complete canonical structured data;
- sanitized rendered HTML and normalized search text;
- adapter-specific metadata JSON for legacy fields not promoted to common columns;
- publish/noindex state and edit-on-GitHub URL.

### ContentRelation

- source document;
- relation type such as `author`, `guest`, `speaker`, `host`, `book_author`, `maintainer`, `related`, `parent`, `wiki_link`, or `citation`;
- stable target kind/key, resolved target document/path, label, order, and optional timestamp.

### ContentAsset

- release, source path, stable public path, immutable S3 key, content type, size, and checksum.

Assets are stored under release-specific keys. The Django asset resolver selects the active release while keeping the original public URL; edge caching makes the stable path efficient.

## Person identity

`Person` is the one canonical public profile concept.

- Its stable source key is the existing `short` identifier.
- Public path preserves the established `/people/<short>.html` canonical. Its clean and
  trailing-slash aliases redirect directly to the `.html` final.
- There is no public People catalogue at `/people`; Person details are discovered only through
  exact content and event relationships.
- Names, bio, portrait, and social links come from the active GitHub profile.
- Roles are derived from ordered relationships, so the same person can be an author, guest, speaker, host, instructor, book author, or maintainer at the same time.
- Course/event teaching relationships may explicitly link a staff user to a public person profile;
  neither record implies the other and the relation grants no account or content authority.
- A content candidate with an unknown required person key fails validation.
- Person-key renames require an explicit alias and permanent redirect; a filename rename alone cannot silently create a new identity.

`accounts.MemberProfile` is not a Person. It is a private, database-owned, one-to-one community and
learner-onboarding record for an authenticated account. Signup, profile completion, Slack access,
course registration, migration, support correction, export, or deletion must not query for or write
a Person as a side effect. Names, email, organization, seniority, links, Slack state, and course
activity never infer a match. A later reviewed relation may connect independently existing records,
but it synchronizes no fields, publishes no private profile value, assigns no editorial role, and
grants no account, course, Studio, or staff permission.

The bounded public projection selects all 438 checked `_people/*.md` profiles from
`DataTalksClub/datatalksclub.github.io@ee43d3fa0929faf691178d79f19528e6f15a83e5` and excludes
only `_people/_template.md`. It projects `/people/<short>.html`, checked portraits, sanitized
biographies and public social links, and derives roles only from exact checked article/book author,
podcast guest, and event speaker keys. A book author is linked only when its structured identifier
exactly equals a Person slug. A podcast-type event contribution is suppressed when its exact
recording URL or YouTube ID matches a podcast and the same Person is both speaker and guest. It does
not create or join a `MemberProfile`, user, staff identity, or account.

## Podcast projection contract

The accepted podcast snapshot consumes `DataTalksClub/content` at immutable commit
`e29f56ce70bd997171a78a9f0facc9354797f421`, tree
`c82b0c6ff462dcdd7140f03f2e7d884ed10ff8fa`. All 205 episode files contain a non-empty scalar
string `description`; missing, blank, null, numeric, boolean, sequence, and mapping forms fail
before parity or persistence, with no `intro`, `short`, transcript, or generated fallback. Both
`season` and `episode` are required native positive integers: missing/null values, booleans,
strings, floats, zero, and negatives fail rather than being coerced. Projection generation remains
deterministic and retains source record order; public catalogue and homepage presentation share the
validated numeric ordering defined in specification 02.

The 19 descriptions added after the repaired baseline are bound by
`editorial-overlays/2026-08-10-podcast-descriptions.yaml` at SHA-256
`63969508134e8b2ef3c8471e9c8dbccc96842fcfc25225fe02e1ed5a4f5926f6`. The adapter validates its
exact schema, ordered target set, field, baseline/migration binding, description digests, and whole
target-file digests without running repository code.

Article image sources are validated before HTML sanitization. Markdown and raw-HTML images must
use one exact root-relative adopted `/images/posts/`, `/images/podcast/`, or `/images/books/` path;
remote, protocol-relative, query/fragment, duplicate/missing source, remote theme source, `srcset`,
event-handler, and CSS-loading forms fail with a bounded source-path diagnostic. Sanitization must
never turn a rejected source into a broken `<img>` without `src`.

The immutable `e29f56...` bootstrap contains five historical remote `<img>` tags across two
articles that are already absent from the checked #105 article-block projection and have no entry
in the 815 source-owned media set. For that commit only, the adapter binds the exact two article
checksums and five complete tag literals, omits those tags before rendering, retains the untouched
raw Markdown, and records the five URLs in management-only adapter evidence. Any byte drift or any
other remote image fails; later commits do not inherit this bootstrap migration transform. Future
editorial changes should replace such media with repository-owned local assets.

## Repository adapters

Each source uses an explicit adapter with fixtures from real legacy files.

### Structured editorial adapter

- Uses registered source `dtc-content`, repository `DataTalksClub/content`, branch `main`, mount
  `/`, and only the adopted article, podcast, transcript, book, provenance, and media paths.
- Keeps articles as YAML-front-matter Markdown, podcast and book records as YAML mappings, and each
  podcast transcript as a separate YAML mapping referenced by one episode.
- Retains complete decoded metadata, exact source bytes/checksums, ordered discussion/transcript
  structures, source paths, edit links, and immutable release links.
- Preserves post date-prefix slug rules and collection-specific permalink rules.
- Preserves exact `/blog/*.html`, `/podcast/*.html`, `/books/*.html`, and adopted `/images/...`
  contracts without inventing transcript routes.
- Replaces recurring Liquid includes with code-owned render extensions.
- Migrates charts, MathJax, YouTube, FAQ blocks, and structured-data declarations without allowing arbitrary executable script from content.

### Remaining main-site adapter

- Normalizes people, tools, conferences, other main pages, and their editorial metadata from
  `DataTalksClub/datatalksclub.github.io` until their owning migrations land.
- Must not emit article, podcast, podcast transcript, book, or adopted media records after the
  structured editorial source is registered.
- Treats legacy `_data/events.yaml` as migration input only; database events become authoritative after cutover.

### Docs adapter

- Preserves source-path pretty URLs, explicit permalinks, parent/grand-parent navigation, ordering, breadcrumbs, and heading anchors.
- Supports code fences, tables, callouts, Mermaid, images, and safe `relative_url` rewriting.
- Preserves the source edit target and source-derived modification details for Studio/audit use,
  without rendering repository provenance on the public page.
- Rejects ambiguous title-based parent references.

### FAQ adapter

- Preserves course and section order, stable ten-character question anchors, and raw Markdown answers.
- Does not interpret dbt/Jinja/Liquid-looking expressions inside answers.
- Generates the exact current course and JSON feed contracts.
- Preserves the source edit target for Studio/audit use and optional image behavior, without
  rendering repository provenance on the public page.

### Podwiki adapter

- Parses typed wiki chips, aliases, citations, timestamp labels, tags, and related pages.
- Preserves graph nodes/edges, JSON shape, hash deep links, and search filters.
- Keeps mirrored podcast/person/book registry items non-public and resolves them to main-site canonicals.
- Preserves git-derived dates, SEO fields, and content-type catalogs.

## Sync safety

- Webhooks use HMAC-SHA256 verification over the raw body and unique delivery IDs.
- Only configured repositories, branches, directories, file types, and commit SHAs are accepted.
- Work uses a shallow immutable checkout in an isolated temporary directory with traversal, symlink, file-count, byte-size, and time limits.
- Sync tasks accept scalar source/run identifiers, never serialized model objects.
- Concurrent requests coalesce behind one source lock and one pending follow-up flag.
- A successful remote-HEAD check skips unchanged commits.
- Parsing/rendering does not occur in model `save()` methods.
- HTML is sanitized by content-kind allowlists. Unsafe URL protocols, event-handler attributes, inline scripts, and arbitrary network fetches are rejected.
- Validation finishes before activation. A failure never deletes or partly updates the active release.
- Scheduled reconciliation detects missed webhooks and branch drift.

## Activation, invalidation, and bounded freshness

Candidate validation computes a deterministic route manifest before activation. It contains changed
public detail paths and every dependent public hub, alias/redirect, feed, sitemap, search surface,
and stable asset path whose representation may change. It never contains preview/management links,
queries, email, member/profile values, or other secrets/PII.

The atomic activation transaction stores that manifest, replaces the path claims and active-release
pointer, and creates one unique edge-invalidation intent keyed by distribution and content release.
Network work starts only after commit. A leased worker coalesces compatible paths, records provider
ID/state, retries bounded transient failures, and alerts on a terminal result. For the first cache
release, exactly one coalesced `/*` invalidation for an activated release is valid; a later optimized
manifest must preserve the same correctness.

Invalidation failure never rolls back an already committed active pointer. Public readers may see
only the prior safe anonymous representation until its route-class TTL expires: editorial details
at most 600 seconds, hubs/feeds/sitemap at most 300 seconds, public course/event pages at most 60
seconds, redirects/stable assets according to their explicit longer class. Stale-if-error remains
bounded by specification 02 and cannot apply to previews, search/query results, authenticated or
private state. Activation status exposes aggregate invalidation counts/state/latency without raw
sensitive paths.

## Initial database-candidate provenance and release boundary

The `dtc-content` adapter is network-free and accepts a caller-supplied checkout already verified
as an immutable configured commit. The accepted bootstrap contains 55 articles, 205 podcast
episodes, 203 separate transcripts, 98 books, and 815 source-owned media: 561 database documents in
total. The complete checked public projection has 1,253 media because it additionally contains 438
legacy-main Person portraits; those portraits are outside this adapter.

Bounded YAML event scanning uses the safe LibYAML parser before construction, rejects aliases and
depth/node overruns, and constructs only safe normalized mappings. Media preflight retains each
validated article/podcast/book parse for the document pass rather than reparsing 358 files. Book
fragments are validated before one final sanitizer pass, and transcript HTML is assembled only
from code-owned elements plus escaped source values. CPU-bound validation of the accepted 815-file
media set uses at most four process workers when at least two CPUs and the fork start method are
available; workers inherit only the current invocation's already-bounded immutable byte mapping,
return path-sorted results, never reread the checkout, and exit before adaptation returns. Small
sets, single-CPU hosts, daemon workers, and platforms without fork validate sequentially. There is
no cross-invocation cache or accepted-checksum validation bypass. An unexpected validator failure
or pool construction, submission, result, or shutdown failure becomes the fixed content-free
`media_validation_worker_failed` diagnostic at the deterministic sorted source path; arbitrary
exception text never crosses the adapter or command boundary, and invocation-local worker state is
cleared before a retry. A parent `KeyboardInterrupt` or `SystemExit` is not converted or swallowed,
but payload, iterator/future, and executor references are unconditionally cleared before it
propagates. This keeps repeated full-corpus validation reliably within the fixed 60-second readiness
bound without raising that bound or weakening parsing, sanitization, or media validation.

Each accepted release keeps four evidence layers distinct:

1. Original migration: `DataTalksClub/content@373bef2912342ece1d2a2d2a9395aa3417243283`,
   legacy main `ee43d3fa0929faf691178d79f19528e6f15a83e5`, and immutable `migration.yaml`
   SHA-256 `dd78a343a5f387a74afa914fc6c7e19790e202aa5d6fa9aba08bfda5995c5f86`.
2. Repaired baseline: commit `b9a40ba974fdef67ee3a2a70f114734f2581033c`, tree
   `701fa3f7aa35973e65736a188161c480982f1cb3`, its source CI, repair-manifest digest, and
   replacement-attestation digest. These artifacts attest that baseline, not the later source.
3. Editorial source: commit `e29f56ce70bd997171a78a9f0facc9354797f421`, tree
   `c82b0c6ff462dcdd7140f03f2e7d884ed10ff8fa`, source CI run `31365358459`, and the strict
   description-overlay evidence above.
4. Website parity: the validated checked projection manifest/tree/podcast identities and
   deterministic adopted-source/comparison digests.

The original migration source remains a deterministic `referenced_asset_missing` rejection, not a
fallback or retained release. Checkout, overlay, parsing, relation, parity, or transactional
preparation failure leaves every prior release and the baked public projection unchanged. An exact
replay returns the same ready/active/superseded release without duplicate children or audit rows.

Issue #38 owns GitHub authentication, webhooks, reconciliation, candidate operations, previews,
activation/rollback orchestration, and future Studio/admin-API workflows. This adapter adds none of
those routes or capabilities. Public requests continue reading #105's checked baked projection even
if an isolated database release becomes active; switching readers is separately groomed cutover
work. Adapter stable ordering also never replaces specification 02's podcast presentation ordering.

## Search and graph

- Content activation builds a candidate search projection before swapping it active.
- Search indexes headings/sections where current docs and Podwiki behavior depends on them.
- FAQ JSON feeds remain separate stable public contracts even though FAQ content also enters search.
- Podwiki graph JSON remains deterministic for a given set of source commits.
- Search or graph build failure blocks that release when it would regress a currently working public feature.

## Studio and API management

Both interfaces can:

- list/configure/enable/disable allowed sources;
- inspect active commit and freshness;
- trigger reconciliation or an exact-commit sync;
- inspect run counts, warnings, and file-level errors;
- preview a ready candidate through authenticated noindex URLs;
- activate a ready release and roll back to a retained valid release;
- inspect routes, relations, unresolved links, search status, and assets;
- open the source file on GitHub.

Only security administrators can change repository allowlists, branches, secret references, or resource limits. Content operators can sync, preview, activate, and roll back approved sources.

## Acceptance criteria

- All current source files either import successfully or appear in an explicit, reviewed exception list.
- All required people and cross-repository references resolve.
- Rendering fixtures cover raw HTML/Liquid, docs features, literal FAQ Jinja, and Podwiki chips/citations.
- Invalid signatures, replayed deliveries, traversal, unsafe HTML, invalid metadata, oversize repositories, and missing references fail safely.
- A failed release leaves public routes, assets, search, graph, and active commit unchanged.
- Activation stores one deterministic public route manifest and durable idempotent invalidation
  intent; retries, provider failure, and first-release wildcard fallback preserve bounded freshness.
- Member onboarding and course activity have zero automatic Person lookup, write, link, publication,
  role, or permission side effect.
- Studio and API expose the same content management capabilities and permissions.
