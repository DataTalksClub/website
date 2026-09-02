# Production hosting, DNS, and content migration runbook

Status: draft for owner review — D1–D3 resolved by the owner 2026-09-02 (§3.1); remaining
BLOCKED markers reference the still-open decisions in §3.2
Date: 2026-09-02 (updated same day after owner resolutions D1–D3)
Scope: DataTalks.Club domain, hosting, edge, cost, and content-ingestion migration across
`DataTalksClub/website` (this repo) and `DataTalksClub/aws-infra`
Relationship to existing documents: complements — does not replace — the milestone roadmap in
[`_docs/specs/09-migration-rollout-roadmap.md`](../specs/09-migration-rollout-roadmap.md) and the
data-migration checklist in [`_docs/migration-checklist.md`](../migration-checklist.md). Those own
*what* is migrated (data, application behavior, milestone gates); this runbook owns *where the site
is hosted, how DNS moves, what it costs, and the exact operational sequence*. Section 1 audits the
existing documents and records where they are stale or contradictory.

Per `_docs/PROCESS.md`, every implementation step below that changes product code or infrastructure
goes through a groomed GitHub issue; this runbook is the sequence and the specification source for
grooming those issues, not authorization to skip the lifecycle.

## 0. How to read this runbook

- Repo tags: **[aws-infra]** = work in `~/git/aws-infra` (state root named per step);
  **[website]** = work in this repository; **[GoDaddy]** = manual work in the GoDaddy console;
  **[AWS console/CLI]** = operator action with production credentials.
- **BLOCKED(Dn)** — the step cannot be executed until open decision *Dn* (section 3) is settled by
  the owner. A BLOCKED step is a feature of this runbook, not an omission.
- **ONE-WAY** — hard or expensive to reverse; requires explicit go/no-go.
- **WAIT(x)** — hard wait with expected duration; do not busy-poll, use the event where one exists.
- **CREDS** — requires production AWS credentials (`387546586013`) or GoDaddy account access.
- Every phase ends with verification and rollback. A step an operator cannot execute without
  guessing is a defect in this document; file it.
- All prices in section 14 are **estimates** (eu-west-1, on-demand, 730 h/mo). The repository
  contains no billing data. Verify actuals in Cost Explorer before and after each phase.
- **The document has two layers** (owner requirement): the Outline directly below is the whole
  migration as a scannable step list — every entry carries the same identifier as its detail
  section, so `1.5` in the outline is specified fully at §6 step 1.5. Read the outline first;
  drop into a detail section only when executing or reviewing that step.

Live-DNS observations cited below were captured 2026-09-02 with `dig` from this machine and are
snapshots, not contracts; re-verify during Phase 0.

---

## Outline — every step, in order

Markers: **[P]** production AWS credentials (`387546586013`) · **[G]** GoDaddy console access ·
**[OW]** one-way / expensive to reverse · **[W:x]** hard wait · **[B:x]** blocked on x.
Lanes: **MAIN** = apex/DNS critical path; **COURSES**, **EMAIL**, **DEV**, **CLEANUP** run
**in parallel** with MAIN and with each other — only order *within* a lane is strict, plus the
named cross-lane joins.

**Decisions.** Resolved: D1 (two-stage apex swap), D2 (minimal shared dev; sandbox retired),
D3 (two courses Lambdas), D13 (Relay is the sender) — §3.1. Open, each gating the steps that
cite it: D4 cache freshness · D5 media keys · D6 zone root placement · D7 registrar transfer
(deferred) · D8 courses-zone fold-in · D9 RDS Multi-AZ timing · D10 SPF strategy · D11 git
history rewrite · D12 direct-sync source manifest · **D14 SES identity owner (on the EMAIL
critical path)** · D15 Relay sandbox data (clean start recommended) — §3.2.

**Phase 0 — inventories and prerequisites (MAIN; read-only)** — details §5

- 0.1 **[G]** Export the complete GoDaddy zone, every record incl. `_domainkey` sweep
- 0.2 **[G]** Record the `www`/`join` forwarding configurations
- 0.3 **[P]** Pull the 3-month Cost Explorer baseline
- 0.4 **[P]** Identify + record the courses hosted zone (`Z00653771…`)
- 0.5 Owner settles open decisions D4–D10
- 0.6 **[P]** Verify SES production access (= E.1; multi-day external lead if missing)
- 0.7 Re-verify compatibility artifacts (`make compatibility-artifacts-check`)

**Phase 1 — DNS hosting moves to Route 53 (MAIN)** — details §6

- 1.1 **[P]** **[B:D6,D10]** Create `main/dns` zone; byte-copy every record from 0.1
- 1.2 **[P]** `join` replacement: API GW custom domain (+ ACM CNAME at GoDaddy now)
- 1.3 `www` strategy — build the Phase 2 distribution first (recommended)
- 1.4 Pre-delegation gate: `dns-parity-check` green; GoDaddy TTLs lowered **[W:48h]**
- 1.5 **[G]** **[OW-ish]** NS cutover at the registrar; record freeze; mail checks at +1 h/+24 h **[W:48h]**
- 1.6 Update stale DNS claims in aws-infra docs

**Phase 2 — apex → S3/CloudFront, stage 1 of the D1 swap (MAIN; needs Phase 1)** — details §7

- 2.1 Rebuild the four pinned legacy trees (deterministic, digest-verified)
- 2.2 **[P]** `main/legacy-site`: bucket + OAC + resolver function + distribution (incl. `www`)
- 2.3 **[P]** Upload the trees (`legacy-static-sync`; second run must report 0 uploads)
- 2.4 **[B:2.3]** Staging parity crawl — zero regressions on all 2,937 rows
- 2.5 **[P]** Apex alias swap GitHub Pages → CloudFront (rollback: alias revert, ≤10 min)
- 2.6 Keep GitHub Pages live + frozen ≥30 days

**Phase 3 — media → S3 (MAIN-independent; #301 / aws-infra PR #30)** — details §8

- 3.1 **[P]** Land PR #30; apply `main/dtc-website` (bucket + upload role only)
- 3.2 Groom + implement #301 (projection integrity without the git-tracked tree)
- 3.2a Resolve the confirmed orphan media file (1,254 files vs 1,253 records)
- 3.3 **[P]** `media-sync` initial upload (checksum-driven, idempotent, resumable)
- 3.4 **[P]** `media-verify`: 1,253/1,253 digest matches + two-way set reconciliation
- 3.5 Wire the `/images/*` behavior once the Phase 4 distribution exists

**Phase 4 — production stack, `prod.`/`dev.`, stage-2 swap (MAIN + DEV)** — details §9

- 9.1 **[P]** **[B:D9]** Production website root (module instantiation; policy-suite churn budgeted)
- 9.2 **[P]** Minimal `dev.datatalks.club` on shared prod infra (≈ $10/mo; tradeoffs accepted, D2)
- 9.3 Staging discipline while both serve: 4 duplicate-content controls, `prod.` noindexed
- 9.4 **[P]** **[OW-SEO]** Stage-2 apex swap after the 4-item gate (parity on `prod.` vs live apex,
  M8 steps 1–4, caching evidence, rehearsed rollback); alias rollback 1–10 min; S3 legacy warm ≥30 d

**Phase 5 — anonymous edge caching (MAIN; must be proven before 9.4)** — details §10

- 10.i [website] Route-cache registry + class headers + no-Set-Cookie/CSRF tests
- 10.ii **[P]** [aws-infra] Per-class edge policies + anonymous classifier + response guard
- 10.iii **[B:D4]** Reviewed narrow invalidation grant (worker role) — after or with direct-sync work

**Phase 6 — `courses.` two Lambdas (COURSES lane; fully parallel — zone already on Route 53)** — details §11

- 6a.1 Fix maintenance Lambda 200 → 503 + `Retry-After`; track the root, add backend
- 6a.2 **[P]** Deploy dark + cert; rehearse the alias swap on `dev.courses.datatalks.club`
- 6a.3 **[P]** Activate during the M8 freeze (rollback: alias revert, rehearsed)
- 6A′ **[P]** Compatibility views on the new stack — APIs never cross-host redirect
- 6c.1 **[P]** `main/courses-redirect`: generated path map, 301 GET/HEAD, gated 308
- 6c.2 **[P]** **[B:consumer gate]** Redirect Lambda live; CMP infra retained through its window

**Phase E — email via Relay (EMAIL lane; parallel; does not wait for DNS)** — details §11E

- E.1 **[P]** = step 0.6 (SES production access)
- E.3 **[P]** **[B:D14 hand-off]** `main/relay` root; bind to the `datatalks.club` identity + a
  Relay-owned configuration set; deploy dark
- E.4 Clean-start the data volume (D15); sandbox Relay stays as dev Relay
- E.5 **[B:audit]** GATE — commissioned code audit + observability must-fix list closed
- E.6 **[B:E.5]** Battle-testing ramp, stages 0–4; abort trigger 3% bounce / 0.08% complaint
- E.7 Datamailer stays read-only; fixes the 7.4 teardown order

**Phase 7 — decommission and harvest (CLEANUP; after all rollback windows)** — details §12

- 7.0 **[P]** SES identity `state mv` out of `main/cmp` (**execute early, with E.3**)
- 7.1 **[P]** **[OW]** Destroy `main/cmp` (final Aurora snapshot is the point of no return)
- 7.2 **[OW]** Retire the four GitHub Pages deployments
- 7.3 Retire `main/legacy-site` after the 9.4 window
- 7.4 Sandbox preconditions; teardown order: sandbox Relay → sandbox Datamailer → `sandbox/website`
- 7.5 **[P]** Remove the cross-account media-bucket read (PR #30 grant)
- 7.6 **[P]** **[OW]** Destroy `sandbox/website`; retire its OIDC pipeline + policy fixtures
- 7.7 **[P]** Cost-Explorer harvest vs §14 (expected ≈ $270–340/mo freed)

Reference layers (not steps): §13 content-ingestion programme (parallel, own gates) · §14 cost
and security analysis · §15 script specifications · §16 risk register · §17 non-goals ·
Appendix A DNS worksheet.

---

## 1. What already exists, and how sound it is

The owner suspected "we already have something somewhere". Correct — but nothing existing covers
the DNS/registrar move, the GitHub Pages → S3 rehost, or cost. Inventory of what exists and its
condition:

### 1.1 Document inventory

| Document | Covers | Condition |
| --- | --- | --- |
| `_docs/specs/09-migration-rollout-roadmap.md` | Milestones 0–8, cutover/rollback discipline, redirect-Lambda timing (line 116), DNS/edge cutover as milestone-8 step 5 | Sound as a milestone plan. Assumes DNS control already exists; has **no** GoDaddy→Route 53 procedure, no legacy-site rehost, no cost model |
| `_docs/specs/02-url-link-seo-compatibility.md` | URL preservation contract, route-cache classes (lines 188–211), redirect-Lambda behavior (152–154), "DNS cutover is not approved until the complete SEO parity report passes" (line 304) | Sound and current; this runbook treats it as binding |
| `_docs/specs/08-aws-development-terraform.md` | Development stack, cache/WAF/invalidation design, production promotion (line 279), redirect-Lambda stack (293–305) | Mostly sound; three defects noted in 1.2 |
| `_docs/migration-checklist.md` | Data migration per source (CMP DB, editorial, accounts, events, email) | Sound, current (Luma/Eventbrite evidence updated 2026-08-31). Data only — no hosting/DNS |
| `_docs/compatibility/README.md` | Pinned sources, 2,937-row baseline, crawler/compare harness, `/slack.html` exception | Sound and precise; the harness is reused by this runbook (section 15) |
| `_docs/audits/2026-08-14-github-editorial-source-projection-inventory.md` + `scripts/validate_github_editorial_source_projection_inventory.py` | Editorial source/projection evidence | **Historical snapshot only** — see 1.2 item 1 |
| `aws-infra/docs/inventory.md` | Account/root inventory | Dated 2026-06-24 (line 3); predates `sandbox/website`, `main/dtc-website`, `main/maintenance-page`; one DNS claim is imprecise (1.2 item 4) |
| `aws-infra/docs/state-boundaries.md` | State-root layout rules | Sound; used for the zone-placement decision D6 |
| `aws-infra/main/common/MIGRATION.md` | Completed 2026-06-24 shared-network adoption | Done; historical record |
| GitHub issues #38/#272/#273/#276/#278 (direct-sync programme), #291–#294/#290 (adapters), #301 (media), aws-infra PR #30 | Content-ingestion future, media move | See sections 8 and 13; the programme epics are explicitly BLOCKED by their own PM notes |

Conclusion: **extend, don't replace.** No existing document is "the plan" for hosting/DNS/cost;
this runbook is the missing piece and defers to spec 02/09 wherever they already rule.

### 1.2 Soundness findings (verify-don't-trust results)

1. **Stale audit evidence, frozen deliberately but easy to misread.**
   `scripts/validate_github_editorial_source_projection_inventory.py:84-107` pins
   `media.json = 6b6670d0…` and `podcasts: 205`; the live projection manifest has
   `media.json = 199c7860…` and `podcasts: 203` (`content/public_projection/manifest.json`,
   `artifacts`/`counts`). This is *intentional* — the validator checks the audit document against
   frozen snapshot `539bd8c6…` (script line 26) — but anyone treating the 2026-08-14 audit as a
   current inventory will act on wrong counts. Treat that audit as historical only.
2. **Sibling-repo doc contradicts its own Terraform.** AISL's
   `~/git/ai-shipping-labs/_docs/integrations/s3_content.md` describes the content bucket as
   "public-read by design"; the actual Terraform blocks all public access and uses OAC
   (`aws-infra/main/aisl/content_cdn.tf:24-42`). Warning for this migration: docs drift from
   infra; every claim in this runbook cites the `.tf` line, not a description of it.
3. **Spec 08 internal drift.** (a) `_docs/specs/08-aws-development-terraform.md:279` names the
   production root `main/website`, but the root actually being created is `main/dtc-website`
   (aws-infra PR #30) — reconcile the spec or the root name before Phase 4. (b) Spec 08:106 still
   says a hub cache key "may include only its canonical positive `page`", while spec 02:193 (post
   `643ea32`/#196) allows only the Podcast `season` selector and drops `page`. Spec 02 wins; fix
   spec 08 when the caching issue is groomed.
4. **DNS claims in aws-infra are half-right.** `docs/inventory.md:20` and
   `main/slack-redirect/README.md:8-9` say `datatalks.club` DNS "lives at GoDaddy, not Route 53".
   True for the **apex zone** (live NS: `ns31/ns32.domaincontrol.com`), but
   `courses.datatalks.club` is a **delegated Route 53 subdomain zone** — GoDaddy holds NS records
   delegating it to four `awsdns` servers (verified by `dig NS courses.datatalks.club`), and
   `main/cmp/app_prod.tf:163-173` / `app_dev.tf:157-167` write alias records into hosted zone
   `Z00653771YEUL1BFHEDFR`. That zone is referenced by hard-coded ID and is **not itself managed
   in any aws-infra root** (unowned resource; see Phase 0 step 0.4). This materially improves the
   plan: the `courses.` Lambda swap (Phase 6) is a Route 53 change we already control, independent
   of the GoDaddy move.
5. **CMP database config is internally confused (cost-relevant).** `main/cmp/db.tf:16-19` sets a
   Serverless v2 scaling block, but the single cluster instance is provisioned `db.t3.medium`
   (`db.tf:42-48`), so the serverless configuration is inert and the cluster bills as a fixed
   instance. Not a defect to fix in this runbook, but the cost baseline in section 14 uses the
   provisioned reality, and any "CMP already uses serverless" assumption is false.
6. **Maintenance page returns HTTP 200.** `main/maintenance-page/lambda/index.js:434-440` serves
   the maintenance HTML with status 200. Used as a takeover for `courses.datatalks.club`, that
   would tell crawlers and API clients the outage content *is* the resource. Phase 6 requires a
   503 + `Retry-After` change first.
7. **Gap: nothing anywhere covers** GoDaddy record inventory/export, registrar-move sequencing,
   www/join forwarding replacement, GitHub Pages project-site mounts (`/docs`, `/faq`,
   `/podwiki` are *separate repos* served under the apex by GitHub — an apex rehost must carry
   all four trees or it 404s three sections), or a cost target. This runbook adds all of these.
8. **`dev.datatalks.club` / `prod.datatalks.club` appear nowhere in `_docs/`.** The owner's
   naming is new. The apparent conflict with spec 02's assumption that the production canonical
   origin is `https://datatalks.club` (spec 02:277) was resolved by the owner on 2026-09-02
   (§3.1 D1): `prod.` is staging only and the apex swaps to the Django site — consistent with
   spec 02/09. Spec 08/09 should still gain a sentence naming the `prod.` staging hostname when
   next amended.

---

## 2. Target-state architecture

End state after all phases (hostnames verified live 2026-09-02 unless marked *new*):

| Hostname | Today | Target (end state) | Owned by |
| --- | --- | --- | --- |
| `datatalks.club` | GitHub Pages (A → 185.199.108–111.153), four merged repo trees | **Two-stage swap (D1):** stage 1 = CloudFront + S3, same static content, new hosting only; stage 2 = the Django website edge (milestone-8 cutover, spec 09 §M8.5, mechanics in §9.4) | [aws-infra] `main/legacy-site` then `main/dtc-website` |
| `www.datatalks.club` | GoDaddy forwarder, `301 → https://datatalks.club/` | Same 301, served from AWS (CloudFront alias on the apex distribution) | [aws-infra] `main/legacy-site` |
| `courses.datatalks.club` | CMP prod ECS via shared ALB (`main/cmp/app_prod.tf:163-173`) | During cutover: maintenance Lambda. After consumer gate: redirect Lambda (spec 08:293-305) | [aws-infra] `main/cmp` → `main/maintenance-page` → `main/courses-redirect` |
| `dev.courses.datatalks.club` | CMP dev ECS (`app_dev.tf:157-167`) | Retired with CMP | [aws-infra] `main/cmp` |
| `join.datatalks.club` | GoDaddy forwarder `301 →` execute-api → Slack invite (`main/slack-redirect/README.md:11-19`) | Same behavior, hop served by API Gateway custom domain (GoDaddy forwarding dies with the NS move) | [aws-infra] `main/slack-redirect` |
| `dev.datatalks.club` *new* | — | Minimal dev service **sharing the production stack's ALB/VPC/RDS via host routing** (D2, AISL/CMP pattern — §9.2); replaces `web.dtcdev.click` | [aws-infra] production website root |
| `prod.datatalks.club` *new* | — | Django **staging** pre-apex-swap, `noindex` throughout; after the stage-2 swap it 301s to the apex and is retired (D1, §9.4) | [aws-infra] `main/dtc-website` |
| `mail.datatalks.club` | MX → `feedback-smtp.eu-west-1.amazonses.com` (SES MAIL FROM, `main/cmp/iam_ses.tf:67-71`) | Unchanged, record carried into Route 53; all Relay sending aligns through it (§11E E.0) | [aws-infra] `main/dns` |
| `relay.datatalks.club` *new* | — (sandbox Relay lives at `relay.dtcdev.click`) | Production Relay host (Phase E; `main/relay`, single SSM-managed EC2) | [aws-infra] `main/relay` |
| `web.dtcdev.click` | Sandbox Django stack (`sandbox/website`, zone `Z05963572WVWFHDQZH5NE`) | **Retired after migration** — owner: "sandbox infra will be gone once we migrate" (Phase 7 steps 7.5–7.7); dev moves to `dev.datatalks.club` first | [aws-infra] `sandbox/website` (destroyed) |
| MX/TXT/SPF/DMARC on apex | Google Workspace MX; SPF `include:dc-aa8e722993._spfm.datatalks.club` (which resolves to `include:_spf.google.com`); DMARC `p=none`; 2× `google-site-verification` | Byte-equivalent in Route 53 | [aws-infra] `main/dns` |

```
                       .club registry (NS TTL ~86400)
                              │
              NS: GoDaddy ──► NS: Route 53 zone `datatalks.club`   [Phase 1, main/dns]
                              │
        ┌──────────────┬──────┴────────┬──────────────┬───────────────┐
        ▼              ▼               ▼              ▼               ▼
   apex A/AAAA     www alias      join alias     courses NS      MX/TXT/SPF/
   alias ─► CF     ─► same CF     ─► APIGW       (delegation     DKIM/DMARC
   distribution    (301 to apex)  custom domain  kept, zone      (copied byte-
        │                         (slack-        Z00653771…)     equivalent)
        ▼                         redirect)          │
   [Phase 2] S3 + OAC +                              ▼
   CloudFront Function                    alias: CMP ALB ─► maintenance
   (4 pinned trees,                       Lambda ─► website edge ─►
   2,937 preserved URLs)                  redirect Lambda   [Phase 6]
        │
        ▼ [stage-2 apex swap, §9.4: gate = 2,937-row parity green on prod. vs live apex]
   Django website edge (CloudFront ─► ALB ─► ECS, main/dtc-website)
   (dev.datatalks.club rides the same ALB via host rule; S3 legacy
    distribution stays warm ≥30 days as the instant rollback target)
```

State-root ownership when the dust settles:

| Root (account `387546586013` unless noted) | Owns | Status |
| --- | --- | --- |
| `main/dns` *new* | Route 53 apex zone, mail/verification records, `courses` NS delegation records | Phase 1 |
| `main/legacy-site` *new* | Legacy static bucket, CloudFront distribution + function, ACM certs, apex/www aliases | Phase 2 |
| `main/dtc-website` (PR #30, currently untracked) | Media bucket now; full production website stack later (module `django-website`) | Phase 3–4 |
| `main/maintenance-page` (untracked) | Activatable maintenance Lambda | Phase 6a |
| `main/courses-redirect` *new* | Redirect Lambda + API Gateway + alias (spec 08:293-305) | Phase 6c |
| `main/slack-redirect` | join Lambda + API custom domain | Phase 1 |
| `main/cmp` | CMP until decommission | Phase 7 retires |
| `sandbox/website` (account `817685572750`) | Development Django stack (`web.dtcdev.click`) during the migration only | Destroyed in Phase 7 (steps 7.5–7.7) |

---

## 3. Decisions

### 3.1 Resolved by the owner (2026-09-02)

- **D1 — RESOLVED: `prod.datatalks.club` is staging, then the apex swaps to it.** Owner: "prod
  is staging before migration then it will swap to apex. First apex will serve the current
  datatalks.club static website via S3 and then we will swap them." So the apex is a
  **two-stage swap**: stage 1 (Phase 2) moves the *current static site* from GitHub Pages to
  S3/CloudFront — identical content, hosting change only; stage 2 (§9.4, at milestone 8) points
  the apex at the Django site once the full 2,937-row parity gate is green on `prod.` against the
  live apex. `prod.` is `noindex` throughout its life and 301s to the apex after the swap. This
  matches spec 02:277/02:304 and spec 09 M8.5; no spec amendment needed beyond naming the staging
  hostname.
- **D2 — RESOLVED: dev is minimal and shares production infrastructure.** Owner: "dev setup will
  follow the same idea as in ai shipping labs or cmp. I don't want to pay 150 per month so it
  will be minimal", and "sandbox infra will be gone once we migrate." The AISL/CMP pattern is
  verified in-repo and is identical in both: **one shared ALB with a host-based listener rule
  for the dev hostname** (`main/aisl/ecs.tf:193,273-289`; `main/cmp/alb_shared.tf:36-51`), one
  shared VPC and ECS cluster (`main/aisl/ecs.tf:54`; `main/cmp/app.tf:70`), and **one shared
  database instance with separate logical databases** — AISL: one `db.t4g.micro`, two database
  URLs (`main/aisl/db.tf:22-29,67-87`); CMP: one Aurora cluster, `…/dev` and `…/prod` databases
  on the same endpoint (`main/cmp/secrets.tf:25-42`). Dev adds only a task definition, a 1-task
  service, a target group, a listener rule, a log group, and a DNS record. Design, cost
  (≈ **$8–12/mo marginal**), and fidelity tradeoffs: §9.2. Consequence: `sandbox/website` is
  decommissioned after the migration (Phase 7 steps 7.5–7.7) and the second-stack option is
  rejected.
- **D3 — RESOLVED: two Lambdas with distinct purposes.** Owner: "there will be two lambdas
  1) maintenance while migrating 2) redirect after migrating." Lambda 1 = the maintenance page
  (`main/maintenance-page`, untracked) active only during the `courses.` migration window —
  **after** fixing its 200-instead-of-503 response (finding 1.2-6; Phase 6a.1). Lambda 2 = the
  permanent redirect Lambda (spec 08:293-305), replacing Lambda 1 after migration, with compat
  views on the new stack in between (Stage A′) so API consumers never cross-host-redirect.
  Both operate via alias swaps in the `courses.datatalks.club` Route 53 zone, whose delegation
  to AWS is verified live (`dig NS courses.datatalks.club` → `awsdns-*`) — **this lane is
  therefore fully independent of the apex/GoDaddy migration and proceeds in parallel** (Phase 6).
- **D13 — RESOLVED by alignment: Relay is the sender.** The owner's later same-day direction —
  "Relay will need to go to main account too. Let's actually use it and just start
  battle-testing it… Adjust the migration plan — let's go with Relay" — supersedes the earlier
  Datamailer-first instruction entirely. Because the standing specs already mandate Relay-only
  sending (`app-boundaries.md:66`; spec 09:143), **no spec amendment is needed and no interim
  exception exists**; Datamailer stays read-only migration/history/reconciliation input. The
  owner attached two conditions — a code audit and sufficient operational visibility — which
  gate production sending (§11E E.5, BLOCKED pending commissioned audit results).

### 3.2 Still open — each blocks the steps that reference it

- **D4 — Edge-cache freshness mechanism** (the owner wants "cache updated on updates" while
  `cloudfront:CreateInvalidation` is asserted-rejected in the policy suite). Options in
  section 10.4. **Recommendation: bounded TTLs first (no invalidation dependency), then a
  separately reviewed narrow invalidation grant to the worker role** per spec 08:126-138.
- **D5 — Media S3 key scheme (#301).** Recommendation: object key = public path minus leading
  slash (`images/authors/ aashishnair.jpg`), preserving the two literal-space keys byte-exact, so
  CloudFront path→key mapping is identity and no rewrite layer is needed. Note the on-disk
  projection tree drops the `images/` prefix (`content/public_projection/media/authors/...`), so
  the uploader maps `record_key` → key, not directory walk → key.
- **D6 — Zone placement.** `docs/state-boundaries.md:19-24` assigns DNS zones to the account
  `common` root; `main/common` currently owns VPC/bastion/OIDC. **Recommendation: a dedicated
  `main/dns` root** — registrar-facing NS-critical records deserve their own blast radius; note
  the deviation from state-boundaries.md explicitly in that root's README.
- **D7 — Registrar transfer.** Moving DNS *hosting* to Route 53 (NS change at GoDaddy) is Phase 1
  and low-risk. Transferring the *registration* to Route 53 Domains is optional, adds transfer-lock
  timing (60-day rules), and changes nothing operational. **Recommendation: defer** until the NS
  move has been stable ≥30 days; verify `.club` transfer support at that time (unverified here).
- **D8 — Fold `courses.datatalks.club` zone into the new apex zone, or keep the delegation?**
  Keeping the delegation means copying four NS records into the new apex zone and touching nothing
  else; folding means migrating the records and updating `main/cmp` and Phase 6 stacks.
  **Recommendation: keep the delegation through the migration; fold later** as cleanup.
- **D9 — RDS Multi-AZ at launch?** +100% instance cost (§14). Recommendation: launch single-AZ
  with 7-day backups + final-snapshot + deletion protection; enable Multi-AZ when course
  operations move (milestone 4+), because that is when hours-long RTO stops being acceptable.
- **D10 — SPF strategy at DNS move.** The live SPF chains through a GoDaddy-managed macro record
  (`dc-aa8e722993._spfm.datatalks.club TXT "v=spf1 include:_spf.google.com ~all"`).
  Recommendation: replace with a direct `v=spf1 include:_spf.google.com include:amazonses.com ~all`
  on the apex in Route 53 — *after* Phase 0 inventory confirms which services actually send as
  `@datatalks.club` (SES mail-from exists: `iam_ses.tf:67-71`; whether DKIM for Google is
  configured is **unknown** — no TXT found at `google._domainkey` — inventory must settle it).
- **D11 — Git history rewrite for the 303 MB `.git`** after #301 removes media. Out of scope here
  (#301 lists it as a separate owner decision); listed so it is not lost.
- **D12 — Direct-sync source rollout manifest.** #273's PM note states the "owner-approved
  exhaustive source rollout manifest does not exist". Every direct-sync cutover (section 13.3)
  is blocked behind it. This runbook does not invent one.
- **D14 — Terraform ownership of the `datatalks.club` SES identity. NOW ON THE RELAY CRITICAL
  PATH.** Today `main/cmp` (`iam_ses.tf:54-71`) owns the shared sending identity, and CMP is
  scheduled for destruction (7.1); `main/relay` must bind to that identity to send at all
  (§11E E.3), so the hand-off is a prerequisite for production email, not just for teardown
  (risk #2). Options: move to `main/common` (matches `docs/state-boundaries.md:19-24`, shared
  resources) or to `main/relay` (the sole sender). **Recommendation: `main/common`** — the
  identity outlives any one sender. Executed as step 7.0's procedure, but scheduled before or
  alongside E.3, long before Phase 7.
- **D15 — Relay sandbox data disposition at the sandbox→main promotion.** The sandbox EBS data
  volume holds Relay's PostgreSQL (templates, delivery/event records — all test-era,
  `sandbox/relay/README.md:13-15`). **Recommendation: clean start** (§11E E.4) — re-publish
  production templates through Relay's own draft/publish flow; migrate nothing. Owner confirms
  or names data worth carrying.

---

## 4. Phase plan and ordering constraints

The owner's original order was: (1) S3 rehost → (2) dev/prod subdomains → (3) courses Lambda →
(4) redirect Lambda → (5) DNS to Route 53. **One correction is load-bearing:**

> **DNS must move first, not last.** The apex cannot alias to CloudFront from GoDaddy: GitHub
> Pages works there only because GitHub publishes four stable anycast A records; CloudFront has no
> stable IPs, and an apex `CNAME` is impossible in the DNS protocol. Route 53 alias records are
> the standard mechanism. Rehosting the apex on S3/CloudFront (owner item 1) therefore *requires*
> Route 53 hosting (owner item 5) to be in place already.

Also: the two `courses.` Lambda stages need no GoDaddy work at all (the subdomain zone is already
Route 53, finding 1.2-4), so they sequence with the CMP milestones, not with the DNS move.

```
Phase 0  Inventories, decisions, cost baseline           (no infra change)
Phase 1  Route 53 hosting move: zone, parity, NS cutover  ── must precede 2
Phase 2  GitHub Pages → S3/CloudFront apex rehost         ── needs 1
Phase 3  Media → S3 (#301 / aws-infra PR #30)             ── independent of 1–2; before 4
Phase 4  prod. staging + minimal dev + production root +   ── needs 1; stage-2 swap needs
         the stage-2 apex swap (§9.4)                          milestone-8 gates + Phase 5
Phase 5  Anonymous edge caching                            ── needs 4 (deploys with the stack);
                                                             must be proven BEFORE milestone-8 cutover
Phase 6  courses.: maintenance Lambda → website edge →     ── a) anytime after rehearsal;
         redirect Lambda                                       b/c) gated by spec 09 M4–M8
Phase E  Email: Relay sandbox → main (eu-west-1), audited  ── parallel lane; E.1 SES check in
         and battle-tested; Datamailer stays read-only         Phase 0; E.5 audit gate BLOCKED
Phase 7  Decommission CMP + GitHub Pages + legacy S3 (after   ── after rollback windows close;
         its window) + sandbox Relay → sandbox Datamailer →      7.0 SES hand-off precedes 7.1
         sandbox/website (E.7 order)
```

Independent lanes: Phases 1–2 (edge/DNS), Phase 3 (media), Phase E (email — it does *not* wait
for the DNS move, §11E E.0), the courses lane (Phase 6, already-delegated Route 53 zone), and
the content-ingestion programme (section 13) can proceed in parallel; the hard joins are at
Phase 4 (media bucket feeds the website distribution), the D14 SES identity hand-off (before
Relay production sending *and* before CMP teardown — step 7.0 executed early), and milestone 8
(everything).

---

## 5. Phase 0 — Inventories and prerequisites

**Goal:** every record, cost, and decision input captured; nothing mutated.

**0.1 [GoDaddy] [CREDS] Export the complete DNS zone.** In the GoDaddy DNS console for
`datatalks.club`, export the zone file (or capture every record via the GoDaddy API, script
`dns-export`, section 15.1). Capture *all* records with TTLs, including: apex A (4× GitHub Pages,
TTL 600 observed), `www` (forwarding), `join` (forwarding), `courses`/NS (4× awsdns), MX (5×
Google), TXT (SPF, 2× google-site-verification), `_dmarc`, `dc-aa8e722993._spfm`, `mail` MX
(SES), and — critically — **any DKIM CNAMEs/TXT** (`*._domainkey.*`) and any records this
runbook's remote probing could not enumerate (wildcards, service-verification records, forwarding
config). Store the export under `.local/migration-data/dns/` (gitignored path convention,
`_docs/migration-checklist.md:87-91`) — treat as sensitive-ish operational data, don't commit.
*Verify:* the export reproduces every record listed in section 2's table. *Duration:* 30 min.
*Rollback:* n/a (read-only).

**0.2 [GoDaddy] Inventory the two forwarders.** In GoDaddy's Forwarding settings, record the exact
config for `www` (observed: `301 → https://datatalks.club/`; the forwarder answers `405` to
`HEAD`) and `join` (observed: `301 → https://tpnjn3u8kj.execute-api.eu-west-1.amazonaws.com/`).
These **stop working the moment NS moves** — replacements are built in Phase 1. *Duration:* 10 min.

**0.3 [AWS console/CLI] [CREDS] Cost baseline.** Cost Explorer, main account, last 3 full months,
grouped by service and by tag where present. Record the CMP-attributable total next to section
14.1's estimates. Also confirm the July 2026 NAT anomaly is resolved (context:
`main/common/vpc_endpoints.tf:1-14`, ~$173 of NAT data processing from Fargate image pulls).
*Duration:* 30 min.

**0.4 [AWS console/CLI] [CREDS] Identify and adopt the courses hosted zone.**
`aws route53 get-hosted-zone --id Z00653771YEUL1BFHEDFR` — confirm the zone name (expected
`courses.datatalks.club.`; this runbook *infers* it from the live NS delegation and
`main/cmp/app_prod.tf:164` and has not confirmed the name). Record its record set
(`list-resource-record-sets`). This zone is currently referenced by hard-coded ID from `main/cmp`
but managed by no root; decide (with D6/D8) whether to `import` it into `main/dns` for
drift-protection. *Verify:* zone NS match the live delegation (`ns-2001.awsdns-58.co.uk` etc.).
*Duration:* 20 min.

**0.5 Owner settles the open decisions D4–D10** (D1–D3 resolved, §3.1). BLOCKED steps downstream
reference them individually.

**0.6 [AWS console/CLI] [CREDS] Verify SES production access in the main account** — run Phase
E's step E.1 now (`aws sesv2 get-account --region eu-west-1`, §11E). It is the only step in this
runbook with potential multi-day *external* lead time (an AWS support request if access is
missing), so it goes ahead of everything that doesn't depend on it.

**0.7 [website] Re-verify the compatibility artifacts are intact** (they gate Phases 2 and 8):

```console
make compatibility-artifacts-check
make compatibility-source-artifacts-check
```

*Expected:* both pass (checked digests per `_docs/compatibility/README.md:92-105`). *Duration:*
minutes.

---

## 6. Phase 1 — DNS hosting moves to Route 53

**Goal:** Route 53 is authoritative for `datatalks.club` with byte-equivalent behavior; GoDaddy
remains registrar (D7 defers transfer). **Email records are the highest-blast-radius item in this
entire runbook** — the phase is structured so that delegation happens only after machine-verified
parity.

**1.1 [aws-infra] Create the `main/dns` root** (BLOCKED(D6) if the owner prefers `main/common`).
New directory `main/dns/` with `versions.tf` (Terraform `>= 1.10`, AWS `~> 6.0`, S3 backend —
match `main/dtc-website/versions.tf`), `backend.hcl.example`
(bucket `dtc-terraform-state-387546586013`, key `main/dns/terraform.tfstate`, region `eu-west-1`,
`use_lockfile`), `main.tf` (provider guarded by `allowed_account_ids = ["387546586013"]`), and
`zone.tf` containing `aws_route53_zone.apex` for `datatalks.club` plus one
`aws_route53_record` per record from the 0.1 export:

- apex `A` 185.199.108.153/109/110/111 TTL 600 (keep GitHub Pages for now — Phase 2 changes this);
- `courses` `NS` → the four awsdns servers from 0.4 (delegation preserved, D8);
- MX (5 Google records), TTL as exported;
- TXT: SPF (per D10 — copy verbatim including the `_spfm` include *and* the
  `dc-aa8e722993._spfm` TXT itself, or the flattened replacement), both
  `google-site-verification` strings, `_dmarc`;
- `mail` MX `10 feedback-smtp.eu-west-1.amazonses.com` **and** `mail` TXT
  `v=spf1 include:amazonses.com ~all` (SES MAIL FROM + its SPF, both verified live —
  `main/cmp/iam_ses.tf:67-71`, §11E E.0; Phase E sending depends on these);
- `mail._domainkey` TXT (SES DKIM key, verified live) plus every other `_domainkey` record found
  in 0.1 (Google's DKIM selector, if configured, was not found by probing);
- **no** `www`/`join` records yet (they come from 1.2/1.3 outputs).

```console
cd ~/git/aws-infra/main/dns
terraform init -backend-config=backend.hcl
terraform plan   # expect: 1 zone + N records to add, 0 destroy
terraform apply  # CREDS
```

*Verify:* `terraform output` the four assigned NS; then for **every** record type:
`dig @<assigned-ns1> datatalks.club MX +norec` etc. matches the GoDaddy answers (script
`dns-parity-check`, section 15.2, exits 0). *Duration:* 2–3 h. *Rollback:* `terraform destroy`
(zone not yet referenced by the registrar — harmless).

**1.2 [aws-infra] Replacement for the `join` forwarder.** Extend `main/slack-redirect` with an
API Gateway v2 custom domain: `aws_acm_certificate` for `join.datatalks.club` (regional,
eu-west-1) with DNS validation records added in `main/dns` (cross-root: export the validation
records as outputs, add them as records in `main/dns`, or let `main/dns` own them via variables —
keep it explicit either way), `aws_apigatewayv2_domain_name`, `aws_apigatewayv2_api_mapping` to
the existing HTTP API `tpnjn3u8kj` (`main/slack-redirect/README.md:28`), and an alias record
`join.datatalks.club` in the new zone. WAIT(ACM validation: minutes once the CNAME is live in the
*answering* zone — note it will not validate until after 1.5 delegation **unless** you also add
the validation CNAME at GoDaddy now; **do both**, it is one extra record and removes a
post-delegation wait). *Verify:* after delegation, `curl -s -o /dev/null -w '%{http_code}
%{redirect_url}' https://join.datatalks.club/` → `301` to the Slack invite (two hops become one:
the old GoDaddy hop disappears). The Lambda's `URL` env var remains the untouched rotating secret
(`main/slack-redirect/README.md:33-38`). *Duration:* 1 h + validation wait. *Rollback:* remove the
alias record; GoDaddy forwarding still exists until 1.5.

**1.3 [aws-infra] Replacement for the `www` forwarder.** Deferred into Phase 2 (the apex
CloudFront distribution serves `www` with a redirect function). Until Phase 2, carry `www` as a
record reproducing GoDaddy's current answer (A 15.197.225.128 / 3.33.251.168) so behavior is
unchanged through the NS move — those GoDaddy forwarder IPs keep serving the 301 as long as the
forwarding config exists in the GoDaddy account. **Hole to acknowledge:** if GoDaddy disables
forwarding for zones it no longer hosts, `www` breaks between 1.5 and Phase 2. Mitigation: execute
Phase 2 step 2.1–2.5 (distribution live, `www` alias ready) *before* 1.5, or accept a bounded
`www` outage (it is a redirect hostname, ~zero SEO surface, but flag it). **Recommendation: build
the Phase 2 distribution first and fold the www alias into 1.1's record set.** *Duration:* folded
into Phase 2.

**1.4 Pre-delegation gate.** All must hold: 1.1 parity script exits 0 (including MX/TXT byte
equality); 1.2 join alias answers correctly when queried against the new NS directly; www strategy
from 1.3 chosen; GoDaddy TTLs already low (observed 600 s on apex A, ~3600 s on MX/www — lower
the ≥3600 ones to 600 in GoDaddy **48 h before** 1.5 so caches converge quickly; note this does
NOT speed up the NS record itself — the `.club` registry delegation TTL is ~86400 and not ours to
lower). *Duration:* gate review 30 min, TTL lead time 48 h.

**1.5 [GoDaddy] [CREDS] [ONE-WAY-ish] NS cutover.** In GoDaddy's domain (registrar) settings,
replace the nameservers with the four Route 53 NS from 1.1. **Do not delete any GoDaddy zone
records or forwarding config** — the intact GoDaddy zone is the rollback. WAIT(propagation: new
resolvers converge immediately; full convergence bounded by the ~86400 s registry NS TTL — treat
**48 h** as the mixed-answer window). During the window both servers answer; because 1.4 proved
byte-parity, mixed answers are equivalent — **freeze all record changes in both zones for the
window.** *Verify:* `dig +trace datatalks.club NS` shows awsdns from the registry;
`dns-parity-check` against public resolvers (1.1.1.1, 8.8.8.8) exits 0; send/receive a test email
to `@datatalks.club` and confirm SPF/DKIM/DMARC pass headers (Gmail "show original") — **within
the first hour and again at 24 h**; `curl` apex, www, join, courses. *Duration:* 15 min action +
48 h observation. *Rollback:* re-enter `ns31/ns32.domaincontrol.com` at GoDaddy; same
convergence bound; the untouched GoDaddy zone resumes. Keep the GoDaddy zone intact ≥30 days.

**1.6 [aws-infra] Post-cutover:** update `main/slack-redirect/README.md` and
`docs/inventory.md` DNS claims (finding 1.2-4). *Duration:* 30 min.

---

## 7. Phase 2 — GitHub Pages → S3/CloudFront apex rehost

**Goal:** `datatalks.club` serves the four pinned legacy trees from S3 behind CloudFront with all
2,937 baseline URLs behaving identically; GitHub Pages remains the instant rollback.

**Context that makes this non-trivial** (the classic silent breakage):

- The apex today merges **four repos** via GitHub Pages project-site mounts:
  `datatalksclub.github.io` at `/`, `docs` at `/docs`, `faq` at `/faq`, `podwiki` at `/podwiki`
  (`_docs/compatibility/README.md:34-40`). An S3 rehost of only the main repo would 404 three
  sections (636 baseline URLs).
- Baseline path shapes (computed from `generated-path-baseline.jsonl`): 400 directory paths ending
  `/` (need `index.html` resolution), 2 extensionless *files* (e.g. `/docs/touch` — so any "no
  dot ⇒ directory" heuristic is wrong), 818 `.html`, the rest assets. GitHub Pages also serves
  `/docs` → `301 /docs/` (verified live) and real 404s with a 404 page.
- **S3 website endpoint vs OAC+CloudFront differ exactly here.** The *website endpoint* resolves
  `/path/` → `/path/index.html` natively and redirects `/path` → `/path/` — but it requires a
  public-read bucket and plain-HTTP origin, violating the security floor (§14.4) and the repo's
  own OAC-only pattern (`main/aisl/content_cdn.tf:24-42`, `main/dtc-website/media.tf:33-40`).
  The *REST endpoint with OAC* is private and encrypted but resolves **nothing**: `/docs/`
  fetches the literal key `docs/` and misses. **Chosen design: OAC + a CloudFront viewer-request
  Function** that (a) rewrites `uri` ending `/` to `uri + "index.html"`, and (b) 301-redirects
  exact no-slash directory paths from a generated list to their slash form. The redirect list is
  generated from the baseline's directory set (the 400 rows), **not** from a dot heuristic,
  precisely because of `/docs/touch`.
- S3 keys are byte-exact and case-sensitive, matching GitHub Pages; the baseline retains path
  case and encoding (`_docs/compatibility/README.md:163-165`), and the parity crawl is the proof.
- 404s: with OAC, a missing key returns **403** unless the distribution's OAC policy also allows
  `s3:ListBucket`; grant it so S3 answers 404, and add a CloudFront custom error response mapping
  404 → `/404.html` with response code 404. **Known fidelity loss:** GitHub serves each project
  site's own 404 page (`/docs/404.html` for docs paths); CloudFront supports one error page per
  distribution. The baseline's 2,937 preserve rows are all 200s so the *contract* is unaffected,
  but record the difference in the Phase 2 parity review (the production capture holds seven 404
  observations, `_docs/compatibility/README.md:96-98`).
- Content-Type: `aws s3 sync` guesses MIME types and serves `binary/octet-stream` on misses
  (breaking e.g. `.rustkyll-manifest.json`, extensionless `touch`, `.scss`). The upload script
  must set explicit types (section 15.3).
- Cache-Control at upload: because `cloudfront:CreateInvalidation` is denied by the policy suite
  (section 10.4), **wrong long TTLs are un-fixable without a guardrail change**. Use
  `public, max-age=3600` for HTML/JSON/XML/txt and `public, max-age=86400` for images/fonts/css/js.
  Never `immutable` on this tree.

**2.1 [website] Rebuild the pinned trees** (deterministic, verified):

```console
uv run python scripts/build_pinned_legacy_sources.py --workspace .tmp/legacy-compatibility-sources
uv run python scripts/build_pinned_legacy_sources.py --check
```

*Expected:* clean verification of all five checkouts and 2,937 generated files at the recorded
digests (`_docs/compatibility/README.md:66-75`). *Duration:* ~15–30 min.

**2.2 [aws-infra] Create `main/legacy-site` root.** Same skeleton as 1.1. Resources: private S3
bucket (posture cloned from `main/dtc-website/media.tf:14-92`: ownership enforced, all public
access blocked, SSE-S3, versioning, lifecycle for noncurrent versions, DenyInsecureTransport),
OAC (`aws_cloudfront_origin_access_control`, pattern `main/aisl/content_cdn.tf:37-42`),
CloudFront function (the resolver from the context note; keep it deterministic, no KV store),
CloudFront distribution with aliases `datatalks.club` + `www.datatalks.club`,
`PriceClass_100`, `http_version = "http2and3"`, default behavior GET/HEAD, compress, TTLs 0-min
capped (respect origin Cache-Control), custom error response 404→`/404.html`; a second tiny
behavior or function branch answering `www.` with `301 https://datatalks.club{uri}`; ACM cert in
**us-east-1** for `datatalks.club` + `www.datatalks.club` (CloudFront requirement), DNS-validated
via records in `main/dns` (WAIT: minutes). Bucket policy: `s3:GetObject` + `s3:ListBucket` for
`cloudfront.amazonaws.com` conditioned on the distribution ARN. *Verify:* `terraform plan` creates
only workload-owned resources; **no** `aws_iam_user`/access keys (guardrail; the AISL
`content_cdn.tf:144-150` IAM user is the historical pattern this repo now forbids —
`tests/policy/README.md:156-160` — do not copy it). *Duration:* half a day. *Rollback:* destroy;
nothing references it yet. *Note:* this root sits outside the website-module policy-scan scope
(scan covers module + sandbox root + production fixture, `tests/policy/README.md:11-13`), so no
48-test/manifest churn — but follow the same standards anyway.

**2.3 [website→S3] [CREDS] Upload the four trees** with the `legacy-static-sync` script
(section 15.3): main tree at key prefix `""`, docs at `docs/`, faq at `faq/`, podwiki at
`podwiki/`, explicit Content-Type and Cache-Control per file, checksum-diff idempotent. Run twice;
second run must report `0 uploaded`. *Duration:* ~250 MB, minutes.

**2.4 Staging verification — the gate that protects the 2,937-row contract.** Point a temporary
hostname (e.g. `legacy-stage.datatalks.club` in `main/dns`, or the raw `*.cloudfront.net` name) at
the distribution and run the existing harness against it (host-override extension, section 15.4):
crawl the committed seed set, compare to `legacy-manifest.jsonl`. *Expected:* zero status
regressions on the 2,937 preserve rows; `/docs` → `301 /docs/`; unknown path → 404 with the 404
page; the two extensionless files and the two spaced media paths (`/images/authors/
aashishnair.jpg`, `/images/podcast/production-ml-search-vector-search-embeddings-hybrid
search.jpg` — encoded `%20` in requests) return 200 with correct Content-Type. Differences beyond
the known 404-page fidelity note are stop-ship. *Duration:* crawl ~1–2 h (100 ms/request pacing,
`_docs/compatibility/README.md:192-197`). *Rollback:* n/a (staging).

**2.5 [aws-infra] Apex swap.** In `main/dns`, replace the four GitHub Pages A records with an
alias to the distribution (plus AAAA alias), and point `www` at the same distribution (retiring
the 1.3 stopgap). TTL was 600, so old answers age out in ≤10 min. *Verify:* `curl -sI
https://datatalks.club/` shows `server: AmazonS3`/`via: cloudfront` and 200; spot-check each
section (`/`, `/docs/`, `/faq/`, `/podwiki/`, one blog post, one image); re-run the staging crawl
against the live host; `www` 301s; Search Console shows no error spike over the following week.
*Duration:* 15 min + 10 min TTL. **Rollback (instant, the whole point of this design):** restore
the four GitHub Pages A records; GitHub Pages is still serving — do not disable the Pages builds
or delete the CNAME files until Phase 7.

**2.6 GitHub Pages disposition.** Leave all four Pages deployments live and frozen for the entire
rollback window (≥30 days) — they cost nothing. Retirement is Phase 7.

---

## 8. Phase 3 — Media to S3 (#301, aws-infra PR #30)

**Goal:** the 150 MB git-tracked media tree (`content/public_projection/media/`, 1,254 files)
serves from the `dtc-website-media` bucket; public URLs unchanged; repo slims down.

This phase is owned by issue **#301** (currently `needs grooming`) and PR
**DataTalksClub/aws-infra#30** (open; creates `main/dtc-website` with the bucket, OAC-ready
policy hooks `media.tf:101-116`, and a branch-scoped OIDC upload role `iam_deploy.tf`). The
runbook adds the operational sequence and the holes found while auditing:

**Holes found (hunt results — feed these into #301's grooming):**

1. **Set reconciliation fails today, both directions checked:** the media *tree* has 1,254 files
   but `media.json` has 1,253 records; the orphan is
   `podcast/s24e06-how-to-build-ai-that-actually-ships-in-production.jpg` (present on disk, no
   record). Either it is dead weight (delete) or a record is missing (projection bug). Resolve
   before the first sync — otherwise the uploader's "records drive uploads" rule silently drops a
   live file, or "tree drives uploads" uploads an unowned object forever.
2. **Prefix mismatch:** record keys/public paths carry `images/…` but the tree stores
   `authors/…` etc. without the prefix. The uploader must map `record_key → key` (D5), never walk
   the directory.
3. **Two literal-space keys** (leading space in ` aashishnair.jpg`!). S3 accepts them; CloudFront
   forwards `%20` and S3 decodes it back to the space, so identity keys resolve correctly — but
   shell-based tooling (`aws s3 sync`, globbing) mangles them easily. Use boto3 driven by
   `media.json`, not `aws s3 sync` (section 15.5). #301 explicitly declares *renaming* them a
   non-goal.
4. **Content-Type:** every record carries `content_type`
   (1,253/1,253: 964 jpeg, 283 png, 5 svg+xml, 1 gif) — the uploader must set it explicitly;
   `aws s3 sync` guessing is forbidden.
5. **Cache-Control is a one-way door** while invalidation is denied (§10.4): set
   `public, max-age=86400` (spec 02:191 "stable release asset" class), not `immutable`/1y.
6. **Integrity chain:** `content/public_data.py` validates `manifest["tree_sha256"]` by hashing
   every file under the projection root (#301 cites `content/public_data.py:625`, `:339-352`).
   Removing media from git without reworking that check bricks startup — this is #301's core
   application change and belongs to its groomed issue, not to an infra step.
7. **Verification is mandatory, not optional:** every record carries `provenance.checksum`
   (sha256). The post-sync verifier (section 15.5) GETs every object and compares digests; the
   phase is not done until it exits 0 with 1,253/1,253 verified and zero unexpected objects.

**3.1 [aws-infra] [CREDS] Land PR #30, then apply `main/dtc-website`** (init with
`backend.hcl` per `backend.hcl.example`; plan shows bucket + policy + upload role only; apply).
*Verify:* `terraform output` bucket/role ARNs; `aws s3api get-public-access-block` shows all four
blocks true. *Rollback:* destroy (bucket empty).

**3.2 [website] Groom and implement #301** (application side: projection integrity without the
tree, media URL emission unchanged, CI). Blocked from ad-hoc execution by PROCESS; the runbook
dependency is only that 3.3–3.5 follow it.

**3.2a [website] Resolve the confirmed orphan before the first sync.** Verified twice (this
audit and the coordinator's independent check):
`content/public_projection/media/podcast/s24e06-how-to-build-ai-that-actually-ships-in-production.jpg`
exists on disk and in git with **no `media.json` record** (1,254 files vs 1,253 records). A
record-driven sync silently drops it; a tree-driven sync uploads an unowned object forever.
Determine which is wrong — a missing projection record (fix the projection, count becomes 1,254)
or dead weight (delete the file, count stays 1,253) — record the decision in #301, and only then
run 3.3. `media-verify`'s bidirectional reconciliation (15.5) enforces that the resolution
actually happened.

**3.3 [website] [CREDS] Initial sync** with `media-sync` (section 15.5) via the OIDC role (or an
operator role for the first manual run): 1,253 uploads keyed per D5. Re-run: `0 uploaded`
(idempotency proof). **Partial-failure behavior:** the script is checksum-diff driven, so a crash
at object 700 resumes by re-running; no state file needed.

**3.4 [website] [CREDS] Verify:** `media-verify` exits 0 (bytes match `provenance.checksum` for
all records; no unexpected keys — the 1.254th file resolved per hole 1).

**3.5 Wire serving.** Until the production website stack exists, Django keeps serving media
(nothing changes publicly). When the Phase 4/5 distribution exists, add
`/images/*` behavior → media bucket via OAC and set the distribution ARN in
`media_reader_cloudfront_distribution_arns` (`main/dtc-website/variables.tf:61-70`) — the bucket
policy is already shaped for it (`media.tf:101-116`). Public URLs never change (the bucket is
"not a public origin by itself", `media.tf:1-12`).

---

## 9. Phase 4 — `prod.` staging, minimal `dev.`, the production stack, and the stage-2 apex swap

D1 and D2 are resolved (§3.1); this phase is no longer decision-blocked. It has four parts:
the production root (9.1), the minimal shared dev (9.2), staging discipline while `prod.` and the
apex coexist (9.3), and the stage-2 swap itself (9.4) — the real cutover moment of the whole plan.

### 9.1 [aws-infra] Production website root

Instantiate `modules/django-website` for the production account — either inside
`main/dtc-website` (PR #30 names it the "permanent home of the production datatalks.club website
stack") or as spec 08:279's `main/website`; reconcile the naming (finding 1.2-3a) in the grooming
issue. Inputs follow `tests/fixtures/website-production/main.tf` *in shape only* — see section
14.3 before copying its sizes (the fixture's `db.r7g.large` Multi-AZ + 3×1vCPU web + 2×2vCPU
workers + 365-day logs + `PriceClass_All` would cost roughly 4–5× CMP; it is a policy fixture,
not a sizing decision). Recommended launch profile: §14.3. Policy consequences: a real production
root joins the website Terraform delivery contract — production fixture updates, source-manifest
regeneration (`tests/policy/README.md:29-41`), and the exact accepted-test count (currently 48,
`tests/policy/README.md:99`) all change under review; budget for it in the issue.
Hostname: `prod.datatalks.club` with `robots_header_value = "noindex, nofollow"` **for its entire
life** (9.3/9.4) — production canonicals stay `https://datatalks.club/...` per spec 02:277.

### 9.2 [aws-infra] Minimal `dev.datatalks.club` — the AISL/CMP pattern, priced honestly

The owner rejected a second stack ("I don't want to pay 150 per month"). The pattern both
existing deployments actually use, verified:

- **CMP:** one ALB, HTTPS listener defaults to prod, a priority-100 host rule forwards
  `dev.courses.datatalks.club` to the dev target group (`main/cmp/alb_shared.tf:22-51`); one
  ECS cluster; one Aurora cluster with `…/dev` and `…/prod` logical databases on the same
  endpoint (`main/cmp/secrets.tf:25-42`); dev duplicates only task definition, 1-task service,
  target group, log group, DNS record (`app_dev.tf`).
- **AISL:** identical shape — shared cluster (`main/aisl/ecs.tf:54`), shared ALB with a `dev.`
  host rule (`ecs.tf:193,273-289`), one `db.t4g.micro` with separate dev/prod database URLs
  (`db.tf:22-29,67-87`), dev service 0.25 vCPU/512 MB × 1 (`ecs.tf:350-390`), separate
  `deploy-dev.yml`/`deploy-prod.yml` GitHub workflows.

**Dev design for the new site (same idea):** inside the production root, add a `dev` ECS service
(same immutable image family, 0.25 vCPU/512 MB, `desired_count 1`), a dev target group, an ALB
listener rule keyed on host `dev.datatalks.club` **plus the existing `X-Origin-Verify` header
condition** (the module's ALB only admits CloudFront, `edge.tf:77-100`, `network.tf:246-262` —
dev must not open a bypass), a small second CloudFront distribution (or an alias+SAN on a shared
one) for `dev.datatalks.club` forwarding the same origin secret, a `dev` logical database in the
**same RDS instance** with its own credentials/secret, and a `main/dns` record. Deploy via a
dev-branch workflow mirroring AISL's split. `robots_header_value` stays `noindex, nofollow`
permanently for dev.

**Marginal cost ≈ $8–12/mo** (one Fargate task ~$9 x86 / ~$7 ARM, + logs and a secret; ALB, RDS,
NAT, VPC already paid for by prod). What it buys: a permanently-on, URL-real, HTTPS,
production-account rehearsal target — the same fidelity CMP dev has today.

**What "minimal" costs in fidelity — the owner is choosing these knowingly:**

- *Shared RDS instance:* separate database + credentials prevent cross-database reads/writes, but
  instance-level resources are common — a runaway dev query or migration can exhaust
  connections/IOPS/CPU/storage and degrade prod (noisy neighbour). Dev cannot rehearse
  instance-scoped operations (engine upgrades, Multi-AZ failover, parameter-group changes,
  restore drills) without touching prod. The final pre-cutover migration rehearsals (spec 09
  M7) should therefore run against a **temporary throwaway RDS instance restored from snapshot**,
  not the shared dev database — hours of billing, not a standing cost.
- *Shared ALB/listener:* a misordered listener rule can shadow prod routing (CMP mitigates with
  explicit priorities, `alb_shared.tf:39`); dev deploy churn creates target-health noise — CMP
  learned to alarm on prod only (`main/cmp/observability.tf:55-58`); an ALB-level limit or WAF
  action affects both.
- *Shared VPC/root:* one `terraform apply` blast radius covers dev and prod; the policy suite's
  single-module contract means dev resources are reviewed into the same fixture set.
- *No account isolation:* today's sandbox gives hard account separation; after Phase 7.5–7.7 that
  disappears. IAM scoping (separate dev task role, dev secrets) is the remaining boundary.

**Sequencing:** dev-on-prod-infra can only exist once 9.1 exists; until then `web.dtcdev.click`
remains the development deployment. Cut CI/deploy targets over to `dev.datatalks.club`, prove one
full release cycle there, and only then start the sandbox decommission (7.5).

### 9.3 Staging discipline while `prod.` and the apex both serve content

From the moment `prod.datatalks.club` serves real pages until the stage-2 swap, the same corpus
exists twice (S3 legacy on apex, Django on `prod.`). Duplicate-content risk (#6 in §16) is
controlled by construction, all four controls mandatory:

- `prod.` responses carry `X-Robots-Tag: noindex, nofollow` at edge and application
  (`robots_header_value`, spec 08:264-275) — on HIT, MISS, redirect, error, and asset alike;
- `prod.`'s `robots.txt` disallows crawling and no `prod.` sitemap is ever submitted
  (spec 02:271-279);
- every production-equivalent page on `prod.` declares the **apex** canonical
  (`https://datatalks.club/...`, spec 02:277) — so even a leaked URL consolidates to the apex;
- the apex (S3 legacy) remains the indexed, canonical site throughout; nothing on it changes.

### 9.4 The stage-2 apex swap — the real cutover

This is milestone 8's step 5 (spec 09:161-180) with the mechanics owned here. At the instant of
the swap the Django site must serve **all 2,937 preserved URLs plus every approved
redirect/canonical/feed/sitemap contract at once** — there is no partial cutover of the apex.

**Gate (all BEFORE the swap, none after):**

1. Full parity run **on `prod.` against the live apex's responses**: the harness (15.4
   host-override) crawls `prod.datatalks.club` and compares against the checked manifest *and* a
   fresh capture of the live apex, with zero unexplained differences on the 2,937 preserve rows
   and the one approved `/slack.html` → `/slack` exception behaving exactly as recorded
   (`_docs/compatibility/README.md:126-135`). The complete SEO parity report must pass —
   spec 02:304 makes this the explicit DNS-cutover blocker.
2. Milestone-8 steps 1–4 complete: final content sync + data delta imported and reconciled,
   outbound email paths disabled, smoke green on the new stack (spec 09:163-169).
3. Phase 5 caching evidence green (MISS/HIT, TTL-zero rollback rehearsed — spec 09:154-157), or
   caching deliberately left at TTL-zero for the swap.
4. Rollback rehearsed: alias swapped to the website distribution and back on a low-stakes
   hostname.

**Mechanism and true rollback latency:** in `main/dns`, change the apex A/AAAA **alias** records
from the `main/legacy-site` distribution to the website distribution (`www` follows). Route 53
alias answers are served with a short effective TTL (~60 s for alias-to-CloudFront); resolvers
that cached the pre-swap answer under the prior TTL age out within minutes. **True rollback
latency ≈ 1–10 minutes worldwide**: revert the two alias records and traffic returns to the S3
legacy distribution. That is the entire rollback procedure — which is why gate item 4 rehearses
exactly it.

**After the swap:**

- `prod.datatalks.club` switches from serving pages to a permanent `301 → https://datatalks.club`
  (host-level redirect at its edge), and is removed from sitemaps/docs; retire the hostname
  entirely once external references die out. Its `noindex` history means nothing was indexed to
  clean up.
- The **S3 legacy site is kept intact, warm, and instantly re-pointable for a defined rollback
  window: 30 days minimum** (matching spec 09:178's read-only window and §6's DNS windows), then
  retired in Phase 7.3. Content on it is frozen at the final pre-swap sync; it is the
  "legacy static artifacts for read-only fallback" of spec 09:205. Rollback within the window =
  the alias revert above. **Note the rollback caveat spec 09:194-196 imposes:** once real
  registrations/enrollments occur on the new stack, never point the *entire* site back to static
  hosting — the documented rollback for dynamic-path failures is an application-image rollback on
  the new stack, with the S3 alias revert reserved for catastrophic whole-site failure in the
  first hours.
- Submit the unchanged production sitemap and begin the monitoring regime of spec 09:175-177 /
  spec 02:280-291 (Search Console coverage, 404/5xx/redirect volume, top landing pages).

---

## 10. Phase 5 — Anonymous edge caching (owner requirement, with one hard contradiction)

The owner wants: *"serve a cached version via CloudFront for unregistered users, and when there
are updates this cache should be updated too … merged with our production-prep branch."*

### 10.1 What exists today: caching is designed but OFF

- The module ships a single cache policy with every TTL = 0 —
  `modules/django-website/edge.tf:189-214`, comment: "Disable caching until path-specific public
  asset policies are added" — and an `all_viewer` origin-request policy forwarding all cookies,
  headers, and query strings (`edge.tf:216-231`), which makes caching impossible by construction.
- The *design* is already fully specified: route classes with concrete TTLs and keys in spec
  02:188-211; the anonymous/private classifier, per-behavior allowlists, origin-response guard,
  and rollout gates in spec 08:88-124; rollout evidence requirements in spec 09:154-157 and
  09:248-261. **None of it is implemented** in Django (no route-cache registry in the codebase) or
  Terraform (one zero-TTL behavior). So the owner's ask is scheduled work, not a config flip.

### 10.2 The anonymous/authenticated split (the crux)

Per spec 08:111-119, implement exactly this — do not invent an alternative:

- A deterministic viewer-request CloudFront Function marks a request `anonymous-v1` **only** when
  provably credential-free: no `Authorization`, no signed URL/cookie, no session/auth/CSRF or
  *unknown credential-shaped* cookie, no preview/management token, no malformed encoding. Any
  doubt, or function failure ⇒ private. The marker — never a viewer-supplied lookalike, which the
  function strips — is forwarded and keys public HTML.
- Fail-safe direction: a wrongly-private request costs one origin hit; a wrongly-anonymous
  response cached and replayed is a security incident. Every ambiguity resolves to private, and an
  origin-response guard forces `private, no-store` before storage on any response carrying
  `Set-Cookie`, CSRF, identity, or capability state (spec 02:200 "Unsafe/error" class). If
  isolation cannot be proven for a route, it stays zero-TTL (spec 08:118-119).
- **CSRF specifically:** an anonymous page that emits `{% csrf_token %}` triggers Django's
  `csrftoken` cookie and per-request token — cached, that either serves one visitor's token to
  everyone or poisons form posts. Public cacheable pages must be token-free (forms live on
  private/no-store routes); the guard treats any `Set-Cookie` as disqualifying, so a stray token
  yields a cache miss, not a leak. Add an explicit test: anonymous GET of every public-class route
  emits no `Set-Cookie` and no csrf token in the body.

### 10.3 What implementing it requires

1. **[website]** The versioned route-cache registry of spec 02:182-186/08:90-94: every route
   classified, Django emits the class's `Cache-Control` (edge headers respect origin —
   `edge.tf:235` "application Cache-Control remains authoritative"), tests enforce
   registry↔header agreement, unclassified ⇒ private.
2. **[aws-infra]** Replace the single disabled behavior with per-class cache/origin-request
   policies (spec 08:96-109: normalized path + gzip/br key, no cookies/headers/query except the
   registered `season` selector), the viewer-request function, and the origin-response guard;
   keep `min_ttl = 0` everywhere so origin `no-store` always wins (spec 02:202-203).
3. **Poison/isolation evidence before positive TTLs** (spec 08:314-318, 09:248-261): WAF count
   mode, credentialed-bypass proofs, canaries.

### 10.4 THE CONTRADICTION — "update the cache on updates" vs. the invalidation ban

**Flagged, not resolved silently:** aws-infra's policy suite asserts that adding
`cloudfront:CreateInvalidation` to the website publisher role is rejected
(`tests/policy/test_terraform_policy.py:347`, rule IAM006) and to the deployer role likewise
(`:409`, IAM004); no principal in the repo holds it. Yet spec 08:126-138 *requires* bounded
worker-submitted invalidations for content activation and deploys, and the owner now asks for
freshness-on-update. Options:

| Option | Freshness | Cost/risk | Verdict |
| --- | --- | --- | --- |
| (a) Bounded TTLs + `stale-if-error` only (spec 02 classes: editorial 600 s, hubs 300 s, catalog 60 s) | worst-case = class TTL (≤10 min for editorial) | zero new permissions; spec 09:260-261 already names "bounded class TTL is the correctness backstop" | **do first** |
| (b) Fingerprinted/versioned URLs | instant | works only for `/static/`; the editorial URLs are frozen by the 2,937-row preserve contract — cannot version them | static assets only |
| (c) Narrow `cloudfront:CreateInvalidation` grant to the **worker task role** (not publisher/deployer) | near-instant after activation | a deliberate, reviewed loosening: new IAM surface must pass the exhaustive ownership inventory (`tests/policy/README.md:146-160`), changes the accepted policy-test set, needs its own issue. Note the two asserted-rejections target the *delivery* roles; a worker grant is additive review, not a test deletion | **do second**, exactly as spec 08:126-138 already designed |
| (d) Origin-driven purge at content-sync time without IAM change | impossible — purging *is* invalidation | — | rejected |

**Recommendation (D4):** ship (a) — the owner's freshness requirement is met within minutes and
matches the already-approved spec; then groom (c) as the spec-08 invalidation issue so
activation-coupled freshness lands with the direct-sync work (section 13), whose activation step
is precisely when "there are updates".

### 10.5 Where the work lands, and when

Product work ⇒ groomed issues (PROCESS), merged to `production-prep` (exists, tip `55f6743`) as
the owner directs. Recommended split: **(i)** [website] route-cache registry + headers + CSRF/no-
Set-Cookie tests; **(ii)** [aws-infra] per-class edge policies + classifier function + guard;
**(iii)** [aws-infra+website] the invalidation grant (option c). Sequencing: enable and *prove*
caching on the development stack and on `prod.datatalks.club` **before** the milestone-8 apex
cutover — turning caching on for the first time in front of live apex traffic couples two risky
changes; spec 09:157 already demands the MISS/HIT and TTL-zero rollback reports as milestone-7
evidence. Relative order with other phases: after Phase 4 exists, independent of Phases 1–3;
option (c) after or with the direct-sync activation work.

### 10.6 Verification and rollback

*Verify:* `x-cache: Hit from cloudfront` on repeat anonymous GET of an editorial detail;
authenticated canary — log in, request the same URL, assert `x-cache: Miss`/`RefreshHit` never
serves the anonymous body and the response is `private, no-store`; poison canaries per spec
08:314-318; CloudFront standard logs show no `Set-Cookie` responses with HITs; cache-hit-ratio
alarm ≥70% after warm-up (spec 08:163-166). *Rollback (one step):* the emergency TTL-zero input —
spec 08:168-169 requires cache disable/TTL-zero as a reviewed Terraform input; implement it as a
single variable flipping all public classes back to the `disabled` policy
(`edge.tf:189-214` already exists as the target state), `terraform apply`, effective at the edge
in minutes; since `min_ttl=0` everywhere, origin `no-store` is an additional immediate brake the
application can pull without Terraform.

---

## 11. Phase 6 — `courses.datatalks.club`: two Lambdas, distinct purposes (D3 resolved)

Owner: "there will be two lambdas 1) maintenance while migrating 2) redirect after migrating."
Stage A below is Lambda 1, Stage B is Lambda 2; Stage A′ (compatibility routing on the new
stack) sits between them so API consumers never face a cross-host redirect.

The hostname's zone is already Route 53 (`Z00653771YEUL1BFHEDFR`, finding 1.2-4), so every stage
is an alias swap we control today, independent of the GoDaddy move — **this lane therefore runs
in parallel with Phases 1–4 rather than waiting on them** (delegation to `awsdns-*` verified
live by both this audit and the coordinator). Alias records carry no
client-visible TTL of their own beyond the target's; observed switchover is minutes.

**Stage A — maintenance Lambda, active only during the migration window** (Lambda 1 of the
owner's resolved D3: "maintenance while migrating"):

- **6a.1 [aws-infra]** Track and finish `main/maintenance-page` (currently untracked): add S3
  backend config (it deliberately ships none — `README.md:60-66`), **change the HTML response
  status to 503 with `Retry-After`** (currently 200, `lambda/index.js:434-440`; finding 1.2-6 —
  200 would poison crawlers and fool API clients during the freeze; keep the image 200), keep the
  405-for-POST behavior, `npm test`.
- **6a.2 [aws-infra] [CREDS]** Apply with DNS *disabled* (default) → raw execute-api endpoint.
  Provision the regional ACM cert for `courses.datatalks.club` and the API GW custom domain via
  the documented variables (`README.md:76-95`) but **without** the Route 53 record — that record
  is the activation switch. Rehearse activation on `dev.courses.datatalks.club` first (swap its
  alias from the shared ALB to the API GW domain, verify, swap back) — this rehearses the exact
  production motion against the dev host, as spec 08:303 requires for the redirect stack.
- **6a.3 Activation (during the milestone-8 freeze, spec 09:163):** change the
  `courses.datatalks.club` alias from the CMP ALB (`main/cmp/app_prod.tf:163-173`) to the API GW
  custom domain. *Verify:* `curl -sI https://courses.datatalks.club/` → 503, maintenance body;
  CMP ALB no longer receives the host. *Rollback:* swap the alias back — minutes, rehearsed.

**Stage A′ — post-cutover compatibility routing** (often conflated with Stage B; it is not):
after the unified Django site absorbs CMP (milestones 4–5), `courses.datatalks.club` must serve
**real compatibility views on the new stack**, not redirects — API clients "may not safely
preserve authorization across a cross-host redirect" (spec 02:152). Mechanics: add the hostname
as an alias + cert SAN on the website distribution and to `DJANGO_ALLOWED_HOSTS`; alias swap from
maintenance Lambda to the website edge. This state persists until every browser, script,
certificate tool, and email template is migrated (spec 02:44).

**Stage B — redirect Lambda** (owner item 4; fully specified already, spec 08:293-305, spec
09:116 + M8.5):

- **6c.1 [aws-infra]** New root `main/courses-redirect`: Lambda + HTTP API catch-all + regional
  cert + alias; the Lambda embeds a **generated, reviewed legacy-path map** (produced from
  `_docs/compatibility/course-route-contracts.json`'s 115 route patterns + the 22 literal machine
  samples), preserves query strings, `301` for mapped GET/HEAD HTML, `308` for non-GET **only**
  after client tests prove method/body/auth survival, real 404 (never a homepage redirect) with
  PII-free structured metrics. HTML destinations are the spec-02 canonical course URLs
  (`/courses/<slug>`, spec 02:135-154); each mapping ships only after its destination passes
  production smoke (spec 09:180).
- **6c.2** Deploy dark → rehearse on a non-production hostname → alias swap when the
  browser/API consumer gate passes (spec 09:116 "held inactive until all API consumers are
  ready"). *Rollback:* alias back to the website edge (Stage A′ compatibility views), which
  "must not delete the old ECS/database infrastructure until the observation and rollback window
  expires" (spec 08:305).

---

## 11E. Phase E — Email sending: Relay promoted to the main account, battle-tested

Owner (2026-09-02, **superseding** the same-day Datamailer-first direction): "Relay will need to
go to main account too. Let's actually use it and just start battle-testing it. But I want to
make sure it's actually working well, so 1) audit the code 2) make sure that we have enough
visibility to fix things quickly." And: "Adjust the migration plan — let's go with Relay."

**Relay is the sender.** This *aligns* with the standing specs instead of amending them —
`_docs/architecture/app-boundaries.md:66` ("Datamailer remains read-only
migration/history/reconciliation input, never a sender") and spec 09:143 ("New website code has
no direct Amazon SES or Datamailer send path") already mandate exactly this — so D13 closes as
**resolved by alignment** (§3.1), the spec-amendment work item is removed, and Datamailer keeps
only its specified read-only role (E.7). Parallel-lane status unchanged: nothing in Phases 1–6
waits for Phase E, and Phase E does not wait for the DNS migration (E.0). Two items now sit on
*this lane's* critical path: the commissioned audit gate (E.5) and the SES identity hand-off
(D14 — pulled forward from Phase 7 into E.3).

**Risk reframing from the codebase comparison (coordinator-verified, spot-checked here):**
Relay is a ~5-week-old **fork of Datamailer**, not an independent project — same root commit,
191 of Datamailer's 192 commits shared (fork point datamailer `12a93bb`, 2026-08-07), 181 of
220 tracked files byte-identical including the entire `mailing/` mail engine; the one
consequential code difference is the API-key prefix (`mailing/services/auth.py:9` —
`"relay_"` vs `"dm_"`; verified in both checkouts). Relay adds ~4,200 lines: a generic `jobs/`
task+schedule API, vendored `taskdeck`, per-task IAM role assumption, a readiness probe, and a
containerized gunicorn deploy with a post-deploy smoke test (`scripts/smoke_test_relay.py`)
that Datamailer lacks. Both test suites pass — Datamailer 493, Relay 520, a strict superset —
so the owner's "never been tested" premise does not hold as stated. What *is* true, and what
this phase manages: **Relay has zero real clients and has never carried live traffic.** The
risk is "unproven under real load", not "untested code" — and the shared `mailing/` engine
*is* production-exercised today through CMP's Datamailer path, which narrows the genuinely
unproven surface to Relay's additions (jobs/task API, deploy, IAM assumption) plus
operational behavior at volume. The stronger deployment engineering (gunicorn vs
`manage.py runserver`, readiness probe, worker/scheduler containers, `makemigrations --check`
CI gates) supports the owner's choice.

### E.0 The de-risking fact: SES sending as `@datatalks.club` already works, in eu-west-1

Verified live (2026-09-02) and against Terraform:

- `mail.datatalks.club` MX → `10 feedback-smtp.eu-west-1.amazonses.com` — an SES **custom MAIL
  FROM domain**, exactly what `main/cmp/iam_ses.tf:67-71` provisions
  (`mail_from_domain = "mail.${domain}"`).
- `mail.datatalks.club` TXT → `v=spf1 include:amazonses.com ~all` — SPF **on the MAIL FROM
  domain**, which is where SPF is evaluated for SES with a custom MAIL FROM. SPF for SES sending
  passes today without touching the apex SPF.
- `mail._domainkey.datatalks.club` TXT → a published RSA DKIM key. Note the *shape*: one classic
  TXT key at selector `mail`, not the three Easy-DKIM CNAMEs that CMP's
  `aws_ses_domain_dkim` (`iam_ses.tf:61-64`) would emit — so the live DKIM setup does not match
  the CMP Terraform's mechanism and was at least partly configured out-of-band. Do not assume
  `iam_ses.tf` state mirrors reality; E.1 verifies with the API.
- `_dmarc.datatalks.club` → `p=none` (monitoring only — changes cannot hard-fail delivery).
- Apex MX stays Google Workspace and apex SPF stays the Google-only flattening chain
  (`dc-aa8e722993._spfm…`) — both **correct and untouched**: inbound mail is Google's, SES
  outbound aligns via `mail.`. **No new DNS records are needed to send as
  `courses@datatalks.club`, and none of this blocks on the GoDaddy→Route 53 move.** The Phase 1
  zone copy must simply *carry* these records verbatim (Appendix A rows for `mail` MX/TXT and
  `mail._domainkey`).

This corrects the intuition that SPF surgery is the dangerous step — for sending, the DNS is
done. The genuinely unverified item is E.1.

### E.1 [AWS console/CLI] [CREDS] — verify SES production access in the main account (EARLY; do
this in Phase 0 alongside 0.3)

Sandbox-account evidence says the *sandbox* SES is in sandbox mode
(`sandbox/datamailer/terraform.tfvars.example:5-9` lists three individually verified recipient
identities — the signature of sandbox-mode SES). The **main** account is almost certainly in
production mode (CMP mails real learners: daily deadline reminders,
`main/cmp/cmp_deadline_reminder.tf:8-13`, and the Datamailer outbox path), but this runbook could
not verify it — only sandbox credentials were available. With main-account credentials:

```console
aws sesv2 get-account --region eu-west-1        # expect "ProductionAccessEnabled": true
aws sesv2 get-email-identity --email-identity datatalks.club --region eu-west-1
                                                # expect VerifiedForSendingStatus true; note the
                                                # DKIM configuration (Easy vs BYODKIM, selector)
aws sesv2 get-account --region eu-west-1 --query 'SendQuota'   # note Max24HourSend / MaxSendRate
```

- If `ProductionAccessEnabled` is **true**: record the quota and move on — E is unblocked.
- If **false**: file the SES production-access support request **immediately** — it has
  multi-day external lead time, can come back with questions, and nothing can compress it. That
  request becomes the front of this entire lane; the `main/relay` deployment (E.3) can proceed
  in parallel, send-disabled.

### E.2 What already speaks Relay, and what will — two efforts, kept distinct

**Effort 1 — repoint the existing transactional path: cheap, near-config-only.** Because Relay
serves Datamailer's API byte-identically (fork finding above), the website's existing send call
— `POST /api/transactional/send` with `Authorization: Bearer <key>`
(`course_management/datamailer/client_transactional.py:15-23`, `client.py:96`) — works against
Relay unchanged. Repointing = set the base URL to `main/relay` and issue a `relay_`-prefixed
key. Not an adapter, not a rewrite. The same applies to CMP, which is already Relay-wired:
prod tasks carry `RELAY_URL=https://relay.dtcdev.click`, `RELAY_CLIENT`/`RELAY_AUDIENCE` =
`dtc-courses` (`main/cmp/app_prod.tf:49-59`) with a provisioned `relay-api-key` secret
(`main/cmp/secrets.tf:16-19`; aws-infra merged `cmp-relay-cutover`, commit `378b687`).

**Effort 2 — adopt the specified durable-job Relay architecture: a build, roughly 0% done.**
The `EmailDelivery` model that `app-boundaries.md:34-35` assigns to `email_app` (logical
intents, Relay idempotency/correlation metadata, callbacks/reconciliation) **does not exist** —
`email_app/models.py` is a single docstring: "Transactional email models are intentionally
deferred." The website has zero Relay wiring (no `RELAY_URL`, no client). This is Milestone-6
work (spec 09:119-143), arrives purpose-by-purpose under #22/M8.6 gates, and must not be
estimated as "just switch the URL" — that shortcut describes Effort 1 only.

The adopted Datamailer client itself stays read-only/dry-run per spec — but see the new
boundary-hardening risk (#9, §16): the no-send promise is currently **config-enforced, not
code-enforced**. ~3,900 lines of live-callable Datamailer client exist with reachable call
sites (`courses/views/homework_confirmation.py:8-9,149`); what makes the spec true is
`deploy/task_definitions.py:36-42` pinning `DATAMAILER_URL: ""` and
`DATAMAILER_TRANSACTIONAL_DRY_RUN: "1"`, guarded by
`core/tests/test_deployment_release.py:1001`. Real sends are one env var from live — exactly
the kind of variable an email migration touches. **Hardening recommendation (groomed issue):**
when Effort 1 repoints the transactional path at Relay, make the Datamailer client fail closed
in code (refuse a non-empty `DATAMAILER_URL` outside tests, or excise the send call sites),
not merely in pinned env.

### E.3 [aws-infra] [CREDS] `main/relay` — the promotion, specified

New state root `main/relay`, cloned in shape from `sandbox/relay` (README:8-27: one ARM
`t4g.micro` via SSM with no SSH port, EIP, encrypted root + encrypted data volume at
`/var/lib/relay` for PostgreSQL, Relay-owned `ses-webhooks`/`inbound-email` SQS + DLQs, queue/
DLQ/instance/disk alarms, GitHub OIDC deploy role for `DataTalksClub/relay`), with these
deliberate differences:

- **Account/region:** `387546586013`, everything in `eu-west-1` *including SES*. The sandbox
  split — eu-west-1 host, us-east-1 email (`sandbox/relay/terraform.tfvars.example:1,9`) —
  disappears, because the verified `datatalks.club` identity is eu-west-1 (E.0).
- **Networking:** reuse the `main/common` VPC + public subnets (`main/common/network.tf:32-52`)
  per the D2 shared-infrastructure principle instead of a second dedicated VPC; keep the tight
  no-SSH/SSM security-group shape from `sandbox/relay/main.tf:67-99`. Tradeoff named: shares the
  CMP-era VPC’s blast radius; acceptable for one small host, and one less VPC to own.
- **DNS:** `relay.datatalks.club` — record in `main/dns` after Phase 1 (or at GoDaddy if E runs
  first; the hostname has no email-authentication role).
- **Inbound mail:** optional. Sandbox receives `relay@inbound.relay.dtcdev.click` to S3; a main
  equivalent needs its own MX decision and is **not** required for sending — default: omit.
- **Deploy:** same OIDC/SSM pattern, trust re-pinned to the main account.

**The SES binding — the critical dependency, made explicit.** Relay does not manage SES. In
sandbox it sends "through the existing `dtcdev.click` SES identity and `datamailer-sandbox`
configuration set" (`sandbox/relay/README.md:20-21`; `iam.tf:93` scopes the send permission to
that configuration-set ARN) — i.e. **sandbox Relay sends through Datamailer’s SES
infrastructure**. In main it binds instead to:

1. the existing **verified `datatalks.club` identity** (live in eu-west-1, E.0) — reused, never
   recreated; sending as `courses@datatalks.club` needs zero new DNS (E.0);
2. a **new Relay-owned configuration set + SNS event topic created in `main/relay`** — *not*
   CMP’s `course-management-email-config` (`main/cmp/iam_ses.tf:74-83`), which would recreate
   exactly the D14 ownership trap on a second resource;
3. an instance-role send permission scoped to that identity ARN + configuration set — no
   `aws_iam_user` (policy floor, §14.4).

Because the identity itself is Terraform-owned by `main/cmp` (`iam_ses.tf:54-71`) and CMP is
scheduled for destruction, **the D14 identity hand-off (step 7.0’s `state mv` + clean plans +
canary) is now a prerequisite for Relay production sending, not merely for Phase 7 teardown** —
execute it before or alongside E.3. It is risk #2 in the register.

### E.4 State: what migrates, what does not

The sandbox EBS data volume holds Relay’s PostgreSQL — template versions, delivery/event
records, all of it test-era. **Clean start on main** (D15): production templates are
re-published through Relay’s own draft/immutable-publish flow (spec 06), and test delivery
history has no production value. Queues are ephemeral — drain sandbox before retiring it. The
sandbox stack stays running as the *development* Relay (CMP dev and website dev keep pointing at
`relay.dtcdev.click`) until the sandbox teardown, whose internal ordering E.7 constrains.

### E.5 GATE — code audit + observability. **BLOCKED(audit)**

Owner: "1) audit the code 2) make sure that we have enough visibility to fix things quickly."
Two assessments are commissioned and running against `/home/alexey/git/relay`:

- **(a) adversarial code audit:** correctness of the send/event/inbound pipelines; secrets;
  authentication — an unauthenticated send endpoint would be an open relay; SNS signature
  verification on the webhook drain; MIME parsing safety; idempotency under SQS at-least-once
  redelivery; backup/restore of the single-EBS PostgreSQL; SES rate-limit and suppression
  handling.
- **(b) observability assessment** against the owner’s visibility requirement, made concrete
  in E.6’s stage-1 criterion (trace one message end-to-end in under 10 minutes).

The fork finding (§11E intro) focuses rather than shrinks this gate: the shared `mailing/`
engine inherits Datamailer's production exposure, so audit attention concentrates on Relay's
~4,200 added lines (`jobs/` task API, vendored `taskdeck`, per-task IAM assumption, deploy) and
on properties no test suite settles (open-relay/auth posture at the edge, SNS signature
verification, backup/restore of the single-EBS PostgreSQL, SES limit/suppression behavior).

**Production sending does not begin until the audit’s "must fix before sending production
email" list is closed and the observability gaps are addressed.** Findings will be appended
here when the coordinator delivers them. E.3 infrastructure may proceed in parallel (deployed
dark, send-disabled); E.6 may not start. Independent of the audit outcome, one item is already
known and joins the gate: the single-EBS PostgreSQL needs a scheduled snapshot + one rehearsed
restore before real delivery records accumulate.

### E.6 Battle-testing: a graduated ramp, not a big-bang switch

Preconditions: E.5 gate closed; E.1 production access verified; D14 identity hand-off done.

| Stage | Traffic | Volume / duration | Advance when (all of) | Rollback |
| --- | --- | --- | --- | --- |
| 0 | Allowlisted-recipient transactional canaries, operator-triggered, CMP dev → `main/relay` | handful/day, 1 week | delivery + callback + reconciliation green; DLQs empty; received headers show SPF+DKIM pass and `From: courses@datatalks.club` | point CMP dev back at `relay.dtcdev.click` |
| 1 | One lowest-blast-radius production transactional purpose — the `courses` sender canary of spec 08:224 (e.g. deadline reminders for one active cohort) | tens/day, 2 weeks | zero unexplained ambiguity events (spec 09:131); DLQs empty; bounce/complaint under warning thresholds; **operator traces one arbitrary message end-to-end in <10 min using only the shipped observability** | flip that purpose’s CMP env back (sticky env vars via `cmp-env`, `aws-infra/README.md:41`); hold + reconcile in-flight sends |
| 2 | All CMP transactional purposes | current CMP volume, 2 weeks | same criteria sustained | per-purpose flip back |
| 3 | Website purposes as Milestone 6 lands, one purpose at a time | per purpose | spec 09 M6/M8.6 purpose gates | hold jobs + reconcile; never dual-send (spec 09:202-206) |
| 4 | Bulk/campaign mail — **last** | gradual warm-up | complaint behavior proven across stages 1–3; list hygiene reviewed | stop campaigns; transactional unaffected |

**The SES-existential risk and the abort trigger.** A bounce/complaint breach suspends the
*sending identity* — that takes down **all** `@datatalks.club` mail (CMP’s included), not just
Relay’s. SES review territory is roughly 5% bounce / 0.1% complaint. Wire CloudWatch alarms on
the Relay configuration-set metrics in E.3: **warning at 2% bounce / 0.05% complaint; abort at
3% bounce / 0.08% complaint**. Abort = stop all Relay sending via the kill switch verified in
the E.5 audit, hold jobs, triage recipient lists, resume only after cause is known. Suppression
handling follows the audit’s findings.

### E.7 Datamailer’s actual role, and the sandbox-teardown ordering trap

Datamailer reverts to exactly its specified role: **read-only migration/history/reconciliation
input** (#290; `app-boundaries.md:117-118`). No `main/datamailer` root is built. One explicit
ordering trap for Phase 7: because **sandbox Relay sends through the `datamailer-sandbox`
configuration set and event topic** (E.3), tearing down sandbox Datamailer while sandbox Relay
still operates breaks sandbox Relay. Teardown order inside 7.4–7.6: retire sandbox Relay first
(possible once E.6 stage 2 holds and dev consumers move to `main/relay` or stub), **then**
sandbox Datamailer, then `sandbox/website` per its own preconditions.

### E.8 Cost

`main/relay` ≈ **$15–18/mo** (t4g.micro ≈ $6.7 + public IPv4 ≈ $3.6 + two encrypted gp3
volumes ≈ $3–5 + SQS/SNS/S3/alarms pennies + SES $0.10 per 1,000 mails). Versus the superseded
Datamailer-on-main plan (≈ $13–16): the delta is noise, and **no `main/datamailer` root is
built at all**. Dual-run window (sandbox Relay + `main/relay`) ≈ +$13/mo until E.6 stage 2.
The Phase 7 sandbox harvest now includes both sandbox Relay (≈ $13–15) and sandbox Datamailer
(≈ $13–15).

---

## 12. Phase 7 — Decommission and cost harvest

Only after: milestone-8 acceptance, redirect-Lambda observation window closed, and the ≥30-day
DNS/apex rollback windows expired.

- **7.0 [aws-infra] PRECONDITION for 7.1 — move the SES identity out of CMP.** The
  `datatalks.club` SES domain identity, DKIM, and mail-from are Terraform-owned by `main/cmp`
  (`iam_ses.tf:54-71`), and all Relay production sending (Phase E) binds to that identity.
  `terraform state mv`/`import` those resources into their new owner root (D14 — recommended
  `main/common` per `docs/state-boundaries.md:19-24`), confirm a clean no-change plan in both
  roots, and send a canary email. **This step is scheduled early — before or alongside E.3, not
  at teardown time** (it moved onto the Relay critical path); it is listed here because it is
  the hard precondition for 7.1. Destroying CMP with the identity still in its state **deletes
  the verified sending domain** and breaks all outbound mail (risk #2).
- **7.1 [aws-infra] [CREDS] [ONE-WAY]** Destroy `main/cmp` compute/ALB/dev resources (final DB
  snapshot is enforced — `db.tf:22-24`, `deletion_protection = true` must be lifted knowingly;
  the Aurora final snapshot is the point-of-no-return gate). Keep the snapshot per the retention
  decision in `migration-checklist.md:177`.
- **7.2** Retire the four GitHub Pages deployments (remove custom-domain CNAME files; archive the
  repos' Pages settings). ONE-WAY in practice — re-serving requires re-verification with GitHub.
- **7.3** Retire `main/legacy-site` after the stage-2 swap's own ≥30-day rollback window (§9.4):
  the S3 tree is the designated read-only fallback (spec 09:205) — cheapest thing in the whole
  account; keeping it for a year costs ≈ $1/mo, so err long.
- **Sandbox decommission (owner, D2: "sandbox infra will be gone once we migrate"):**
- **7.4 Preconditions and internal order:** `dev.datatalks.club` (§9.2) has served as the
  working dev deployment through at least one full release cycle; nothing deploys to
  `web.dtcdev.click` any more (`.github/workflows` in the website repo retargeted); no
  compatibility capture or CI job references the sandbox host. Email stacks retire in the E.7
  order — **sandbox Relay first** (after E.6 stage 2, dev consumers moved off
  `relay.dtcdev.click`), **then sandbox Datamailer** (sandbox Relay sends through Datamailer's
  `datamailer-sandbox` configuration set and event topic, `sandbox/relay/README.md:20-21` —
  reversing the order breaks sandbox Relay while it still runs), then `sandbox/website`.
- **7.5 [aws-infra]** Remove the cross-account media-bucket read: drop the sandbox account from
  `media_reader_account_ids` / `media_reader_principal_arns` in `main/dtc-website`
  (`variables.tf:37-59`, granted by PR #30 for the migration period only) and apply — the grant
  is unnecessary once no sandbox workload reads media.
- **7.6 [aws-infra] [CREDS] [ONE-WAY]** Destroy `sandbox/website` (final DB snapshot per its
  inputs, `terraform.tfvars.example:85-86`), then retire its OIDC delivery pipeline
  (`.github/workflows/website-terraform.yml` targets this root) and the sandbox policy-suite
  fixtures per a reviewed policy-evolution issue — the credential-free CI contract pins the
  sandbox root today (`tests/policy/README.md:99-105`), so this is a reviewed change, not a
  deletion. Scope note: **only** `sandbox/website` is in this runbook's scope. Other sandbox
  stacks (Relay, Datamailer until E.6, `dtcdev.click` zone, dataops, etc.) have their own owners
  and lifecycles — in particular `relay.dtcdev.click` is still referenced by CMP prod
  (`main/cmp/app_prod.tf:49-51`) until CMP itself is gone.
- **7.7** Re-run the Cost Explorer comparison against §14 and record actuals (expected harvest:
  the CMP line ≈ $180–225, the sandbox website stack ≈ $65–85, sandbox Relay ≈ $13–15, sandbox
  Datamailer ≈ $13–15).

---

## 13. Content ingestion — how data gets in, now and ongoing

### 13.1 Source inventory (verified against the checked artifacts)

| Source | Pinned at | Lands where | Volume | Mechanism today |
| --- | --- | --- | --- | --- |
| `DataTalksClub/content` (editorial authority) | preferred `1375c506…` accepted, CI-evidence-bound; fallback `373bef2…` explicitly unaccepted (`manifest.json` `sources.preferred_content` / `.fallback_selection`) | articles 55, podcasts 203 (+201 transcripts), books 98, people 438, events 421, media 1,253 (`manifest.json` `counts`) | ~187 MB projection incl. media | Build-time projection, committed (13.2) |
| `DataTalksClub/datatalksclub.github.io` | `ee43d3fa…` (`manifest.json` `sources.legacy_main`; `_docs/compatibility/README.md:36`) | remaining main-site collections (tools/conferences pending #291) + the 2,937-row URL contract | 2,301 generated files | Compatibility evidence + projection provenance |
| `DataTalksClub/docs` | `3f23e006…` (README:37) | `/docs` (174 files) | small | Checked docs projection; offline parser census = #292 |
| `DataTalksClub/faq` | `c8da1dee…` (README:38) | `/faq` (152 files, 1,401 anchors) | small | Checked FAQ projection; adapter = #293 |
| `DataTalksClub/podwiki` | `988b79d0…` (README:39) | `/wiki` family (282 wiki records, graph/search projections) | 310 files | Projection; typed normalization = #294 |
| `DataTalksClub/course-management-platform` | code adopted at `98a23528…` (open-decisions §2); course specs same revision (`manifest.json` `sources.courses`) | 12-course public catalog projection + the live service | live DB | **Service migration, not content sync — 13.6** |
| Course repositories (e.g. `llm-zoomcamp`) | per-repo branch config | Course/Cohort curriculum | per course | Already webhook-shaped: `content_sync/course_repository_webhook.py` + sync (#218, `_docs/planning/2026-08-20-course-repository-curriculum-sync.md`) |
| Datamailer | inventory pin = #290 (P0, groomed) | read-only email history/reconciliation input, never a sender (spec role reaffirmed by D13's Relay resolution) | TBD by #290 | send-disabled import only (spec 09:141-143); live sending is Relay's (Phase E) |
| Luma / Eventbrite exports | protected local snapshots, checksummed (`migration-checklist.md:62-124`) | registration aggregates | 51,873 accepted rows (Luma) | operator-gated aggregate import |
| `DataTalksClub/zoomcamp-scoring`, Mailchimp, CMP SQLite snapshot | per `migration-checklist.md:14, :135-140` and spec 09:300-315 | historical course/email data | TBD | one-time gated imports |

### 13.2 The current mechanism, honestly

It is a **manual, build-time, committed projection** — verified:

- `scripts/build_public_projection.py` is the sole builder (docstring lines 1–8: deterministic,
  pinned to the accepted content revision + its green CI evidence); it has **no Makefile target**
  and is run by hand; its output `content/public_projection/*.json` + `media/` is **committed**.
- CI never rebuilds it: `ci/content_update.py:1-7` — "intentionally a projection checker, not a
  source synchronizer".
- Integrity chain: `manifest.json` records per-artifact sha256 (`artifacts`), a whole-tree
  `tree_sha256`, count canaries (`counts`), and pinned source revisions (`sources`);
  `content/public_data.py` re-derives and compares the tree hash at load (#301 cites `:625`,
  `:339-352`) and pins expected revisions (`public_data.py:109`). A tampered or half-updated
  projection fails closed.
- Update path today: human runs the builder → reviews the diff → commits → normal release. Who
  approves content going live = whoever reviews that commit. Rollback = `git revert` + redeploy.
  Sound, deterministic, slow — and it is why 150 MB of media sits in git (#301).

### 13.3 The direct-sync programme (in flight, all gates closed)

Owner decision #226 (closed; `_docs/specs/open-decisions.md` §1) replaces staged
`ContentRelease` activation with per-source direct sync: *source lock → immutable checkout →
parse/dispatch → direct upsert → soft-delete/draft transition → `SyncLog`*. Current state of the
delivery issues, read 2026-09-02:

- **#273** (schema + historical reconciliation), **#272** (validation receipt/evaluator),
  **#276** (public cutover), **#278** (retire staged behavior): all OPEN, all
  `needs grooming` + `decision` + `human`, each with an explicit PM "BLOCKED / do not dispatch"
  note. #273 states the owner-approved **source rollout manifest does not exist** (D12); #276
  corrects its own premise ("current public authority is heterogeneous"); #278 may start only
  after slices A–E are accepted.
- Adapter slices **#291** (tools/conferences), **#292** (docs parser), **#293** (FAQ adapter),
  **#294** (podwiki normalization), **#290** (Datamailer inventory) are P0 and groomed
  (no `needs grooming` label) — the parsing groundwork proceeds; none changes public authority.
- `content_sync/` already contains the dtc-content adapter/parity/contract code
  (`content_sync/dtc_content/{adapter,parity,contract,media}.py`) and the live course-repository
  webhook path.

**Collision analysis (the crux the owner asked about):** the hosting migration (Phases 1–2, 6)
moves *where responses come from*; the ingestion migration changes *how the app builds them*.
They are **independent except at three joins**: (i) **#301 media** is claimed by both — sequence
it once, as Phase 3, and make the direct-sync media path (#276's family cutover) reuse the same
bucket/key scheme (D5); (ii) **cache freshness** (Phase 5 option c) should key invalidation to
direct-sync activation, so the invalidation-grant issue lands with or after the first family
cutover; (iii) **milestone 8** requires both. Verdict: **run the DNS/hosting lane now without
waiting for direct sync**; nothing in Phases 1–3 or 6a depends on it. Do not, however, cut any
public family to direct sync mid-Phase-2 — freeze content changes during the 48 h DNS window
(step 1.5) and the apex-swap day.

### 13.4 Ongoing update path — target state and its approval/rollback story

Target (per #226): a push to a registered source triggers a verified webhook → durable job →
locked immutable checkout → adapter parse → direct upsert; drafts/soft-deletes keep unpublished
content non-public; `SyncLog` + source status expose partial-sync visibility. The AISL sibling
runs exactly this shape in production (`~/git/ai-shipping-labs/_docs/content.md:7`; webhook
idempotency via delivery-id dedup; S3 media upload inside the sync job,
`integrations/services/github_sync/media.py`) — evidence the pattern operates well at this scale.
**Where DTC must be stricter than AISL:** AISL has no URL-preservation contract; DTC's sync must
keep the fail-closed validation receipts (#272), per-family parity gates against the checked
baselines (#276), and pinned-revision provenance — i.e., adopt AISL's *transport* (webhook + job
+ idempotent upsert) but not its *trust model* (sync-whatever-arrives). Approval: content goes
live on merge to the registered branch of the source repo — the approval gate moves to the
*source repo's* review, which is #226's explicit intent (website stays read-only toward GitHub).
Bad-change rollback: revert the source commit and re-sync (source-scoped soft-delete restores);
cache staleness bounded by Phase 5 TTLs until the invalidation grant lands. Integrity: pinning
survives as "every sync records the exact commit + receipt" rather than "the repo commits the
bytes".

### 13.5 Media, ongoing

After Phase 3, a new upstream image reaches production as: record appears in the projection (or,
later, direct-sync media dispatch) with `provenance.checksum` → `media-sync` uploads the delta
(checksum-diff makes "new images only" automatic; no full re-sync) → object served via the
`/images/*` behavior. The content-addressed key alternative (key = sha256) was considered and
rejected: public paths are the contract and identity keys need no lookup layer (D5). Consistency
check `media-verify` runs after every sync and in CI-scheduled form once the direct-sync path
owns uploads.

### 13.6 `courses.datatalks.club` data is a service migration — kept separate

Its "ingestion" is the CMP database import + delta + freeze choreography owned by
`migration-checklist.md:18-29`, spec 09 milestones 4–5 and M8.1-2, and the registration-count
aggregate rollout (spec 09:300-315). Nothing in this section's sync machinery touches it; the
only interaction is Phase 6's hostname routing. Do not let a "content sync" issue absorb learner
data — different consent, retention, and rollback rules apply (spec 09:209-246).

### 13.7 Genuinely undecided ingestion items (routed to decisions, not invented)

D12 (source rollout manifest — blocks every family cutover); #272's freshness/receipt-age policy
(HUMAN); Datamailer scope beyond the #290 inventory; whether docs/faq/podwiki go direct-sync
before or after the apex cutover (no dependency either way once D12 exists — but during Phase 2
the *legacy static* copies of those sections are what's live, so a direct-sync cutover of `/docs`
before milestone 8 would be invisible until the apex swap; sequence family cutovers after
milestone 8 unless the owner wants them proving out on `prod.` first).

---

## 14. Cost and security: cheaper than CMP, without breaching the floor

All figures are **estimates** (eu-west-1, on-demand, 730 h/mo, single region, current public
prices as of the author's knowledge — re-check at execution; the repo contains no billing data,
so Phase 0.3's Cost Explorer pull is the authority for actuals).

### 14.1 CMP baseline today (what "cheaper than CMP" is measured against)

| Line item | Evidence | Est. $/mo |
| --- | --- | ---: |
| Shared ALB (hours + ~1 LCU) | `main/cmp/app_prod.tf:138-147`, `alb_shared.tf` | 23 |
| Aurora PostgreSQL, provisioned `db.t3.medium` ×1 (serverless block inert — finding 1.2-5) | `main/cmp/db.tf:42-48` | 64 |
| Aurora storage + I/O (size unknown from repo) | `db.tf` | 5–15 |
| Fargate web, prod (0.25 vCPU / 512 MB ×1, 24/7) | `app_prod.tf:16-17,126` | 9 |
| Fargate web, dev (same, 24/7) | `app_dev.tf:16-17,130` | 9 |
| Scheduled Fargate (outbox every 5 min; reminders daily) | `cmp_datamailer_outbox.tf:13`, `cmp_deadline_reminder.tf:13` | 3–10 |
| NAT gateway hours (shared, CMP-dominated) | `main/common/network.tf:81-84` | 35 |
| NAT data processing (post-fix; was ~$173 in July 2026) | `main/common/vpc_endpoints.tf:1-14` | 5–25 |
| Bastion `t4g.nano` | `main/common/bastion.tf:26-36` | 4 |
| Public IPv4 ×~4 (ALB×2, NAT EIP, bastion) @ $0.005/h | — | 15 |
| Secrets Manager (~8 secrets @ $0.40) | `main/cmp/secrets.tf`, `observability.tf:76` | 3 |
| CloudWatch logs (7-day retention) + ~11 alarms + app metrics | `app_prod.tf:1-4`, `observability.tf` | 5–12 |
| Route 53 zone (`courses.`) | `app_prod.tf:163` | 0.5 |
| SES / alarm Lambda / slack-redirect API | `iam_ses.tf`, `observability.tf:121-145`, `main/slack-redirect` | <3 |
| **Total (estimate)** | | **≈ 180–225** |

Dominant items are exactly the usual suspects: **RDS ($70–80) > NAT ($40–60) > ALB ($30 with
IPs) > compute ($30)**. Verified rather than assumed: the July NAT spike and its free-S3-gateway
fix are documented in-repo (`vpc_endpoints.tf:1-14`) — NAT *data* was briefly the single biggest
line.

### 14.2 What the module would cost if the production fixture were copied literally

`tests/fixtures/website-production/main.tf` is a **policy fixture, not a sizing decision** — but
worth pricing to show why it must not be pasted: `db.r7g.large` Multi-AZ (~$370), 3× web
1 vCPU/2 GB x86 (~$108) + 2× worker 2 vCPU/4 GB (~$144), 365-day logs, `PriceClass_All`, single
NAT (~$40) → **≈ $700–900/mo**, 3–4× CMP. The module itself is cost-neutral: every size is an
input (`variables.tf` has almost no defaults), and the sandbox profile
(`sandbox/website/terraform.tfvars.example:54-80`: 0.25/0.5 web, no NAT, `db.t4g.micro`,
PriceClass_100) proves it runs small.

### 14.3 Proposed production profile (recommended inputs) and its estimate

| Line item | Proposed input | Est. $/mo |
| --- | --- | ---: |
| CloudFront (site + legacy + media) | `PriceClass_100`; ≤1 TB + ≤10 M req/mo sits inside the permanent free tier | 0–15 |
| WAF (managed + rate rules, spec 08:143-151) | 1 ACL + ~4 rules + volume | 10–15 |
| ALB + 2 public IPv4 | module as-is | 30 |
| Fargate **ARM64**: 2× web 0.5 vCPU/1 GB + 1× worker 0.5/1 | `task_cpu_architecture = "ARM64"` | 45 |
| RDS PostgreSQL `db.t4g.small`, single-AZ (D9), gp3 20→100 GB autoscale, 7-day backups | `database_*` inputs | 30 |
| NAT ×1 (`nat_gateway_mode = "single"`) + free S3 gateway endpoint in the new VPC | `network.tf:112-143` | 38–45 |
| Secrets ×6 (`secret_container_names`) + 1 KMS key | — | 3.4 |
| CloudWatch: 30-day retention, module alarms, **Container Insights off** | `log_retention_in_days = 30`, `ecs_container_insights_enabled = false` | 5–10 |
| Route 53 apex zone + queries | `main/dns` | 1–3 |
| S3 (media 150 MB + legacy 250 MB + versions) | — | 1–2 |
| maintenance / slack / courses-redirect Lambdas + HTTP APIs | negligible traffic | <2 |
| `dev.datatalks.club` service (D2: shared ALB/VPC/RDS, one 0.25 vCPU task + logs + secret — §9.2) | dev target group + listener rule + service | 8–12 |
| `main/relay` (Phase E: t4g.micro + IPv4 + two gp3 volumes + SES volume) | §11E E.8 | 15–18 |
| **Total (estimate)** | | **≈ 185–222** |

**Side-by-side:** proposed website + dev + email ≈ $185–222 vs CMP ≈ $180–225 → parity while
running *both* during the migration (combined ≈ $370–445 for that period, plus ≈ $13/mo for the
sandbox-Relay dual-run window until E.6 stage 2 — say this plainly), and decisively cheaper
after Phase 7, which removes the CMP line (≈ $180–225), the `sandbox/website` stack (≈ $65–85:
its own ALB + IPv4s + `db.t4g.micro` + two Fargate tasks + logs/KMS/secrets, per
`sandbox/website/terraform.tfvars.example` sizing), sandbox Relay (≈ $13–15), and sandbox
Datamailer (≈ $13–15). The owner's rejected second-stack dev would have added ≈ +$150/mo; the
shared-infra dev (§9.2) adds ≈ $10.

### 14.4 The security floor (not tradeable for savings)

Private task/database subnets in production (fixture asserts it,
`tests/fixtures/website-production/main.tf:27,125-127`); non-public encrypted RDS
(`modules/django-website/database.tf:15,21-22`); TLS in transit end-to-end incl.
CloudFront→ALB HTTPS with prefix-list-only ingress + `X-Origin-Verify` host/header rule
(`network.tf:246-262`, `edge.tf:57-100,291-294`); KMS-encrypted immutable scanned ECR
(`compute.tf:1-19`); WAF on the production distribution (spec 08:85-87 — note the sandbox runs
`cloudfront_web_acl_id = null`; production must not); OAC-only S3 with public access blocked
(`media.tf:33-40`); secrets by reference, never values in Terraform (spec 08:202-207); OIDC-only
IAM — `aws_iam_user`/`aws_iam_access_key` are forbidden by the policy suite
(`tests/policy/README.md:156-160`; the AISL `content_cdn.tf:144-150` user is the grandfathered
counterexample outside scan scope — do not replicate); bounded, redacted logging (spec 08:160-166).

Per-saving honesty table:

| Saving | Touches security/availability? |
| --- | --- |
| S3+CloudFront for legacy/media bytes | No — improves posture (OAC, WAF-able) |
| ARM64 Fargate | No (needs multi-arch image build) |
| PriceClass_100 | No security; non-US/EU visitors hit farther edges (latency, not availability) |
| Logs 30 d, Insights off | Ops tradeoff: shorter forensic window; keep security-relevant logs (WAF/CloudFront/ALB) on their own retention if 30 d is too short for incident response |
| Single-AZ RDS (D9) | **Yes — availability.** AZ failure ⇒ restore-from-backup RTO (hours). Named, deliberately accepted until course operations move |
| Single NAT (not per-AZ) | **Yes — availability.** NAT-AZ outage stalls egress (GitHub/Relay sync), not serving. Accepted |
| No NAT at all (public task subnets, sandbox-style `terraform.tfvars.example:29-30`) | **Breaches the floor for production** (private compute) — rejected; the $40 stays |
| Interface VPC endpoints instead of NAT | Not cheaper here: ~5 endpoints × 2 AZ × $0.011/h ≈ $80/mo vs NAT $40, and endpoints cannot reach GitHub/Relay anyway (external egress still needs NAT). Crossover favors endpoints only when NAT *data* is heavy — that was the July failure mode, already solved by the free S3 gateway endpoint. Rejected |
| Aurora Serverless v2 (0.5 ACU floor ≈ $48) | More than provisioned `db.t4g.small` ($26) at this steady load — a trap at this scale; also CMP's own serverless config is currently inert (finding 1.2-5). Rejected |
| Fargate Spot / scale-to-zero | For dev-shaped stacks only (`services_enabled=false` exists, `compute.tf:227`); not for prod web |

### 14.5 Highest-leverage savings, ranked (biggest saving : least risk)

1. **Dev shares production infrastructure** (D2, resolved — §9.2): ≈ $10/mo marginal instead of
   ≈ $150/mo for a second stack; fidelity tradeoffs accepted knowingly (§9.2).
2. **Decommission CMP + the sandbox account stacks at Phase 7** — removes ≈ $270–340/mo
   combined (CMP $180–225, `sandbox/website` $65–85, sandbox Relay $13–15, sandbox Datamailer
   $13–15); the plan's endgame saving.
3. **Right-size vs the fixture** (t4g.small single-AZ, 0.5-vCPU ARM tasks, 30-day logs) —
   ≈ $500–700/mo avoided vs literal fixture; availability tradeoffs named in 14.4.
4. **Bytes to S3/CloudFront free tier** (legacy site, media, later static) — keeps compute flat
   as traffic grows; free.
5. **ARM64** — ~20% of the Fargate line; free once the image builds multi-arch.
6. **Container Insights off + bounded metric cardinality** — $10–30; minor ops cost.
7. **PriceClass_100** — small but free given the audience; revisit only with data.

---

## 15. Migration scripts — complete specifications

Per `_docs/PROCESS.md`, each *new* script lands through a groomed issue; specs below are the
grooming input. **Reuse rule:** URL parity checking is NOT reinvented — the compatibility harness
already does it (`scripts/build_legacy_manifest.py` crawl/merge/compare/validate,
`_docs/compatibility/README.md:199-233`).

**15.1 `dns-export` [new, or manual]** — Purpose: capture the complete GoDaddy zone.
Inputs: GoDaddy API key/secret (env), domain. Output: `records.json` (name, type, TTL, values)
+ human zone file, written under `.local/migration-data/dns/`. Exit: 0 ok; 2 auth; 3 partial
(any pagination/type fetch failed ⇒ no output file). Idempotent (read-only). Dry-run: n/a.
Failure mode to guard: GoDaddy's API omits forwarding config — step 0.2 covers it manually.
BLOCKED on GoDaddy API access; the console export is an acceptable substitute.

**15.2 `dns-parity-check` [new]** — Purpose: prove Route 53 answers ≡ GoDaddy answers before and
after delegation. Inputs: `records.json` from 15.1; `--ns old|new|resolver <ip>`. Behavior: for
every record, query both sides (`dig @…`), normalize (sort multi-values, case-fold names, ignore
TTL by default, `--strict-ttl` optional), report diffs. Output: table + JSON report. Exit: 0
parity; 1 differences (listed); 3 query failure. Read-only, idempotent, no dry-run needed.
Failure modes: CNAME-flattening artifacts, forwarding-IP records that are *expected* to differ
post-Phase-2 — supports an explicit allowlist file with per-record justification (mirrors the
`approved-expectations` sidecar philosophy, `_docs/compatibility/README.md:126-135`).

**15.3 `legacy-static-sync` [new]** — Purpose: upload the four pinned trees (2.1 output) to the
legacy bucket. Inputs: workspace path, bucket, `--prefix-map` main=/,docs=/docs,faq=/faq,
podwiki=/podwiki, `--dry-run`. Behavior: walk the *generated trees* (which are the 2,937-file
contract — `generated-path-baseline.jsonl` is the allowlist seed,
`_docs/compatibility/README.md:163-165`); per file compute sha256, `HEAD` the object, skip when
`x-amz-meta-sha256` matches; else `put_object` with explicit `ContentType` (extension table +
overrides for `.rustkyll-manifest.json`, `.scss`, extensionless files) and `CacheControl`
(HTML/JSON/XML/txt 3600; assets 86400), `Metadata={"sha256": …}`. Prints
uploaded/skipped/deleted-candidate counts; `--delete` reports (never removes without
`--delete-confirmed`) keys not present in any tree. Exit: 0 clean; 1 uploads failed (lists);
4 tree-verification failed (refuses to run unless `build_pinned_legacy_sources.py --check`
passed in-process or `--skip-source-check`). Idempotent by construction (checksum diff); resumable
by re-run. boto3, never `aws s3 sync` (Content-Type guessing, space handling).

**15.4 Harness host-override [extension to existing]** — Purpose: run the *existing* production
crawler (`scripts/build_legacy_manifest.py production`, README:216-218) against the staging or
post-swap host instead of the live seeds' host, then the existing `merge`/`compare
--fail-on-difference`/`validate` chain unchanged. Change: an `--origin-override host` mapping
applied at request time while recording original seed URLs (the crawler is already DNS-checked/
IP-pinned per README:192-195, so this is a controlled extension, not a new tool). Exit codes and
checkpoint/resume semantics inherit from the harness (README:235-244). This is the Phase 2 gate:
**expected result = zero unexplained differences on the 2,937 preserve rows.**

**15.5 `media-sync` / `media-verify` [new, owned by #301]** — Purpose: upload and prove the
1,253 media records. Inputs: `content/public_projection/media.json` (authoritative), media tree
root, bucket, `--dry-run`. `media-sync`: for each record, key = D5 mapping of `record_key`, local
file = tree path (prefix-stripped, hole 8.2), set `ContentType` from the record (hole 8.4),
`CacheControl: public, max-age=86400` (hole 8.5), `Metadata={"sha256": provenance.checksum}`;
skip on metadata match. `media-verify`: GET every record's object, sha256 == `provenance.checksum`;
then `list-objects-v2` and reconcile both directions — unexpected keys and missing records are
each failures (hole 8.1 found one real orphan already). Exit: sync 0/1(failed uploads)/4(local
file missing for a record — refuse partial silently); verify 0/1(byte mismatch)/2(set mismatch).
Both idempotent and resumable (stateless re-run). Dry-run prints the full action plan including
the two space-key `put` calls verbatim so the operator can eyeball quoting.

**15.6 DNS rollback** — deliberately *not* a script: reverting NS at the registrar (1.5) and
alias swaps (2.5, 6a.3, 6c.2) are single console/terraform actions with rehearsed procedures;
automating registrar changes adds risk instead of removing it.

---

## 16. Risk register (ranked)

| # | Risk | Likelihood | Blast radius | Detection | Mitigation |
| --- | --- | --- | --- | --- | --- |
| 1 | Mail records lost/mangled in NS move (Google MX, SPF chain incl. `_spfm`, SES mail-from MX/SPF, `mail._domainkey` DKIM; Google's own DKIM selector still unfound) | Medium (one DKIM selector remains a known unknown) | **Org-wide email** — highest in plan | Test sends at +1 h/+24 h; DMARC reports (`p=none` today = monitoring works, but also means no enforcement backstop) | Phase 0 full export incl. `_domainkey` sweep; 15.2 parity gate blocks 1.5; GoDaddy zone kept intact ≥30 d for instant NS revert |
| 2 | **CMP teardown deletes the shared `datatalks.club` SES identity** — Terraform-owned by `main/cmp` (`iam_ses.tf:54-71`) while all Relay production sending binds to it (§11E E.3). Elevated: this is now on the *Relay critical path*, not just a Phase 7 hazard | High if the hand-off is skipped; zero once done | All outbound email, org-wide | canary send after every ownership/state change; `sesv2 get-email-identity` status | D14/step 7.0 procedure (`state mv` to the owner root, clean plans both sides, canary) executed before or alongside E.3; hard precondition for 7.1 |
| 3 | S3 rehost silently breaks URL shapes (trailing-slash/index, `/docs` 301, extensionless files, Content-Type, 404s) | High if done naively — the exact failure the OAC/website-endpoint difference causes | 2,937-URL preserve contract; SEO | 15.4 staging crawl vs `legacy-manifest.jsonl` | Function-based resolver from the baseline's real directory set (not dot heuristics); parity gate before apex swap; 10-min-TTL alias rollback to untouched GitHub Pages |
| 4 | SES identity suspension from bounce/complaint breach once Relay ramps — takes down **all** `@datatalks.club` mail, not just Relay's | Low with the E.6 ramp; real with big-bang bulk sending | Org-wide email (existential for the identity) | CloudWatch alarms on the Relay configuration-set metrics: warn 2% bounce / 0.05% complaint | E.6 graduated ramp (transactional first, bulk last); **abort trigger at 3% bounce / 0.08% complaint** = Relay kill switch + hold jobs + triage; suppression handling per E.5 audit |
| 5 | GoDaddy forwarders (`www`, `join`) die at NS move with no AWS replacement live | Certain unless pre-built | Slack onboarding funnel + www traffic | curl checks in 1.5 verify list | 1.2 (join custom domain) and Phase-2-first ordering for www; both built and verified pre-delegation |
| 6 | `prod.datatalks.club` (or the S3 copy + new site together) creates duplicate-content/canonical conflicts | Medium | Rankings for the whole editorial corpus | Search Console coverage + canonical monitoring (spec 02:280-291) | The four §9.3 controls (edge+app `noindex` on `prod.` for its entire life, restrictive robots, no `prod.` sitemap, apex canonicals everywhere); `prod.` 301s to apex after the swap (§9.4) |
| 7 | courses cutover breaks API consumers (scripts, certificate tooling, email links) | Medium | Learner-facing workflows | maintenance-Lambda 503 metrics; redirect-Lambda unknown-path metrics (spec 08:300) | Stage A′ keeps real compat endpoints on the new stack (no cross-host redirect for APIs, spec 02:152); redirect Lambda only after the consumer gate; 503-not-200 fix (1.2-6) |
| 8 | Relay defect surfaces under real load (auth/open-relay, SNS verification, idempotency, single-EBS backup gaps) — zero live clients to date, though the shared `mailing/` engine is production-exercised via CMP's Datamailer path and both suites pass (fork finding, §11E) | Unknown until the audit lands; narrowed to Relay's ~4,200 added lines + operational behavior | Email correctness/security | commissioned code audit + observability assessment (E.5) | **E.5 gate: no production sending until the must-fix list closes**; ramp stages keep early blast radius to allowlisted/low-volume traffic |
| 9 | Spec's no-Datamailer-send promise is **config-enforced, not code-enforced**: ~3,900 lines of live-callable client with reachable call sites (`courses/views/homework_confirmation.py:8-9,149`); only `deploy/task_definitions.py:36-42` env pins (`DATAMAILER_URL: ""`, dry-run `"1"`) keep it inert — one env var from live sends during exactly the kind of change an email migration makes | Medium during Phase E churn | Duplicate/unauthorized sends; spec breach | `core/tests/test_deployment_release.py:1001` guards the pins; env diff review on every deploy | E.2 hardening issue: fail closed in code (refuse non-empty `DATAMAILER_URL` outside tests or excise send call sites) when Effort 1 repoints to Relay |
| 10 | Cache poisoning / auth-content leak when Phase 5 turns caching on | Low with spec design, High without | Security incident | authenticated canary + poison canaries + log audit (10.6) | Fail-closed classifier, origin-response guard, `min_ttl=0`, TTL-zero one-step rollback; enable pre-cutover on low-stakes hosts first |
| 11 | Mixed-NS window serves divergent answers | Low (parity-gated) | Any record | 15.2 against multiple resolvers during window | Byte-parity before delegation + change freeze during the 48 h window |
| 12 | ACM validation stalls a cutover step | Medium (timing, not correctness) | Schedule | ACM console status | All certs DNS-validated *ahead* of their consuming step (1.2 dual-zone CNAME trick; 2.2 before 2.5) |
| 13 | Un-invalidatable wrong object (bad Cache-Control/Content-Type published, `CreateInvalidation` denied) | Medium | Up to max-age per object | 15.4/15.5 verify passes | Short/moderate max-age chosen everywhere (no `immutable` on mutable trees); worst case bounded at 24 h; guardrail-change path exists (10.4c) if ever needed |
| 14 | State-root blast radius (a bad apply in a broad root touches NS-critical records) | Low | DNS | plan review | D6 dedicated `main/dns` root; courses zone optionally imported for drift detection (0.4); untracked roots (`main/dtc-website`, `main/maintenance-page`) get committed + backends before any further apply |
| 15 | Shared dev/prod infra (D2): dev workload degrades prod via common RDS instance/ALB | Medium over time | prod latency/availability, not data (separate databases + credentials) | RDS connection/CPU/IOPS alarms; ALB target-health per target group (prod-only alarms, CMP precedent `observability.tf:55-58`) | §9.2 fidelity controls: 1-task dev, separate db + creds, explicit listener priorities, throwaway snapshot-restored instance for destructive rehearsals |
| 16 | Running both stacks doubles spend during migration | Certain, bounded | Budget | Cost Explorer + budget alarm | §14 sizing keeps the overlap ≈ $370–445/mo; Phase 7 harvest is scheduled, not aspirational |
| 17 | SES main account turns out to be in sandbox mode — learner mail blocked behind an AWS support request | Low (CMP mails learners today) but unverified | Phase E timeline (multi-day external lead) | E.1 / step 0.6 `aws sesv2 get-account` | Verification is front-loaded into Phase 0; if false, the support request files immediately and only E.6 waits |

---

## 17. What this runbook does NOT cover

- Application feature work, data imports, and their gates — owned by spec 09's milestones and
  `_docs/migration-checklist.md` (this runbook only placed hostname/edge mechanics into M8).
- The direct-sync programme's own design decisions (#272/#273/#276/#278) beyond the sequencing
  interface in 13.3; D12 stays with the owner.
- The per-purpose email approval catalog and template content (spec 09 M6/M8.6; #22, #290):
  Phase E covers Relay's *infrastructure* promotion, the SES identity hand-off, the audit gate,
  and the battle-testing ramp; which individual website purposes go live remains owned by those
  issues within the E.6 stage-3 framework. The Relay code audit and observability assessment
  themselves are commissioned separately — their findings gate E.6 but are not reproduced here
  until delivered.
- Git history rewrite for the media blobs (D11), Search Console property administration, Google
  Workspace/DKIM *configuration* (only DNS carriage of its records), registrar transfer execution
  (D7), and any change to `web.dtcdev.click`/`dtcdev.click`.
- Production secret values and their provisioning (spec 08:202-207: out-of-band, never here).

## Appendix A — DNS parity worksheet (seed for 15.1/15.2; live-observed 2026-09-02)

| Record | Type | Observed value (TTL) | Must survive Phase 1 | Changes later |
| --- | --- | --- | --- | --- |
| `datatalks.club` | A ×4 | 185.199.108–111.153 (600) | yes, verbatim | Phase 2.5 → CF alias |
| `www` | forwarder | 301 → apex (3600; HEAD→405) | via 1.3 strategy | Phase 2.5 → CF alias |
| `join` | forwarder | 301 → `tpnjn3u8kj.execute-api…` (3600) | replaced by 1.2 alias | — |
| `courses` | NS ×4 | awsdns set (3600) | yes, verbatim (D8) | fold-in later (D8) |
| apex | MX ×5 | Google (aspmx…) (3600) | yes, verbatim | — |
| apex | TXT | SPF `include:dc-aa8e722993._spfm…`; 2× `google-site-verification` | yes (D10) | SPF flatten (D10) |
| `dc-aa8e722993._spfm` | TXT | `v=spf1 include:_spf.google.com ~all` | yes unless D10 flattens | — |
| `_dmarc` | TXT | `v=DMARC1; p=none;` | yes | tighten later (out of scope) |
| `mail` | MX | `10 feedback-smtp.eu-west-1.amazonses.com` | **yes — live SES MAIL FROM** (`iam_ses.tf:67-71`); Phase E sending depends on it | ownership moves with D14/7.0; record itself unchanged |
| `mail` | TXT | `v=spf1 include:amazonses.com ~all` (SPF on the MAIL FROM domain — where SES SPF is evaluated) | **yes, verbatim** | — |
| `mail._domainkey` | TXT | published RSA DKIM key (classic single-selector TXT, not Easy-DKIM CNAMEs — §11E E.0) | **yes, verbatim** | — |
| other `*._domainkey.*` | TXT/CNAME | none found by probing beyond `mail._domainkey` — **0.1 inventory is still mandatory** (Google DKIM selector unknown) | yes | — |
| `dev.courses` | (in courses zone) | alias → shared ALB | untouched by Phase 1 | retired Phase 7 |
