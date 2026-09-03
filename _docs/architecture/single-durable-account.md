# Single durable account

Issue #100 evolves the Course Management Platform account in place. The only
authentication identity is `accounts.CustomUser`, stored in
`accounts_customuser` and selected by `AUTH_USER_MODEL`. Public content,
courses, account settings, Studio, session-authenticated APIs, the compatibility
token API, and human management principals all resolve this account. No website
user, learner profile, staff user, content-derived account, or second session
model is introduced.

GitHub-owned public `Person` records remain editorial identities. A future
reviewed link may refer to an account, but it cannot create an account, copy
profile values into authentication data, or grant authority.

## Inventory

The machine-readable inventory is emitted without row data:

```console
DJANGO_SETTINGS_MODULE=website.settings.test \
  uv run python manage.py account_identity_inventory
```

Its checksum covers the complete inventory contract. The current categories
are:

| Category | Fields or records | Migration handling |
| --- | --- | --- |
| Identity | primary key, username, email, normalized email, identity state, password hash, last login, joined date | Preserve the row and primary key. A reviewed duplicate becomes `absorbed`; it is never deleted during this window. |
| Authority | active/staff/superuser flags, account role, groups, user permissions | Keep survivor authority only. Source groups and permissions remain provenance on the disabled source and are not unioned. |
| Profile | names, certificate name, country, region, registration role, public URLs, About me | Preserve unchanged values. Every differing value requires an explicit `source` or `survivor` decision. |
| Preference | dark mode and preferred timezone | Preserve the survivor unless a reviewer explicitly selects the source value. |
| External consent | Datamailer preferences keyed by normalized contact email | Never merge or update during reconciliation. The survivor contact remains authoritative; a source contact is left untouched for the later privacy process. |
| Course consent | registration newsletter choice and enrollment display/privacy flags | Preserve each owned row unchanged. Same-scope collisions fail closed instead of combining choices. |

The dependent relationship inventory contains 21 direct or through-table user
references:

| Relationship | Handling |
| --- | --- |
| `accounts.CustomUser_groups.customuser` | Keep as source authority provenance; do not union. |
| `accounts.CustomUser_user_permissions.customuser` | Keep as source authority provenance; do not union. |
| `admin.LogEntry.user` | Keep the historical owner and resolve through the alias. |
| `management_auth.APIPrincipal.user` | Disable a source human principal; do not transfer its authority. |
| `management_auth.APIPrincipal.created_by` | Keep provenance and resolve through the alias. |
| `management_auth.APICredential.created_by` | Keep provenance and resolve through the alias. |
| `core.AuditEvent.actor` | Keep provenance and resolve through the alias. |
| `core.StaffSession.user` | Reparent to the reviewed survivor; the survivor's current capability still controls access. |
| `core.Operation.actor` | Keep provenance and resolve through the alias. |
| `accounts.Token.user` | Keep the compatibility owner and resolve the request through the alias. Token hardening remains owned by #52. |
| `courses.CourseRegistration.user` | Reparent without changing registration or newsletter fields. |
| `courses.Enrollment.student` | Reparent exactly once; duplicate course ownership is quarantined. |
| `courses.Submission.student` | Reparent exactly once. Its enrollment remains the same retained row. |
| `courses.ProjectSubmission.student` | Reparent exactly once. Its enrollment remains the same retained row. |
| `courses.LeaderboardComplaint.reporter` | Reparent exactly once. |
| `courses.LeaderboardComplaint.resolved_by` | Reparent exactly once. |
| `courses.ProjectVote.voter` | Reparent exactly once; duplicate submission ownership is quarantined. |
| `courses.UserWrappedStatistics.user` | Reparent exactly once; duplicate wrapped-period ownership is quarantined. |
| `account.EmailAddress.user` | Move verified addresses only. Unverified claims remain on the absorbed source. |
| `socialaccount.SocialAccount.user` | Move only after the mapping has verified ownership evidence and provider UID conflicts are absent. |

The three explicit many-to-many surfaces are `CustomUser.groups`,
`CustomUser.user_permissions`, and `Course.students` through `Enrollment`.
Logical relationship checksums resolve source IDs through the prospective alias
before apply and the durable alias after apply. Counts and logical checksums must
match before the transaction can commit.

## Authentication and account linking

Verified normalized email is the new learner lookup. The original username and
email columns remain compatibility identifiers. Login checks an eligible
normalized-email candidate first and fails closed if that identity is
ambiguous; it falls back to a unique legacy username only when no account owns
the supplied normalized email. `quarantined` and `absorbed` rows are never
eligible for a new authentication.

GitHub, Google, and Slack remain the configured Allauth providers. The social
adapter has these rules:

1. Social signup is closed. A callback cannot create a second website account.
2. Only adapter-provided, verified email evidence may select an owner. Raw
   provider response fields and notification emails are ignored.
3. Zero or multiple owners, multiple identity claims, unavailable owners, and
   provider UID conflicts create a redacted quarantine outcome and return a
   generic conflict page.
4. A unique verified owner is activated with a portable compare-and-set update
   and then connected. Any identity-state drift between validation and update
   fails closed instead of linking a stale owner. Provider payloads, email
   addresses, access tokens, and social UIDs are never written to the audit or
   metrics payload.
5. Existing provider connections resolve an absorbed ID through the durable
   alias and must still pass provider UID validation.

Allauth email/password reset routes retain their adopted disabled behavior.
Account settings, login connections, logout, and provider callbacks stay on the
same host and account system.

## Sessions and host continuity

Django database sessions remain authoritative. Cookies are host-only,
HttpOnly, `SameSite=Lax`, and use Django's configured absolute lifetime. The
identity release does not change cookie scope or flush the session table.

A session created before a duplicate is absorbed still has the source password
hash and can be decoded by Django. On its next request, the durable session
middleware resolves the reviewed alias, replaces only that session's owner and
auth hash with the survivor, and leaves every unrelated session unchanged. A
missing, inactive, or unsafe survivor flushes only the affected session.
Standard Django password-change, expiry, logout, staff-session revocation, and
account-disable checks remain in force.

Cookies cannot cross from `courses.datatalks.club` to the canonical host under
the approved host-only policy. `/accounts/continue/` therefore performs visible
explicit reauthentication:

- the URL contains only a validated same-host path in `next`;
- absent, external, encoded self-references, and login, logout, continuity, or
  provider-callback destinations resolve to `/` and cannot form an auth loop;
- browser-equivalent parsing happens before that decision: bounded percent
  decoding, slash/backslash normalization, relative resolution, duplicate and
  dot-segment removal, and scheme/authority/control rejection produce one
  canonical local path; only its validated query and fragment are retained;
- an already-authenticated session on the canonical account host returns
  directly to the validated destination without a second login;
- it contains no session, token, code, email, provider UID, or handoff secret;
- replay and refresh merely repeat the same redirect and grant no authority;
- no new expiry or revocation mechanism is needed because no credential is
  created;
- the canonical login cycles/authenticates a normal Django session, so fixation,
  logout, expiry, password change, and disablement use existing Django rules;
- `Referrer-Policy: same-origin` prevents the route from becoming a
  cross-origin referrer channel;
- the destination resolves the same durable account or the link fails closed;
  it never creates an account.

The compatibility host may continue serving its own existing host-only session
until normal expiry, logout, password/security invalidation, or account
revocation. This exception is a reauthentication plan, not cross-host SSO.

## Shared surfaces and API identity

The copied CMP shell is the authentication-aware shell for the whole site.
Signed-out pages show one same-host Login action. Signed-in pages show Courses,
Account settings, Login connections, one Logout action, and the account ID as a
non-visual test hook. Studio is shown only to active staff with the explicit
`core.access_studio` permission. Course admin additionally requires the
`site_admin` or `course_operator` role.

`GET /api/v1/account/identity/` returns the current session account ID.
`GET /api/account/identity/` characterizes the adopted compatibility token and
resolves an old token owner through the same alias. Human management API
principals retain a foreign key to the same account; a principal owned by an
absorbed source is disabled rather than transferred. API credentials and
capabilities are never unioned by reconciliation.

Authenticated HTML and API responses retain the private/no-store response
policy. Content-only review projections do not create users, email addresses,
social accounts, sessions, enrollments, registrations, tokens, staff records,
aliases, quarantine records, or reconciliation runs.

## Audit and metrics contract

The implementation emits redacted outcomes for link success/conflict, merge
success/denial, session rebound/failure, explicit reauthentication, rollback
verification, returning login, and the existing user-created monitor. Reports
contain only snapshot/checksum values, exact numeric mapping IDs required for
review, reason codes, counts, and logical checksums. Runtime OAuth response
data, raw email, social UID, password material, session/cookie values, API
tokens, and handoff credentials are forbidden.

A merge denial persists its quarantine row and audit outcome in one
transaction. Its audit actor is the still-valid source account, then a valid
survivor only when the source is unavailable, and otherwise the non-user
`system:account-reconciliation` authority with a null actor foreign key. A
missing, inactive, absorbed, or quarantined account is never written into the
audit actor foreign key. Apply uses database-portable compare-and-set claims
over the reviewed source and survivor state; it has no vendor-specific branch
or row-lock dependency. Apply-time drift or integrity failures roll back all
alias, relationship, authority, and run writes before they become the redacted
`reconciliation_integrity_conflict` review outcome.

Synthetic tests and screenshots use `.invalid` identities and synthetic UIDs
only. Production-like rehearsal and any evidence involving real account data
remain owned by #60 and must not be copied into this repository or issue.
