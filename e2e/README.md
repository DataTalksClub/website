# E2E smoke tests (Playwright)

End-to-end smoke suite that runs against a **live** CMP deployment (dev by
default) after each deploy. It provisions a full course lifecycle, exercises
the user-facing flows through a real browser, verifies scoring + leaderboard,
and tears the data down again so dev stays clean.

This suite is intentionally **separate from the Django unit tests**
(`courses/tests`, `api/tests`, ...). It has its own `pytest.ini`, never sets
`DJANGO_SETTINGS_MODULE`, and never touches the project database — it only
talks to the remote server over HTTP and a browser.

## Framework

- **Python Playwright + pytest** (`playwright>=1.58.0`, already in the repo's
  dev deps). Chosen over the JS runner so the suite stays in one language /
  one toolchain with the rest of the repo (`uv`, `pytest`), can import nothing
  from Django but reuse the same `.env`, and so CI only needs the existing
  Python environment.
- Provisioning, scoring, assertions and teardown go through the **REST API**
  (fast, token-auth). The genuinely user-facing flows — admin login,
  impersonation, homework/project submission forms, confirmation pages,
  dashboards, leaderboards — go through the **browser**.

## What it covers (issue #194)

| Scenario | File | Driver |
|----------|------|--------|
| 1. Availability & auth (health, admin login, protected redirect) | `tests/test_00_availability.py` | API + browser |
| 2. Course & content provisioning (course, homework w/ FF+CB+MC, project) | `tests/test_01_provisioning.py` | API |
| 3. Enrollment & identity (create/find student, impersonate, profile) | `tests/test_02_enrollment.py` | browser (loginas) |
| 4. Homework flow (submit via UI, confirmation, score, leaderboard) | `tests/test_03_homework.py` | browser + API |
| 5. Project flow (submit via UI, assign reviews, score, stats) | `tests/test_04_project.py` | browser + API |
| 6. Dashboards & stats render | `tests/test_05_dashboards.py` | browser |
| 7. Teardown + pre-run sweep + clean assert | `tests/test_99_teardown.py` | browser + API |

### Email verification is not part of this suite

D1.2ca retired the Datamailer client and with it the `/api/datamailer/send-audits`
endpoint this suite used to read a rendered confirmation back over HTTP. Every
send now records a durable `EmailDelivery` through the package mail app, whose
evidence surface is Studio, not a remote API, so the confirmation-email
assertions were removed from `test_03`/`test_04` rather than pointed at an
endpoint that does not exist. Mail rendering itself is covered locally by
`core/tests/test_mail_templates.py` against the committed `email_templates/`.

The student email is still a unique per-run address
(`settings.student_address(namespace)` -> `<namespace>@example.com`); the
development target sends nothing to it.

### Teardown deletes the course via the Django admin UI

The platform deliberately exposes **no course DELETE API endpoint** — a
standing remote delete capability could let any API client/agent wipe too much
data. Cleanup instead reuses the suite's authenticated admin Playwright session
(the same one used for login + impersonation) and deletes the course through
the **Django admin confirmation screen**
(`/admin/courses/course/<pk>/delete/` → "Yes, I'm sure"). Deleting the `Course`
cascades to **all** of its data (homeworks, questions, projects, submissions,
answers, enrollments, peer reviews are all `on_delete=CASCADE`), so a single
admin delete fully purges a run.

So teardown:

1. removes the student's project submission via the UI (the only remote way
   to delete a submission), then stops impersonating;
2. runs a best-effort API pre-pass on individually-deletable homeworks/projects
   (informative only — the admin delete supersedes it);
3. **deletes the course through the admin UI**, cascading everything away. The
   course pk is resolved from the slug via the admin changelist (the API does
   not return a course id). The next run's **pre-run sweep** admin-deletes any
   stale `e2e-smoke-*` courses the same way.

Teardown stays robust: if the admin delete is unavailable (no admin creds) or
fails, it falls back to **parking** the course (`visible=false`, renamed
`[DELETED] ...`) and reports the residual, so dev still stays clean.

The post-run assertions verify **no _visible_ `e2e-smoke-*` course remains**
and that the course is **fully purged** (no longer retrievable via the API).
The full-purge check requires admin creds; in the API-only subset it is
skipped (the course is parked hidden instead).

> **Teardown now depends on `E2E_ADMIN_EMAIL` / `E2E_ADMIN_PASSWORD`.** These
> were already required for the browser login/impersonation flows; the admin
> deletion in teardown uses the same session. Without them, teardown degrades
> to the park-hidden fallback.

## Running it

From the **repo root**, using `uv` (repo convention):

```bash
# Everything (needs admin creds + token; see env vars below)
cd e2e && uv run --project .. pytest -c pytest.ini

# Just the no-credential availability checks (health, login page, redirect)
cd e2e && uv run --project .. pytest -c pytest.ini tests/test_00_availability.py
```

Browser-driven tests **skip cleanly** (not error) when admin credentials are
absent, so you can always run the API-only subset.

First-time only, install the browser binary:

```bash
uv run playwright install chromium
```

## Required environment variables / CI secrets

Copy `e2e/.env.example` to `e2e/.env` for local runs, or provide as CI
secrets. The suite also falls back to the **repo-root `.env`** for
`DEV_AUTH_TOKEN` and `PUBLIC_BASE_URL`.

| Var | Required for | Notes |
|-----|--------------|-------|
| `E2E_BASE_URL` | all | Defaults to `https://dev.courses.datatalks.club` (or `PUBLIC_BASE_URL`). |
| `E2E_API_TOKEN` | provisioning/scoring/teardown | Scoped staff credential (`dtca_v1_...`, from `.tmp/dev-api-token.txt` after re-running `scripts/add_data.py`). Falls back to `DEV_AUTH_TOKEN`. A legacy `Token` value authenticates learner-level reads but can no longer provision or score. |
| `E2E_ADMIN_EMAIL` / `E2E_ADMIN_PASSWORD` | browser flows + teardown | Staff account; logs in via the admin form (no OAuth). Teardown deletes the course through the admin UI with this session; without it, teardown only parks the course hidden. |
| `E2E_STUDENT_EMAIL` / `E2E_STUDENT_PASSWORD` | optional | If unset, a per-run `<namespace>@example.com` student is created admin-side. With dry-run nothing is delivered, so no special address is needed. |
| `E2E_EXPECTED_VERSION` | optional | If set, asserts `/api/health/` version matches the just-deployed build. |
| `E2E_HEADLESS` | optional | `0` to watch the browser locally. |

**Never hardcode secrets.** Nothing in this suite contains credentials.

## Scheduling (post dev-deploy)

Run the suite as a **post-deploy job** in the dev deploy workflow (see
`.github/workflows/e2e-smoke-dev.yml`): it triggers on `workflow_run`
completion of the dev deploy, waits for `/api/health/` to report the new
version (`E2E_EXPECTED_VERSION`), runs the suite, and fails (alerting) with
the exact scenario that broke. Alternatively, an EventBridge schedule
(consistent with the deadline-reminder infra) can invoke the same command.
