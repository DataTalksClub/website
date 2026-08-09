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

- Main hubs use the owner-approved clean paths `/blog`, `/podcast`, `/events`, `/books`, and
  `/people`. The explicit historical hub aliases redirect permanently in one hop: `/articles.html`
  and `/blog/` to `/blog`; the `.html` and slash variants of Podcast, Events, Books, and People to
  their clean hub; and `/courses/` and `/wiki/` to `/courses` and `/wiki`. Queries are preserved.
- Main editorial details use the owner-approved clean paths `/blog/<slug>`, `/podcast/<slug>`,
  `/books/<slug>`, and `/people/<slug>`. Their established `.html` paths and trailing-slash forms
  redirect permanently in one hop to the clean detail while preserving the raw query. The bounded
  public course catalog uses `/courses/<slug>`; established `/tools/<slug>.html` and
  `/conferences/<slug>.html` routes remain unchanged where they currently exist.
- Docs retain pretty trailing-slash paths under `/docs/`.
- FAQ retains `/faq/<course>.html#<question-id>` and the exact JSON field/path contracts at `/faq/json/`.
- The podcast Wiki uses `/wiki` for the hub and query search, `/wiki/<slug>` for editorial details,
  and reviewed extension-bearing graph/feed/data/asset endpoints. The editorial slug `search`
  remains available at `/wiki/search`.
- Course-platform compatibility routes remain available on `courses.datatalks.club` until every known browser, script, certificate tool, and email template has migrated.
- Path case, percent encoding, Unicode, query strings used by public behavior, and trailing-slash behavior are tested rather than normalized globally.

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

Existing SEO-bearing static course articles redirect from `/blog/*.html` to their clean
`/blog/<slug>` canonical and link to the new course/cohort application.

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
| Public hub/feed/sitemap | Approved hubs, feeds, sitemap, and explicit public JSON feeds | 300 seconds; stale-if-error at most 1 hour | `max-age=0, must-revalidate` | Canonical path; exact positive `page` only where registered |
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
  one canonical positive-integer `page`; duplicate, empty, overlong, out-of-range, malformed, or
  unknown parameters become no-store or a safe 400, never an unbounded variant.
- Known tracking keys may be removed by one safe canonical `GET`/`HEAD` redirect. They are not
  reflected or forwarded while absent from the key. Search text and arbitrary filters are not
  cached in the MVP.
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
