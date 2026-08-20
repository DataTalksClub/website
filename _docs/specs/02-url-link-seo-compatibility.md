# 02 - URL, link, and SEO compatibility

Status: draft

URL and link preservation is a release-blocking requirement, not a best-effort migration task.

## Compatibility scope

The baseline inventory covers:

- all generated main-site pages and collection details;
- all docs pages and headings beneath `/docs/`;
- FAQ pages, ten-character question fragments, and JSON feeds beneath `/faq/`;
- Wiki pages, search/filter URLs, generated graph assets, and hash deep links beneath the
  canonical `/wiki` route family;
- current course-platform HTML and API paths on `courses.datatalks.club`;
- assets beneath `/images/`, `/assets/`, `/docs/assets/`, `/faq/assets/`, and `/wiki/assets/`;
- canonical, alternate, Open Graph, Twitter, structured-data, sitemap, robots, and edit-on-GitHub links;
- internal and external link destinations embedded in every rendered page.

## Route rules that must remain exact

- Main hubs use the owner-approved clean paths `/blog`, `/podcast`, `/events`, and `/books`. The
  explicit historical hub aliases redirect permanently in one hop: `/articles.html` and `/blog/`
  to `/blog`; the `.html` and slash variants of Podcast, Events, and Books to their clean hub; and
  `/courses/` and `/wiki/` to `/courses` and `/wiki`. Queries are preserved.
- Main editorial details preserve the established canonical paths `/blog/<slug>.html`,
  `/podcast/<slug>.html`, `/books/<slug>.html`, and `/people/<slug>.html`. Their clean and
  trailing-slash aliases redirect permanently in one hop to the `.html` detail while preserving the
  raw query. The bounded public course catalog uses `/courses/<slug>`; established
  `/tools/<slug>.html` and `/conferences/<slug>.html` routes remain unchanged where they currently
  exist.
- There is no public People catalogue. `/people`, `/people/`, and `/people.html` return `404`
  without a redirect or canonical. Exact Person detail canonicals remain available for linked
  author, guest, and speaker names.
- Docs retain pretty trailing-slash paths under `/docs/`.
- FAQ retains `/faq/<course>.html#<question-id>` and the exact JSON field/path contracts at `/faq/json/`.
- The podcast Wiki uses `/wiki` for the hub and query search, `/wiki/<slug>` for editorial details,
  and reviewed extension-bearing graph/feed/data/asset endpoints. The editorial slug `search`
  remains available at `/wiki/search`.
- Course-platform compatibility routes remain available on `courses.datatalks.club` until every known browser, script, certificate tool, and email template has migrated.
- Path case, percent encoding, Unicode, query strings used by public behavior, and trailing-slash behavior are tested rather than normalized globally.

### Canonical public Event identity

The sole public Event detail canonical is
`/events/<positive-public-id>/<current-title-slug>`. `public-id` is one plain base-10 integer matching
`[1-9][0-9]*`; it has no sign, prefix, or leading zero. It is stable, immutable, unique, and never
reused or renumbered. The current title-derived slug is cosmetic and never selects the Event. Event
UUID remains the immutable internal identity and the only management-route lookup key.

Exact numeric-only and known numeric/stale-slug `GET`/`HEAD` requests redirect permanently in one
hop to the numeric/current-slug canonical with the raw query preserved. The accepted lowercase
RFC 4122 UUID/current-slug and UUID-only public URLs are compatibility aliases with the same direct
destination. Each reviewed date/title alias records both its clean and accepted trailing-slash
spelling explicitly. Alias ownership and reason remain in the checked route manifest; resolution
does not guess the opposite slash form, a nearby date/title, case, source/provider key, or slug.

Canonical numeric `GET`/`HEAD` is terminal `200` and self-canonical. A query-bearing terminal detail
remains `200` with a query-free production canonical but is `no-store`; approved redirects retain
`public, max-age=300`. Zero, negative, signed, zero-padded, overlong/malformed, unknown, alternate-case
UUID, canonical trailing-slash, unrecorded alias-slash, and other non-exact forms are rendered `404`
without a redirect (`max-age=0` when query-free, otherwise `no-store`). Unsafe methods return `405`,
exact `Allow: GET, HEAD`, and `no-store`. No public Event response falls back to a hub or homepage.

Canonical, Open Graph, JSON-LD Event and breadcrumb URLs, internal links, registration/calendar
builders, feeds, and sitemaps emit only the numeric/current-title-slug form. UUID/date paths are
redirect sources only. The identity manifest separately records UUID/source provenance, numeric
canonical, and exact aliases; a missing, duplicate, ambiguous, or renumbered UUID/public-ID mapping
fails closed before activation. A deterministic replay preserves all reviewed assignments.

### Podcast catalogue season navigation

Each successful `/podcast` catalogue response contains exactly one complete actual season. Seasons
are ordered by numeric season descending, and episodes within the selected season are ordered by
numeric episode descending. Duplicate season/episode values use published date descending and then
slug ascending as deterministic tie-breakers; genuine gaps and duplicates are preserved. The clean
`/podcast` path is a moving latest-season representation selected from the maximum validated numeric
season, never a hard-coded season.

The only selector the catalogue reads is one exact ASCII `season=[1-9][0-9]{0,8}` value; every
other parameter in the raw query rides along under the split recorded below. An existing older season
uses `/podcast?season=N`, an exact self-canonical, the title
`DataTalks.Club Podcast — Season N — DataTalks.Club`, and sequence relations in descending catalogue
order: `prev` targets the adjacent newer actual season and `next` targets the adjacent older actual
season. Relations and navigation skip gaps in the actual inventory. A relation or control for the
latest season uses clean `/podcast`; all older controls use their exact normalized season query.

An explicit query for the current latest season returns `200` but uses the clean canonical and
latest-season title, and no internal link emits that query. Season controls list every actual season
in numeric descending order, visibly name each `Season N`, mark the current control once with
`aria-current="page"`, and provide explicit adjacent labels such as
`Newer season — Season 13` and `Older season — Season 11`. A response never combines seasons or
invents a missing one.

Commit `643ea32` (2026-08-17, recorded in #196) split the raw query before the season grammar
reads it, which deliberately amends the earlier whole-query refusal: the `season` selector is kept
and handed to the grammar unchanged, and every other parameter — an unknown key, a campaign tag
such as `utm_source`, the former `page` parameter, an alternate-case spelling, or a mixed query —
is dropped, never reflected or forwarded, so a tagged URL such as `/podcast?page=2` serves the
byte-identical clean page declaring the clean canonical and never becomes an unbounded variant.
The grammar stays exactly as strict about what it does read: duplicate, empty, signed, zero,
leading-zero, encoded, non-ASCII, overlong, or otherwise non-exact `season` forms return bounded
non-reflective `400` responses with `no-store`. The whole raw string stays length-bounded, so an
enormous query is refused rather than parsed. An exact normalized positive season absent from the
actual inventory returns a bounded non-reflective `404` with `no-store`, without a latest/nearest
fallback. `GET` and `HEAD` have identical status and metadata behavior, with an empty HEAD body.
Anonymous `POST` is rejected before catalogue/query work with `405`, exact `Allow: GET, HEAD`, and
`no-store`.

The `.html` and slash hub aliases preserve the raw query byte-for-byte in their one-hop redirects.
Season-query hub URLs stay out of the sitemap, whose podcast portion remains exactly clean
`/podcast` plus all 205 canonical podcast detail `.html` URLs. The shared canonical validator's
only query exception is exact normalized `/podcast?season=N` syntax; the view emits such a canonical
only for an existing non-latest season. All other canonical-query rejection remains unchanged.

### Canonical Wiki route

The product owner selected `/wiki` as the sole podcast-Wiki route family on 2026-08-09 and then
explicitly exempted the short-lived `/podwiki/` mount from preservation. This intentionally amends
the earlier preservation rule:

- canonical, alternate, navigation, content, forms, graph/search, sitemap, asset, and other
  generated internal URLs use the clean `/wiki` family directly;
- `/podwiki/` and every path beneath it are absent and return a real `404`; and
- no redirect or compatibility map from `/podwiki/` is created.

This is a route migration, not a source-identity rename: the editorial repository and adapter may
continue to be called Podwiki where provenance requires it.

## Canonical course URLs

The new canonical course structure is:

- `/courses/<course-slug>` for the reusable course landing page;
- `/courses/<course-slug>/cohorts/<cohort-slug>` for one dated delivery;
- cohort-relative paths for dashboard, calendar, homework, projects, peer review, leaderboard, and certificates.

Existing SEO-bearing static course articles remain at their established `/blog/<slug>.html`
canonical and link to the new course/cohort application. Clean and slash aliases redirect directly
to the `.html` final.

This includes content pages such as
`/blog/guide-to-free-online-courses-at-datatalks-club.html`: the path, meaningful page content,
canonical, metadata, links, and sitemap behavior remain the accepted compatibility contract. A lack
of observed indexing is not permission to rewrite or retire an editorial page.

Existing `courses.datatalks.club` routes initially reach copied compatibility views in the unified Django app. After all links and clients are migrated, that hostname moves to a small Terraform-managed redirect Lambda. HTML paths receive explicit one-hop redirects only after their destination is verified. Existing API routes remain functioning compatibility endpoints until each consumer is updated because API clients may not safely preserve authorization across a cross-host redirect.

The redirect Lambda uses a generated explicit path map, preserves query strings, emits `301` for GET/HEAD HTML, and uses `308` for non-GET only after client tests prove method/body/auth behavior. Unknown paths are logged without PII and return a real `404`, not a homepage redirect.

## URL and link manifest

Before building replacement views, a deterministic crawler writes a versioned manifest containing, for every current URL:

- source repository and source path;
- public path and expected status;
- redirect target when present;
- canonical URL;
- title, meta description, first heading, language, and robots directives;
- structured-data types and identifiers;
- all fragment IDs;
- all internal links, external links, form actions, and asset URLs;
- normalized main-content fingerprint;
- last-modified/sitemap state where present.

The manifest is built from both committed generated sites and a production crawl. Differences are reviewed because a generated tree can be stale while production can contain redirects or edge behavior absent from source.

Every manifest row is classified as:

- `preserve`: same path and equivalent response;
- `redirect`: one approved permanent redirect to an equivalent canonical destination;
- `retire`: an intentional `410 Gone`, requiring owner approval and evidence that no replacement exists.

No catch-all redirect to the homepage is allowed.

## Route cache and canonical contract

One versioned code-owned registry classifies every Django route. It generates or is consumed by
Django cache-header tests, Terraform policy assertions, and deployed smoke expectations; CI fails
for an unclassified route or adapter disagreement. Unlisted routes are private/disabled, not
implicitly public.

| Route class | Initial examples | Edge TTL and stale policy | Browser policy | Shared-cache key |
| --- | --- | --- | --- | --- |
| Fingerprinted static | Versioned `/static/` filenames | 365 days; no stale error object | `public, max-age=31536000, immutable` | Normalized path plus CloudFront-normalized gzip/Brotli only |
| Stable release asset | Code-owned active-release assets without a fingerprint | 24 hours; invalidate on activation | `public, max-age=3600` | Path plus normalized encoding |
| Editorial detail | Approved article, podcast, Person, book, docs, FAQ, and wiki details | 600 seconds; stale-if-error at most 24 hours | `max-age=0, must-revalidate`; ETag/Last-Modified | Canonical path plus normalized encoding; no query |
| Public hub/feed/sitemap | Approved hubs, feeds, sitemap, and explicit public JSON feeds | 300 seconds; stale-if-error at most 1 hour | `max-age=0, must-revalidate` | Canonical path; exact normalized positive selector only where registered (`season` for Podcast) |
| Public course/event catalog or detail | Anonymous-stable catalog/detail pages only | 60 seconds; stale-if-error at most 5 minutes | `max-age=0, must-revalidate` | Canonical path; exact allowlisted pagination only |
| Code-owned permanent redirect | Explicit public alias/redirect manifest only | 24 hours | `public, max-age=300` | Normalized source path; query follows the redirect contract |
| Public 404 | Clean credential-free, query-free unknown `GET`/`HEAD` | 30 seconds; no stale-if-error | `max-age=0` | Normalized path |
| Search or arbitrary query | Search and unlisted filters | Disabled | `private, no-store` | None |
| Private/dynamic | Accounts, Studio, admin/cadmin, learner, onboarding/Slack, registration/management, preview, export, and authenticated/private APIs | Disabled, including error caching | `private, no-store` | None |
| Operational | Health/readiness/metrics, webhooks/callbacks, jobs/providers | Disabled | `private, no-store` or an explicit operational equivalent | None |
| Unsafe/error | Unsafe methods; 400/401/403/405/409/429/5xx; or responses containing `Set-Cookie`, `private`, `no-store`, `Vary: *`, CSRF, identity, PII, or capability state | Disabled | `no-store` | None |

Only `GET` and `HEAD` are eligible in public classes. All policies retain `min_ttl = 0`, so an
origin/edge no-store decision wins. CloudFront does not cache a new origin error merely because a
successful object exists. Stale-if-error serves only a previously cached anonymous public
representation within the class bound, never registration or other time-sensitive/private state.

Positive caching does not normalize or redesign public URLs. The cached representation carries the
same production canonical, robots directives, structured data, headings, content, links, and assets
as a miss. `robots.txt` and sitemap remain explicit versioned public outputs: production exposes the
accepted crawl/sitemap contracts, while development disallows crawling and exposes no production
sitemap. Neither robots nor cache status is an authorization control.

## Query and poisoning rules

- Static, detail, feed, and sitemap cache keys contain no query parameter. A named hub may allow
  one exact registered positive-integer selector; Podcast allows only normalized `season`. Since
  `643ea32` (#196), a raw query is split before any grammar reads it: the parameters a hub actually
  selects on are kept — a duplicate, empty, overlong, out-of-range, or malformed selector still
  becomes no-store or a safe 400 — and every other parameter, including former `page` and unknown
  or tracking keys, is dropped, never reflected or forwarded, so a tagged URL collapses onto the
  clean canonical, never an unbounded variant. The whole raw string stays length-bounded.
- Known tracking keys may be removed by one safe canonical `GET`/`HEAD` redirect. They are not
  reflected or forwarded while absent from the key. `643ea32` (#196) records why no allowlist of
  tracking parameters exists: every tool invents its own, so a list of the ones we happen to know
  would just move the breakage to the next vendor. Dropping every non-selector parameter is the
  allowlist-free form of the same not-reflected, never-a-variant intent. Search text and arbitrary
  filters are not cached in the MVP.
- Host, User-Agent, Referer, Accept-Language, CloudFront country, `X-Forwarded-*`, viewer-supplied
  internal headers, and arbitrary cookies are not public cache-key inputs. Accept-Encoding uses
  CloudFront's gzip/Brotli normalization rather than raw viewer values.
- Cacheable origin requests receive only the per-class allowlist. Duplicate headers/query keys,
  alternate Host, encoded separators, path normalization, omitted-but-forwarded values, conflicting
  cache directives, and compression variants have poisoning tests and fail safely.
- A permanent redirect is cacheable only from the explicit manifest and still obeys its approved
  query preservation/removal rule. Preview/management tokens and private query variants are never
  redirected through or stored in a public object.

## Link preservation checks

For each rendered Django page, the compatibility test resolves:

- relative links against the current page;
- root-relative links against `datatalks.club`;
- FAQ fragments against IDs in the target page;
- docs and Markdown heading fragments using the existing slug algorithm;
- Podwiki chips/citations against wiki, podcast, book, and person targets;
- people references by stable `short` identifier;
- assets against their stable public path;
- external destinations without rewriting their query parameters.

Broken references fail candidate content activation. Explicitly optional or historical external links may warn instead of fail, but the warning is visible in Studio.

## SEO parity gate

Cutover is blocked unless:

- 100% of inventoried URLs resolve with the approved status and destination;
- there are no unexplained `404`, soft-404, redirect loop, or redirect-chain results;
- production canonicals are exact and self-consistent across main, docs, FAQ, Podwiki, and courses;
- page titles, descriptions, primary headings, meaningful body content, and image metadata have no unexplained loss;
- existing Event, BlogPosting, PodcastEpisode, FAQ, breadcrumb, Organization, WebSite/SearchAction, and other JSON-LD contracts validate where currently present;
- sitemap output contains the correct canonical paths and source-derived `lastmod` values;
- robots behavior does not accidentally block production content;
- internal link and asset crawls have no regression from the accepted baseline;
- response performance and rendered HTML remain crawlable without JavaScript.

SEO enhancements, such as filling current FAQ metadata gaps, ship only after parity is measured. They must not obscure migration regressions.

## Development and preview behavior

Every response from `web.dtcdev.click`, content previews, and unpublished course/cohort pages must:

- include `X-Robots-Tag: noindex, nofollow`;
- be disallowed in the development `robots.txt`;
- expose the production-origin root and section sitemap structure for parity inspection without
  submitting it to crawlers;
- use the corresponding `https://datatalks.club/...` canonical for a production-equivalent public page;
- require authentication for private previews and never expose preview tokens to analytics or logs.

## Cutover monitoring

Monitor by path and referrer for:

- `404`, `410`, `5xx`, and redirect volume;
- crawler traffic and sitemap fetches;
- canonical mismatches and duplicate paths;
- top landing-page response time;
- Search Console coverage, crawl, indexing, and structured-data changes;
- changes in organic entrances and ranking pages, interpreted separately from seasonal traffic.

The legacy static build and URL manifest remain available throughout the rollback window.

## Acceptance criteria

- A committed test fixture inventories every current route and public contract.
- A Django crawl has zero unexplained compatibility differences.
- Every intentional change has one explicit redirect/retirement record, owner, reason, and test.
- A link checker validates targets and fragments across all site sections and course compatibility routes.
- Development and previews are demonstrably non-indexable.
- The generated route registry agrees across Django, Terraform assertions, and smoke tests; an
  anonymous public miss/hit preserves the same canonical/robots/sitemap contract.
- Query, header, cookie, encoding, redirect, 404, unsafe-method, and origin-error tests prove that
  only the exact public classes above create bounded cache objects.
- DNS cutover is not approved until the complete SEO parity report passes.

## Historical registration-total representation

The checked canonical event detail `/events/<positive-public-id>/<current-title-slug>` may show one exact, unrounded, non-negative
integer as `N registered` when the events query reports complete accepted coverage. The event hub,
sitemaps, structured data, search, caches, and every other public surface omit the total. They also
omit provider splits, source/mapping state, external event identifiers, and all attendee identity.
Review-required, source-missing, quarantined, or incomplete evidence is omission, never an inferred
zero. `registered` is not attendance evidence.

Activating, replacing, rolling back, or invalidating a contribution increments the event's public
total revision and commits one durable invalidation intent for the exact canonical detail path,
coalesced by canonical event plus revision. Until #109 proves anonymous cache isolation and the
invalidation provider, any event detail carrying a total is `no-store` with zero browser/shared
TTL. The detail remains canonical, indexable in production, and contains no total in JSON-LD.
