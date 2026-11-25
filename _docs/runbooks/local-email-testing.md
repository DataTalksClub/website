# Testing email flows locally

Local settings deliver mail to the in-process `memory` backend, so nothing is
rendered and nothing can be clicked. To see real emails, run
[outbox](https://github.com/DataTalksClub/outbox): a Relay-compatible test
server with a browser inbox. The site's package `relay` mail backend talks to
it exactly as it talks to Relay. Nothing leaves the machine.

## 1. Start the outbox

```bash
git clone https://github.com/DataTalksClub/outbox ~/git/outbox
cd ~/git/outbox
OUTBOX_DEFAULT_FROM="DataTalks.Club <noreply@datatalks.club>" \
OUTBOX_CALLBACK_URL=http://127.0.0.1:8000/internal/mail/callback \
OUTBOX_CALLBACK_SECRET=local-outbox-callback-secret \
uv run outbox                        # inbox at http://127.0.0.1:8025
```

The callback variables are optional. With them, the outbox reports each
message back as `delivery.accepted` and then `delivery.delivered`, signed like
Relay, so the `EmailDelivery` row reaches `delivered`. Point the URL at the
port the site runs on. The secret must equal `LOCAL_OUTBOX_CALLBACK_SECRET` in
`website/settings/local.py`.

## 2. Start the site against it

```bash
DTC_LOCAL_OUTBOX_URL=http://127.0.0.1:8025 \
DTC_LOCAL_SITE_URL=http://localhost:8000 \
uv run python manage.py runserver
```

With `DTC_LOCAL_OUTBOX_URL` set, `website.settings.local` switches
`MAIL_BACKEND` to `relay`. It sets `RELAY_BASE_URL` to the outbox, sets the
API key and callback secret to fixed local placeholders, and sets `SITE_URL`
to `DTC_LOCAL_SITE_URL`. `SITE_URL` is the origin of every link in mail,
such as verify, manage and password reset. Its default is
`http://localhost:8000`; set it to the origin you browse. Jobs still run
inline. Without the variable, local mail stays in `memory` as before.

## 3. Templates

`email_templates/` purposes (course registration, homework, certificates) are
rendered by Relay from its catalog. Load them into the outbox once:

```bash
DTC_LOCAL_OUTBOX_URL=http://127.0.0.1:8025 uv run python manage.py import_mail_templates
```

This writes `core.MailTemplatePublication` rows with the outbox's version
numbers into the local database. Purposes that have no template in this
repository (the package `events.*` and `accounts.*` mails) still arrive. The
outbox's lenient mode renders them as a list of their context values, links
included, and marks them `template_not_found`. Real Relay would reject them
with a `404`.

### Before production

Templates added here reach Relay only through `import_mail_templates`. The deploy's
`web-migrate` step runs it, but a template a flow already sends must be published in
Relay before that flow goes live, or Relay rejects the send with a `404`. Publish these
in the production Relay catalog (run `manage.py import_mail_templates` against it, or let
the first deploy that carries them do it, and check its output says `published:`):

- `event-registration-confirmed`: "You're registered", with the Zoom join link (2026-11-25).
- `event-reminder`: the 24h/20m event reminders, with the Zoom join link (2026-11-25).

## 4. Look at the mail

- Inbox: http://127.0.0.1:8025, newest first. "Clear all" empties it.
- A message shows a Gmail-style reading view with Gmail web, mobile and dark
  renderings, the plain-text part with clickable links, and the headers.
- For scripts: `GET /api/messages/latest?to=<address>` returns the newest
  message with its bodies and extracted `links`.

Use `@example.com` test addresses, for example `dev-test+1@example.com`.
Never put real addresses or production data into the outbox. Delete test
registrations from the local database when you are done.
