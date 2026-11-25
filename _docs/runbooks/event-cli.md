# Event management CLI (`dtc`) — Operator & Orin reference

Status: **live** (mirrors AI Shipping Labs `asl` CLI). The full table of where each content type is authored is in [content authoring](../content-authoring.md). New live event listings are created only on the DTC site, with `dtc events create` on prod.datatalks.club, and people register only through the site's own registration form. There is no Luma (owner decision, 2026-11-25): never put a Luma, Eventbrite or other third-party registration link in an event's materials. `_data/events.yaml` is not a path for event listings.

This document answers all 10 open questions in `bots-management/agents/contracts/orin/dtc-website-events.md`.

---

## 1. CLI name, install, login, and lifecycle commands

- **CLI name:** `dtc` (package `dtc-cli` in `dtc_cli/`).
- **Install:**
  ```bash
  uv tool install "git+https://github.com/DataTalksClub/website#subdirectory=dtc_cli"
  # Or from a local checkout:
  uv tool install --editable ./dtc_cli
  # Or run directly without installing:
  uv run --project dtc_cli dtc --help
  ```
- **Login (device approval flow or token):**
  ```bash
  dtc login                          # opens browser /device approval for https://prod.datatalks.club
  dtc login --url https://prod.datatalks.club
  dtc login --token-stdin            # non-interactive / bot login via stdin
  dtc whoami                         # verify active token & server health
  dtc logout                         # remove ~/.config/dtc/credentials.json
  ```
  Environment overrides: `DTC_URL`, `DTC_TOKEN`, `DTC_PROFILE_PATH`.

- **Orin on production (service token):** browser device approval needs a staff
  sign-in, and production currently has no Google/GitHub/Slack `SocialApp` rows
  (they were deliberately not imported at the Coolify cutover and have not been
  configured since). Orin therefore signs in with a scoped service token kept in
  the dataops secrets store, never with a personal login:
  ```bash
  dataops secrets get website-production.orin-dtc-token --field token \
    | dtc login --url https://prod.datatalks.club --token-stdin
  dtc whoami                         # health.status must be "ok"
  dtc events list --upcoming
  ```
  The secret `website-production.orin-dtc-token` holds the fields `token`, `url`,
  `principal_id`, `credential_id`, `expires_at` and `scopes`. The token belongs to
  the service principal `service:orin-events-publisher` and carries only
  `studio.home.read` plus the `events.manage.{list,detail,create,update,publish,banner}`
  and `events.manage.speakers.{list,create}` scopes. Registrant export,
  attendance import, messaging, cancel and complete are outside its scope and
  return `permission_denied`. It expires after 89 days (see `expires_at`); rotate
  it before then and replace the secret.

- **Event commands** (`<event>` accepts numeric `public_id` e.g. `448`, `slug`, or UUID):
  - **List:** `dtc events list [--status draft|upcoming|in_progress|past|cancelled|archived] [--upcoming] [--past] [--format json|table]`
    `draft`, `cancelled` and `archived` are stored editorial states; `upcoming` (start now or later),
    `in_progress` (started, not yet ended) and `past` (ended; no stored end means one hour after the
    start) are derived from the schedule and are what `status` reports in every response.
  - **Get:** `dtc events get <event>` (returns `public_url`, `public_id`, `id` UUID, `speakers`, `banner_url`, `reminder_intervals`, `registration_counts`, etc.)
  - **Create** (published by default; pass `--no-publish` for `draft`). Every event is created with
    a description and its picture, in one transaction; the server refuses a create without either
    (`event_description_required`, `event_picture_required`). Pass `--banner FILE` for your own
    1000x1000 picture; without it the CLI renders the standard picture locally with banner-generator
    (`dtc-event-speaker`, from the title, type, speakers' names and portraits, and the start time in
    `--timezone`) and sends it with the create call. The renderer is the banner-generator checkout at
    `--banner-generator-dir`, `$DTC_BANNER_GENERATOR_DIR`, or `~/git/banner-generator` (it needs the
    `dtc-event-speaker` template, banner-generator `main` from 9533fea on). `--banner-output PATH` keeps
    the rendered PNG so you can look at it. The picture goes up in WAF-sized chunks
    (`{"picture_chunk": ...}` on `POST /api/v1/admin/events`), then the create call names it by
    `"picture": {"upload_id": ...}`; a direct API caller can instead send
    `"picture": {"image_base64": ..., "content_type": ...}` inline.
    ```bash
    dtc events create \
      --title "Event Title" \
      --type webinar \
      --start-datetime "2026-12-01T17:00:00+01:00" \
      --end-datetime "2026-12-01T18:00:00+01:00" \
      --timezone Europe/Berlin \
      --description-file ./description.md \
      --location "https://youtube.com/live/..." \
      --speakers alexeygrigorev \
      --tags "mlops,community"
    ```
    All flags on `create` and `update`:
    - `--title` (Title Case)
    - `--slug` (auto-derived from title if omitted)
    - `--description` / `--description-file` (Markdown)
    - `--type` (`podcast`, `webinar`, `workshop`, `conference`, `meetup`, `office-hours`, `project-of-the-week`)
    - `--kind` (`standard`, `workshop`, `meetup`, `q_and_a`)
    - `--platform` (`zoom`, `custom`, `in_person`)
    - `--start-datetime`, `--end-datetime` (ISO 8601), `--timezone` (default `Europe/Berlin`)
    - `--location` (public stream URL or venue)
    - `--join-url` (protected join link)
    - `--recording-url`
    - `--speakers` (comma-separated person `short` keys)
    - `--tags` (comma-separated display tags)
    - `--season`, `--episode` (integers, for podcasts)
    - `--materials` (JSON array or `@file.json` of `{"label": "...", "url": "..."}`)
    - `--reminder-intervals` (e.g. `24h,20m`, `24h`, or `none`)
    - `--sponsor-sharing` / `--no-sponsor-sharing`
    - `--collect-company` / `--no-collect-company`
    - `--sponsor-name` (who registrants consent to share their email with, section 6)
    - `--sponsor` (sponsored event: comma-separated sponsor directory keys, section 6a)
    - `--publish` / `--no-publish`
  - **Update:** `dtc events update <event> [--title ...] [--start-datetime ...] ...` (only passed flags are sent; schedule/location changes bump `ics_sequence`).
  - **Publish:** `dtc events publish <event>` (`draft` -> published, allocates `public_id`; the event then reports `upcoming`, `in_progress` or `past` from its schedule). A draft has no `public_id` yet, so address it by its UUID or slug until it is published; publishing needs a type and at least one speaker. Before 2026-11-25 every publish on prod failed with `internal_error` (the package's series-enrolment step used `FOR UPDATE` over an outer join, which PostgreSQL rejects); fixed in website PR #558.
  - **Hireable With AI sessions and other Academy sessions:** the public sessions page is `academy/sessions.md` in `DataTalksClub/content` (dates, titles, calendar sequence); the matching event listing is created and rescheduled with `dtc events create` / `dtc events update`. Change both in the same session.
  - **Recordings:** `dtc events update <event> --recording-url https://www.youtube.com/watch?v=...` (works on past events). The old `_data/events.yaml` `youtube:` field maps to `recording_url`.
  - **Cancel:** `dtc events cancel <event> [--reason "..."]` (closes registration, bumps `ics_sequence`, queues cancellation email + cancelled ICS). A `past` event cannot be cancelled.
  - **Complete:** `dtc events complete <event> [--recording-url "https://youtube.com/..."]` (stores no status: refuses an event that has not ended yet, or a draft/cancelled/archived one, and only attaches the recording; the event reports `past` because its end has passed).

---

## 2. Attaching a speaker and creating a new speaker

- **Attach existing speaker(s):** pass `--speakers <short1>,<short2>` on `create` or `update`. The server resolves each `short` against existing `Host` rows or the synced people catalogue (`_people/<short>.md`).
- **Create a new speaker:**
  - In the event system immediately:
    ```bash
    dtc events speakers create --short janedoe --name "Jane Doe" --title "Staff ML Engineer"
    dtc events speakers list [--search jane]
    ```
  - For a full public `/people/<short>.html` profile page, add `_people/<short>.md` (and `images/authors/<short>.jpg`) in `DataTalksClub/datatalksclub.github.io`. This is the one content type that is still authored on the old repository (interim); the `dtc-main-site` source is now part of the Content Sync workflow, so the profile appears after the next Content Sync run (dispatch it: `gh workflow run content-sync.yml -R DataTalksClub/website`). Moving people authoring to `DataTalksClub/content` is a tracked follow-up.

---

## 3. Event banner: uploaded, size, and command

- Every event has a picture from the moment it exists (`events/pictures.py`): it is sent with the
  create call (above), it can be replaced with `upload-banner`, and it can never be cleared. An event
  written before this rule that still has no picture accepts no other edit until one is uploaded.
  On PostgreSQL the completeness triggers (`event_registrants` migrations 0005/0007) also refuse to
  commit an event without a type, speaker, description or picture.
- Studio's create endpoint (`studio:event-create`) takes the picture as a required `picture` file
  field (multipart) or the same JSON `picture` object. There is no "Generate standard picture" button:
  banner-generator documents no HTTP render endpoint the site can call (its Lambda Function URL is
  private and token-protected), so the standard picture is rendered locally by the CLI.
- **Size & format:** `1000x1000` square PNG, JPEG, or WebP (max 5 MB).
- **Replace a picture:**
  ```bash
  dtc events upload-banner <event> --file ./banner-1000x1000.png
  ```
- **Public URL:** served at `/events/<public_id>/banner` and automatically wired into the event page and `og:image`.

### Render the standard event picture

The standard picture is the speaker-led `dtc-event-speaker` template in
[`banner-generator`](https://github.com/AI-Shipping-Labs/banner-generator) (`~/git/banner-generator`).
It shows the speaker portraits, names, type pill, weekday + date, local time and title, sized to stay
readable at the ~290px the event page shows it. Fields are documented in that repo's README.

1. Get the event's facts: `dtc events get <event>` (title, speakers, start time). Download each
   speaker's portrait from `/images/authors/<short>.jpg` into a local folder.
2. Write a spec (outside the repo, e.g. `.tmp/`), using the event's own timezone for the date and time:
   ```json
   {
     "template": "dtc-event-speaker",
     "size": "luma",
     "output": "/abs/path/.tmp/event-banner.png",
     "kind": "workshop",
     "title": "Observing Python Applications Using Prometheus",
     "date_label": "Tue 13 Dec 2022",
     "time_label": "17:00 CET",
     "speakers": [
       {"name": "Jessica Greene", "image_url": "file:///abs/path/.tmp/jessicagreene.jpg"},
       {"name": "Vanessa Aguilar", "image_url": "file:///abs/path/.tmp/vanessaaguilar.jpg"}
     ]
   }
   ```
   `kind` is `webinar`, `workshop`, `podcast` or `conference`. Add `"crop": {"zoom": 1.5, "x": 65, "y": 20}`
   to a speaker to zoom a portrait that has stray edge content. With no speakers, use `"speakers": []`.
3. Render it: `cd ~/git/banner-generator && uv run banner-generator render /abs/path/.tmp/event-spec.json`.
   Look at the PNG at ~290px wide before uploading.
4. Upload it with `dtc events upload-banner <event> --file /abs/path/.tmp/event-banner.png`, from the
   Studio event page's banner upload (`studio:event-banner`), or through the API:
   `POST /api/v1/admin/events/<event_id>/banner` with JSON `{"image_base64": "...", "content_type": "image/png"}`
   (capability `events.manage.banner`; the CLI chunks large files for you).

`"size": "og"` renders the same picture as a 1200x630 social card (LinkedIn, X, Slack). The site's
`og:image` still uses the square banner.

---

## 4. Export registrants, import attendance, message registrants

- **Export registrants** (never paste attendee emails into chats, Tasks, or git):
  ```bash
  dtc events export-registrants <event> --output .tmp/registrants.csv --format csv
  dtc events export-registrants <event> --format json --sponsor-consented-only --output .tmp/sponsor.json
  dtc events export-registrants <event> --newsletter-only --output .tmp/newsletter.csv
  ```
- **Import attendance:**
  ```bash
  dtc events import-attendance <event> --file ./attendees.csv --status attended
  dtc events import-attendance <event> --emails "a@example.com,b@example.com" --status no_show
  ```
- **Message registrants** (replaces Luma email blasts):
  ```bash
  dtc events message <event> --subject "Update" --body-file ./msg.txt --audience confirmed --dry-run
  dtc events message <event> --subject "Update" --body-file ./msg.txt --audience confirmed
  ```
  `--audience` supports `confirmed` (default), `attended`, or `all_active`.

---

## 5. Per-event reminder control

- Default intervals: `24h,20m` before `start_datetime`.
- Set on `create`/`update` with `--reminder-intervals 24h` (e.g. for Project of the Week daily reminder) or `--reminder-intervals none` to disable.
- Inspect or update anytime:
  ```bash
  dtc events reminders <event>
  dtc events reminders <event> --intervals 24h
  ```

---

## 6. Sponsor email-sharing consent and company field

- Enable on `create` or `update`:
  ```bash
  dtc events update <event> --sponsor-sharing --collect-company --sponsor-name "Acme Corp"
  ```
- When enabled, the public registration form on `/events/<public_id>/<slug>` displays the **Company** field and **"Share my email with the event sponsor (Acme Corp)"** checkbox.
- Both `company` and `sponsor_sharing_consent` are included in `dtc events export-registrants`, and `--sponsor-consented-only` filters to consenting registrants.

## 6a. Sponsored events: the sponsor's logo

- A sponsored workshop or webinar links to one or more entries of the sponsor
  directory (`/sponsors`, Studio > Sponsors), by key:
  ```bash
  dtc events update <event> --sponsor dlthub
  dtc events update <event> --sponsor ''   # remove
  ```
  The same `sponsors` list (of keys) is accepted by the admin API and the Studio
  event endpoints on create and update; it replaces the whole list. An unknown
  key is refused (`unknown_sponsor`): add the sponsor to the directory first,
  with its logo (`logo_asset_key`, a file under `core/static/core/sponsors/`).
- The event page shows "Sponsored by" with the logo under the picture, the
  `/events` card shows the logo in its meta row, and both link to the sponsor's
  `/sponsors` entry (or its own site when it is not listed there).
- `dtc events create` renders the picture with the first sponsor's logo
  ("Sponsored by" row of banner-generator's `dtc-event-speaker`). For an existing
  event, re-render the picture and `dtc events upload-banner` it.

---

## 7. Newsletter consent sync to Relay

- `newsletter_consent` is stored on each `EventRegistration` row in the database, **not** automatically synced to Relay yet.
- To export consenting subscribers for newsletter import:
  ```bash
  dtc events export-registrants <event> --newsletter-only --output .tmp/newsletter.csv
  ```

---

## 8. Host for announcements before DNS cutover

- Use **`https://prod.datatalks.club`** (e.g. `https://prod.datatalks.club/events/<public_id>/<slug>`).
- Do not use `https://datatalks.club` for new event links until the apex DNS cutover is completed.

---

## 9. Existing Luma events and new listings

- Already-published Luma event links stay valid on Luma until those events happen. Do not delete or break them.
- Historical Luma events and past registrations are imported by Developer using `scripts/prod/import_events.py` and `scripts/prod/import_event_registrants.py`.
- During the transition, every new public DataTalks.Club live event (podcast, webinar, workshop, and other live events) is created on both Luma and the DTC site. The DTC site listing is created on `prod.datatalks.club` with `dtc events create`. If the DTC site step is blocked, still publish the event on Luma and add the site listing as soon as it works. Luma is removed from the process only when Alexey says the migration is complete.

---

## 10. Where podcast pages, transcripts, speaker profiles, and `_data/events.yaml` live now

| Legacy source | New location / replacement |
| --- | --- |
| Podcast episode pages | `DataTalksClub/content` (`podcasts/<slug>.yaml`, synced via `dtc-content` webhook) |
| Podcast transcripts | `DataTalksClub/content` (`podcasts/transcripts/<slug>.yaml`, synced via `dtc-content` webhook) |
| Speaker profiles (`/people/<short>.html`) | Interim: `DataTalksClub/datatalksclub.github.io` (`_people/<short>.md`, synced by the Content Sync workflow, source `dtc-main-site`) + `dtc events speakers create` |
| `_data/events.yaml` + Luma scraping (no longer edited; the old file is frozen at 2026-11-25) | Website database via `dtc events list --upcoming --format json` (update `week_sources.py` / `week_sources.rb` to read this instead of `_data/events.yaml`) |
