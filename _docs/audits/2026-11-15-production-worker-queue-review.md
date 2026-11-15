# Production worker and Relay queue review — 2026-11-15

Scope: migration status report §3 row 12 and §5 decision 6; Hub intake
`2b8e57dd-4c74-4a11-831f-125d3fdfbc2f`, parent `bf3a67fb`.

## Decision

At 13:54–13:55 UTC, the website had **zero nonterminal job intents and zero
pending email deliveries**. Relay's exact `dtc-website` client had **zero
nonterminal jobs, zero enabled schedules, zero queued/sending transactional
messages, and zero pending recipient imports**. There is no backlog to purge
or approve in this snapshot. Do not retry historical dead or sent records.

The audit's statement that production has no worker is stale. The existing
`worker-k7iwuoavkuajhiyhrvpgovr2` on contabo-a has been running since
2026-11-10 19:00:51 UTC and reports healthy. This review did not start, restart,
redeploy, stop, or invoke a handler on it. No email was requested or sent by
this review. The empty queue is a point-in-time finding, not permission to
send email to real people or replay historical deliveries.

## Read-only evidence

Queries ran with `psql -X -v ON_ERROR_STOP=1` inside explicit `BEGIN READ ONLY`
transactions against the primary databases on contabo-b. No payloads,
addresses, tokens, message bodies, or intent identifiers were exported.

Website database, 2026-11-15 13:54:29 UTC:

| Handler | Status | Count | Created range (UTC) |
| --- | --- | ---: | --- |
| `cb_mail.deliver` | dead | 4 | Oct 26–Oct 30 |
| `cb_mail.deliver` | succeeded | 3 | Oct 26–Nov 10 |
| `content.release.invalidate` | succeeded | 1 | Oct 23 |
| `content_sync.course_repository_sync.import_commit` | succeeded | 3 | Oct 26 |
| `courses.registration_contacts` | succeeded | 1 | Nov 10 |
| `events.qna.provision` | succeeded | 448 | Oct 23 |

All 460 intents are terminal: 456 succeeded, 4 dead. Pending, submitted,
running, and failed each have count zero, including future work. Therefore
there is no pending-intent age to report. Website `cb_mail_emaildelivery`
contains 2 dead and 5 provider-accepted deliveries; no other states exist.
All seven recipient addresses fail the reserved example/invalid-domain check
and must be treated as potentially real. This check does not prove mailbox
ownership or that accepted mail was delivered.

Relay database, exact client `dtc-website`, 13:55:14 UTC:

| Object | Finding |
| --- | --- |
| `relay_jobs` | 7 `system.echo`, all succeeded, created Oct 2–Oct 13 |
| `relay_schedules` | 0 enabled |
| `transactional_messages` | 5 sent, created Oct 26–Nov 10; no other states |
| `recipient_list_import_jobs` | 0 outside succeeded/failed |

The separate `dtc-courses` and `dtc-newsletter` clients are not website-worker
queues and were not changed. The courses service, its hostname, and redirects
were not changed. DNS and CloudFront were not changed.

## Existing worker configuration and restart conditions

Both website web and worker use the `website` database at the shared primary
and `relay.datatalks.club`. Worker settings are `website.settings.worker`,
`RELAY_SYNC_SCHEDULES=false`, and `RELAY_TRANSACTIONAL_DRY_RUN=0`.
That environment setting permits real delivery, but the operational database
override wins; the effective override was not verified in this review. No
dry-run protection is asserted. Existing queued deliveries are not rechecked
when the policy changes (see the transactional-send policy runbook).

The live worker uses image
`nax9srpks6z7w1jgpebiuwuo:26125bf1271af30c0f8e552dadcfabc0f450d252`,
with restart `unless-stopped`, 1 GiB memory, no CPU quota, and networks
`coolify` and `k7iwuoavkuajhiyhrvpgovr2`. Web uses image revision
`6695fff2f605c126093ae8c3d7153ee9cdbe0848`. Thus the worker exists but does
not match the image/240 MiB/1 CPU target in the production deployment runbook.
These are deployment-owner follow-ups, not changes made during this read-only
queue review. A healthy heartbeat does not establish image parity.

Before any later worker replacement or activation:

1. Recheck **all** claimable states (`pending`, `submitted`, `failed`), running
   leases, future intents, and website mail states; also check Relay's exact
   `dtc-website` client. Do not invoke `jobs_run_due`, `jobs_sweep`, a retry,
   schedule sync, or a send as a queue inspection technique.
2. If any nonterminal mail or recipient-bearing domain job appears, keep a
   proposed worker off. Reconcile its related delivery and Relay state with
   the owner privately; record an explicit per-record discard/retry decision.
   Real-recipient sends remain forbidden under this intake. Do not bulk-update
   statuses or purge rows; preserve terminal history and idempotency records.
3. Have the Coolify deployment owner align the worker with web's immutable
   image and the reviewed singleton, private-network, resource-limit and
   heartbeat contract. Do not create a second production worker while the
   existing resource is running. Review its rollout/rollback configuration.
4. Repeat the aggregate queue review immediately before releasing the process.
   If a no-real-mail rehearsal is needed, suppress new mail in an isolated
   environment and use reserved test recipients; changing the dry-run setting
   alone does not neutralize existing deliveries.

## Repeatable aggregate queries

On the website primary, inside a read-only transaction:

```sql
SELECT handler, status, count(*), min(created_at), max(created_at),
       min(available_at)
FROM cb_jobs_jobintent GROUP BY handler, status ORDER BY handler, status;
SELECT state, count(*), min(created_at), max(created_at)
FROM cb_mail_emaildelivery GROUP BY state;
```

On the Relay primary, filter **exactly** the website client, not a wildcard
that would include other applications:

```sql
SELECT task_type, status, count(*), min(created_at), max(created_at)
FROM relay_jobs
WHERE client_id = (SELECT id FROM clients WHERE slug = 'dtc-website')
GROUP BY task_type, status;
SELECT count(*) FROM relay_schedules
WHERE client_id = (SELECT id FROM clients WHERE slug = 'dtc-website')
  AND enabled;
SELECT status, count(*), min(created_at), max(created_at)
FROM transactional_messages
WHERE client_id = (SELECT id FROM clients WHERE slug = 'dtc-website')
GROUP BY status;
SELECT count(*) FROM recipient_list_import_jobs
WHERE client_id = (SELECT id FROM clients WHERE slug = 'dtc-website')
  AND status NOT IN ('succeeded', 'failed');
```

Live curl verification at 13:54 UTC returned HTTP 200 from
`https://prod.datatalks.club/health/ready`, reporting ready, source SHA
`6695fff2f605c126093ae8c3d7153ee9cdbe0848`, and configuration, database and
migrations all OK. This verifies web availability only.
