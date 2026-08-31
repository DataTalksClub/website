# Editorial route SEO cutover and rollback runbook

This runbook governs the 796 editorial finals (including 793 `.html` finals and three hierarchical
podcast finals) and 1,590 direct clean/slash aliases in
[`editorial_route_migration.json`](../../content/public_projection/editorial_route_migration.json).
The `s24e04` GenAI Pilots episode is hierarchical-only: its former flat-slug path is not an alias
and must return `404` without a redirect.
Its machine-checkable thresholds are
[`editorial-route-seo-cutover-policy.json`](editorial-route-seo-cutover-policy.json). The primary
owner is the **production SEO cutover commander**; the **website on-call engineer** executes a
rollback; the **product manager** accepts recovery. Automated tests validate the contract but never
activate production, submit Search Console data, or handle production records.

## Hold point and baseline

Production cutover remains a HUMAN gate. Before authorizing it, the owner verifies that the checked
preferred migration-manifest digest matches the policy, all 796 finals pass with `200` and one
self-canonical, and all 1,590 aliases pass with a one-hop query-preserving `301`. Any omission,
duplicate, final/alias collision, chain, or loop stops the cutover.

Capture a UTC baseline no later than 24 hours before cutover. Use the preceding 28 complete days and
compare each post-cutover day with the median of the same weekday in the four baseline weeks. Exclude
only a documented full-site outage, analytics outage, or one-off campaign. Preserve both the raw
export in an access-controlled operational store and a redacted aggregate for the release record.
Never capture account, token, registration, raw IP, or other production-record data here.

Build the old/new comparison from the persisted manifest, not a sampled or synthesized route list:

1. Normalize every clean and slash alias to its recorded `final_path`; keep each final as its own
   comparison key.
2. Export production Search Console page-indexing, crawl, Google-selected canonical, clicks, and
   impressions for those exact URL groups. Do not submit or inspect the development hostname.
3. Aggregate organic landing sessions by the same key and weekday. Report clean/slash aliases,
   `.html` finals, and their combined group so a redirect-induced spelling change is not mistaken
   for lost traffic.
4. Derive Googlebot HTTP aggregates only from provider-validated crawler traffic. Use the provider's
   published crawler verification method and discard raw addresses after aggregation; a
   `User-Agent` string alone is not crawler identity.
5. Probe every final for `200`, self-canonical, matching Open Graph URL, JSON-LD URL, and sitemap
   membership. Probe every alias for one query-preserving `301` to its recorded final. Verify the
   production robots and sitemap contracts separately.

## Monitoring windows

The owner records results every 15 minutes for the first two hours, hourly through 72 hours, daily
from complete UTC day 4 through day 14, and weekly from day 15 through day 28. Search Console data is
lagged; HTTP and contract failures use their immediate windows, while indexing and organic
thresholds use only complete days.

Stop or roll back when any exact threshold in the policy is met:

- one failed route, robots, sitemap, or canonical contract in a 15-minute exhaustive probe;
- editorial `5xx` at or above 1.0% over 15 minutes with at least 100 requests;
- provider-validated Googlebot alias responses that are not direct `301`s at or above 1.0% over 60
  minutes with at least 20 alias requests;
- organic landing sessions at or below 70% of the weekday baseline for two consecutive complete days,
  eligible after complete day 7;
- indexed equivalents at or below 85% of baseline for three consecutive complete days, with at least
  40 URLs lost, eligible after complete day 14; or
- at least 80 wrong Google-selected canonicals for two consecutive complete days, eligible after
  complete day 7.

In the policy, `stop_or_rollback` means stop before the canonical traffic switch and roll back
immediately at or after it. `rollback` means immediate rollback at or after the switch.

The threshold is based on the combined old/new URL group. A documented excluded baseline event must
be recorded before recalculation; it cannot be declared after a threshold is breached merely to
avoid rollback.

## Rollback and recovery

1. Freeze unrelated application and editorial releases. Record the first breached threshold using
   aggregate redacted evidence.
2. Keep all 1,590 permanent aliases active as direct redirects. Never send them to the home page or
   introduce a chain.
3. Restore the last accepted immutable application image while retaining compatible Django dynamic
   endpoints and forward-only data, as required by specification 09.
4. Re-run all 796 final and 1,590 alias probes plus robots, sitemap, canonical, Open Graph, and JSON-LD
   checks before calling the rollback stable.
5. Verify the breached 15-minute or 60-minute HTTP window twice. Continue through the next complete
   UTC day for delayed Search Console and organic recovery.
6. The SEO cutover commander records the decision, the website on-call engineer records execution,
   and the product manager accepts recovery before release work resumes.

Retain the legacy static build and route manifest for at least 28 days. Keep the permanent redirects
indefinitely; ending one requires a separately groomed, measured, and accepted product change.
