# Slack invite and approval flow

Owner direction: intake `be517170-c46d-41db-ac10-d0a4ebdcc924`, 16 November 2026,
points 1–6 and Orin notes t2545u, t2546u, t2547u. This direction supersedes the
automatic shared-link onboarding in `open-decisions.md` sections 19/21 and
`05-events-registration-email.md` only for the new website release candidate.

## Deployment boundary

Build, enable, and verify the flow on `https://prod.datatalks.club`. It is a
release candidate, and enabling there is explicitly wanted. Do not change
`join.datatalks.club`, current signup emails, DNS, or Slack workspace settings.
Do not post to Slack or send test invites/emails to real people. Every controlled
test recipient must match `dev+...@dtcdev.click`. Preserve newsletter subscription
semantics; a Slack approval is separate from newsletter subscription.

## People, approval, and accountability

An authenticated applicant with verified email completes a private questionnaire:
who they are, where they work (including an explicit not-currently-working
answer), and what they do. The account owns one application and sees its pending,
approved, joined, rejected, or banned state. Personal answers are visible only to
the applicant and authorized reviewers; sharing a vouch request does not publish
the questionnaire or email address.

An existing, verified, active Slack member can sponsor an applicant through an
invitation or a shareable vouch-request link. A site account alone does not prove
Slack membership. The vouch page must display this before confirmation:

> By vouching, you take responsibility with your own reputation for inviting this
> person. If they turn out to be a spammer, you will be banned too. Only vouch for
> people you really know.

Vouching requires an explicit acknowledgement and a CSRF-protected authenticated
POST. Self-vouching, banned sponsors, replay that changes the accountable sponsor,
and unverified accounts are denied. Anonymous visitors may reach the shareable
page and sign in, but cannot vouch. A token alone cannot approve or join Slack.

Approval happens on the website, with a staff-only review queue and explicit
approve/reject actions. Course participants are automatically approved using the
current course-domain participation/registration records, never a self-reported
claim or imported legacy projection. A ban always overrides automatic approval.
Record approval source, actor, time, and accountable inviter/voucher where one
exists. Do not send repetitive approval-request emails to Alexey by default.

Persist the inviter-to-invitee chain. Banning a member revokes their website Slack
access and every descendant's access, transitively, and prevents further inviting
or vouching. Apply the mutation atomically and safely on repeated requests. Retain
the chain for review; do not erase its evidence by reparenting an approved member.
This explicit website moderation action is separate from read-only Slack join
monitoring. Actual Slack account deactivation must be reported separately from
website access revocation and cannot be claimed without provider evidence.

## Link delivery and join detection

Only an approved, unbanned applicant receives a site-owned opaque redirect link.
Bind it to the applicant, store no raw Slack invite in domain records, and allow
one atomic redemption. GET/HEAD previews must not consume it; use an explicit
authenticated POST to redeem. Replays, wrong-account access, rejection, and bans
fail closed. Disable outstanding links as soon as a matching Slack join is
detected. Match through verified identity evidence; never infer an account from
an unverified email claim. Provider outages preserve approval and show a safe
unavailable state rather than leaking a link or pretending a join was detected.

Resolve the Slack destination only at the server boundary from the approved
secret channel. It must never appear in HTML, email, JSON, database snapshots,
logs, screenshots, or reports. A normal HTTP redirect necessarily exposes its
destination to the redeeming browser in the `Location` header; it cannot make a
shared Slack URL unknowable after redemption. Document that limitation. If
strict destination secrecy or individual invitation revocation is required,
the implementation needs an appropriate Slack invitation API rather than a
shared invite URL. Do not claim that consuming a site link revokes a copied
Slack invite URL.

## Read-only join monitoring

Probe existing dataops-managed credentials without printing values. Determine
the actual workspace plan and effective token scopes through safe read-only
provider calls. Investigate `team_join`, available admin invite data,
`admin.inviteRequests`, and Audit Logs API. Do not change app scopes, event
subscriptions, workspace settings, or membership to make monitoring work.

Record each observed Slack join idempotently by workspace/member/event identity.
Classify it as website-approved, outside website approval, or unresolved when
identity/approval/provider evidence is insufficient. Keep observed inviter
evidence distinct from the website sponsor; a missing inviter must remain
unknown. Expose an authorized on-site report/alert for outside-flow joins and
unresolved evidence. The monitor never approves, bans, kicks, invites, posts,
or changes Slack settings. Reporting ingestion gaps and unavailable inviter
attribution is required; silence is not proof that no bypass occurred.

The final report states which plan/scopes were actually observed, which APIs
worked, which returned permission/plan restrictions, whether join ingestion is
live, and whether inviter attribution is available. No tokens or member emails
appear in reports. Reserve a section for Orin's separate investigation of how
banned accounts got in; do not invent her findings.

## Verification and delivery

Focused tests cover approval and permission denial, real course eligibility,
the exact vouch warning and acknowledgement, banned/self sponsors, cascade bans,
atomic single-use redemption, join-triggered invalidation, idempotent monitoring,
and approved/outside/unresolved classification without automatic action.
Inspect application, vouch, and review/report pages at desktop and mobile sizes.
Verify private responses and redirect responses are no-store.

Create a PR, obtain independent tester and PM acceptance, require green checks
on its exact head, merge, deploy to the RC, and verify with a controlled test
applicant. Record exact commit/PR/check/deployment identities and limits in
`~/tmp/agent-reports/2026-11-16-slack-invite-approval.md`. Do not mark unfinished
provider integration or unavailable live verification as passed.

## Questions for Alexey

- Who should have ongoing approval/moderation responsibility beyond existing
  authorized site reviewers?
- If the existing plan/tokens cannot expose inviters or joins, which additional
  access or plan change should be authorized, and by whom?
- Does “banned too” require immediate Slack account deactivation as well as
  website access revocation? Which provider capability is approved for that?
- Is the unavoidable browser visibility of a redeemed shared Slack destination
  acceptable, or is a per-person Slack invitation mechanism required?
- What should Orin's separate banned-account investigation change, once her
  findings are available?
