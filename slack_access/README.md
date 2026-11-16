# Website Slack access

The approved design is `_docs/specs/slack-invite-approval.md` (issue #521).

Entry: `/community/slack/`; review: `/studio/slack/applications/`; report:
`/studio/slack/joins/`; explicit website moderation: `/studio/slack/members/ban/`.
The public `/slack` page links to the application. No existing signup mail,
join.datatalks.club route, or Slack workspace setting is changed.

Deploy migrations normally. Resolve these **secrets only at runtime**:

- `SLACK_INVITE_URL`: existing HTTPS `join.slack.com/t/...` destination.
- `SLACK_MONITOR_TOKEN`: existing token with `users:read` and `users:read.email`.
- `SLACK_WORKSPACE_ID`: expected workspace identity, checked by `auth.test`.

A reviewer needs an active staff session, `core.access_studio`, and explicit
`slack_access.review_applications`. Website bans additionally require explicit
`slack_access.ban_members`. Superuser status alone grants neither permission.
No default approval emails or Slack posts are sent. Approved applicants collect
their single-use site invitation on their application page. GET/HEAD previews
never consume it; POST does, atomically. Unredeemed links expire after seven days.
A newly issued link replaces an unused earlier link. Consumption cannot be reset
through the applicant UI. Joining invalidates any outstanding link.

Run `uv run --frozen python manage.py monitor_slack_joins` in the deployed
application environment on a serialized periodic schedule. It fetches every page
before any database mutation. A failed or incomplete fetch preserves the last
successful state. Output is aggregate counts only. The first complete fetch is
a historical baseline, never a mass bypass alert. Subsequent unseen members appear
in the authorized report. No emails, tokens, raw provider responses or snapshots
are logged or written to files. Existing members are bound only to a unique
verified website email. Missing/ambiguous identity never grants sponsor authority.

`users.list` does not prove actual join time. A timestamped observation can be
classified website-approved only if approval preceded the join. Poll-only approvals
are unresolved; joins without email are unresolved; other unapproved joins are
reported outside-flow. Ordinary-member inviter is unknown with the observed scopes;
documented guest inviter evidence is retained separately from website sponsors.
Review the report's last-success time: an old timestamp means a coverage gap.
A first snapshot and polling cannot recover join-and-leave activity between polls.

The monitor has no Slack write method. Ban is an explicit website operation,
including accountable ancestors and all descendants, and does not deactivate
Slack accounts. HTTP redirects expose the Slack destination to the redeeming
browser; consuming the website link cannot revoke a copied shared Slack URL.
Additional provider access and owner decisions are required for stronger guarantees.


Staff list/review, ban preview/confirmation, and join reports are registered
capabilities shared with `/api/v1/admin/slack/`. Both adapters use the same
services, explicit permissions, audit, idempotency and stale state/scope checks.
`site_admin` includes the two Slack permissions; `sync_studio_roles` applies the
reviewed role manifest after migration without choosing new group members.


Active enrollment writes reconcile an existing pending application. A missed
bulk-import signal can be reconciled with the explicit CSRF-protected course
eligibility action. GET and HEAD never change application state. Authenticated
website mutations admit at most30 attempts peridentity perminute using portable
database write fences. Throttles return429 plusRetry-After, storage failure503;
repeated vouch confirmation never appends another decision. Counter rows store
only a hashed subject/current window and expire after one day of inactivity.
