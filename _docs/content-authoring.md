# Structured content authoring and source ownership

## Where each kind of content is authored (since 2026-11-25)

All new DataTalks.Club content is authored on the new site's sources. The old repository
`DataTalksClub/datatalksclub.github.io` is read-only history plus the interim home of people
profiles (see the last row); its README and PR template say so. `_data/events.yaml` and
`_podcast/`, `_posts/`, `_books/` there are **not** edited any more.

| Content | Author it here | How it reaches the site |
| --- | --- | --- |
| Events (webinars, workshops, podcast live sessions, Hireable With AI sessions), banners, recordings | Website database through the `dtc` CLI: `dtc events create / update / upload-banner / publish / complete` ([event CLI runbook](runbooks/event-cli.md)) | Live at once on https://prod.datatalks.club (no sync step) |
| Podcast episodes and transcripts | PR to `DataTalksClub/content`: `podcasts/sNN/eMM.yaml` and `eMM-transcript.yaml`, image in `images/podcast/` | Content Sync workflow: after the content PR merges, run `gh workflow run content-sync.yml -R DataTalksClub/website` (the page is live about a minute later). The workflow also has a ten-minute cron, but GitHub throttles scheduled runs (measured 2026-11-25: one run every several hours), so do not wait for it |
| Articles / blog | PR to `DataTalksClub/content`: `articles/YYYY/yy-mm-dd-slug.md`, images in `images/posts/` | Content Sync |
| Books (Book of the Week) | PR to `DataTalksClub/content`: `books/YYYY/yy-mm-dd-slug.yaml`, images in `images/books/` | Content Sync |
| Academy pages and the Hireable With AI landing/sessions page | PR to `DataTalksClub/content`: `academy/*.md` (session dates live in `academy/sessions.md` front matter) | Content Sync |
| `/slack` page copy | PR to `DataTalksClub/content`: `slack.yaml` | Content Sync |
| Sponsors | Website database, Studio `/studio/sponsors/` (staff sign-in) | Live at once |
| Header announcement banner | Studio `/studio/settings` (`site.announcement.enabled` and `site.announcement.message`, one safe line) | Live at once |
| Navigation | Website repository (`DataTalksClub/website`): reviewed template change | Normal website deploy |
| Course content | The course repositories registered on the website (see [course content push and pull](runbooks/course-content-push-and-pull.md)) | Course repository sync |
| Newsletter | Authored in the newsletter tool (Relay/Datamailer), not in any website repository | Not part of the website |
| People (speaker/author profiles) | Interim: `_people/<short>.md` + `images/authors/<short>.jpg` in the old repository, plus `dtc events speakers create` for the event system | Content Sync of `dtc-main-site` (the same workflow syncs it; dispatch it after pushing); moving this into `DataTalksClub/content` is tracked as a follow-up |

An event on the website always needs a type and at least one speaker before it can be published.
Order for a new event: `dtc events create --no-publish`, `dtc events upload-banner`, `dtc events publish`
(or create with the default publish when the banner is not needed yet).

Every PR to `DataTalksClub/content` runs `uv run python scripts/validate_content.py` and the tests; merge when green, then dispatch Content Sync as above and open the public page on https://prod.datatalks.club to confirm. Removing a record is a PR that deletes the file; the next sync withdraws the page.

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

An article's frequently-asked-questions pairs live in its own front matter, as a `faq:` list of
`question`/`answer` mappings. Where the body should show them, it keeps the legacy marker line
`{% include faq-accordion.html ... %}` (its arguments are ignored): the articles parser turns that
line into a typed `faq` block, and the page draws the FAQ panel exactly there, so the body's own FAQ
heading sits directly above the panel and anything written after the marker follows it. A body with
`faq:` but no marker gets the panel after its last block. A marker without `faq:`, or a second
marker, is refused by the sync.

A sponsored article declares its sponsor in front matter rather than in a hand-written note:

```yaml
sponsor:
  name: Arize
  url: https://arize.com/
```

The page then opens the reading column with one disclosure, "This post is sponsored by <name>. We
thank the <name> team for their ongoing support of the community.", drawn as the shared quiet
callout. It sits at the top, before the first paragraph, so a reader knows the post is sponsored
before reading it (the usual practice for sponsored content, and what disclosure guidance asks
for). `name` must be non-empty and `url` an `https://` address; anything else is refused.

Podcast episode files are UTF-8 YAML mappings at `podcasts/<slug>.yaml`. Required identity fields
include `slug`, exact `legacy_path`, `title`, `description`, `season`, `episode`, `guests`, and
`image`. `description` must be a non-empty string; missing, blank, null, numeric, boolean, sequence,
and mapping values fail with no fallback to `intro`, `short`, transcript text, or generated copy.
Both numeric fields must be native positive YAML integers; booleans, strings, floats, null/missing,
zero, and negative values fail rather than being coerced. Links, resources, clips, and unknown
legacy fields remain structured. Transcript segments must never be placed in this mapping.

The `links` mapping is reserved for listening destinations such as YouTube, Apple Podcasts, and
Spotify. Show-note links mentioned in an episode belong in the ordered `resources` front-matter
list, so they can be imported without parsing prose:

```yaml
resources:
- title: LinkedIn
  url: https://www.linkedin.com/in/example/
- title: Project website
  url: https://example.com/
```

Every episode keeps this field in its front matter; use `resources: []` when there are no show-note
links. The source stores only the editorial `title` and `url`; the website importer derives
presentation-only fields such as internal-route resolution, external-target behavior, and `rel`.

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

## Call-to-action buttons in a body

A Markdown body (an article, an Academy page, a documentation page, a course page) turns a link
into the site's button by putting the link alone on its own line and marking it:

```markdown
Subscribe to get the rest of the articles as they're published:

[Subscribe to the Substack](https://aishippingblog.com/){.button}
```

- `{.button}` is the secondary action (card surface, ink text) and is the default choice.
  `{.button-primary}` is the green primary action; use it only for the one thing the page is
  asking the reader to do.
- The line must hold nothing but the link and the marker. A marker after a link inside a sentence,
  in a list item, or spelled any other way (`{.btn}`, `{.button-red}`) is dropped and the link stays
  an ordinary link, so the marker never shows as text. No other class can come from content.
- The destination is `https://`, `http://`, `mailto:`, a site path (`/academy/`) or a fragment. An
  outside destination gets `rel="noopener noreferrer"`; course pages also open it in a new tab, as
  they do every outside link.
- Keep "→" and other decoration out of the label; the button is the affordance. One button per
  section at most, for a genuine call to action (subscribe, register, apply, join, sign up), never
  for an ordinary reference link.
- Do not hand-write a button in raw HTML with inline styles; the sanitizer strips the styling and
  the reader gets a plain link.

It renders as `<p class="prose-action">` holding the `templates/core/_button.html` control
(`cta cta-secondary interactive-lift`, or `cta-primary`). The parser is `core.markdown_actions`.

## Elements in a body: facts, cards, steps, callouts

An article body (a blog article, an Academy guide or page) can place a few of the site's own
components exactly where they belong in the text. Each is a fenced block whose info string names
the element and whose content is YAML -- the same fence mechanism community-base uses for
```` ```embed ```` and AI Shipping Labs for ```` ```eventwidget ````. Any other info string stays
a code sample.

````markdown
```facts
label: Key facts            # the strip's accessible name
items:
  - label: Start
    value: April 2027
  - label: Seats
    value: 15
```

```cards                    # one to three cards
items:
  - title: Agentic Engineering
    body: |                 # Markdown: paragraphs, lists, links
      Ship software with coding agents.
    link:                   # optional; drawn as the card's secondary button
      label: More about Agentic Engineering
      href: /academy/agentic-engineering/
```

```steps                    # the shared numbered steps (core/_steps.html)
items:
  - A line of text is a step.
  - title: A step with a title
    body: And a line under it.
```

```callout
variant: info               # info (default), quiet or attention
body: A short note, with [a link](/academy/faq/).
```
````

- `facts` is the design system's spec strip; `cards` are the soft `.card`s, two to a row from the
  tablet width; `steps` is the numbered-steps component; `callout` is `.callout`.
- The fields are checked when the content syncs. A missing field, an unknown variant, more than
  three cards or a link that is not a site path or `https://` address fails the sync with the
  file named, so a broken block never reaches a page.
- Markdown in `body` goes through the same sanitizer as the rest of the body. Raw HTML in a body is
  still not a way to get a component.
- A page's FAQ is not an element: write it as the closing `## FAQ` section with `###` questions,
  and the page draws it with the FAQ block (`core/_faq.html`).
- The one primary action stays the page's closing action; a card's link is a secondary button.

The parser is `content.article_components`; the page draws each element in
`templates/public/_prose_component.html`.

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
