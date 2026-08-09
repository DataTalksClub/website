# Account reconciliation runbook

This runbook exercises the #100 identity machinery with synthetic or approved
production-shaped data. It does not authorize a production cutover. Issue #60
owns the production-like copy, final delta/write freeze, provider/worker pause,
backup/restore proof, canaries, and cutover approval.

All commands use `uv`. All mappings and reports must remain below the
project-local `.tmp/` directory with restricted permissions. Never paste a
report containing real account IDs into an issue, log, screenshot, or test
artifact.

## Preconditions

- Use an immutable application revision that can read migrations 0011 and 0012.
- Disable outbound email, Datamailer sync, provider writes, and workers in the
  rehearsal environment.
- Record an immutable 64-character SHA-256 snapshot ID through the approved
  snapshot process.
- Confirm the database backup and restore evidence owned by #60.
- Make sure the reviewed mapping has an independent review reference and
  contains no email, name, social UID, provider payload, token, or session key.

## 1. Expand and inventory

Apply the expansion and normalized-identity migrations:

```console
DJANGO_SETTINGS_MODULE=website.settings.test \
  uv run python manage.py migrate
```

Emit the row-free schema/auth inventory:

```console
DJANGO_SETTINGS_MODULE=website.settings.test \
  uv run python manage.py account_identity_inventory \
  > .tmp/account-identity-inventory.json
chmod 600 .tmp/account-identity-inventory.json
```

Verify `accounts.CustomUser`, `accounts_customuser`, all 21 dependent relation
entries, all three many-to-many surfaces, GitHub/Google/Slack, database
sessions, host-only cookies, and explicit reauthentication.

## 2. Run the no-write duplicate inventory twice

Replace the synthetic value below with the approved snapshot SHA-256:

```console
DJANGO_SETTINGS_MODULE=website.settings.test \
  uv run python manage.py reconcile_accounts \
  --snapshot-id aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa \
  --output .tmp/account-reconciliation-dry-run-1.json

DJANGO_SETTINGS_MODULE=website.settings.test \
  uv run python manage.py reconcile_accounts \
  --snapshot-id aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa \
  --output .tmp/account-reconciliation-dry-run-2.json

cmp .tmp/account-reconciliation-dry-run-1.json \
  .tmp/account-reconciliation-dry-run-2.json
```

The dry run performs no writes and no outbound side effects. Review exact
source IDs, candidate counts, hashed evidence kinds, risk codes, relationship
counts/checksums, and the report checksum. It never chooses a survivor.

## 3. Review the mapping

Copy `_docs/runbooks/account-reconciliation-mapping.example.json` into `.tmp/`
and replace only the synthetic IDs, snapshot ID, and review reference through
the approved review process.

```console
chmod 600 .tmp/account-reconciliation-mapping.json
```

Each mapping must have:

- distinct positive source and survivor IDs;
- `verified_normalized_email` or separately documented
  `manual_verified_ownership` evidence;
- a decision for every differing profile/preference field;
- `survivor_only` when authority signatures differ;
- no mapping chains or cycles.

Do not approve same-course enrollment, same wrapped-period, same project-vote,
conflicting provider UID, missing verified-email evidence, inactive source, or
unavailable survivor conflicts. They belong in quarantine and require a new
reviewed disposition.

## 4. Apply idempotently

```console
DJANGO_SETTINGS_MODULE=website.settings.test \
  uv run python manage.py reconcile_accounts \
  --snapshot-id aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa \
  --mapping .tmp/account-reconciliation-mapping.json \
  --apply \
  --output .tmp/account-reconciliation-apply-1.json

DJANGO_SETTINGS_MODULE=website.settings.test \
  uv run python manage.py reconcile_accounts \
  --snapshot-id aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa \
  --mapping .tmp/account-reconciliation-mapping.json \
  --apply \
  --output .tmp/account-reconciliation-apply-2.json
```

The first apply must retain source rows, preserve survivor IDs, create exactly
one alias per source, disable source human management principals, reparent only
the allowlisted ownership relations, and reconcile logical relationship counts
and checksums in one transaction. Portable compare-and-set claims must reject
any source or survivor state that changes after validation; no database-specific
locking path is required. The second apply must report `idempotent_replay: true`
and create no extra alias or apply run.

A blocked mapping writes only redacted quarantine/audit evidence and exits with
an error. It must not create an alias, move a relation, link a provider, or
change account authority.

The quarantine row and denial audit are atomic. A stale or missing survivor
uses the valid source account for audit attribution; when neither reviewed
account is valid, the event uses `system:account-reconciliation` with no user
foreign key. The command must return the controlled `quarantined` report and
must never print a raw database integrity error. An unexpected integrity
failure during apply rolls back all merge writes and reports only
`reconciliation_integrity_conflict`.

## 5. Verify the application

Use synthetic returning learner and staff accounts to verify:

- normalized-email and legacy-username login;
- GitHub, Google, and Slack callbacks with verified and conflicting claims;
- the same account ID on `/`, `/courses/`, account settings,
  `/api/v1/account/identity/`, and the compatibility identity endpoint;
- preserved profile, preferences, enrollment, submission, review, score,
  certificate, complaint, vote, and audit ownership;
- a pre-release source session rebounds to the survivor while unrelated
  sessions remain valid;
- logout, expiry, password change, disablement, and staff-session revocation;
- Studio/course-admin navigation follows explicit capabilities;
- `/accounts/continue/` carries only safe `next`, rejects auth-flow loops,
  requires normal login across hosts, returns a canonical-host authenticated
  session directly to its intended path, and creates no account or cross-host
  credential;
- literal, mixed-case encoded, and double-encoded dot segments, slash or
  backslash variants, duplicate separators, and relative references are
  normalized before the login/logout/continuity/provider-callback deny check;
  malformed, control-bearing, scheme, authority, and protocol-relative values
  fall back to `/`, while ordinary content/course query strings and fragments
  remain intact;
- authenticated responses are private/no-store;
- no outbound email/provider operation occurred.

Capture only synthetic desktop/mobile screenshots under `.tmp/screenshots/`.
Scan screenshots, reports, and logs for forbidden email, provider payload,
social UID, password, session, cookie, token, and credential values.

## 6. Validate the rollback window

Create a synthetic account-owned write after apply, then run:

```console
DJANGO_SETTINGS_MODULE=website.settings.test \
  uv run python manage.py reconcile_accounts \
  --snapshot-id aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa \
  --mapping .tmp/account-reconciliation-mapping.json \
  --rollback-check \
  --output .tmp/account-reconciliation-rollback-check.json
```

Rollback is an application-revision rollback, not a reverse data move. The
check requires all aliases and absorbed source rows, one authenticating
survivor, retained post-cutover writes, no reversed relationships, and no
global session flush. Pin or pause workers/provider callbacks before changing
application revision. A login/session/link/conflict threshold breach is a #60
release rollback trigger.

Do not reverse 0011/0012 after reconciliation has moved relationships. Their
backward migration exists only to prove the empty pre-apply expand window. Do
not delete aliases or absorbed rows until #60 completes the full rehearsal,
production verification, restore proof, and rollback window and the privacy
process separately approves contraction.
