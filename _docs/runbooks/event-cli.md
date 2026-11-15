# Event management CLI (`dtc`) — Operator & Orin reference

Status: **live** (mirrors AI Shipping Labs `asl` CLI). Replaces Luma and `_data/events.yaml` for DataTalks.Club events.

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

- **Event commands** (`<event>` accepts numeric `public_id` e.g. `448`, `slug`, or UUID):
  - **List:** `dtc events list [--status draft|upcoming|completed|cancelled|archived] [--upcoming] [--past] [--format json|table]`
  - **Get:** `dtc events get <event>` (returns `public_url`, `public_id`, `id` UUID, `speakers`, `banner_url`, `reminder_intervals`, `registration_counts`, etc.)
  - **Create** (published by default; pass `--no-publish` for `draft`):
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
    - `--sponsor-name`
    - `--publish` / `--no-publish`
  - **Update:** `dtc events update <event> [--title ...] [--start-datetime ...] ...` (only passed flags are sent; schedule/location changes bump `ics_sequence`).
  - **Publish:** `dtc events publish <event>` (`draft` -> `upcoming`, allocates `public_id`).
  - **Cancel:** `dtc events cancel <event> [--reason "..."]` (closes registration, bumps `ics_sequence`, queues cancellation email + cancelled ICS).
  - **Complete:** `dtc events complete <event> [--recording-url "https://youtube.com/..."]` (marks `completed`).

---

## 2. Attaching a speaker and creating a new speaker

- **Attach existing speaker(s):** pass `--speakers <short1>,<short2>` on `create` or `update`. The server resolves each `short` against existing `Host` rows or the synced people catalogue (`_people/<short>.md`).
- **Create a new speaker:**
  - In the event system immediately:
    ```bash
    dtc events speakers create --short janedoe --name "Jane Doe" --title "Staff ML Engineer"
    dtc events speakers list [--search jane]
    ```
  - For a full public `/people/<short>.html` profile page, also open a PR in `DataTalksClub/datatalksclub.github.io` adding `_people/<short>.md` (and `images/authors/<short>.jpg`).

---

## 3. Event banner: uploaded, size, and command

- Banners are **uploaded by the operator** (the DTC site does not auto-generate banners).
- **Size & format:** `1000x1000` square PNG, JPEG, or WebP (max 5 MB).
- **Command:**
  ```bash
  dtc events upload-banner <event> --file ./banner-1000x1000.png
  ```
- **Public URL:** served at `/events/<public_id>/banner` and automatically wired into the event page and `og:image`.

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

## 9. Existing Luma events (podcasts 24 Nov–2 Dec, etc.)

- Already-published Luma event links stay valid on Luma until those events happen — do **not** delete or break them.
- Historical Luma events and past registrations are imported by Developer using `scripts/prod/import_events.py` and `scripts/prod/import_event_registrants.py`.
- From 15 Nov 2026 onward, **no new Luma events are created**; all new events are created on `prod.datatalks.club` via `dtc events create`.

---

## 10. Where podcast pages, transcripts, speaker profiles, and `_data/events.yaml` live now

| Legacy source | New location / replacement |
| --- | --- |
| Podcast episode pages | `DataTalksClub/content` (`podcasts/<slug>.yaml`, synced via `dtc-content` webhook) |
| Podcast transcripts | `DataTalksClub/content` (`podcasts/transcripts/<slug>.yaml`, synced via `dtc-content` webhook) |
| Speaker profiles (`/people/<short>.html`) | `DataTalksClub/datatalksclub.github.io` (`_people/<short>.md`, synced via `dtc-main-site` webhook) + `dtc events speakers create` |
| `_data/events.yaml` + Luma scraping | Website database via `dtc events list --upcoming --format json` (update `week_sources.py` / `week_sources.rb` to read this instead of `_data/events.yaml`) |
