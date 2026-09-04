# Production hosting, DNS, and content migration runbook

Status: draft for owner review — D1–D3, D13 and D18–D27 resolved by the owner 2026-09-02
(§3.1); remaining BLOCKED markers reference the still-open decisions in §3.2
Date: 2026-09-02 (updated same day after owner resolutions D1–D3, then again in the evening
after D18–D27, the costed infrastructure design, and the content-pipeline findings)
Scope: DataTalks.Club domain, hosting, edge, cost, and content-ingestion migration across
`DataTalksClub/website` (this repo) and `DataTalksClub/aws-infra`
Relationship to existing documents: complements — does not replace — the milestone roadmap in
[`_docs/specs/09-migration-rollout-roadmap.md`](../specs/09-migration-rollout-roadmap.md) and the
data-migration checklist in [`_docs/migration-checklist.md`](../migration-checklist.md). Those own
*what* is migrated (data, application behavior, milestone gates); this runbook owns *where the site
is hosted, how DNS moves, what it costs, and the exact operational sequence*. Section 1 audits the
existing documents and records where they are stale or contradictory.

**Companion design documents (linked, never restated here).** Four documents now carry detail this
runbook deliberately does not duplicate. Where one of them contradicts a passage below, the
companion wins and this runbook is corrected:

- [`_docs/design/specs/data-migration-architecture.md`](../design/specs/data-migration-architecture.md)
  — the CMP database import, the local dataset, the curriculum-format split, and the cohort
  identity model (§13.9 defers to it).
- [`_docs/design/specs/unit-content-pipeline.md`](../design/specs/unit-content-pipeline.md) — the
  course-repository → unit-page path, its twenty measured divergences, and the unified
  `cohorts/<year>/<module>/` layout migration (§13.10 defers to it).
- [`_docs/design/specs/script-inventory.md`](../design/specs/script-inventory.md) — what every
  script under `scripts/` is for and which are deletion candidates (§15 defers to it for the
  *existing* inventory; §15 still specifies the *new* migration scripts).
- The costed Terraform design for website prod/dev plus Relay prod/dev, currently at
  `.tmp/infra-design-dev-prod-relay.md` (1,129 lines, design-only, no Terraform written). §14 is
  now a summary of it, not an independent estimate. **That file lives outside version control;
  giving it a home under `_docs/design/specs/` is an open item (§3.2 D36).**

**A structural note, recorded rather than acted on.** This runbook now carries three things with
different lifetimes: the hosting/DNS/edge cutover *sequence* (§§0–12, 15–17 — genuinely a
runbook, consumed step by step by an operator), a cost-and-topology *design* (§14, now a summary
of a 1,129-line companion), and a content/data-ingestion *programme* (§13, ~350 lines that mostly
defer to two companions). The recommended re-cut, when someone has an issue to hang it on: keep
§§0–12 and 15–17 as the runbook; move §14 wholesale into
`_docs/design/specs/infrastructure-cost-and-topology.md` (D36) leaving a one-table summary and a
link; and reduce §13 to its migration-sequencing interface, letting
`data-migration-architecture.md` and `unit-content-pipeline.md` own the detail they already own.
Doing that now, mid-migration, would churn every §-reference in issue #309 and in the phase
handoffs, so it is proposed, not performed.

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
D3 (two courses Lambdas), D13 (Relay is the sender), and — 2026-09-02 evening — D18 (unified
`cohorts/<year>/<module>/` curriculum layout), D19 (modules-format rollout scope), D20
(convention over configuration), D21 (`zoomcamp-template` owns the convention), D22 (`dev.` runs
fake data), D23 (Datamailer suppression copy is a first-send precondition), D24 (no homework
slug rewrites), D25 (six cohorts skipped), D26 (the cost bar is website-only), D27 (apex-redirect
SEO impact accepted) — §3.1. Open, each gating the steps that cite it: D4 cache freshness ·
D5 media keys · D6 zone root placement · D7 registrar transfer (deferred) · D8 courses-zone
fold-in · D9 RDS Multi-AZ timing · D10 SPF strategy · D11 git history rewrite · D12 direct-sync
source manifest · **D14 SES identity owner (on the EMAIL critical path)** · D15 Relay sandbox
data (clean start recommended) · D16 Luma-exporter home · D17 Luma refresh owner/cadence ·
**D28 SITE.md rollout shape (two incompatible implementations in flight)** · D29–D36 from the
costed design (dev-database placement, Relay instance size, transactional volume, NAT form,
policy-scan sequencing, `modules/relay-host`, bastion retirement, design-doc home) — §3.2.

**Phase 0 — inventories and prerequisites (MAIN; read-only)** — details §5

- 0.1 **[G]** Export the complete GoDaddy zone, every record incl. `_domainkey` sweep
- 0.2 **[G]** Record the `www`/`join` forwarding configurations
- 0.3 **[P]** Pull the 3-month Cost Explorer baseline
- 0.4 **[P]** Identify + record the courses hosted zone (`Z00653771…`)
- 0.5 Owner settles open decisions D4–D10
- 0.6 **[P]** Record the live SES **sending quota and maximum send rate** (= E.1); production
  access already exists — this step establishes the two numbers a 130,000-recipient campaign
  has to clear
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
- 9.4 **[P]** **[OW-SEO]** Stage-2 apex swap after the 6-item gate (parity on `prod.` vs live
  apex, M8 steps 1–4, caching evidence, rehearsed rollback, **data freshness: sources re-synced
  ≤ 72 h + ≥ N future-dated events — §13.8**, **full-fidelity CMP import proven on a disposable
  target — §13.9, currently unbuilt**); alias rollback 1–10 min; S3 legacy warm ≥30 d

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

- E.1 **[P]** = step 0.6 (SES quota + send rate recorded against the 130,000-recipient campaign)
- E.3 **[P]** **[B:D14 hand-off]** `main/relay` root; bind to the `datatalks.club` identity + a
  Relay-owned configuration set; **outbound-only — inbound stays unconfigured (audit key
  mitigation)**; `--memory` limits (1 GiB host); backups = part of done (C3); **observability on
  the django-website injected-alarm contract, decided before the Terraform is written**; deploy dark
- E.4 Clean-start the data volume (D15); sandbox Relay stays as dev Relay
- E.5 GATE — both assessments **delivered**; **[B:remediation]**: code audit verdict "not ready
  to send production email" — minimum fix list C1–C4 + H2 + H5 via groomed issues (E.5a); plus
  observability items 1–9 (E.5b; 1 + 1b < 1 day → 15-min detection)
- E.6 **[B:E.5a minimum list]** Ramp: stage −1 shadow week (`dry_run` at volume), then stages
  0–4; hard precondition: canary + `outbox_*` alarms re-pointed at Relay, live subscriber
  proven; abort 3% bounce / 0.08% complaint
- E.7 Datamailer stays read-only; fixes the 7.4 teardown order

**Phase 7 — decommission and harvest (CLEANUP; after all rollback windows)** — details §12

- 7.0 **[P]** SES identity `state mv` out of `main/cmp` (**execute early, with E.3**)
- 7.0a **[P]** Rehome the email canary (health probe + alarms) out of `main/cmp` into `main/relay`
- 7.1 **[P]** **[OW]** Destroy `main/cmp` (final Aurora snapshot is the point of no return)
- 7.2 **[OW]** Retire the four GitHub Pages deployments
- 7.3 Retire `main/legacy-site` after the 9.4 window
- 7.4 Sandbox preconditions; **corrected** teardown order: sandbox Relay → `sandbox/website` →
  CMP (7.1) → sandbox Datamailer
- 7.5 **[P]** Remove the cross-account media-bucket read (PR #30 grant)
- 7.6 **[P]** **[OW]** Destroy `sandbox/website`; retire its OIDC pipeline + policy fixtures
- 7.7 **[P]** Cost-Explorer harvest vs §14 (expected ≈ $270–340/mo freed)

Reference layers (not steps): §13 content-ingestion programme (parallel, own gates; §13.10 is
the course-repository curriculum layout) · §14 cost and security analysis, now split into
**Budget A (website, measured against CMP)** and **Budget B (Relay, judged separately)** per
D26 · §15 script specifications · §16 risk register · §17 non-goals · Appendix A DNS worksheet.

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
| The editorial source/projection inventory (since deleted) | Editorial source/projection evidence | **Historical snapshot only** — see 1.2 item 1 |
| `aws-infra/docs/inventory.md` | Account/root inventory | Dated 2026-06-24 (line 3); predates `sandbox/website`, `main/dtc-website`, `main/maintenance-page`; one DNS claim is imprecise (1.2 item 4) |
| `aws-infra/docs/state-boundaries.md` | State-root layout rules | Sound; used for the zone-placement decision D6 |
| `aws-infra/main/common/MIGRATION.md` | Completed 2026-06-24 shared-network adoption | Done; historical record |
| GitHub issues #38/#272/#273/#276/#278 (direct-sync programme), #291–#294/#290 (adapters), #301 (media), aws-infra PR #30 | Content-ingestion future, media move | See sections 8 and 13; the programme epics are explicitly BLOCKED by their own PM notes |

Conclusion: **extend, don't replace.** No existing document is "the plan" for hosting/DNS/cost;
this runbook is the missing piece and defers to spec 02/09 wherever they already rule.

### 1.2 Soundness findings (verify-don't-trust results)

1. **Stale audit evidence, since deleted.** The 2026-08-14 editorial source/projection
   inventory and its validator pinned `media.json = 6b6670d0…` and `podcasts: 205` against a
   frozen snapshot, while the live projection manifest carries different values
   (`content/public_projection/manifest.json`, `artifacts`/`counts`). Both the audit and its
   validator have been removed; read counts from the live manifest.
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
  gate production sending. Both commissioned assessments have since landed — the code audit's
  verdict is "not ready to send production email" — so the gate is now the E.5a/E.5b
  remediation lists (§11E E.5, BLOCKED(remediation)).

#### Resolved later the same day (2026-09-02, evening session)

- **D18 — RESOLVED: one curriculum layout, `cohorts/<year>/<module>/module.yaml`.** Owner:
  *"let's unify. it should be in cohorts."* Verified state of the three module-format repositories
  on 2026-09-02: `llm-zoomcamp` already conforms — 7 `module.yaml` files under
  `cohorts/2026/<module>/`; `machine-learning-zoomcamp` holds 9 at the repository root; and
  `ai-dev-tools-zoomcamp` holds 4 at the repository root. The latter two move. The importer
  accepts both shapes today (`content_sync/course_repository.py:641-643`, `:657-674`), so this is
  a repository-content change, not a website change. **No public URL moves:**
  `/courses/<family>/<year>/modules/<module>[/<unit>]` is built from the module *directory name*
  and the unit *filename stem* (`courses/urls.py:44-54`), and
  `_docs/compatibility/generated-path-baseline.jsonl` (2,937 rows, re-counted) contains no
  `/courses/<family>/<year>/modules/…` row at all. Migration mechanics, the nine `homework.md`
  collisions, and the 103 `](../)` back-links that change meaning with depth are specified in
  `unit-content-pipeline.md` §5.
  **⚠️ This reverses an earlier recommendation.** The 2026-09-02 session handoff (since deleted)
  advises reverting `llm-zoomcamp@c04db93` because it contradicts
  `llm-zoomcamp/cohorts/README.md`. Under D18 `c04db93` is **correct**; the README is what is
  wrong and must be rewritten. That handoff line is superseded by this entry. Verified 2026-09-02:
  `c04db93` and `machine-learning-zoomcamp@1aa481e` are **now on their repositories' `origin/main`
  (`944b35e`, `61fdaab`)** — the "unpushed local commits" blocker recorded earlier today is
  closed; what remains unpushed in all three repositories is the newer `SITE.md` commit (D28).
- **D19 — RESOLVED: scope of the modules-format rollout.** Every course adopts the modules format
  **except `stock-markets-analytics-zoomcamp`, which stays `legacy` for 2026**.
  `data-engineering-zoomcamp` is a **conversion candidate to assess, not a commitment**. Verified:
  `data-engineering-zoomcamp`, `mlops-zoomcamp` and `stock-markets-analytics-zoomcamp` carry no
  `module.yaml` and no `course.yaml` today.
- **D20 — RESOLVED: convention over configuration for unit and page structure.** The structure of
  unit notes is consistent across courses and **derived by convention**, not declared per course.
  Consequence for this plan: every per-course special case in the importer is a cost to be
  justified, not a feature. `unit-content-pipeline.md` §2 already separates the five deliberate
  special cases from the four heuristics; the heuristics are the ones D20 targets.
- **D21 — RESOLVED: `DataTalksClub/zoomcamp-template` is the home of that convention.** Verified
  contents: `STRUCTURE.md`, `docs/conventions.md`, and `templates/root-README.md`,
  `templates/module-README.md`, `templates/cohort-README.md`. **Any layout change is incomplete
  until the template repository carries it** — that repository is part of the D18 change set, not
  a follow-up.
- **D22 — RESOLVED: `dev.datatalks.club` carries fake data, never production-derived.** This
  settles the largest open risk in the shared-dev design: dev Relay never sees a real address, so
  the shared dev database stays genuinely low-stakes and the §9.2 fidelity tradeoffs stand as
  written. It answers the costed design's Q2 outright. Consequence: the full-fidelity CMP import
  (§13.9) is *never* pointed at dev — its rehearsal target is a disposable snapshot-restored
  instance, which §13.9's dry-run gate already requires.
- **D23 — RESOLVED: Datamailer is the source of Relay's suppression state, and copying it is a
  precondition of the first production send.** Not a Phase 7 cleanup item. Two consequences, both
  load-bearing:
  (i) **sandbox Datamailer must survive until that copy is done**, which reinforces — and further
  constrains — the 7.4 teardown ordering trap (§12 7.4, corrected below);
  (ii) D15's clean start remains correct as a *deployment* decision (no test-era rows migrate)
  but is no longer a complete answer to state: suppression and unsubscribe records are the one
  category that must arrive before stage 1 of the ramp, because an empty suppression list
  re-mails people who unsubscribed or hard-bounced, which feeds straight into the SES reputation
  thresholds of E.6/risk #4. Where the export proves impractical, seed **SES account-level
  suppression** instead — worth doing regardless, since today nothing outside Relay's own
  PostgreSQL holds this state.
- **D24 — RESOLVED: no homework slug rewrites.** The `^hw(\d+)$` → `homework-0N` transform is
  removed (`1f4be1a`), and the `homework_slug_overrides` hook went with the local importer it
  belonged to: there is one course path now and it has no override table. A repository homework
  binds by the `content_id` its YAML declares; where a pre-existing unowned row already holds the
  repository's slug the import refuses rather than guessing, and pairing a CMP row with a
  repository row is `courses/services/cmp_content_import.py`'s job. Rationale
  recorded in the commit: the transform was right for `llm-zoomcamp-2026` and wrong for
  `ml-zoomcamp-2026`, whose `hw01…hw10` already match its repository modules exactly; deriving one
  identity two ways is what split the AI Dev Tools course family and needed migration 0052 to
  repair.
- **D25 — RESOLVED: six cohorts stay skipped for now.** `ai-bootcamp-2025`, `ai-hero-2025`,
  `ai-hero-2026`, `sma-zoomcamp-2026`, `ai-buildcamp-2`, `ai-buildcamp-3` — enumerated with their
  reasons at `courses/services/cmp_content_import.py:59-71`, so a missing cohort is a decision
  someone can revisit rather than an unmatched branch. **Four need only a mapping entry to
  return. `ai-buildcamp-2`/`-3` do not:** their `2` and `3` are **edition numbers, not years**,
  which the family+year cohort model cannot express. That one needs design work and is not a
  data-entry task.
- **D26 — RESOLVED: the cost bar applies to the website only.** Owner: *"cost for dtc website
  should be equal to cmp or less. for relay it's different."* §14 is restructured accordingly into
  **Budget A** (website prod + dev + all shared plumbing, measured against CMP's $180–225) and
  **Budget B** (Relay prod + dev, judged on its own merits). A single blended total is no longer
  the unit of judgement — but §14.3 still states the combined invoice, because the invoice does
  not respect budget boundaries.
- **D27 — RESOLVED (recorded earlier the same day, re-verified here): the SEO impact of the apex
  redirect is accepted; no action.** The `/docs/`, `/faq/`, `/podwiki/` 302s to
  `datatalksclub.github.io` stay as they are. Recorded in full with the observed facts at §7
  C3/C4/C5; still present and consistent. This decision touches **neither C1 (the compatibility
  contract no longer describes reality) nor C2 (GitHub Pages became a permanent dependency)**,
  which remain open.

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
  or names data worth carrying. Audit interaction (C3, §11E E.5a): clean start is fine
  precisely because sandbox data is disposable — but the *production* volume will hold the
  non-reconstructible suppression list, so a backup/restore story is part of `main/relay`'s
  definition of done before any real sending, not an afterthought.
- **D16 — Where does the Luma exporter live?** The tool producing the events dump sits in a
  personal temp directory (`/home/alexey/tmp/luma-exporter/` — `uv` project with Makefile,
  README, tests), outside both repos (§13.8). A required migration input with no home in
  version control is a risk in itself. Options: its own `DataTalksClub` repository (it holds no
  PII itself — the *outputs* do); a directory in the website repo. **Recommendation: a small
  dedicated repo**, keeping PII-bearing outputs strictly in `.local/` per the existing pattern.
- **D17 — Who owns re-running the Luma export, on what cadence, once the site is live?** An
  events site whose event source is a manually-run local script is a standing staleness risk —
  exactly the failure verified in §13.8 (newest event 2026-08-31, zero future events on
  2026-09-02). Options: (a) named human owner + calendar cadence (weekly + before each cutover
  gate); (b) scheduled automation (needs D16 resolved first, plus credential custody for the
  Luma token — which is deliberately not stored anywhere in-repo,
  `migration-checklist.md:80-85`). **Recommendation: (a) now, (b) as a groomed issue after
  D16**; either way the §9.4 freshness check is the enforcement backstop.
- **D28 — SITE.md rollout shape. Two incompatible implementations are in flight and the owner has
  not chosen.** Both agree that a course description comes from a repository's `SITE.md` rather
  than its `README.md`; they disagree on the `course.yaml` contract and on the copy.

  | | Lane 1 | Lane 2 |
  | --- | --- | --- |
  | Where | committed to **local `main`** in `llm-zoomcamp`, `machine-learning-zoomcamp`, `ai-dev-tools-zoomcamp` (unpushed) | **six PRs opened from `origin/main`** |
  | `course.yaml` | **deletes** `description_path` | **repoints** `description_path` at `SITE.md` |
  | Copy | short — 128 / 162 / 141 bytes, measured | long — ~551–635 characters, sourced from `content/docs_projection.json` |

  **The website side has already landed, and it decides part of this.** Verified on this branch
  (`b81d825`): `content_sync/course_repository.py:711-741` no longer lists `description` or
  `description_path` in the `course.yaml` allowed-key set, and `_strict_mapping` (`:297-315`)
  fails closed on any unknown key. So **Lane 2's repointed `description_path` would be rejected
  outright** with `unknown_key /description_path`, and Lane 1's deletion is what the parser now
  requires. An earlier note held the opposite — that deleting both keys would make an equality
  hold and fail the import closed. That is **not** what the code does: `_parse_site_description`
  (`:767-796`) reads `SITE.md` alone, returns `None` when it is absent, and the importer then
  leaves the stored description untouched (`curriculum_import.py:339-343`). That "leave it alone"
  branch is what keeps `data-engineering-zoomcamp`, `mlops-zoomcamp` and
  `stock-markets-analytics-zoomcamp` intact, and it is permanent behaviour rather than a
  transitional fallback.
  **Recommendation: Lane 1's convention (drop `description_path`) with Lane 2's copy** — the short
  strings are noticeably thinner than the catalogue copy the site already publishes.
  Two verified facts to carry into the decision:
  (i) `origin/main` in all three repositories still carries `description_path` in `course.yaml`
  and has **no `SITE.md`**, so as things stand a production webhook import of any of the three
  fails closed on the unknown key — the repository change is not optional once the parser change
  ships, and the two must be sequenced together;
  (ii) `course.yaml` exists on `origin/main` for **all three** module-format repositories (llm, ml
  **and** ai-dev-tools) and for none of `data-engineering-zoomcamp`, `mlops-zoomcamp`,
  `stock-markets-analytics-zoomcamp`. An earlier note claiming `course.yaml` existed only in
  unpushed commits, and only for llm/ml, is wrong.
  The manifest builder that used to keep `SITE.md` out of the snapshot no longer exists: both
  transports now read the repository's whole exported tree, so `SITE.md` arrives with everything
  else and the parser reads it as the course description.
- **D29 — Dev-database placement.** A dedicated `db.t4g.micro` for the shared dev database
  (≈ $15.68/mo) versus two logical databases on the production website instance ($0). The costed
  design declines the free option deliberately: the $15.68 buys keeping a dev EC2 host out of the
  production database's security group. Budget A passes either way. **Recommendation: dedicated
  instance.**
- **D30 — Relay production database size.** `db.t4g.medium` (≈ $52.56/mo) versus `db.t4g.small`
  (≈ $26.28). The binding constraint is the index working set against RAM, which crosses 2 GiB
  inside a year at 130k/week. **Recommendation: `db.t4g.medium`**; `t4g.small` is defensible only
  once pruning is proven before go-live.
- **D31 — Transactional email volume.** The costed design assumed 30,000/month as a placeholder.
  It moves postage by roughly $1 per 10,000 messages, so it matters far more for database growth
  than for the bill. **Recommendation: confirm the real number before sizing storage.**
- **D32 — NAT gateway (≈ $41.09/mo) or NAT instance (≈ $7.46)?** Budget A clears its bar with the
  gateway. **Recommendation: gateway**; the instance stays a documented lever, not a default.
- **D33 — When does `main/dtc-website` join the policy scan?** Adding it turns its existing,
  legitimate `cloudfront:CreateInvalidation` grant
  (`main/dtc-website/legacy_static_site.tf:459-469`) into a violation. **Recommendation: add the
  three new roots first and defer `main/dtc-website` until the legacy publisher retires at 7.3** —
  sequenced with the D4 invalidation decision, not separately.
- **D34 — Extract `modules/relay-host`, or duplicate two ~200-line Relay roots?**
  **Recommendation: extract.** Duplication guarantees dev/prod Relay drift, and the drift lands on
  the stack that sends production email.
- **D35 — Retire the `main/common` bastion in favour of SSM Session Manager?** −$6.70/mo, and it
  removes a 24/7 SSH-exposed host whose allowlist is one hand-maintained home IP
  (`main/common/main.tf:6-10`). All four new workloads are SSM-only already.
  **Recommendation: yes, as a separate small issue** — not on this plan's critical path.
- **D36 — Where does the costed infrastructure design live?** It is currently
  `.tmp/infra-design-dev-prod-relay.md`, outside version control — the same class of risk as D16's
  homeless Luma exporter. **Recommendation:
  `_docs/design/specs/infrastructure-cost-and-topology.md`, with §14 reduced to a summary that
  links it.**

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
         and battle-tested; Datamailer stays read-only         Phase 0; E.5 BLOCKED(remediation)
Phase 7  Decommission GitHub Pages + legacy S3 (after its     ── after rollback windows close;
         window) + sandbox Relay → sandbox/website → CMP →         7.0 SES hand-off precedes 7.1;
         sandbox Datamailer (corrected order, §12 7.4)             Datamailer retires LAST
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

**0.6 [AWS console/CLI] [CREDS] Record the live SES sending quota and maximum send rate** — run
Phase E's step E.1 now (`aws sesv2 get-account --region eu-west-1`, §11E). Production access
already exists on the main account and the owner states the quota has been raised above the
default, so this is a **verification step, not a blocker**. What it establishes is two numbers,
not one, because a weekly newsletter is bursty: the **rolling-24-hour sending quota**
(`Max24HourSend`, which must clear ~130,000 recipients in a day *alongside* transactional
traffic) and the **maximum send rate** (`MaxSendRate`, recipients/second — 130,000 arrives in one
window, not spread over 24 h, so the rate decides whether a campaign takes minutes or hours).
If both clear the volume, nothing further is needed. If either does not, a quota increase is an
AWS support request with multi-day external lead time and should be raised immediately — which is
why the step still sits ahead of everything that does not depend on it.

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

> ## ✅ EXECUTED 2026-09-02 — and the executed design differs from the plan below
>
> The apex is live on S3/CloudFront. `curl -sI https://datatalks.club/` returns 200 with
> `x-amz-version-id` and `x-amz-server-side-encryption: AES256`, so the main tree is being
> served from the bucket.
>
> **What changed:** only the **main site tree** was rehosted into S3. The other three trees —
> `/docs/`, `/faq/`, `/podwiki/` — were **not** rebuilt and uploaded. A CloudFront
> viewer-request Function now answers them with `302` to `datatalksclub.github.io`:
>
> ```
> GET https://datatalks.club/faq/ai-dev-tools-zoomcamp.html
>   → HTTP/2 302, x-cache: FunctionGeneratedResponse from cloudfront
>   → location: https://datatalksclub.github.io/faq/ai-dev-tools-zoomcamp.html
> ```
>
> Same for `/docs/…` and `/podwiki/…`, verified live.
>
> **The split, measured against `generated-path-baseline.jsonl`:**
>
> | Tree | `source_id` | Rows | Now |
> | --- | --- | ---: | --- |
> | Main site | `dtc-main-site` | 2,301 | **Served from S3** (200) |
> | Docs | `dtc-docs` | 174 | 302 → `datatalksclub.github.io` |
> | FAQ | `dtc-faq` | 152 | 302 → `datatalksclub.github.io` |
> | Podwiki | `dtc-podwiki` | 310 | 302 → `datatalksclub.github.io` |
>
> **636 rows — 21.7% of the contract — now redirect instead of returning 200.**
>
> ### Consequences that must be worked through
>
> **C1 — the compatibility contract no longer describes reality.** All 636 rows carry
> `classification: "preserve"` and `expected_status: 200`. The baseline schema hard-codes
> `const: "preserve"`, so a redirect is not expressible in the baseline at all; it can only be
> recorded through the digest-bound approved-expectations sidecar. Until that is done, the
> Phase 2.4 parity crawl reports 636 regressions and **cannot function as a gate** — neither
> for this phase nor for the 9.4 stage-2 swap that depends on it. This is the highest-priority
> follow-up in the phase.
>
> **C2 — GitHub Pages is now a permanent production dependency, not a rollback net.** Step 2.6
> ("leave all four Pages deployments live and frozen for ≥30 days") and step 7.2 ("retire the
> four GitHub Pages deployments") are both invalidated for three of the four. `docs`, `faq` and
> `podwiki` must stay **live and maintained indefinitely**, because the apex has no copy of
> their content. Only the main-site Pages deployment is a disposable rollback target.
>
> **C3/C4/C5 — SEO impact accepted. Owner decision, 2026-09-02: no action.** These pages are
> not meaningfully indexed, so the search consequences of the redirect do not need mitigating.
> Recorded here with the observed facts so the finding is not re-raised by a later audit:
>
> - `/podwiki/` on github.io → `<link rel="canonical" href="https://datatalks.club/podwiki/">`
> - `/docs/…` on github.io → `<link rel="canonical" href="https://datatalks.club/docs/…" />`
> - `/faq/…` on github.io → **no canonical tag** (152 rows), and
>   `https://datatalksclub.github.io/robots.txt` 404s, so nothing restrains crawling there
> - the status code is `302`, which keeps the source URL canonical rather than consolidating
>   ranking onto `datatalksclub.github.io`; leaving it as-is is also the cheaper option if
>   these trees are ever served from the apex again
> - the apex `robots.txt` still advertises `Sitemap: https://datatalks.club/podwiki/sitemap.xml`,
>   which now 302s off-domain
>
> **This decision does not touch C1 or C2**, which are contract-integrity and availability
> problems rather than search problems.
>
> **C6 — issue #306 is superseded.** It records that 310 legacy `/podwiki/*` URLs would 404 at
> cutover. They now redirect instead, so the 404 risk is gone; what replaces it is C1 and C2.
> Close or rewrite #306 rather than leaving it to describe a condition that no longer exists.
>
> **C7 — the redirect must survive the stage-2 swap.** When the apex alias moves from this
> distribution to the Django site (§9.4), these 636 paths break unless the new distribution
> carries the same Function, or Django itself answers them. Add this to the 9.4 gate.
>
> ### What in the plan below still applies
>
> Steps 2.1–2.3 remain accurate **for the main tree only** — the pinned rebuild, upload with
> explicit Content-Type and Cache-Control, and the twice-run idempotency check all still stand
> for `dtc-main-site`'s 2,301 rows. The rest of the section is retained as the record of what
> was designed and why, and because the directory-resolution and Content-Type traps it
> documents still govern the tree that *is* in S3.

**Goal (as originally planned):** `datatalks.club` serves the four pinned legacy trees from S3
behind CloudFront with all 2,937 baseline URLs behaving identically; GitHub Pages remains the
instant rollback.

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
exists twice (S3 legacy on apex, Django on `prod.`). Duplicate-content risk (#7 in §16) is
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
5. **Data-freshness gate (§13.8):** events, cohorts, and content re-synced from their sources
   within ≤ 72 h of the swap, and the concrete check "**at least N future-dated events exist**"
   passes (owner-set N, floor 1). Verified 2026-09-02: the newest event anywhere in the current
   projection is 2026-08-31 — without this gate the new site launches with an empty
   upcoming-events section and superseded cohorts (#307), a day-one visible failure. The same
   check joins the post-swap monitoring set.
6. **Full-fidelity import proven (§13.9):** the complete CMP database import — all accounts,
   enrollments, submissions, real PII — has been rehearsed end-to-end at least once against a
   disposable snapshot-restored target, its verification suite passed (row counts, referential
   integrity, identity-free spot checks), and the target destroyed. Every rehearsal to date
   proved only the *sanitized* path; this gate exists because the path that actually runs on
   migration day is currently unbuilt.

**Mechanism — the DNS change alone is necessary but not sufficient.** ⚠️ Correction to the
earlier text: CloudFront refuses an alternate domain name that another distribution already
claims, and **both apex names are claimed by the legacy distribution today**
(`main/dtc-website/legacy_static_site.tf:269`). Pointing a second distribution at
`datatalks.club` returns `CNAMEAlreadyExists`. The supported procedure is:

1. add the alias to the *new* distribution with `aws cloudfront associate-alias
   --target-distribution-id <new> --alias datatalks.club` (and again for `www`), which AWS
   permits because the requester demonstrably controls the DNS for the name — this moves the
   claim without an outage;
2. **then** flip the Route 53 alias records to the new distribution;
3. **then** remove both names from the legacy distribution's `aliases` in Terraform, so the next
   plan is clean.

Doing step 3 first is what turns a rehearsed swap into an outage: the legacy distribution stops
answering for the apex before the new one is allowed to. Rehearse the whole three-step sequence
on a low-stakes hostname (gate item 4). A related module question is open as D-level detail:
either add an `additional_aliases` input to `modules/django-website` for a fully graceful swap,
or simply flip its `hostname` input and accept a minutes-long planned gap on `prod.` — which is
`noindex` staging for its entire life, so the gap costs nothing. **Recommendation: flip
`hostname`**, and keep a module contract change off the critical path.

**True rollback latency:** in `main/dns`, change the apex A/AAAA **alias** records
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

Product work ⇒ groomed issues (PROCESS), merged to `main` as
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
containerized gunicorn deploy with a post-deploy smoke test (`scripts/smoke_test_relay.py`
**in the Relay repository**, beside `smoke_test_sandbox.py` and `smoke_test_staging.py` — it
does not exist in this repository and is not expected to)
that Datamailer lacks. Both test suites pass — Datamailer 493, Relay 520, a strict superset —
so the owner's "never been tested" premise does not hold as stated, though the delivered code
audit qualifies the comfort: at least the retry-critical send path has passing tests that cover
dead code rather than the live path (E.5a C1). What *is* true, and what
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

### E.1 [AWS console/CLI] [CREDS] — record the live SES quota and send rate (EARLY, in Phase 0)

> ## ✅ ANSWERED 2026-09-02 — this step is complete
>
> The owner queried SES directly for account `387546586013` in **eu-west-1**:
>
> | | |
> | --- | ---: |
> | `Max24HourSend` (rolling 24 h quota) | **150,000** |
> | `MaxSendRate` | **50/second** |
> | Sent in last 24 h | 4 |
> | Production access | Enabled |
> | Account status | Healthy, sending enabled |
>
> So the increase from the 2026-08-09 baseline (50,000/24 h, 14/s) **was granted in part** —
> 3× the daily quota and 3.6× the rate, short of the 200,000/200 requested. This closes the
> follow-up item that support document left open, and supersedes every figure below.
>
> **Against a 130,000-recipient campaign:**
>
> - **Send rate is comfortable.** 130,000 ÷ 50/s ≈ **43 minutes** for the burst.
> - **The daily quota is the binding constraint, with a thin margin.** One newsletter leaves
>   **20,000 recipients** (13% of quota) for that day's transactional mail, and the list can
>   grow only **~15%** before the newsletter alone reaches the cap.
>
> Two foreseeable ways to breach it, both worth watching rather than fearing: the list growing
> past ~150,000, and a newsletter day coinciding with a transactional spike — a cohort launch
> or a deadline-reminder run.
>
> **Owner decision, 2026-09-02: ramp first, then request the increase.**
> *"we will slowly ramp it up and then ask for quota increase."* The E.6 ramp (stage −1 shadow
> week, then stages 0–4) runs far below 150,000/day throughout, so no quota work gates starting.
> The outstanding request to 200,000/24 h and 200/s becomes a **precondition of the final ramp
> stage**, not of the first — and by then there will be real send data to justify it, which is a
> stronger request than the speculative one filed in August.
>
> Add to the E.6 stage gates: before the stage whose volume exceeds ~120,000/day, confirm the
> increase is granted. Re-query rather than trusting this table — a quota can move.

**Historical context, superseded by the box above.** The main account has SES production access
in `eu-west-1` — attested by
`aws-infra/docs/aws-support/2026-08-09-ses-newsletter-quota-increase.md:19` ("Production access
enabled, sending enabled, enforcement healthy", account `387546586013`) and corroborated by CMP
mailing real learners today (`main/cmp/cmp_deadline_reminder.tf:8-13`). The owner further states
the production quota has since been raised above the default. What this step establishes is
**the two live numbers**, because a 130,000-recipient weekly campaign is bursty and each number
constrains it differently:

- **`Max24HourSend`** — the rolling-24-hour recipient quota. It has to clear ~130,000 in one day
  *plus* that day's transactional traffic.
- **`MaxSendRate`** — recipients/second. A campaign is delivered in one window, not spread across
  24 hours, so this decides its duration: at 14/s a 130,000 campaign takes ≈ 2.6 hours; at 200/s
  ≈ 11 minutes (AWS's own arithmetic in the support request,
  `2026-08-09-ses-newsletter-quota-increase.md:22-24`).

**The only quota figures recorded anywhere in the estate, with their dates — cited as history,
not as current state.** `2026-08-09-ses-newsletter-quota-increase.md:17` records the main
account's quota **at submission on 2026-08-09** as 50,000 recipients/24 h and 14 recipients/s,
and requests 200,000/24 h and 200/s (Service Quotas requests
`86d22a77…`/`291b4c24…`, support cases `178628060500737`/`178628058900407`, both **"Open,
unassigned" as of that date**). That document's own follow-up item 2 — "record approved values
and decision dates" — was never completed, so the approved numbers exist only in the AWS console.
Recording them is part of this step. (A separate 50,000/day + 14/s figure at
`sandbox/datamailer/README.md:61` is the **sandbox** account in **us-east-1**, dated 2026-07-02 —
do not read it as the production quota.)

The same document also carries a volume figure worth reconciling against §14: it tells AWS
**5–7 newsletters/month, ≈ 650,000–910,000 recipients/month**, where the costed design assumes a
strictly weekly cadence (563,333/month). Postage scales linearly, so the SES line is
**$56–91/mo** across that range rather than a single number. Settle the real cadence with D31.

With main-account credentials:

```console
aws sesv2 get-account --region eu-west-1        # expect "ProductionAccessEnabled": true
aws sesv2 get-email-identity --email-identity datatalks.club --region eu-west-1
                                                # expect VerifiedForSendingStatus true; note the
                                                # DKIM configuration (Easy vs BYODKIM, selector)
aws sesv2 get-account --region eu-west-1 --query 'SendQuota'   # note Max24HourSend / MaxSendRate
```

- **Both numbers clear the campaign** (quota ≥ ~130,000 + transactional headroom in a rolling
  24 h; rate high enough that one campaign completes inside its send window): record them in this
  runbook and in `2026-08-09-ses-newsletter-quota-increase.md`, configure Relay's production
  throttling *from the live quota while reserving capacity for transactional traffic*, and move
  on — E is unblocked.
- **Either falls short:** file the Service Quotas increase immediately. It has multi-day external
  lead time and can come back with questions, so it goes to the front of this lane; `main/relay`
  (E.3) still proceeds in parallel, send-disabled. Only E.6's ramp waits.
- Also record `ProductionAccessEnabled` and the DKIM configuration returned by
  `get-email-identity` — E.0 found the live DKIM to be a classic single-selector TXT rather than
  the three Easy-DKIM CNAMEs CMP's Terraform would emit, so identity state is read from the API,
  never inferred from `iam_ses.tf`.

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
boundary-hardening risk (#10, §16): the no-send promise is currently **config-enforced, not
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
- **Inbound mail: OFF in production — a requirement, not a default (audit key mitigation,
  E.5a).** Leave `SQS_INBOUND_EMAIL_QUEUE_URL` and `INBOUND_EMAIL_ROUTES` unset and create no
  inbound MX/receipt resources in `main/relay`. This keeps the entire measured inbound-DoS
  class (C5, C6, H8–H12) dormant: with the serial single-worker design, inbound email is a
  remote, unauthenticated DoS against *outbound* email. Enabling inbound later is a separately
  gated project with its own fix list (E.5a "before enabling inbound").
- **Host sizing and containment:** the host is a `t4g.micro` with **1 GiB RAM** (the sandbox
  deploy assumed 2 GB); declare `--memory` limits on every container so any OOM is contained
  rather than host-wide, and revisit instance size against measured outbound load during E.6.
- **Database: managed RDS, not PostgreSQL on the host.** `sandbox/relay` runs its own PostgreSQL
  on an EBS volume (`sandbox/relay/user_data.sh.tftpl:62` creates `/var/lib/relay/postgres`; that
  root contains no `aws_db_instance`). Production does not copy that. `main/relay` gets
  **`db.t4g.medium`, single-AZ, gp3 `allocated = 50` → `max_allocated = 500` GB**, encrypted,
  private, 7-day backups, `skip_final_snapshot = false`, `deletion_protection = true` (D30 for the
  instance size, D9 for Multi-AZ). Relay reaches its database only through `DATABASE_URL` and
  issues no privileged SQL, so **Terraform creates both the database and its unprivileged owner
  role** — Relay creates neither.
- **Sizing driver — the number that changes the design.** 130,000 newsletter recipients weekly is
  ~563,000 messages/month, and delivery history is measured at **250–400 MB per 50k campaign**,
  i.e. **0.65–1.04 GB per weekly campaign and 34–54 GB/year unpruned**. A 20 GiB volume is
  exhausted in four to six months, which is why `allocated` starts at 50.
- **Retention and pruning are a go-live prerequisite, not an open milestone.**
  `prune_db_task_results` exists in Relay and is **never invoked** — because nothing has ever run
  at volume there. It must be scheduled and proven before the ramp reaches full send volume:
  RDS storage autoscaling is **one-way**, so an unpruned year silently ratchets the storage floor
  up by ~$4–7/mo and never comes back down. Ship it as `pruning.tf` in the same root, in the same
  change.
- **Backups (audit C3): scheduled RDS backups plus one rehearsed restore are part of this root's
  definition of done.** State this precisely, because the audit's phrasing invites a misreading:
  **C3 is not a defect in Relay.** Relay has never had a client — the website is its first, on
  both dev and prod — so backups, a pruning job, and monitoring on its data volume are
  capabilities that were never *required*, not capabilities that were skipped. They become
  requirements the moment Relay carries the suppression list, which is not reconstructible and is
  what protects the SES reputation thresholds. Treat every item in this bullet list the same way:
  a requirement for a first production client, not a fault list.
- **Deploy:** same OIDC/SSM pattern, trust re-pinned to the main account — but note audit
  **C4: no production deploy path exists yet**; building it is part of the minimum gate.
- **Verification items inherited from the audit's could-not-assess list:** confirm live queue
  redrive (`maxReceiveCount`, `VisibilityTimeout`, DLQ wiring), IAM scoping, `DEBUG` off,
  signature-verification mode, absence of demo API keys in the production DB, the Cognito
  pool's self-signup setting, real SES account state, and botocore's retry classification for
  `SendEmail` — none of these is assumed safe.
- **Observability wiring — decide BEFORE writing the Terraform (time-sensitive, from the E.5b
  assessment):** do **not** copy the sandbox stacks' alarm plumbing. Both carry an inherited
  empty-topic bug — `alarm_email = ""` default with `count = var.alarm_email == "" ? 0 : 1`
  (`sandbox/datamailer/variables.tf:47-51` + `main.tf:194`; `sandbox/relay/monitoring.tf:6`) —
  meaning **their alarms may currently fire into the void**. Adopt instead the
  `modules/django-website` observability contract, the only pattern in the repo that takes
  `alarm_actions = var.alarm_action_arns` as an injected required variable, validates log
  retention (`runtime-variables.tf:242-249`), and exports alarm names for drift detection
  (`outputs.tf:240-251`). (Adopt the *contract* — injected actions, exported names, validated
  retention — not a full module instantiation; the module builds an ECS/ALB/RDS stack and Relay
  is one EC2 host.) Since `main/relay` does not exist yet this is a free choice now and an
  expensive retrofit later. *Verification item:* prove the alarm topic has at least one real,
  confirmed subscriber before E.6 stage 0 — a delivered test notification, not a plan diff.

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
history has no production value.

**The one exception, and it is a precondition rather than a migration (D23): suppression and
unsubscribe state.** Datamailer is the source of truth for who has unsubscribed, hard-bounced or
complained, and **copying that state into Relay is a precondition of the first production send**,
not a Phase 7 cleanup task. An empty suppression list re-mails exactly the people whose addresses
already damaged the sending reputation, which is the failure mode E.6's abort thresholds exist to
catch. Two consequences:

- **Sandbox Datamailer must survive until the copy is done and verified.** This is a second,
  independent constraint on the 7.4 teardown order, on top of the configuration-set dependency
  (E.7) and on top of CMP still being Datamailer's client until 7.1.
- If the export proves impractical, the fallback is to seed **SES account-level suppression**
  instead — worth doing regardless, because today nothing outside Relay's own PostgreSQL holds
  this state.

Queues are ephemeral — drain sandbox before retiring it. The
sandbox stack stays running as the *development* Relay (CMP dev and website dev keep pointing at
`relay.dtcdev.click`) until the sandbox teardown, whose internal ordering E.7 constrains.

### E.5 GATE — both assessments delivered. **BLOCKED(remediation)**

Owner: "1) audit the code 2) make sure that we have enough visibility to fix things quickly."
Both commissioned assessments of `/home/alexey/git/relay` have now landed (2026-09-02):

- **(a) adversarial code audit — DELIVERED. Verdict: NOT ready to send production email.**
  Findings and the revised gate in E.5a.
- **(b) observability assessment — DELIVERED.** Verdict and must-do list in E.5b.

**The gate is now real remediation work, not assessment-waiting.** Production sending (E.6)
does not begin until E.5a's minimum fix list is closed and E.5b's pre-production items are
done. E.3 infrastructure may proceed in parallel (deployed dark, send-disabled); E.6 may not
start. **Every fix below is remediation work that goes through its own groomed issue per
`_docs/PROCESS.md` — nothing in this runbook authorises skipping that lifecycle or shipping a
fix unreviewed.**

#### E.5a Code audit — verdict: not ready to send production email

The fork finding (§11E intro) is qualified by the audit, not overturned: the engine is shared
with production-exercised Datamailer, but the audit found passing tests that cover **dead code
rather than the live path** on at least the retry-critical send path (C1) — so "both suites
pass" is weaker assurance than it looked.

**Four original criticals (outbound-relevant):**

- **C1** — no retry on the live send path; the retry tests exercise dead code.
- **C2** — SNS certificate-URL validation bypass: signature verification is defeatable.
- **C3** — **no database backup.** Postgres lives on a single EBS volume on a single host; send
  history and the **suppression list** are not reconstructible. Interacts with D15: clean-start
  is fine for sandbox data, but production must have a backup/restore story *before* it holds
  the suppression list — losing suppression risks re-mailing addresses that bounced or
  complained, feeding directly into the SES reputation abort thresholds (E.6, risk #4).
- **C4** — no production deploy path.

**The structural finding that reshapes the risk.** `scripts/deploy_relay_sandbox.sh:162` runs
**one** `db_worker` with a strictly serial loop: inbound mail, ALL outbound transactional and
campaign sending, and SES webhook handling share one process, one task at a time; no container
declares a `--memory` limit. Therefore **inbound email is a remote, unauthenticated, zero-cost
denial of service against outbound email** — inbound is the one input any stranger can supply.
Measured, not theorised:

- **C5 (critical)** — unbounded MIME part count (`mailing/inbound_mime.py:74-75`,
  `services/inbound_email.py:33`: full `.read()`, no size check). 500,000 parts from 2.5 MB
  input → 46 s CPU, **373 MiB RSS** (~150× amplification); SES accepts ~40 MB inbound → ~5.8 GiB
  projected — against a `t4g.micro` with **1 GiB RAM** (not the 2 GB the deploy assumed). The
  OOM killer takes `relay-worker`; `--restart unless-stopped` revives it; one such email every
  few seconds is a sustained total outage of outbound mail.
- **C6 (critical)** — unknown charset name raises `LookupError` uncaught
  (`inbound_mime.py:78-87` — `errors="replace"` guards bad bytes, not a bad codec name), and
  the traceback lands in an **untruncated** `TextField` (`django_tasks_db/models.py:123,248`)
  with the attacker's charset string embedded verbatim twice. **~25 emails with a 40 MB
  parameter fill the 2 GB volume and take Postgres read-only.** No prune/retention job exists.
- **H8** — no cap on attachment count/bytes (~37 bytes per counted part): one 40 MB email →
  ~1M synchronous S3 PUTs → **~8 hours of blocked worker**, and ~1M billed PUTs.
- **H9** — routing trusts `To`/`Cc`/**`Bcc`** headers instead of the SES envelope recipient
  (already collected): one message injects events into every configured route.
- **H10** — SPF/DKIM/DMARC/spam/virus verdicts are parsed and then **never checked or
  forwarded**; `sender.addresses` derives from `From:` alone — a spoofed sender with a FAIL
  virus verdict republishes downstream as authentic, and the v1 contract gives consumers no
  field to filter on.
- **H11** — inbound idempotency key is `sha256(message_id + route)` with attacker-controlled
  `Message-ID` (`services/inbound_email.py:41`): pre-claiming predictable IDs silently discards
  the real message as `idempotent_replay` — targeted suppression of inbound business mail.
- **H12** — SQS message deleted immediately after a local DB insert (`sqs_worker.py:40-47`);
  the real work happens later in `db_worker` with no retry, so **the inbound DLQ is unreachable
  by construction**, whatever Terraform configures. Zero `logger.` calls exist in `sqs.py`,
  `ingress.py`, `sqs_worker.py`, `inbound_mime.py`, or `services/inbound_email.py`; an
  OOM-killed worker leaves the row `RUNNING`, which `ready()` never re-selects.

**THE KEY MITIGATION — the phase is built around it.** Inbound processing runs only when
`SQS_INBOUND_EMAIL_QUEUE_URL` and `INBOUND_EMAIL_ROUTES` are configured, and `.env.example`
ships them blank. **Promoting Relay outbound-only — `SQS_INBOUND_EMAIL_QUEUE_URL` left unset in
production — leaves C5, C6, and H8–H12 dormant.** That is an explicit design decision of E.3,
the single highest-value constraint available, and it costs nothing (E.3 already omits inbound
by default; the audit upgrades that from "default" to "requirement").

**Revised gate:**

- Minimum before ANY real mail (E.6 stage 0): **C1, C2, C3, C4, H2, H5** closed (H2 and H5 per
  the audit's full findings list, which this runbook does not reproduce), plus E.5b items 1+1b+4.
- Before enabling inbound at all — a later, separately-gated step outside this plan's scope:
  C5, C6, H8 (input caps *before* parsing: object size, part count, attachment count/bytes,
  nesting depth), H9, H10, H12, **plus `--memory` limits on every container** so an OOM is
  contained rather than host-wide.
- **Shadow period**: one week of real payloads through the already-built `dry_run: true` mode
  before live sending — exercises rendering and suppression at volume with zero reputation
  exposure. Added to E.6 as stage −1.

**What the audit could NOT assess** — so this runbook implies no assurance where none exists:
the external Terraform's live values (queue `maxReceiveCount`, `VisibilityTimeout`, DLQ
redrive, IAM scoping, alarms), live host state (actual `DEBUG` value, signature-verification
mode, whether demo API keys exist in the real DB), the Cognito pool's self-signup setting
(which decides whether finding H4 is critical or benign), real SES account state, and
botocore's retry classification for `SendEmail`. Each becomes a verification item in E.3/E.6
rather than an assumed-safe default.

#### E.5b Observability assessment — verdict and pre-production must-do list

**Verdict: not enough visibility today to operate Relay in production — but this is a gate,
not a blocker.** Items 1 + 1b below are under a day's combined work and move Relay from
"no detection" to "15-minute detection with an auto-filed ticket," reusing infrastructure that
already exists and works.

**The highest-value finding — a working email canary already exists and is free to inherit.**
`main/cmp/cmp_deadline_reminder.tf:98-110` runs `manage.py monitoring_datamailer_health --json`
on EventBridge **every 15 minutes**; failures raise `datamailer.health_warning`, firing alarm
`cmp-prod-datamailer-health-warning` → SNS → email **and** an auto-filed GitHub issue
(`main/cmp/observability.tf:66-166`). Two more app-event alarms exist: `datamailer.outbox_failed`
and `datamailer.outbox_dispatch_failed` (`observability.tf:11-22`). Re-pointing all three at
Relay is configuration, not a build — and it is **mandatory before Relay carries real traffic**
(E.6 precondition; silent-failure risk #5, §16).

Pre-production must-do list (ordered; effort per item):

1. **Give the `main/relay` alarm topic a real subscriber** — reuse CMP's
   `lambdas/cloudwatch_alarm_to_github.py` verbatim (SNS → email + auto-filed issue). ~1 h.
   **Precondition: item 4** — that handler files issues into a *public* GitHub repo and
   deliberately publishes only log context, never log lines
   (`cloudwatch_alarm_to_github.py:171-172`); redaction must land before logs become quotable.
1b. **Re-point CMP's health probe and the two `datamailer.outbox_*` alarms at Relay** — ~2 h,
   the highest value per hour in this list.
2. JSON `LOGGING` config + send-path events + `ADMINS`/root handler for 500s — ~1 d. **No prior
   art exists in these repos** — Datamailer, AISL, and CMP all log printf-style plain text with
   no correlation IDs and no redaction, so this makes Relay *exceed* the estate; align with
   dtc-website's unmerged #265 logging contract rather than inventing a new one.
3. Log the two silent-swallow sites — `relay/mailing/sqs.py:47` (handler exception → message ID
   appended to the batch-failure list with no log) and
   `relay/mailing/services/campaign_sender.py:157` (send exception classified, unlogged). ~30 m.
4. **Redact PII from logs** — `relay/mailing/services/mailchimp.py:195,199` logs raw recipient
   email addresses (inherited verbatim from Datamailer), and gunicorn access logs expose
   `contacts/<email>/` paths. ~2 h. Precondition for item 1, as above.
5. EMF-based metrics + alarms on send failure, oldest `QUEUED`, stuck `SENDING` — ~1 d. Cheaper
   than it sounds: CMP already emits embedded-metric-format JSON on stdout through the existing
   log stream (`main/cmp/app_prod.tf:61-71`), so **no new IAM is needed** — there is no
   `cloudwatch:PutMetricData` grant anywhere in `main/cmp/`.
6. SES reputation alarms — copy `main/aisl/email.tf:167-195` (`Reputation.BounceRate` ≥ 5% and
   the complaint twin). ~1 h. This is the concrete implementation of the E.6/risk-#4
   identity-suspension protection.
7. Widen the CloudWatch agent to the data volume; alarm on memory and data-volume disk — ~2 h.
8. `/health/ready` returns version/source_sha/image_digest plus per-check detail — ~4 h.
9. `relay-alert-investigator` read-only role modelled on `main/cmp/iam_alert_investigator.tf` —
   scoped `logs:FilterLogEvents`/`StartQuery` plus alarm/ECS/RDS describes, assumable only from
   the sandbox role, 1-hour sessions; CMP's alarm Lambda already hands the operator a
   pre-computed ready-to-run assume command. ~2 h. The best debugging-ergonomics win available
   for an SSM-only host.

House-wide context, so this is not misread as a Relay deficiency: dashboards, saved queries,
and composite alarms are an estate-wide gap — zero `aws_cloudwatch_dashboard` and zero
`aws_cloudwatch_query_definition` exist anywhere in aws-infra.

### E.6 Battle-testing: a graduated ramp, not a big-bang switch

**What actually constrains this ramp is deliverability evidence, not sending capacity.** The
account has production access and raised quota (E.1); the stages below exist to accumulate bounce
and complaint evidence at increasing volume, and their advance criteria are all reputation and
observability criteria. Capacity appears only once, at stage 4, where a full campaign has to fit
inside its send window at the live `MaxSendRate`.

Preconditions: **E.5a minimum fix list closed — C1, C2, C3, C4, H2, H5, each through its own
groomed issue** — plus E.1's quota and rate recorded against the campaign size; the Datamailer
suppression copy landed and verified (D23) before stage 1; retention/pruning scheduled and proven
before stage 4; D14 identity hand-off done; production
config confirmed **outbound-only** (`SQS_INBOUND_EMAIL_QUEUE_URL` unset — E.3/E.5a key
mitigation); and **E.5b items 1 + 1b done — the alarm topic has a proven live subscriber and
CMP's 15-minute health probe plus the two `datamailer.outbox_*` alarms are re-pointed at Relay
before any real traffic** (otherwise the estate's only email monitoring stays green while
watching a system that no longer sends the mail — risk #5).

| Stage | Traffic | Volume / duration | Advance when (all of) | Rollback |
| --- | --- | --- | --- | --- |
| −1 | **Shadow week**: real production payloads through the already-built `dry_run: true` mode — exercises rendering, templating, and suppression at volume with **zero reputation exposure** (audit recommendation) | full realistic volume, 1 week | dry-run outcomes reconcile against expected sends; no worker stalls/OOM; suppression decisions correct on known-suppressed fixtures | none needed — nothing was sent |
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
still operates breaks sandbox Relay. That is one of **three** dependencies holding sandbox
Datamailer alive; the other two are CMP production still being its client until 7.1, and the D23
suppression copy that must complete before Relay's first production send. The corrected order is
therefore **sandbox Relay → `sandbox/website` → CMP (7.1) → sandbox Datamailer**, specified in
full at §12 step 7.4.

### E.8 Cost — **corrected; the previous figure was wrong by roughly 8×**

This section previously priced all of `main/relay` at **$15–18/mo**. That estimate predated the
130,000-recipients-per-week number and omitted SES postage almost entirely. **Relay production is
≈ $144/mo, and Relay prod + dev is ≈ $155/mo** (Budget B, §14.3). It does not threaten the
website's cost bar, because D26 judges Relay on a separate budget — but the old number must not
survive anywhere.

| Driver | Est. $/mo | Share |
| --- | ---: | --- |
| SES postage (563k newsletter + 30k transactional + message data) | 62.69 | 40 % |
| Relay production database (`db.t4g.medium` + 50 GB gp3) | 58.89 | 38 % |
| Everything else — two hosts, volumes, queues, secrets, KMS, logs, alarms | 33.70 | 22 % |
| **Relay production** | **144.03** | |
| Relay dev (`t4g.micro`, database on the shared dev instance) | 11.45 | |
| **Budget B total** | **155.48** | |

Three things follow:

- **SES postage is the largest single line and has no infrastructure alternative.** The only
  levers are cadence and list segmentation, both owner decisions. At the 5–7 newsletters/month
  stated to AWS (E.1) rather than a strict weekly cadence, the postage line is **$56–91/mo**.
- **The database line is a consequence of retention, not of instance choice.** 34–54 GB/year of
  unpruned delivery history is what drives both the instance size (D30) and the storage floor;
  the pruning job (E.3) is what keeps it from ratcheting.
- **Relay is largely new spend, not a substitution.** What disappears is sandbox Datamailer at
  ≈ $13–15/mo. The newsletter itself moves off **Mailchimp**, whose cost sits outside this
  account entirely — so the AWS invoice rises while the total marketing-mail cost may not. Record
  the Mailchimp line before the cutover if that comparison is going to be made.

Dual-run window (sandbox Relay + `main/relay`) ≈ +$13/mo until E.6 stage 2. **No
`main/datamailer` root is built at all.** The Phase 7 sandbox harvest includes sandbox Relay
(≈ $13–15) and sandbox Datamailer (≈ $13–15) — the latter landing after 7.1, not at 7.4 (§12).

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
- **7.0a [aws-infra] PRECONDITION for 7.1 — rehome the email canary out of CMP.** The
  15-minute health probe and its alarm→SNS→GitHub pipeline live in CMP's stack
  (`cmp_deadline_reminder.tf:98-110`, `observability.tf:66-166`) and, after E.5b item 1b, they
  are the production monitoring for *Relay*. Destroying `main/cmp` with them still in its state
  silently deletes Relay's only email monitoring — the same trap class as the D14 identity.
  Move the probe schedule + alarm resources (or equivalents) into `main/relay` before 7.1, and
  prove the canary still fires end-to-end (test alarm → notification received) afterward.
- **7.1 [aws-infra] [CREDS] [ONE-WAY]** Destroy `main/cmp` compute/ALB/dev resources (final DB
  snapshot is enforced — `db.tf:22-24`, `deletion_protection = true` must be lifted knowingly;
  the Aurora final snapshot is the point-of-no-return gate). Keep the snapshot per the retention
  decision in `migration-checklist.md:177`. **Privacy note (owner observation to record, not
  migration work):** CMP has no erasure feature (§13.9), so for personal data *not* carried
  forward into the new site, **the disposal of the CMP database and its RDS snapshots is itself
  the erasure event**. The final-snapshot retention window is therefore a privacy decision as
  much as an ops one: set an explicit expiry for the final snapshot and every automated/manual
  snapshot, delete them when the rollback window closes, and record the deletions. The
  pre-existing observation — personal data for tens of thousands of people in a system with no
  deletion capability — is resolved by this disposal and is noted here because decommissioning
  is the moment that question gets asked.
- **7.2** Retire the four GitHub Pages deployments (remove custom-domain CNAME files; archive the
  repos' Pages settings). ONE-WAY in practice — re-serving requires re-verification with GitHub.
- **7.3** Retire `main/legacy-site` after the stage-2 swap's own ≥30-day rollback window (§9.4):
  the S3 tree is the designated read-only fallback (spec 09:205) — cheapest thing in the whole
  account; keeping it for a year costs ≈ $1/mo, so err long.
- **Sandbox decommission (owner, D2: "sandbox infra will be gone once we migrate"):**
- **7.4 Preconditions and internal order — CORRECTED.** `dev.datatalks.club` (§9.2) has served as
  the working dev deployment through at least one full release cycle; nothing deploys to
  `web.dtcdev.click` any more (`.github/workflows` in the website repo retargeted); no
  compatibility capture or CI job references the sandbox host.
  **Sandbox Datamailer retires last, and after 7.1 — not inside 7.4.** Three independent
  dependencies hold it alive, and the earlier ordering (Relay → Datamailer → `sandbox/website`)
  only accounted for the first:
  1. **sandbox Relay sends through Datamailer's `datamailer-sandbox` configuration set and event
     topic** (`sandbox/relay/README.md:20-21`, `sandbox/relay/messaging.tf:35-47`), so sandbox
     Relay must go first or it breaks while still serving as the dev sender;
  2. **CMP production is Datamailer's client** until `main/cmp` is destroyed at 7.1 — the path is
     HTTPS plus a shared API key over the public internet, with no cross-account IAM grant to
     revoke (`sandbox/datamailer/main.tf:73-100`), so nothing has to be un-granted; the only
     requirement is not to destroy the endpoint while CMP still calls it. Note CMP's Datamailer
     URL is set through the sticky-env mechanism rather than Terraform
     (`main/cmp/app_prod.tf:101-105` sets `ignore_changes = [container_definitions]`), so the live
     value must be read from the running task definition, not from the repo;
  3. **the D23 suppression copy** — Datamailer is the source of Relay's suppression state, and
     that copy is a precondition of the *first production send*, so Datamailer must outlive it.
  Resulting order: **sandbox Relay → `sandbox/website` → CMP (7.1) → sandbox Datamailer.**
  Consequence for §14.5: Datamailer's ≈ $13–15/mo lands *after* the CMP decommission. Counting it
  at 7.4 understated the transitional bill by that amount for the whole window.
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
| Course repositories (e.g. `llm-zoomcamp`) | per-repo branch config | Course/Cohort curriculum | per course | Already webhook-shaped: `content_sync/course_repository_webhook.py` + sync (#218, the 2026-08-20 curriculum-sync note (since deleted)) |
| Datamailer | inventory pin = #290 (P0, groomed) | read-only email history/reconciliation input, never a sender (spec role reaffirmed by D13's Relay resolution) | TBD by #290 | send-disabled import only (spec 09:141-143); live sending is Relay's (Phase E) |
| Luma / Eventbrite exports | protected local snapshots, checksummed (`migration-checklist.md:62-124`; contract `_docs/migration-data/event-registration-sources.json`) | new-website events + registration aggregates — **see §13.8**, incl. the mandatory pre-cutover freshness gate | Luma 166 events / 51,873 accepted rows; Eventbrite 209 / 24,001 | operator-gated aggregate import; exporter re-run for fresh events (§13.8) |
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
data — different consent, retention, and rollback rules apply (spec 09:209-246). The
full-fidelity form of that import — the one that actually runs at cutover — is specified in
§13.9, including its unbuilt-mechanism finding and the mandatory pre-swap dry-run gate.

### 13.7 Genuinely undecided ingestion items (routed to decisions, not invented)

D12 (source rollout manifest — blocks every family cutover); #272's freshness/receipt-age policy
(HUMAN); Datamailer scope beyond the #290 inventory; whether docs/faq/podwiki go direct-sync
before or after the apex cutover (no dependency either way once D12 exists — but during Phase 2
the *legacy static* copies of those sections are what's live, so a direct-sync cutover of `/docs`
before milestone 8 would be invisible until the apex swap; sequence family cutovers after
milestone 8 unless the owner wants them proving out on `prod.` first).

### 13.8 Events: the Luma data dump, the freshness gate, and the PII boundary

Owner (2026-09-02): "there should be some data dump with new events that we will use for the
new website — it's taken from Luma. Make sure it's mentioned in the migration plan." It exists,
is documented below, and carries one prominent warning and one mandatory gate.

**Where the dump lives (verified 2026-09-02).** The prepared aggregate is at
`.local/migration-data/events/luma-aggregate-v1/` in the main checkout — 332 files, one
`.csv` + `.json` pair per event, named `<date>_<slug>_<evt-id>`, captured
`2026-08-29T20:46:15Z`. Siblings: `.local/migration-data/events/luma/` (raw per-event exports)
and `.local/migration-data/events/eventbrite/` (archive). The
`.local` events directory is **mode 700 and outside the git tree, deliberately** — this runbook
documents where it lives and how to regenerate it
(`scripts/prepare_event_registration_sources.py`, invocation in `migration-checklist.md:92-99`)
and never embeds its contents.

**The checked contract that describes it** — `_docs/migration-data/event-registration-sources.json`
(committed provenance: `3adb7b9` "Document located Luma migration snapshot"; values re-verified
against the file):

- Luma: `adapter: historical-aggregate-v1`, `event_total: 166`, `registration_total: 51,873`
  approved, `excluded_registration_total: 51` declined, `row_total: 51,924`,
  `status_policy: historical-status-v1`, `tree_sha256: 5362e8c2…`, and
  **`activation_state: "mapping_review_required"`**.
- Eventbrite: `event_total: 209`, `registration_total: 24,001` attending,
  `source_archive_sha256` + `prepared_archive_sha256` recorded, `unsupported_xlsx_total: 1`,
  same **`mapping_review_required`** state.

**The exporter — a required input with no home in version control (D16).** The tool that
produces the raw Luma exports lives at `/home/alexey/tmp/luma-exporter/` — a `uv` project with
Makefile, README, and tests, **outside both repos, in a personal temp directory**. That is
itself a migration risk: the pipeline that feeds the events section depends on a script that no
checkout contains. Its `luma-events/` output already holds events **newer than the prepared
aggregate** (e.g. `2026-09-01_ml-zoomcamp-2026-pre-course-live-q-a`), i.e. fresh data has been
pulled but not re-aggregated.

**Why this is a cutover gate, not housekeeping.** Verified today (2026-09-02): the newest date
anywhere in the 421-record events projection is **2026-08-31** — the site currently has **zero
future-dated events**. Launching the new site in this state means an empty upcoming-events
section and superseded cohorts on day one (issue #307) — a visible failure exactly where the
migration is supposed to shine. Therefore §9.4's swap gate gains a **data-freshness item**:
within a defined window before the apex swap (recommend ≤ 72 h), events, cohorts, and content
are re-synced from their sources and the concrete check "**at least N future-dated events
exist**" passes (owner sets N; N ≥ 1 is the floor, the realistic value is the count of genuinely
scheduled upcoming events). The same check joins post-swap monitoring so staleness cannot
silently return — an events site fed by a manually-run local script is a standing staleness
risk (risk #19; D17 assigns ownership and cadence).

**The PII boundary — explicit, non-negotiable.** These dumps hold **51,873 Luma + 24,001
Eventbrite registrations with real names and email addresses.** They stay out of the git tree
and out of CI; no row is ever quoted in a report, issue, screenshot, or log (only checksums and
aggregate counts are safe facts — `migration-checklist.md:80-85`); and
`activation_state: "mapping_review_required"` means **the registration data is explicitly not
cleared for activation** — any step that would make it publicly visible requires the owner's
mapping review first (exact event-mapping, exclusion, and quarantine rules per spec 09:280-299).
Privacy/retention authority: `_docs/specs/07-security-privacy-operations.md`; registration-
migration owners: #112, #242, #243 — cross-referenced, not restated. Note the freshness gate
and the activation review are **independent**: refreshing the *event catalogue* (titles, dates,
descriptions) for the gate does not require activating *registration totals*.

**Repo-hygiene warning — HEAD is currently un-rebuildable (verified 2026-09-02).**
`_docs/migration-data/event-registration-sources.json` sits modified-but-uncommitted in the
working tree with refreshed Luma facts (`event_total` 166 → **174**, capture
`2026-09-02T11:26Z`) while committed HEAD still says 166 — and a rebuild from committed HEAD
fails with `checksum_drift` against the refreshed Luma export. The refreshed export artifacts
and their facts file must be **committed together** as one change; until then, anyone
regenerating from HEAD gets a hard failure, and the numbers quoted above are the *committed*
contract, not the in-flight refresh.

### 13.9 Full-fidelity CMP database import — two modes, only one of them built

Owner (2026-09-02): "the whole import should contain PII and enrollment. It should be the
entire database. For testing now we can skip it, but we need to make sure it works." The
runbook previously contemplated only the first of two import modes; the second is the one that
actually runs on migration day, **and it has never been exercised**.

| Mode | Tool | Contents | Status |
| --- | --- | --- | --- |
| **Sanitized** | `review_import/` via `make review-data` (`Makefile:469`) | 0 enrollments, 0 submissions, 1 synthetic user (`review-admin@example.invalid`, `review_import/workflow.py:42`) | Working; correct for local dev, CI, design review — every rehearsal so far proved *this* path |
| **Full fidelity** | — | the entire CMP **production** database: **20,009 accounts, 20,907 enrollments, 36,547 submissions**, all real PII (counts from the newest nightly production export — see the provenance block below; the 17,582/18,945/34,764 figures quoted in an earlier draft came from CMP's *dev* SQLite and are not the migration's numbers) | **Unbuilt. No tool, no runbook step, never run. A cutover blocker hiding in plain sight** |

**Mechanism — what would actually perform it.** Two candidates examined, one eliminated:

- `scripts/load_rds_export.py` is **not viable** (empirically confirmed): `main()` is disabled
  and returns 2 (`load_rds_export.py:832-837`); it raises
  `RuntimeError: courses_course: source export is missing required local columns without
  defaults: ['course_id']`; CMP has no `courses_course_family`, `courses_module`, or
  `courses_unit` tables at all; and `plan.default_values` evaluates callable defaults once per
  table, so every copied cohort would receive the same UUID and violate two unique constraints.
- `review_import/` is the working importer but **sanitizes by design**.

The full path is therefore **an extension of `review_import` with sanitization disabled behind
an explicit, loudly-named flag** (recommended — it reuses the schema mapping, fail-closed
checks, and idempotency that already work), or a not-yet-written tool. Either way this is
**unbuilt work requiring its own groomed issue(s) per `_docs/PROCESS.md`** — this runbook
sequences it, nothing here builds it.

**Provenance — where the export actually is, and how it is produced.** Located and verified
2026-09-02.

**A nightly cron already produces a full production export. Nobody has to build this part.**

```cron
0 1 * * * cd /home/alexey/rds-export && /usr/bin/flock -n /tmp/rds-export-cmp.lock \
  ./notify_run.sh cmp uv run run_pipeline.py --db cmp --schema prod
```

The chain is Aurora → RDS manual snapshot (`cmp-YYYY-MM-DD`) → Parquet export to S3 →
`parquet_to_sqlite.py` → SQLite, plus an upload of the result back to S3. Tooling lives in
`github.com/alexeygrigorev/rds-export`; the deployed checkout is `~/rds-export` (a second
working copy sits at `~/git/rds-export`). Terraform for the export role, bucket and KMS key is
`aws-infra/main/rds-export/`. AISL runs the same pipeline an hour later.

**Which file to use, stated as a rule rather than a filename:** the production export is **the
newest `/data/tmp/rds-export/rds-prod-*.db`**, produced by the nightly cron above. The directory
holds six daily rotations, so a filename in this document is stale within 24 hours; always take
the newest. As of 2026-09-02 that is:

| | |
| --- | --- |
| **Latest production export** | `/data/tmp/rds-export/rds-prod-20260902-012536.db` |
| Size / shape | 235 MiB (246,493,184 bytes) · 38 tables · 664,806 rows |
| S3 copy | `s3://course-management-rds-backups-387546586013/sqlite/rds-prod-20260902-012536.db` |
| Schema state | latest applied migration `0043_remove_registrationcampaign_email_body_markdown_and_more`, 2026-08-31 |
| Retention on disk | six daily rotations in `/data/tmp/rds-export/`; **always take the newest** |

Production row counts as of that export:

| Table | Rows | Table | Rows |
| --- | ---: | --- | ---: |
| `courses_answer` | 218,157 | `courses_enrollment` | 20,907 |
| `courses_criteriaresponse` | 107,691 | `accounts_customuser` | 20,009 |
| `django_session` | 71,095 | `account_emailaddress` | 20,005 |
| `courses_projectevaluationscore` | 38,026 | `courses_peerreview` | 13,041 |
| `courses_submission` | 36,547 | `data_datamailersendaudit` | 10,174 |
| `data_datamaileroutboxevent` | 33,529 | `courses_projectsubmission` | 4,261 |
| `courses_courseregistration` | 27,656 | `courses_question` | 616 |
| `socialaccount_socialaccount` | 21,761 | `courses_homework` | 128 |
| `courses_project` | 52 | `courses_course` | 21 |

Catalogue-scale tables re-verified against that export on 2026-09-02: `courses_course` **21**,
`courses_project` **52**, `courses_question` **616**, `courses_homework` **128**.

⚠️ **`course-management-platform/db/db.sqlite3` is CMP's *dev* database, not production**
(owner, 2026-09-02) — an earlier draft of this section said otherwise. Its counts differ
materially: 17,582 accounts against production's 20,009, 595 questions against 616, and it has
**no `courses_courseregistration` table at all** while production holds 27,656 rows there. Any
schema mapping derived from the dev copy is incomplete. Use the newest `rds-prod-*.db` export.

⚠️ **`Project Attempt N` is real production copy, not placeholder junk.** Verified against the
2026-09-02 export: **31 of 52 `courses_project` rows** carry a title of that shape
(`Project Attempt 1`, `Project Attempt 2`, `Project Attempt 3`, plus a lowercase-`a` variant),
and the phrase is explained to learners in a public FAQ entry —
`content/faq_projection.json:310`, *"Project: What is Project Attempt #1 and Project Attempt #2
exactly?"* Anyone treating those rows as test data will delete live catalogue content. **The real
placeholder marker is the string `Production-like generated`**, written into a project's
*description* by the local seeder (`courses/services/local_course_seed.py:362`) and asserted on by
`scripts/verify_local_dataset.py:130`; it appears **zero** times in the production export. Filter
on that, never on the title.

**Tables that must never be imported**, whatever the mode: `django_session` (71,095 live
session keys), `socialaccount_socialaccount` (21,761 OAuth tokens), `socialaccount_socialapp`,
and `accounts_token`. They carry live credentials, they change nothing a page renders, and
copying them is pure downside. The `data_datamailer*` tables are recipient addresses and
delivery history — PII, and in scope only if the target actually needs send history.

**Handling.** Both the export directory and the dev copy were world-readable (mode 644 files in
755 directories) when found on 2026-09-02, holding 20,009 real accounts; both were tightened to
mode 600 inside mode-700 directories — the same defect, and the same fix, as the `.local/`
finding in §13.8. `/home/alexey/git/rds-export/.env` carries a **static AWS access key pair**
and was also world-readable; likewise tightened. That key pair is worth a separate look: the
aws-infra policy suite forbids `aws_iam_user` and `aws_iam_access_key` precisely because
long-lived static keys are what this repository has decided not to use.

**At cutover**, do not use a stale export. At the milestone-8 write freeze (spec 09:163) take a
fresh Aurora snapshot, run the same chain, record the snapshot identifier and the output
checksums, and feed *that* artifact to the importer — the nightly run is the rehearsal
mechanism, not the cutover artifact. The runbook step is "produce the export at the freeze",
with the exact procedure documented in the importer's groomed issue.

**The schema-drift blocker (verified).** `make review-data` currently **fails closed**:
`category=schema-unknown-table table=courses_emailcampaign` (raised at
`review_import/workflow.py:731`). Cause: the site's adopted CMP schema is pinned at `98a2352`
(2026-08-04) while the dump is CMP HEAD `6d3cc0e`; three tables added upstream after the pin —
`courses_emailcampaign` (migration 0043), `courses_systemprojectevaluation` and
`courses_systemevaluationcriteriaresponse` (0041) — do not exist in the target schema.
⚠️ **Correction: they are not empty in production.** Counted against the 2026-09-02 export:
`courses_emailcampaign` **1**, `courses_systemprojectevaluation` **1**,
`courses_systemevaluationcriteriaresponse` **11**. An earlier draft called all three empty and
therefore cheap to skip. They are not skippable: **there is no skip-empty escape hatch**, the
failure is a real schema-drift failure, and dropping the rows would silently discard production
records. Resolve it properly — adopt the upstream migrations into the pinned schema, or re-pin
the adoption and re-run its characterization suite — before any cutover import. Hand-editing
tables out of a dump is not a resolution. The drift keeps growing while CMP stays live, so
re-check at freeze time and re-count these three then.

**Verification (all without printing any learner's identity):** per-table row-count
reconciliation source↔target; referential-integrity sweep (no orphaned enrollments/submissions);
spot checks that a known learner's enrollments, submissions, and scores survived — executed by
an operator with access, reported only as pass/fail and counts. This instantiates the general
rules of `migration-checklist.md:169-176` and spec 09:209-224 for this specific import.

**Privacy and legal — the single largest movement of personal data in the whole migration.**
Authority: `_docs/specs/07-security-privacy-operations.md` (retention, minimisation, erasure);
registration-migration owners #112, #242, #243 — cross-referenced, not restated. This runbook
adds the operational handling: the export lives only in operator-controlled, mode-700,
non-git-tracked locations (the `.local/` pattern, §13.8) and named encrypted S3 staging with
least-privilege read; encrypted at rest and in transit at every hop of the Aurora → S3 → SQLite
chain; every intermediate copy (Parquet, SQLite, scratch restores) is enumerated at export time
and **deleted after verification, with the deletion recorded**. The export never enters git,
CI, logs, screenshots, or issue bodies; reports carry counts and checksums only.

On erasure, the owner's statement (2026-09-02) settles it: **"There are currently no erasure
requests in CMP — it's not a feature that exists."** Consequence, stated plainly: the CMP
import is a straight forward-migration of records that have never been subject to an erasure
action, so **no tombstone reconciliation is required at import time** — the importer needs no
such step and executors should not go looking for one. The obligation moves *forward*, not
away: once these ~20,009 accounts with enrollments and submissions land, they enter a system
that **is** being built with retention/erasure machinery — spec 07 plus #256 (privacy export/
correction), #257 (retention registry), #258 (erasure tombstone replay on restores), #259
(privacy invalidation/processor receipts) — and are covered by it from that moment. That
handover is a genuine post-migration item owned by those issues, not an import-time one.

**Rollback — the least reversible step in the migration.** If the full import lands wrong
*before* the apex swap: destroy and re-import; nothing public happened. If discovered *after*
DNS has moved and real writes have landed on the new stack, spec 09's rules govern
(09:184-206): no destructive reverse of a successful migration, application-image rollback
with the database retained, expand/contract discipline. The import-specific additions:
snapshot the target database immediately **before** the full import (the restore point);
rehearse restoring it; and treat the gap between freeze and swap as the window where aborting
is still cheap — which is why the dry-run gate below must happen earlier still.

**The dry-run gate (mandatory, pre-swap).** Testing may proceed sanitized for now, per the
owner — but before the apex swap the full-fidelity path must be **proven end-to-end once**:
run the complete import against a disposable target (snapshot-restored RDS instance, never the
shared dev database — §9.2 already reserves throwaway instances for destructive rehearsals),
run the full verification suite above, record pass/fail + counts, then **destroy the target
and its credentials and record the destruction**. This joins §9.4's swap gate as item 6,
alongside the data-freshness gate.

### 13.10 Course-repository curriculum layout and the unit content pipeline

Course curricula arrive from the course repositories, not from `DataTalksClub/content`, and that
path gained a decided layout and six landed fixes on 2026-09-02. The full end-to-end path, its
twenty measured divergences, and the migration mechanics live in
[`_docs/design/specs/unit-content-pipeline.md`](../design/specs/unit-content-pipeline.md); this
section records only what the migration sequence needs.

**The layout (D18).** `cohorts/<year>/<module>/module.yaml` for every module-format course.
Verified today: `llm-zoomcamp` conforms (7 modules under `cohorts/2026/`),
`machine-learning-zoomcamp` (9) and `ai-dev-tools-zoomcamp` (4) hold `module.yaml` at the
repository root and move. **No public URL changes** — the module slug is the directory name and
the unit slug is the filename stem, and the 2,937-row baseline contains no
`/courses/<family>/<year>/modules/…` row. Three things break under a naive `git mv` and must be
repaired in the same commit: nine `homework.md` collisions in ML, 102 relative image references,
and 103 `](../)` back-links whose meaning depends on directory depth. The convention itself is
owned by `DataTalksClub/zoomcamp-template` (D21), whose `STRUCTURE.md`, `docs/conventions.md` and
`templates/{root,module,cohort}-README.md` are part of the change set — and
`llm-zoomcamp/cohorts/README.md` currently documents the *opposite* convention and must be
rewritten.

**Rollout scope (D19).** Every course except `stock-markets-analytics-zoomcamp`, which stays
`legacy` for 2026. `data-engineering-zoomcamp` is a candidate to assess. Neither it, `mlops-`
nor `sma-` carries `module.yaml` or `course.yaml` today.

**Content-pipeline fixes that have landed on `main`.** Recorded because each one
changes a number an earlier draft of this runbook or its companions quoted:

| Fix | Commit | State |
| --- | --- | --- |
| Every repository-relative link a lesson writes now resolves | `2c97886` | **272** relative non-`.md` links (45 llm + 219 ml + 8 ai-dev-tools) are rewritten to upstream GitHub blob/tree URLs; **0 unresolved**. Independently re-measured for this update: 272 exactly. A "288" figure circulated today; it is not reproducible under this definition and 272 is the count to use. |
| Duplicate title heading removed | `1b8352a` | **103 of 181** imported units opened by repeating their own title (the ML lessons, which write `## 1.1 …` where LLM writes `# …`). Removals go from 74 to **177 of 181**. The **4 remaining are the ai-dev-tools lessons**, whose bodies open `# Module N — <Title>` while the unit title is `<Title>`: the stripper is *correct* to leave them, because it only removes a heading whose text equals the title — but the page therefore still prints near-identical text twice, which is a **content** fix in the course repository, not a code fix. |
| Lesson video and code files persisted | `c928f37` | New columns `Unit.video_url`, `Unit.code_sources` (migration 0053) and `Homework.instructions_source_path` (0054). ⚠️ **They are empty until a reimport**, which needs a fresh commit or a cleared run row — a deployed migration alone does not populate them. |
| `_VIDEO_LINE_RE` deleted | `c928f37` | It matched **0** of the 181 units. Verified: no such symbol remains anywhere under the repository. |
| Import refused when the commit is not publicly resolvable | `fbab381` | The guard, and the manifest field `commit_public: false`, stay correct. ⚠️ **Its stated mechanism is wrong and is corrected here:** the "Edit on GitHub" 404 is **branch-path absence, not commit-SHA resolution**. `UnitRepository.edit_url()` builds `{base}/edit/{branch}/{source_path}` (`courses/services/unit_assets.py:63-70`), and `raw_url`/`browse_url` are branch-based too (`:72-88`) — no commit SHA appears in any public affordance. The link 404s when the *path* does not exist on the public branch. Since `c04db93` and `1aa481e` are now on `origin/main`, those paths exist and the condition is largely closed; the guard remains the right backstop for the next unpushed import. |
| Homework slugs copied verbatim | `1f4be1a` | D24 — the `^hw(\d+)$` → `homework-0N` transform is gone; `homework_slug_overrides` is the only mechanism. |

**Convention over configuration (D20).** `unit-content-pipeline.md` §2 separates five deliberate
special cases from four heuristics. The heuristics are what D20 targets, and one of them
disappears with the D18 move: ML's `homework.md` module-directory fallback
(`unit_links.py:168-171`) exists only because ML keeps a stub `<mod>/homework.md` distinct from
the real `cohorts/2026/<mod>/homework.md`. **Delete it with the move, not before.**

**Skipped cohorts (D25)** are enumerated at `courses/services/cmp_content_import.py:59-71`.

---

## 14. Cost and security: two budgets (D26), and seven corrections

All figures are **estimates** (eu-west-1, on-demand, 730 h/mo, single region, current public
prices as of the author's knowledge — re-check at execution; the repo contains no billing data,
so Phase 0.3's Cost Explorer pull is the authority for actuals).

**Two budgets, not one total (D26).** The owner's bar — "cost for dtc website should be equal to
cmp or less. for relay it's different" — applies to the website only. Relay is judged on its own
merits.

| | Scope | Bar | Est. $/mo | Verdict |
| --- | --- | --- | ---: | --- |
| **Budget A** | website prod + website dev + every shared piece of plumbing they need, plus the three small Lambdas | ≤ CMP's **$180–225** | **169.98** | **PASS** — $10.02 under the bottom of the bar, $32.52 under the midpoint |
| **Budget B** | Relay prod + Relay dev | judged separately | **155.48** | 40 % of it is SES postage, which has no infrastructure alternative |
| **Combined** | what actually lands on the invoice | — | **325.46** | against ≈ $193–240 today (CMP + `main/common` + sandbox Datamailer) |

The invoice does not respect budget boundaries: the combined bill **rises by roughly
$85–132/mo**, and essentially all of the increase is Relay carrying a 130,000-recipient weekly
newsletter that runs on Mailchimp today. The website lane itself gets *cheaper* by $10–55/mo.

**Seven corrections to the figures this section used to carry.** Each is folded into the tables
below rather than left to contradict them; they come from the costed design (see the companion
list at the top of this runbook), which read the Terraform rather than describing it.

1. **§11E E.8 priced all of `main/relay` at $15–18/mo.** The real figure is ≈ $144 for Relay
   production alone, of which ≈ $63 is SES postage — an ~8× miss, and the single largest
   arithmetic hole this section had. Corrected at E.8 and in Budget B below.
2. **"Turn Container Insights off" would break two alarms.** The module's `web_running_tasks` and
   `worker_running_tasks` alarms read the `ECS/ContainerInsights` namespace
   (`modules/django-website/observability.tf:27-52`, `:53-78`) with
   `treat_missing_data = "breaching"` whenever services are enabled (`:43`, `:69`). Turning
   Insights off puts **both into permanent ALARM**. **Insights stays ON**, budgeted at $8/mo.
   Turning it off is a lever available only *after* those two alarms are replaced.
3. **The "free S3 gateway endpoint in the module" does not exist.** `modules/django-website`
   contains **no `aws_vpc_endpoint` of any kind**; the endpoint that fixed the July 2026 NAT-data
   spike lives only in `main/common` (`vpc_endpoints.tf`). A new website VPC therefore starts with
   the July failure mode **un-fixed**. The endpoint must be created at root level against
   `module.website.task_route_table_ids` — no module change needed, but it is not free by default,
   it is free *once someone writes it*.
4. **A new NAT does not replace an existing cost.** `main/aisl` runs inside `main/common`'s VPC
   and depends on its NAT (`main/aisl/common.tf:1-17`, `main/common/network.tf:77-84`), so
   `main/common`'s NAT can **never** be retired at Phase 7. The new NAT is a genuine second line.
   Budget A still clears its bar with it — but not for the reason this section previously gave.
5. **Aurora Serverless v2 was re-examined at a 0-ACU floor and is still rejected.** The floor can
   now be 0, but 0 means *paused*, and the dev readiness probe touches the database every 30
   seconds forever (the ALB contract is `/health/ready`, matcher `200`, no redirects), so the
   pause never fires and it bills ≈ **$43.80/mo** — 2.8× the recommended dev option. See 14.4.
6. **§9.4's apex swap was incomplete.** CloudFront returns `CNAMEAlreadyExists`; the
   `aws cloudfront associate-alias` procedure is required. Corrected in §9.4.
7. **§7.4's teardown order understated the transitional bill.** Sandbox Datamailer retires after
   CMP, not before it. Corrected in §12 7.4; the effect on 14.5 is noted there.

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

### 14.3 Budget A — the website, measured against CMP

Volume assumptions behind the lines below: ~50 GB/mo NAT egress, ~4 GB/mo log ingest, ~0.3 ALB
LCU, ~5 GB ECR, CloudFront inside its permanent 1 TB / 10 M-request free tier.

| Line | Basis | Est. $/mo |
| --- | --- | ---: |
| ALB hours + LCU + 2 public IPv4 | module as-is | 27.45 |
| NAT gateway hours + EIP + ~50 GB data | single NAT (`nat_gateway_mode = "single"`) | 41.09 |
| **S3 gateway VPC endpoint** | gateway endpoints are free — **but must be written at root level; the module has none** (correction 3) | 0.00 |
| Fargate **ARM64** prod: 2× web 0.25 vCPU/0.5 GB + 1× worker 0.25/0.5 | `task_cpu_architecture = "ARM64"` | 21.63 |
| Fargate **ARM64** dev: 1× web 0.25/0.5, plus per-deploy migration tasks | §9.2 | 7.31 |
| RDS `db.t4g.small` single-AZ (D9) + 20 GB gp3, 7-day backups — website prod | `database_*` inputs | 28.82 |
| RDS `db.t4g.micro` single-AZ + 20 GB gp3 — **shared dev database** (D29) | dev website + dev Relay, two logical databases | 15.68 |
| KMS ×1 + Secrets Manager ×10 (6 prod, 4 dev) | — | 5.20 |
| CloudWatch logs (prod 30 d, dev 14 d) + 11 alarms | `log_retention_in_days = 30` | 4.10 |
| **ECS Container Insights — ON** | correction 2; widest error bar in this table ($3–15) | 8.00 |
| CloudFront prod + dev, `PriceClass_100` | inside the free tier | 0.00 |
| WAFv2 ACL + 2 rules + volume, production distribution only | spec 08:143-151 | 8.00 |
| ECR ~5 GB + S3 (media + legacy tree + versions) | — | 0.70 |
| Route 53 apex zone + queries | `main/dns` | 1.00 |
| maintenance / slack-redirect / courses-redirect Lambdas + HTTP APIs | negligible traffic | 1.00 |
| **Budget A total** | | **169.98** |

| | |
| --- | ---: |
| CMP bar (14.1) | 180 – 225 |
| **Budget A** | **169.98** |
| **Verdict** | **PASS** |

**Levers, each independent** — listed so the headroom is visible, not because any is recommended:
dev logical databases on the production instance instead of a dedicated dev instance (D29)
−$15.68; NAT instance instead of gateway (D32) −$33.63; Container Insights off **only after
replacing the two alarms it feeds** −$8.00. Two things Budget A can absorb without breaching the
bar: website RDS Multi-AZ (D9 flipped) +$26.28 → $196.26, and prod web tasks at 0.5 vCPU/1 GB
+$14.42 → $184.40.

### 14.3b Budget B — Relay, judged separately

| Line | Est. $/mo |
| --- | ---: |
| SES postage: 563,333 newsletter + 30,000 transactional + ~28 GB message data | 62.69 |
| RDS `db.t4g.medium` single-AZ + 50 GB gp3 (D30) | 58.89 |
| EC2 `t4g.small` + root/spool gp3, private subnet, **no public IPv4** | 14.86 |
| SNS + SQS + Secrets ×3 + KMS + logs/alarms + NAT share + S3 | 7.59 |
| **Relay production** | **144.03** |
| Relay dev (`t4g.micro`, database on the shared dev instance, dry-run-dominant SES) | 11.45 |
| **Budget B total** | **155.48** |

The postage line moves with cadence, not with infrastructure: at the 5–7 newsletters/month stated
to AWS support rather than a strict weekly cadence it is **$56–91/mo** (E.1). Settle it with D31.

### 14.3c The combined invoice, and the transitional peak

| | Est. $/mo |
| --- | ---: |
| Budget A + Budget B, steady state | **325.46** |
| Today: CMP + `main/common` (14.1) | 180 – 225 |
| Today: sandbox Datamailer, CMP's actual mail path | 13 – 15 |
| **Change** | **+$85 to +$132/mo** |

**During the migration everything runs at once**: CMP + `main/common` ($180–225), sandbox
Datamailer ($13–15), sandbox Relay ($13–15), `sandbox/website` ($65–85), Budget A and Budget B —
a peak of roughly **$597–665/mo**, i.e. ≈ $305/mo above steady state, ≈ $915 for a three-month
overlap. **Compressing that overlap is worth more than any single sizing decision in this
document.** Two things make the real peak lower than the table: before `services_enabled = true`
the new website root costs roughly its plumbing only (≈ $121/mo with zero Fargate), and Budget
B's SES line is near zero through E.6's `dry_run` shadow week.

After Phase 7 the harvest removes the CMP line (≈ $180–225), `sandbox/website` (≈ $65–85: its own
ALB + IPv4s + `db.t4g.micro` + two Fargate tasks + logs/KMS/secrets, per
`sandbox/website/terraform.tfvars.example` sizing), sandbox Relay (≈ $13–15) and — **last, after
7.1** — sandbox Datamailer (≈ $13–15). `main/common` is **not** in the harvest: AISL needs its VPC
and NAT (correction 4). The owner's rejected second-stack dev would have added ≈ +$150/mo; the
shared-infra dev (§9.2) adds ≈ $7–16 depending on D29.

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
| Logs 30 d | Ops tradeoff: shorter forensic window; keep security-relevant logs (WAF/CloudFront/ALB) on their own retention if 30 d is too short for incident response |
| ~~Container Insights off~~ | **Withdrawn (correction 2).** `web_running_tasks` and `worker_running_tasks` read the `ECS/ContainerInsights` namespace with `treat_missing_data = "breaching"`; turning Insights off puts both into permanent ALARM, which is an availability-detection regression bought for $8/mo. Available only after those two alarms are replaced |
| Single-AZ RDS (D9) | **Yes — availability.** AZ failure ⇒ restore-from-backup RTO (hours). Named, deliberately accepted until course operations move |
| Single NAT (not per-AZ) | **Yes — availability.** NAT-AZ outage stalls egress (GitHub/Relay sync), not serving. Accepted |
| No NAT at all (public task subnets, sandbox-style `terraform.tfvars.example:29-30`) | **Breaches the floor for production** (private compute) — rejected; the $40 stays |
| Interface VPC endpoints instead of NAT | Not cheaper here: ~5 endpoints × 2 AZ × $0.011/h ≈ $80/mo vs NAT $40, and endpoints cannot reach GitHub/Relay anyway (external egress still needs NAT). Crossover favors endpoints only when NAT *data* is heavy — that was the July failure mode. ⚠️ **It is solved in `main/common` only:** `modules/django-website` has no VPC endpoint at all, so the **S3 gateway endpoint must be written into the new root** or the new VPC ships with the July failure mode un-fixed (correction 3). Interface endpoints: still rejected |
| Aurora Serverless v2 | **Re-examined at a 0-ACU floor and still rejected (correction 5).** 0 ACU means *paused*, not "smaller": the dev service's ALB readiness contract is `/health/ready` with matcher `200`, which touches the database every 30 s forever, so the pause never fires and the bill is 730 × 0.5 × $0.12 ≈ **$43.80/mo** — 2.8× the recommended dev option. Even the optimistic out-of-hours-pause case (≈ $13.06) only matches `db.t4g.micro` while adding 10–15 s cold starts to the environment whose job is to rehearse production, and it switches engine family (extensions, parameter groups, `pg_dump` fidelity, minor-version cadence), so dev would stop being a faithful rehearsal of either instance. Corroborated in-repo: CMP's serverless block is inert against a provisioned `db.t3.medium` (finding 1.2-5) — the account has already paid once for believing a serverless block that was not billing as serverless |
| Fargate Spot / scale-to-zero | For dev-shaped stacks only (`services_enabled=false` exists, `compute.tf:227`); not for prod web |

### 14.5 Highest-leverage savings, ranked (biggest saving : least risk)

1. **Dev shares production infrastructure** (D2, resolved — §9.2): ≈ $7–16/mo marginal instead of
   ≈ $150/mo for a second stack; fidelity tradeoffs accepted knowingly (§9.2), and made
   genuinely low-stakes by D22 (dev carries fake data).
2. **Decommission CMP + the sandbox account stacks at Phase 7** — removes ≈ $270–340/mo
   combined (CMP $180–225, `sandbox/website` $65–85, sandbox Relay $13–15, sandbox Datamailer
   $13–15); the plan's endgame saving. **Timing correction:** the Datamailer $13–15 lands after
   7.1, not at 7.4 (§12), and `main/common` is not part of the harvest at all — AISL depends on
   its VPC and NAT.
2b. **Compress the dual-run overlap.** At ≈ $305/mo above steady state, each month of full
   overlap costs more than any sizing decision in this section saves (14.3c).
3. **Right-size vs the fixture** (t4g.small single-AZ, 0.5-vCPU ARM tasks, 30-day logs) —
   ≈ $500–700/mo avoided vs literal fixture; availability tradeoffs named in 14.4.
4. **Bytes to S3/CloudFront free tier** (legacy site, media, later static) — keeps compute flat
   as traffic grows; free.
5. **ARM64** — ~20% of the Fargate line; free once the image builds multi-arch.
6. ~~**Container Insights off**~~ — **withdrawn** (correction 2): it disables the two runtime
   alarms that prove the services are actually running. Bounded metric cardinality still applies.
7. **PriceClass_100** — small but free given the audience; revisit only with data.

### 14.6 The policy suite is the long pole, and it is credential-free

Three fail-closed blockers stand between this design and any `terraform apply`, all in
`~/git/aws-infra` and all verified by reading the checker. None of them needs AWS credentials,
which is why the amendment is **step 0** of any implementation order rather than a footnote.

- **`SOURCE_DIRECTORY_SCOPES` is a literal four-element tuple** —
  `("modules/django-website", "sandbox/website", "tests/fixtures/website-production",
  "tests/policy")` at `tests/policy/safe_repository_inputs.py:16-20`. **None of the new roots is
  in it.** Adding one also means adding every new `.tf`/`.tftest.hcl` by exact path to
  `EXPECTED_TERRAFORM_CONFIG_FILES`, regenerating
  `tests/policy/website-terraform-source-manifest.json`, and moving the pinned test counts in
  `check_website_terraform_workflow.py` — the accepted-test count (48 today) is asserted as a
  literal string.
- **(a) A production root under the `DataTalksClub` owner cannot pass today.** Any scanned file
  containing `github_repository_owner = "DataTalksClub"` is forced to *also* declare
  `github_deployment_environment = "sandbox"` and four other exact sandbox values or it raises
  **`OIDC004`** (`check_terraform_policy.py:1892-1906`), and every literal OIDC subject must be
  one of exactly two allowed sandbox strings or it raises **`OIDC005`** (`:1865-1876`). Both
  checks must become environment-aware before a production root is scannable.
- **(b) Relay cannot enter scope at all.** `aws_iam_instance_profile` is in the enumerated
  provider inventory (`check_terraform_policy.py:103`) but has **zero** permitted owners —
  `EXPECTED_ALLOWED_IAM_RESOURCE_OWNERS` (`:238-262`) has no entry for it — and unowned IAM types
  fail closed. Relay is an EC2 host and needs one. The amendment must name exact `(file, label)`
  ownership for the instance profile, host/send/deploy roles, their inline policies, and the two
  managed-policy attachments. That is a real review, not a rubber stamp: the check exists so a
  new IAM mechanism cannot appear without a human writing it down.
- **(c) Adding `main/dtc-website` turns an existing grant into a violation.** It already grants
  `cloudfront:CreateInvalidation` + `GetInvalidation`, scoped to the legacy distribution ARN, to
  the legacy static-site publisher role (`main/dtc-website/legacy_static_site.tf:459-469`). That
  is legal only because the root is outside the scan today. Sequence the scope change with the D4
  invalidation decision (D33).
  **Related documentation defect found while verifying this:** `main/dtc-website/README.md:141-143`
  states that *"no `cloudfront:CreateInvalidation` permission is granted anywhere in this root"*.
  Its own Terraform grants exactly that, 300 lines away. Same class as finding 1.2-2 — fix the
  README in whichever issue touches that root next.

One more gap in the same family, worth folding into the amendment issue:
**`main/maintenance-page` has no deployment gate.** `main/courses-redirect` gates all its
resources on `var.enable_deployment` (default `false`) with a `check` backstop;
`main/maintenance-page` has no such variable, no backend block, no `allowed_account_ids` and no
tags — so `terraform apply` at defaults stands up a live public HTTP API immediately. "Inactive"
there means only that nobody has applied it (§11 6a.1 already records the 200-vs-503 defect in
the same root).

---

## 15. Migration scripts — complete specifications

Per `_docs/PROCESS.md`, each *new* script lands through a groomed issue; specs below are the
grooming input. **Reuse rule:** URL parity checking is NOT reinvented — the compatibility harness
already does it (`scripts/build_legacy_manifest.py` crawl/merge/compare/validate,
`_docs/compatibility/README.md:199-233`).

**Before writing any of these, read
[`_docs/design/specs/script-inventory.md`](../design/specs/script-inventory.md)** — it inventories
what already exists under `scripts/` and, critically, records that the adoption ledger pins
14 files there (`_docs/adoption/course-platform/copied-files.tsv`, asserted at exactly 768 rows by
`core/tests/test_course_platform_adoption.py:238`), so "unreferenced" does not mean deletable.
This section owns only the *new* migration scripts.

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
| 5 | **Silent monitoring failure at the Relay cutover**: the estate's only email canary — CMP's 15-minute `monitoring_datamailer_health` probe and the `datamailer.outbox_*` alarms (`cmp_deadline_reminder.tf:98-110`, `observability.tf:11-22`) — keeps passing while watching a system that no longer sends the mail. Green alarms, no email, nobody notified. Compounded by the sandbox stacks' empty-topic bug (alarms with zero subscribers, §11E E.3) | High if E.5b item 1b is skipped; zero once done | All email failure detection | deliberate test alarm → confirmed human notification, rehearsed at E.6 stage 0 | E.6 hard precondition: re-point probe + both alarms at Relay and prove a live subscriber **before** real traffic; rehome the probe out of CMP before teardown (step 7.0a — same trap class as D14) |
| 6 | GoDaddy forwarders (`www`, `join`) die at NS move with no AWS replacement live | Certain unless pre-built | Slack onboarding funnel + www traffic | curl checks in 1.5 verify list | 1.2 (join custom domain) and Phase-2-first ordering for www; both built and verified pre-delegation |
| 7 | `prod.datatalks.club` (or the S3 copy + new site together) creates duplicate-content/canonical conflicts | Medium | Rankings for the whole editorial corpus | Search Console coverage + canonical monitoring (spec 02:280-291) | The four §9.3 controls (edge+app `noindex` on `prod.` for its entire life, restrictive robots, no `prod.` sitemap, apex canonicals everywhere); `prod.` 301s to apex after the swap (§9.4) |
| 8 | courses cutover breaks API consumers (scripts, certificate tooling, email links) | Medium | Learner-facing workflows | maintenance-Lambda 503 metrics; redirect-Lambda unknown-path metrics (spec 08:300) | Stage A′ keeps real compat endpoints on the new stack (no cross-host redirect for APIs, spec 02:152); redirect Lambda only after the consumer gate; 503-not-200 fix (1.2-6) |
| 9 | **Relay audit findings (delivered): not ready to send production email.** Outbound gate: C1 no live-path retry (tests cover dead code), C2 SNS signature bypass, C3 no database backup (suppression list unrecoverable), C4 no production deploy path. Separate dormant class: measured inbound-mail DoS against outbound (C5 OOM at ~150× memory amplification on a 1 GiB host, C6 disk-fill to read-only Postgres in ~25 emails, H8–H12) — dormant **only while inbound stays disabled** | Certain if E.6 starts before remediation; inbound class certain if inbound is ever enabled un-fixed | All outbound email; SES reputation | E.5a revised gate; E.6 stage −1 shadow week; per-fix groomed-issue evidence | **E.5a minimum list (C1–C4, H2, H5) closed before any real mail; production config outbound-only (`SQS_INBOUND_EMAIL_QUEUE_URL` unset); inbound enablement is a separately gated future project** |
| 10 | Spec's no-Datamailer-send promise is **config-enforced, not code-enforced**: ~3,900 lines of live-callable client with reachable call sites (`courses/views/homework_confirmation.py:8-9,149`); only `deploy/task_definitions.py:36-42` env pins (`DATAMAILER_URL: ""`, dry-run `"1"`) keep it inert — one env var from live sends during exactly the kind of change an email migration makes | Medium during Phase E churn | Duplicate/unauthorized sends; spec breach | `core/tests/test_deployment_release.py:1001` guards the pins; env diff review on every deploy | E.2 hardening issue: fail closed in code (refuse non-empty `DATAMAILER_URL` outside tests or excise send call sites) when Effort 1 repoints to Relay |
| 11 | Cache poisoning / auth-content leak when Phase 5 turns caching on | Low with spec design, High without | Security incident | authenticated canary + poison canaries + log audit (10.6) | Fail-closed classifier, origin-response guard, `min_ttl=0`, TTL-zero one-step rollback; enable pre-cutover on low-stakes hosts first |
| 12 | Mixed-NS window serves divergent answers | Low (parity-gated) | Any record | 15.2 against multiple resolvers during window | Byte-parity before delegation + change freeze during the 48 h window |
| 13 | ACM validation stalls a cutover step | Medium (timing, not correctness) | Schedule | ACM console status | All certs DNS-validated *ahead* of their consuming step (1.2 dual-zone CNAME trick; 2.2 before 2.5) |
| 14 | Un-invalidatable wrong object (bad Cache-Control/Content-Type published, `CreateInvalidation` denied) | Medium | Up to max-age per object | 15.4/15.5 verify passes | Short/moderate max-age chosen everywhere (no `immutable` on mutable trees); worst case bounded at 24 h; guardrail-change path exists (10.4c) if ever needed |
| 15 | State-root blast radius (a bad apply in a broad root touches NS-critical records) | Low | DNS | plan review | D6 dedicated `main/dns` root; courses zone optionally imported for drift detection (0.4); untracked roots (`main/dtc-website`, `main/maintenance-page`) get committed + backends before any further apply |
| 16 | Shared dev/prod infra (D2): dev workload degrades prod via common RDS instance/ALB | Medium over time | prod latency/availability, not data (separate databases + credentials) | RDS connection/CPU/IOPS alarms; ALB target-health per target group (prod-only alarms, CMP precedent `observability.tf:55-58`) | §9.2 fidelity controls: 1-task dev, separate db + creds, explicit listener priorities, throwaway snapshot-restored instance for destructive rehearsals |
| 17 | Running everything at once during the migration. **Corrected upward:** the peak is ≈ **$597–665/mo** against a ≈ $325 steady state — roughly $305/mo of overlap, ≈ $915 for three months — not the ≈ $370–445 previously stated, which omitted Relay's real cost and retired sandbox Datamailer too early | Certain, bounded | Budget | Cost Explorer + budget alarm | §14.3c; compressing the overlap is worth more than any single sizing decision. Phase 7 harvest is scheduled, not aspirational — but `main/common` is not in it (AISL depends on it) and Datamailer lands after 7.1 |
| 18 | **SES quota or send rate falls short of a 130,000-recipient campaign.** Downgraded: the main account has production access (`aws-infra/docs/aws-support/2026-08-09-ses-newsletter-quota-increase.md:19`) and the owner states the quota has been raised, so this is no longer a "sandbox mode" blocker. What is genuinely unverified is the **two live numbers** — the rolling-24-hour quota and the per-second send rate. A weekly newsletter is bursty: at 14/s a campaign takes ≈ 2.6 h, at 200/s ≈ 11 min | Low | E.6 stage 4 only (transactional stages are unaffected at any plausible quota) | E.1 / step 0.6 `aws sesv2 get-account --query 'SendQuota'`, recorded against the campaign size | Front-loaded into Phase 0; if either number falls short, the Service Quotas increase files immediately (multi-day external lead) and only the ramp waits. Record the approved values in the aws-support document, whose own follow-up item to do so was never completed |
| 19 | **Events staleness at and after cutover** — the events pipeline is a manually-run local script in a personal temp dir (§13.8, D16/D17); already realised once: zero future-dated events in the projection on 2026-09-02 (newest = 2026-08-31), which would have shipped an empty upcoming-events section and superseded cohorts (#307) | High until D17 assigns ownership; certain without the gate | Day-one credibility of the new site's events/courses surface | §9.4 gate item 5 pre-swap; the same "≥ N future-dated events" check in post-swap monitoring | Mandatory ≤ 72 h pre-swap re-sync + freshness check; D16 (exporter into version control) and D17 (owner/cadence, later automation) remove the root cause |
| 20 | **The cutover-day import path is unbuilt and unproven** — every rehearsal so far exercised the *sanitized* importer (0 enrollments, synthetic user); the full-fidelity path (**20,009 accounts / 20,907 enrollments / 36,547 submissions** in the newest production export, real PII) has no tool (`load_rds_export.py` empirically non-viable; `review_import` sanitizes by design) and is blocked by verified schema drift (`courses_emailcampaign` + two 0041 tables absent from the `98a2352`-pinned schema; `make review-data` fails closed at `review_import/workflow.py:731`). **Those three tables hold 1/1/11 production rows, not zero — there is no skip-empty escape hatch.** Also the least reversible step once DNS has moved and real writes land | Certain, until the §13.9 work is groomed, built, and rehearsed | Learner data integrity; cutover timeline; privacy (largest PII movement in the migration) | §9.4 gate item 6; drift re-check at freeze; import verification suite (counts/integrity/spot checks) | §13.9: build as a review_import extension via groomed issues; resolve drift properly (no hand-edits); fresh freeze-time export; pre-import target snapshot; disposable-target dry-run before the swap; intermediates deleted and recorded (no erasure reconciliation needed — CMP has no erasure feature, §13.9) |
| 21 | **The aws-infra policy suite blocks every new root, fail-closed, in three independent ways** (§14.6): `SOURCE_DIRECTORY_SCOPES` is a literal four-element tuple containing none of them; `OIDC004`/`OIDC005` force any `DataTalksClub`-owned scanned root to declare sandbox claims; `aws_iam_instance_profile` has zero permitted owners so Relay cannot enter scope; and adding `main/dtc-website` converts its existing `cloudfront:CreateInvalidation` grant into a violation | Certain, until amended | The entire implementation schedule — every apply is gated on the delivery workflow | The suite itself; it fails before credentials are issued | Treat the amendment as **step 0**: credential-free, reviewable in parallel with everything else, and the longest pole. Scope, OIDC environment-awareness, and the IAM-ownership entries are three separate reviews (D33 sequences the fourth) |
| 22 | **Relay's delivery history outgrows its storage, one-way.** 130k/week produces 0.65–1.04 GB per campaign and 34–54 GB/year unpruned; `prune_db_task_results` exists and has never been invoked, because Relay has never had a client. RDS storage autoscaling only ratchets upward | Certain at full volume without a pruning job | Relay availability (a full volume takes PostgreSQL read-only), then a permanent cost floor | Free-storage alarm + data-volume disk alarm (E.5b item 7) | Ship `pruning.tf` in the same change as `main/relay`; prove it before E.6 reaches full send volume. Storage starts at 50 GB with a 500 GB ceiling so the ramp cannot hit the wall mid-campaign |
| 23 | **Relay sends with an empty suppression list.** Datamailer holds the unsubscribe/hard-bounce/complaint state; D15's clean start migrates nothing by default | High if D23's copy is treated as cleanup | SES reputation — i.e. all `@datatalks.club` mail, the same blast radius as risk 4 | Bounce/complaint alarms would catch it *after* the damage; the real detection is a pre-send reconciliation count | D23: the copy is a **precondition of the first production send**, before E.6 stage 1, and sandbox Datamailer stays alive until it is verified. Fallback: seed SES account-level suppression |

---

## 17. What this runbook does NOT cover

- Application feature work, data imports, and their gates — owned by spec 09's milestones and
  `_docs/migration-checklist.md` (this runbook only placed hostname/edge mechanics into M8).
- The direct-sync programme's own design decisions (#272/#273/#276/#278) beyond the sequencing
  interface in 13.3; D12 stays with the owner.
- The per-purpose email approval catalog and template content (spec 09 M6/M8.6; #22, #290):
  Phase E covers Relay's *infrastructure* promotion, the SES identity hand-off, the audit gate,
  and the battle-testing ramp; which individual website purposes go live remains owned by those
  issues within the E.6 stage-3 framework. The delivered audit findings are summarised in
  E.5a/E.5b as gate inputs; the full audit reports, and the remediation work itself, live in
  their own documents and groomed issues per `_docs/PROCESS.md` — this runbook sequences them
  but does not replace them.
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
