# 03 - GitHub content and people

Status: draft

## Source ownership

The following repositories remain editorial sources of truth:

- `DataTalksClub/datatalksclub.github.io`: main pages, articles, podcasts, people, books, tools, conferences, and legacy editorial data;
- `DataTalksClub/docs`: docs pages and navigation hierarchy;
- `DataTalksClub/faq`: FAQ courses, sections, records, and JSON source;
- `DataTalksClub/podwiki`: wiki pages, typed links/citations, graph, and search source.

Studio never creates a conflicting published database override. In the MVP it provides source status, candidate preview, validation diagnostics, activation/rollback, and edit-on-GitHub links.

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
- search/graph build identifiers and asset manifest checksum.

States are `queued`, `fetching`, `validating`, `ready`, `active`, `superseded`, `invalid`, and `failed`. Only a complete `ready` release can become active.

### ContentDocument

- release, content kind, stable key, source path, checksum, and source timestamps;
- exact public path, slug, title, summary, canonical URL, and SEO fields;
- raw frontmatter JSON and raw Markdown/body;
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
- Public path is the owner-approved clean `/people/<short>` route; the established `.html` and
  trailing-slash forms redirect directly to it.
- Names, bio, portrait, and social links come from the active GitHub profile.
- Roles are derived from ordered relationships, so the same person can be an author, guest, speaker, host, instructor, book author, or maintainer at the same time.
- Course/event teaching relationships may optionally link a staff/member user to a public person profile; neither record implies the other.
- A content candidate with an unknown required person key fails validation.
- Person-key renames require an explicit alias and permanent redirect; a filename rename alone cannot silently create a new identity.

The bounded public projection selects all 438 checked `_people/*.md` profiles from
`DataTalksClub/datatalksclub.github.io@ee43d3fa0929faf691178d79f19528e6f15a83e5` and excludes
only `_people/_template.md`. It projects `/people/<short>`, checked portraits, sanitized
biographies and public social links, and derives roles only from exact checked author, guest, and
speaker keys. It does not create or join a `MemberProfile`, user, staff identity, or account.

## Repository adapters

Each source uses an explicit adapter with fixtures from real legacy files.

### Main-site adapter

- Preserves post date-prefix slug rules and collection-specific permalink rules.
- Supports old and current podcast frontmatter variants.
- Normalizes people, books, tools, conferences, and editorial metadata.
- Replaces recurring Liquid includes with code-owned render extensions.
- Migrates charts, MathJax, YouTube, FAQ blocks, and structured-data declarations without allowing arbitrary executable script from content.
- Treats legacy `_data/events.yaml` as migration input only; database events become authoritative after cutover.

### Docs adapter

- Preserves source-path pretty URLs, explicit permalinks, parent/grand-parent navigation, ordering, breadcrumbs, and heading anchors.
- Supports code fences, tables, callouts, Mermaid, images, and safe `relative_url` rewriting.
- Preserves edit-on-GitHub and source-derived modification details.
- Rejects ambiguous title-based parent references.

### FAQ adapter

- Preserves course and section order, stable ten-character question anchors, and raw Markdown answers.
- Does not interpret dbt/Jinja/Liquid-looking expressions inside answers.
- Generates the exact current course and JSON feed contracts.
- Preserves edit-on-GitHub links and optional image behavior.

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
- Studio and API expose the same content management capabilities and permissions.
