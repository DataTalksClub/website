# Development owner bootstrap

This runbook applies only to `web.dtcdev.click`. It does not import or reuse a production user,
password hash, session, provider account, or API token.

## Application contract

The deployed image provides:

```text
uv run python manage.py bootstrap_development_owner
```

The command:

- exits before prompting unless `DTC_ENVIRONMENT=development`;
- requires an interactive terminal for both standard input and the non-echoing password prompts;
- accepts no email or password command-line option;
- prints only a fixed result category and safe object/revocation counts;
- creates or reconciles one exact owner, the code-owned `site_admin` role, one linked human API
  principal, and the credential-less `development-automation` service principal;
- requires explicit confirmation before resetting an existing owner password; and
- revokes owner staff sessions and human API credentials on reset, while preserving the separately
  visible service credential lifecycle.

The owner signs in at `/accounts/login/`, then manages the development automation credential at
`/studio/access/api-credentials/`. Django admin remains a separate surface at `/admin/`.

## Authorized operator handoff `[HUMAN]`

Infrastructure must first provide a dedicated one-off ECS task using the released application image,
the development database connection, and an interactive ECS Exec channel. The task must not be the
web, worker, or ordinary migration task. The final operator mechanism should bind a dedicated AWS
Secrets Manager record containing only the confirmed development owner email and new password to
that one-off workflow without placing either value in a task override, shell history, GitHub secret,
CI input/output, issue comment, log, screenshot, or reusable task definition.

After that external mechanism is reviewed, the authorized operator opens the interactive channel and
runs the command above. They enter the confirmed identity and password only through its prompts and
record only the fixed category/count output. The current code intentionally rejects a noninteractive
secret injection, so provisioning the dedicated task and a reviewed non-echoing operator bridge is a
human operations step rather than an application or deploy-permission change in issue #107.

Do not attach the owner secret to the web/worker containers, invoke `scripts/create_superuser.py`,
pass credentials through ECS command/environment overrides, or capture the one-time Studio token as
lifecycle evidence.

## Safe verification

1. Confirm `/admin` redirects directly to `/admin/` and `/studio` directly to `/studio/`.
2. Sign in through the development form and confirm Django admin and Studio retain distinct branding.
3. In Studio, create a token with only `studio.home.read` and copy it to the approved secret store.
4. Confirm the token can call `GET /api/v1/admin/health` and cannot call credential-management or
   unknown routes.
5. Navigate away and back; the token must not be recoverable. Rotate or revoke it when needed.

Never paste an email, password, token, digest, cookie, session identifier, or authorization header in
an operator report.
