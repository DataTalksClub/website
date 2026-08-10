# Structured content authoring and source ownership

`DataTalksClub/content` is the sole editorial source for DataTalks.Club articles, podcast episodes,
podcast transcripts, books, and their adopted media. Published database overrides are not allowed.
`DataTalksClub/datatalksclub.github.io` remains the migration and URL-contract provenance for these
collections and continues to own people, tools, conferences, and other unmigrated main-site data.

The initial accepted database-candidate source is the immutable commit
`e29f56ce70bd997171a78a9f0facc9354797f421`, tree
`c82b0c6ff462dcdd7140f03f2e7d884ed10ff8fa`, with 55 articles, 205 podcasts, 203 separate
transcripts, 98 books, and 815 content media. The 815 count excludes the 438 Person portraits in
the complete 1,253-media #105 public projection. The original `373bef...` migration commit remains
failure provenance only and is rejected at its first missing required media reference.

The repaired baseline is the earlier commit `b9a40ba974fdef67ee3a2a70f114734f2581033c`, tree
`701fa3f7aa35973e65736a188161c480982f1cb3`. Its repair manifest and replacement attestation attest
only that baseline. The accepted source is its descendant after the separately reviewed 19-record
podcast-description overlay; these are distinct provenance layers.

## Repository contract

The registered website source has stable identifier `dtc-content`, repository
`DataTalksClub/content`, allowed branch `main`, and mount `/`. Its versioned adapter reads only:

```text
articles/*.md
podcasts/*.yaml
podcasts/transcripts/*.yaml
books/*.yaml
images/posts/**
images/podcast/**
images/books/**
migration.yaml
repairs/2026-08-09-missing-media.yaml
editorial-overlays/2026-08-10-podcast-descriptions.yaml
```

Filenames and `legacy_path` values are compatibility identities. Do not rename them as a routine
editorial change. The adapter preserves exact legacy URL case and percent encoding and does not
apply a global trailing-slash rule, fallback route, or redirect.

Articles remain UTF-8 Markdown with one leading YAML front-matter mapping. Their filename is
`YYYY-MM-DD-<slug>.md`, and the public path remains `/blog/<slug>.html`. The Markdown body must be
non-empty. Unknown front-matter fields remain available as source metadata. HTML, URLs, and the
small allowlist of legacy Liquid includes are validated and rendered by website-owned code.

Podcast episode files are UTF-8 YAML mappings at `podcasts/<slug>.yaml`. Required identity fields
include `slug`, exact `legacy_path`, `title`, `description`, `season`, `episode`, `guests`, and
`image`. `description` must be a non-empty string; missing, blank, null, numeric, boolean, sequence,
and mapping values fail with no fallback to `intro`, `short`, transcript text, or generated copy.
Both numeric fields must be native positive YAML integers; booleans, strings, floats, null/missing,
zero, and negative values fail rather than being coerced. Links, resources, clips, and unknown
legacy fields remain structured. Transcript segments must never be placed in this mapping.

Exactly 19 description additions are declared in
`editorial-overlays/2026-08-10-podcast-descriptions.yaml`, SHA-256
`63969508134e8b2ef3c8471e9c8dbccc96842fcfc25225fe02e1ed5a4f5926f6`. Do not edit this historical
manifest. A future editorial change edits the authoritative podcast YAML and requires a new reviewed
source commit/evidence contract; it must not be disguised as a repair or migration rewrite.

An episode may contain one relative transcript value of the form
`transcripts/<episode-file>.yaml`. The matching file below `podcasts/transcripts/` is a separate YAML
mapping with the same podcast slug and an ordered `segments` list of mappings. A transcript has its
own source checksum and edit/provenance links, but no public route, canonical, sitemap entry, or
independent indexable page. A missing, escaping, duplicate, mismatched, inline, or orphan transcript
invalidates the complete candidate. An episode may omit the reference when it genuinely has no
transcript.

Book files are UTF-8 YAML mappings at `books/<slug>.yaml`. They retain exact `legacy_path`, title,
description, cover/image, dates, ordered authors and links, structured discussion `archive` with
ordered replies, prose `summary`, and all unknown legacy fields. Do not convert books to Markdown or
flatten their discussion archive.

Media stays byte-for-byte below its adopted `images/` tree. Supported files are GIF, JPEG/JPG, PNG,
and safe passive SVG. The website verifies extension/content agreement, source checksum, size,
references, and exact #34 legacy contract identity where #34 observed the path before a candidate
can become ready. The accepted #105 projection contains eight repaired media paths and two podcast
paths that #34 never observed; those ten records retain an explicit absence of legacy identity and
are bound to #105's checked projection instead of receiving fabricated contract provenance.
Symlinks, traversal, active SVG content, external SVG references, missing media, and unsupported
files fail closed.

Article Markdown and raw HTML must reference repository-owned media through an exact root-relative
`/images/posts/`, `/images/podcast/`, or `/images/books/` URL without a query or fragment. Remote or
protocol-relative image URLs, `srcset`, event handlers, remote theme variants, and CSS URL loading
are rejected before sanitization; authors must first add the media to this repository and use its
local path.

The accepted `e29f56...` migration pin predates this rule and contains five remote image tags in two
articles. They are absent from the checked public article blocks and from the source-owned media
inventory. The bootstrap adapter therefore omits only those five exact, checksum-bound tags from
rendered HTML while retaining raw source and management-only omission evidence. This exception is
not available to later revisions; edit those articles to repository-owned media before the next
accepted content commit.

## Editing and provenance

The operator-facing edit action opens the one authoritative file on the `main` branch of
`DataTalksClub/content`. A podcast episode and its transcript therefore have different edit links.
Release evidence uses a `blob/<full-commit-sha>/...` link instead of the moving branch. Every prepared
document and asset retains source path and SHA-256 checksum, and every release retains the exact
content commit, adapter/schema/rendering versions, migration provenance, source counts, and checked
legacy compatibility-artifact digest.

Release evidence preserves four separate layers: the original migration; the repaired `b9a40...`
baseline with its repair manifest and replacement attestation; the current `e29f56...` editorial
source with source CI and description overlay; and deterministic parity against the checked website
projection. Never claim that the older repair artifacts directly attest the later editorial commit.
The projection keeps source array order; the public podcast catalogue applies its separately tested
season/episode presentation ordering without changing source or adapter order.

Legacy URL contracts continue to identify `dtc-main-site` and its pinned legacy revision. Moving
editorial ownership does not rewrite those observations to claim they originated in the new
repository. The edit-link destination is the one reviewed source-ownership change; it cannot hide an
unrelated URL, canonical, metadata, link, asset, or crawlability difference.

Author, guest, and book-author values remain ordered exact keys. Keys with an approved #105 Person
path are required relations and an unresolved lookup blocks preparation. The accepted bootstrap
also preserves 18 display-only relations for exact keys with no approved Person path as optional,
unlinked relations, matching #105 without inventing a Person, profile path, or fuzzy identity.

## Validation and release handoff

The website adapter performs no clone, fetch, webhook handling, outbound request, source-repository
script execution, or public-request parsing. A read-only verification command accepts an explicit
already checked-out repository and expected full commit. It rejects an unexpected origin, SHA,
working-tree change, checkout root, symlink, schema/count drift, overlay drift, invalid native
podcast metadata, or invalid source content.

Issue #38 supplies the shared operational flow: accept and deduplicate a configured `main` webhook or
scheduled reconciliation, fetch and verify the immutable commit in isolation, run the complete
adapter, show a private candidate preview and compatibility evidence, activate once, observe the
less-than-15-minute freshness target, and retain the former release. Studio and
`/api/v1/admin/` use the same permissions, services, confirmation, revision/idempotency, audit, and
redaction behavior. This source contract adds no second webhook, public mutation, GitHub write
credential, or database content editor.

#105 already renders the accepted source through the checked baked projection at the preserved
article, podcast, and book hubs/details and stable media paths. This adapter proves semantic parity
with that projection and can prepare an immutable database release, but activation of such a release
does not switch a public reader. A separately groomed database-read cutover must preserve #105's
transcript embedding and lack of transcript routes. Invalid source, checkout, repair, overlay,
parity, replay, or preparation evidence leaves the baked public projection and any prior database
release intact.
Rollback selects a retained database release through the shared audited service; it does not select
the rejected `373bef...` source, destructively re-import content, or switch editorial authority.
