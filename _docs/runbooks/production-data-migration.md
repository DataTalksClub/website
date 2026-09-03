# Production data migration

> ### Read this before anything else
>
> **`/accounts/signup/` is open on this site today.** It is allauth's own view, it
> is not shadowed the way `accounts/login/`, `accounts/email/` and
> `accounts/password/reset/` are, and `ACCOUNT_ALLOW_REGISTRATION = False` does
> **not** close it — that is not an allauth setting and nothing reads it.
>
> **Measured**, in the production configuration: `POST /accounts/signup/` creates
> an account with a usable password and signs it in.
>
> It **cannot** take over an imported account — also measured: a signup against an
> address that already exists is refused and changes nothing, because
> `ACCOUNT_UNIQUE_EMAIL` holds. So this is unwanted local password accounts on a
> site whose sign-in is meant to be provider-only, not stolen member history.
>
> **This is live now, before any migration.** It should be closed before 20,009
> accounts and 5 privileged rows land — §11 A5, with the evidence in §5.4.

How every source of data reaches the production database, in what order, how we
know each step worked, and what to do when one does not.

This is the **execution plan**, and it is also the **work queue**. Several things
it requires do not exist yet; §11 lists them in build order. The sequence is:
read this → build what §11 says is missing → rehearse locally (§8) → run against
production.

`_docs/runbooks/data-ingest.md` is the per-source reference that goes with it —
what each source is, where it lives, and which script owns it. Read that one to
understand a source; read this one to run the migration. Where the two disagree,
this document is the decision and the reference is the thing to correct.

Tracked in [#310](https://github.com/DataTalksClub/website/issues/310), which is
referenced from the production checklist in
[#309](https://github.com/DataTalksClub/website/issues/309).

Every count here was measured on branch `production-prep` on 2026-09-03 against
the export `/data/tmp/rds-export/rds-prod-20260902-012536.db` and the checkouts
in `~/git`. Numbers marked **(measured)** were produced by running the step.

---

## 1. The two sync models

Every source is one of these, and the distinction decides almost everything else
about how it is treated.

**Git-synchronised.** Upstream keeps changing; a push re-syncs us. These keep
running after launch and must be idempotent, because they will run hundreds of
times.

**One-time import.** Frozen history, extracted once at migration. After it has
run, the source is irrelevant. These must also be re-runnable — a migration gets
rehearsed — but they are not part of steady-state operation.

A source with no declared fate is a bug in this plan, not an omission. So here is
every source `data-ingest.md` §2 enumerates, with its fate. Nothing is left out;
where the answer is "we deliberately do nothing", it says so.

| # | Source | Model | Fate and owner | Step |
| --- | --- | --- | --- | --- |
| 1 | Course repositories (3) | git-synchronised | `content_sync/course_repository_ingest.py` | 3 |
| 2 | `DataTalksClub/content` — articles, podcasts, books | git-synchronised after migration | push-sync — **to build**, §11 B3 | 7 |
| 3 | `DataTalksClub/podwiki` — wiki, graph, search | pinned build → git-synchronised | source stays at podwiki; **we serve `/wiki`** | 7 |
| 4 | `DataTalksClub/faq` (6 courses / 70 sections / 1,401 questions) | **git-synchronised** | **owner ruling: comes into our database via content sync** — to build, §11 B8 | 7 |
| 5 | `DataTalksClub/docs` (106 pages) | **git-synchronised** | **owner ruling: same as FAQ** — to build, §11 B8 | 7 |
| 6 | CMP repo `production_like_course_specs.json` | pinned build | local seeding only; produces the unused `courses.json`. **Not migrated** — §14 | — |
| 7 | Legacy `_people` (443) | pinned build | push-sync — move to `DataTalksClub/content`, §11 B5 | 7 |
| 8 | Legacy `_data/events.yaml` (421) | pinned build | one-off export — **no home yet**, §11 B6 | 7 |
| 9 | Legacy `images/authors/` (438) | pinned build | one-off export → CDN; **live defect**, §11 B7 | 7 |
| 10 | Legacy `_data/faqs/` article FAQ (159 pairs) | pinned build | one-off export — **already done and committed** | — |
| 11 | CMP export — course content (991 rows) | one-time | `scripts/prod/import_cmp_content.py` | 4 |
| 12 | CMP export — learner data (510,519 rows) | one-time | **to build**, §11 A3 | 5 |
| 13 | `zoomcamp-scoring` — pre-2024 history | one-time | `scripts/prod/import_legacy_zoomcamp.py` | 2 |
| 14 | Event identity manifest (421 / 1,684) | one-time | `scripts/prod/import_events.py` | 6 |
| 15 | Event description bridge | one-time | committed capture; `scripts/build_event_description_bridge.py` | 7 |
| 16 | Luma aggregates | one-time | `scripts/prod/import_events.py` → `events/importers.py` | 6 |
| 17 | Eventbrite aggregates | one-time | as above | 6 |
| 18 | Public media objects (1,253 / 154 MB) | one-time publish, then CDN-resident | **owner ruling: to the CDN and out of git** — `manage.py public_media_*` → `dtc-website-media`, §11 B11 | **8** |
| 19 | **Sponsors** | one-time then Studio | **owner ruling: give it an import script** — to build, §11 B9 | 9 |
| 20 | **Testimonials** | one-time then Studio | **owner ruling: same script** — to build, §11 B9 | 9 |
| 21 | `rds-aisl_prod` | — | **explicitly out of scope** — §14 | — |

The owner's original list had ten sources. `DataTalksClub/podwiki`, `faq`,
`docs`, the CMP repository's course specs, public media, sponsors and testimonials
were folded into "the content repository" and are not in it and never were.

---

## 2. Where content lives after the migration

`DataTalksClub/datatalksclub.github.io` stops being a content source. Taking
content from it **in the interim is fine**; depending on it at the end is not.

**The legacy repository is never read at Django request time.** If it vanished
right now no visitor would see a change — every editorial page is served from
JSON committed into this repository. The six reads are all at *projection-build*
time. That changes the urgency: this is a rebuild risk and a one-off-export risk,
not a live-site risk. The single exception is `public_media_hydrate`, below.

Copied to `DataTalksClub/content`, then push-synchronised from there:

| Legacy folder | Files at HEAD | Becomes | State |
| --- | --- | --- | --- |
| `_posts` | 55 | articles | **already in `content`** as `articles/*.md` |
| `_podcast` | 209 | podcast episodes | **already in `content`** |
| `_books` | 100 | books | **already in `content`** as `books/*.yaml` |
| `_people` | 443 | people | **not moved. This is the work** |
| `_conferences` | 2 | — | reaches no page; no destination — §12 decision 2 |

Stays in its own repository, and **we serve it**:

- **Wiki** — `DataTalksClub/podwiki`, read from `_wiki/*.md` plus `graph/` and
  `search/`. Note the asymmetry: `/podwiki/` *redirects* to the legacy site, but
  `/wiki` is *served by this application* from the projection.
- **FAQ** — `DataTalksClub/faq`. 6 courses, 70 sections, 1,401 questions.
- **Docs** — `DataTalksClub/docs`. 106 pages.

**Owner ruling on FAQ and docs.** Both come into our site through content sync,
the same shape as the wiki: the source repository stays where it is and we serve
the pages. That settles the contradiction the reference document found — Django
serves `/faq/` and `/docs/` from `content/{faq,docs}_projection.json` while a
CloudFront viewer-request function 302s both to the legacy site, and in production
the redirect fires first. **Django wins. The two 302s are transitional and retire
once the sync works** (§11 B10).

> **This is not already how it works, despite appearances.** There is **no builder
> in this repository** for either projection. `content/faq_projection.json` and
> `content/docs_projection.json` are reviewed in by hand and only *checked*, by
> `ci/content_update.py`. Every other content family has a reproducer; these two
> do not, and there is no sync, no webhook and no `ContentSource` row for either
> repository. It is a build item (§11 B8), not a done item.

Goes to the CDN, **and out of every git repository** — owner ruling, both halves.
This is **step 8**, with its own checkpoint; the summary here is so the fate is
visible from the content section too.

- `images/` and `assets/` → the `dtc-website-media` bucket (1,253 objects,
  ~154 MB today). See [#301](https://github.com/DataTalksClub/website/issues/301).
- **This repository already keeps no images.** The work the "no images in a
  repository" ruling implies is in `DataTalksClub/content`, which tracks **815**
  of them. §11 B11.
- **Consequence:** moving an article to the content repository while its images
  move to the CDN means something has to rewrite the image references. Today that
  job is done at build time inside `scripts/build_public_projection.py`
  (`_article_blocks`, `_copy_media`). There is **no equivalent on the database
  ingest path**. §11 B3 owns it; step 8 says why it is easy to miss.

Stays behind — build machinery, not content: `_includes` (22), `_layouts` (6),
`_site` (2,318), `_tools` (2), `assets/` (4) and the Jekyll index pages.

**Redirecting is not ingesting.** `/podwiki/` is 302'd to the legacy site by a
CloudFront viewer-request function in `DataTalksClub/aws-infra`, and `/mediakit/`
301s there from `website/urls.py`. Those are pages we deliberately do not host and
they are not affected by any of the above. `/faq/` and `/docs/` are 302'd by the
same CloudFront function today, but per the ruling above those two are **ours** —
their redirects are transitional and retire with §11 B10.

---

## 3. The ordering constraints that drive everything

### 3.1 Homework identity must be final before submissions land

Course data has two overlapping sources. CMP holds cohorts, homework, questions,
projects, review criteria and registration campaigns. The course repositories
hold modules, units and homework. **They collide on homework.**

CMP's slug wins. The reconciler implements that by **renaming the repository's
homework row in place** — the row keeps its `source_content_id`, its
`source_path`, its imported instructions Markdown, its units and its
`Module.terminal_homework` binding; only the slug and CMP's own fields change.
Verified in the current database: `ml-zoomcamp-2026` carries `hw01`…`hw10`, each
still holding `source_path: cohorts/2026/NN-…/homework.yaml`.

> **Correction to an earlier statement of this rule.** CMP's slug is *not*
> uniformly `hw1`. It is `hw1`…`hw4` for `ai-dev-tools-2026`, `dlt` plus
> `hw1`…`hw5` for `llm-zoomcamp-2026`, and `hw01`…`hw10` for `ml-zoomcamp-2026`.
> The rule is "whatever CMP says, copied verbatim", not a shape.

That produces the hard constraint:

> **Homework identity must be final before any learner submission lands.**

If submissions attach first and homework rows are renamed or merged afterwards,
those submissions point at rows that no longer mean what they meant.

### 3.2 Only one importer bootstraps, which is why structure is seeded first

`scripts/prod/__init__.py` declares `BOOTSTRAP_FIRST = "import_legacy_zoomcamp"`,
and `scripts/tests/test_prod_imports.py` checks the declaration against each
module's `BOOTSTRAPS_EMPTY_DATABASE`. It is true: `Cohort.save()` resolves a
cohort's course family from its slug through `canonical_family_slug`, so the
legacy importer needs no catalogue. **Measured**: against a database that had
only just been migrated, importing `mlops-zoomcamp-2022` created the
`mlops-zoomcamp` family, the cohort, 6 homeworks, 2 projects, 569 users and 569
enrollments.

`import_cmp_content` does **not** bootstrap, but the earlier statement of why was
imprecise. It is not "only updates cohorts that already exist". It will *create* a
cohort when the slug is named in the reviewed `COHORT_FAMILY_IDENTITIES` mapping
**and the family row already exists**; it never mints a family
(`cmp_content_import.py`: `family = Course.objects.filter(slug=family_slug).first()`
… `if family is None: return None`). Against a genuinely empty database no family
exists, so it imports nothing and reports every cohort under
`skipped_not_in_local_catalogue`. A silent no-op, not an error.

**The gap that behaviour opened, and how it is closed.** Left to the importers
alone, the families that would exist after the legacy import and the repository
pull are:

| Family | Created by |
| --- | --- |
| `de-zoomcamp`, `ml-zoomcamp`, `mlops-zoomcamp` | `import_legacy_zoomcamp` |
| `ai-dev-tools`, `llm-zoomcamp`, `ml-zoomcamp` | `pull_course_repositories` |
| **`sma-zoomcamp`** | **nothing** |

`sma-zoomcamp` has no course repository — no `course.yaml`, so neither transport
can ingest it — and no pre-2024 edition. Locally the family comes from
`manage.py seed_local_courses`, which **refuses to run against anything but local
SQLite**. So on production nothing would create it, and the **1,081 enrollments**
CMP holds against `sma-zoomcamp-2024`, `-2025` and `-2026` would have nowhere to
land.

**Owner ruling: `sma-zoomcamp` comes from CMP.** That is now what happens, and
step 1 is what makes it possible. Worth being precise about why it was ever
missing, because the obvious diagnosis is wrong:

- It is **not** one of the five owner-skipped cohorts. `SKIPPED_COHORTS` is
  `ai-bootcamp-2025`, `ai-hero-2025`, `ai-hero-2026`, `ai-buildcamp-2`,
  `ai-buildcamp-3`. **No skip has to be lifted.** `sma-zoomcamp-2026` used to be
  on that list and was already removed, with the reason recorded in the service:
  it is visible and active in CMP and its family is already reviewed.
- All three editions are already in `COHORT_FAMILY_IDENTITIES`, so the CMP
  importer is already willing to create them.
- The **only** thing missing was the family row, which is exactly and only what
  step 1 writes.

So CMP is the source of truth for the `sma-zoomcamp` cohorts, their content and
their 1,081 enrollments, and step 1 supplies the one structural row CMP's importer
will not mint for itself.

The same ruling dissolves the general form of the problem. `import_cmp_content`
being a silent no-op against an empty database stops mattering once structure
exists before it runs. The behaviour is unchanged and is still worth knowing —
it is why an import can report success having written nothing — but it is no
longer a hazard in this sequence.

### 3.3 The consequence for ordering

Structure first, then the one importer that bootstraps, then the repositories,
then CMP in two phases with the repository pull between them. Users still come
from CMP — just at step 5 rather than step 2, and nothing before then needs
them.

---

## 4. The order

Each step states what it runs, what it should produce, a checkpoint you can run,
and what to do when it fails. **A checkpoint that does not pass stops the
migration.** Do not run the next step to see if it helps.

Two conventions used throughout:

- **`$TARGET`** is the environment that selects the database. For the rehearsal
  that is `DTC_ENVIRONMENT=local DJANGO_SETTINGS_MODULE=website.settings.local
  DTC_SQLITE_PATH=<path>`; for production it is the production settings module and
  its own credentials. Nothing else in a command changes between the two.
- **`$EXPORT`** is the chosen CMP export, e.g.
  `/data/tmp/rds-export/rds-prod-20260902-012536.db`. Read in place, read-only.

> **The export is not frozen.** `/data/tmp/rds-export/` receives a new dump every
> day. "One-time" is a decision about cutover, not a property of the source — CMP
> is still live and still being written to. Pick one export deliberately, record
> its filename in the run log, and use that same file for every step and every
> checkpoint.

### Step 0 — Schema

```
$TARGET uv run --frozen python manage.py migrate --no-input
```

Nothing else. Data-bearing migrations already seed the event identity manifest
(`events/0005`), homepage testimonials (`courses/0056`), the sponsor directory
schema (`core/0005`) and certificate-name backfills (`accounts/0005`, `0012`).

**Checkpoint**

```
$TARGET uv run --frozen python manage.py migrate --check
$TARGET uv run --frozen python manage.py makemigrations --check --dry-run
$TARGET uv run --frozen python manage.py shell -v 0 -c '
import sys
from django.apps import apps
BUSINESS = ["courses.Cohort", "courses.Homework", "accounts.CustomUser", "events.Event"]
rows = {name: apps.get_model(name).objects.count() for name in BUSINESS}
print(rows)
sys.exit(0 if not any(rows.values()) else 1)'
```

The third command is the one that matters: it proves this is a fresh target and
not a re-import over unknown state.

**Failure and recovery.** Nothing has been written. Fix and re-run. A failed
`migrate` is the only step in this plan that can leave a database in a state no
re-run repairs — if it fails part-way, drop the database and start again rather
than migrating forward from an unknown point.

**Duration.** Under a minute.

### Step 1 — Structural seed

**No script exists yet.** §11 A1. This step is an owner decision recorded here as
a specification.

Every importer after this one *reconciles*: it matches upstream rows against rows
that are already present, and against an empty database it writes nothing and
reports success. Rather than working around that with import ordering, this step
puts the structure in place first, so each import lands against something.

> **What it is.** A migration-only script that creates the **course-family rows**
> the later steps write against, from the reviewed identity data already in this
> repository. **Structure only. No content.** It invents no title beyond the
> reviewed one, no description, no date, no homework, no cohort copy. Everything
> a learner reads still comes from CMP or a course repository.

**Its input is already reviewed and already in the tree.** Do not invent a new
file: `courses/course_family_catalog.py` holds `COURSE_FAMILY_TITLES` — the six
published families (`de-zoomcamp`, `ml-zoomcamp`, `llm-zoomcamp`,
`mlops-zoomcamp`, `sma-zoomcamp`, `ai-dev-tools`) with their reviewed titles — and
`COHORT_FAMILY_IDENTITIES`, the thirteen cohort slugs that map to a family and a
year. The first is what this step writes. The second is what step 4 uses to decide
which CMP cohorts it may create, and it is why this step only needs to write
families.

**It is not `seed_local_courses`.** That command reads
`scripts/production_like_course_specs.json`, invents copy ("Practice assignment
for …", "Production-like generated"), and refuses to run outside local SQLite
(`local_course_seed.assert_local_database`). It is a development convenience.
This step is production-capable and writes six rows.

```
$TARGET uv run --frozen python scripts/prod/seed_course_structure.py \
    --database <target>          # name to be settled by §11 A1
```

**Why it goes here, before the imports rather than after.** The pre-2024 importer
derives a family from a cohort slug through `canonical_family_slug` and
`get_or_create`, so if the family already exists it attaches to the reviewed row
instead of minting one from a slug and a stripped title. The course-repository
ingest does the same. And step 4 can then create every reviewed CMP cohort,
because the only thing it ever lacked was the family row.

**Checkpoint** — every family that later steps write against exists, and nothing
else was created:

```
$TARGET uv run --frozen python manage.py shell -v 0 -c '
import sys
from courses.models import Cohort, Course
from courses.course_family_catalog import COURSE_FAMILY_TITLES
have = dict(Course.objects.values_list("slug", "title"))
missing = sorted(set(COURSE_FAMILY_TITLES) - set(have))
extra = sorted(set(have) - set(COURSE_FAMILY_TITLES))
wrong = sorted(s for s, t in COURSE_FAMILY_TITLES.items() if s in have and have[s] != t)
cohorts = Cohort.objects.count()
print("families", len(have), "missing", missing, "extra", extra, "wrong_title", wrong)
print("cohorts (must be 0 — this step writes structure, not content):", cohorts)
sys.exit(1 if (missing or extra or wrong or cohorts) else 0)'
```

A second run must change nothing: the write is keyed on the family slug.

**Failure and recovery.** **Recoverable by re-run**, and it is the cheapest step
in the plan — six rows, no source outside this repository, nothing downstream of
it yet. If it half-runs, run it again.

**Duration.** Seconds.

### Step 2 — Bootstrap: pre-2024 Zoomcamp history

`DataTalksClub/zoomcamp-scoring`, 7 editions. This is the first step that reads an
upstream source, and it is **the only importer that can populate an empty
database** — `Cohort.save()` resolves a cohort's family from its slug, so it needs
no catalogue. With step 1 in place it attaches its cohorts to the reviewed
`de-zoomcamp`, `ml-zoomcamp` and `mlops-zoomcamp` families rather than deriving
them.

```
git clone https://github.com/DataTalksClub/zoomcamp-scoring ~/git/zoomcamp-scoring

$TARGET uv run --frozen python scripts/prod/import_legacy_zoomcamp.py \
    --database <target> --source-repo ~/git/zoomcamp-scoring --list

$TARGET make import-legacy-zoomcamp IMPORT_DATABASE=<target>
```

Run `--list` first; it discovers the editions and writes nothing.

The correct edition slugs are `de-zoomcamp-2022`, `de-zoomcamp-2023`,
`ml-zoomcamp-2021`, `ml-zoomcamp-2022`, `ml-zoomcamp-2023`, `mlops-zoomcamp-2022`,
`mlops-zoomcamp-2023`. (An earlier draft wrote these as `de-2022`, `ml-2021` and
so on; those are not slugs this system uses.) The source is **pre-2024**, not
pre-2023 — CMP carries data from 2024 onward.

Discovered shape, **measured** with `--list`:

| Edition | Homeworks | Projects | Certificate source |
| --- | ---: | ---: | --- |
| `de-zoomcamp-2022` | 6 | 2 | yes |
| `de-zoomcamp-2023` | 8 | 2 | yes |
| `ml-zoomcamp-2021` | 9 | 3 | **no** |
| `ml-zoomcamp-2022` | 10 | 3 | yes |
| `ml-zoomcamp-2023` | 9 | 3 | yes |
| `mlops-zoomcamp-2022` | 6 | 2 | yes |
| `mlops-zoomcamp-2023` | 6 | 2 | yes |

`mlops-zoomcamp-2022` is the proven reference. **Measured** on an empty database:
6 homeworks, 2 projects, **1,462** homework submissions, **91** project
submissions, **569** users, **569** enrollments, **82** graduates carrying a
certificate name, and **0** certificate URLs matched. An earlier draft called that
last number "82 certificates"; the importer reports `graduates: 82,
urls_matched: 0`, and the resulting rows are 82 `certificate_name` values with no
`certificate_url`. Whether that is acceptable is a decision (§12 decision 3), not a
checkpoint failure.

Also **measured**: this importer creates users with a real email on the account's
`email` column and **no `account_emailaddress` row at all**, and every created
account has an unusable password. That is fine for OAuth matching — §5 explains
why — but it means an `EmailAddress`-only check would find nothing.

**Checkpoint**

```
$TARGET uv run --frozen python manage.py shell -v 0 -c '
import sys
from courses.models import Cohort, Course
EXPECTED = {"de-zoomcamp-2022","de-zoomcamp-2023","ml-zoomcamp-2021",
            "ml-zoomcamp-2022","ml-zoomcamp-2023","mlops-zoomcamp-2022",
            "mlops-zoomcamp-2023"}
have = set(Cohort.objects.values_list("slug", flat=True))
missing = sorted(EXPECTED - have)
families = sorted(Course.objects.values_list("slug", flat=True))
print("missing:", missing, "families:", families)
sys.exit(1 if missing else 0)'
```

Then re-run the import and confirm the counts are identical: every write is keyed
on a natural key, so a replay creates no duplicate row.

**Failure and recovery.** **Recoverable by re-run; a clean database is not
needed.** There is no run-level transaction here — writes are per row — so a
mid-edition failure leaves that edition partially imported and its leaderboard and
statistics stale. Re-running the same `--edition` completes it and recalculates
(`_recalculate` runs per edition at the end). Import edition by edition rather
than all seven at once, so a failure costs one edition.

**Duration.** **Measured**: `mlops-zoomcamp-2022` took **244.5 s** wall on the
development machine. It is the second-smallest edition. Budget roughly 5 minutes
per edition and **30–45 minutes for all seven**; record the real per-edition
numbers during the rehearsal in §8.4.

### Step 3 — Course repositories

```
$TARGET make content-sources        # register ContentSource rows
$TARGET make content-checkouts CONTENT_CHECKOUT_ROOT=<dir>   # the only networked step
$TARGET make content-pull     CONTENT_CHECKOUT_ROOT=<dir>    # offline
```

Three repositories are registered: `ai-dev-tools-zoomcamp`, `llm-zoomcamp`,
`machine-learning-zoomcamp`. `mlops-zoomcamp` and
`stock-markets-analytics-zoomcamp` are deliberately absent — they have no
`course.yaml`, so neither transport can ingest them.

Only `cohorts/2026/` carries a `cohort.yaml` in each repository, so this step
creates exactly **three** cohorts and the `ai-dev-tools`, `llm-zoomcamp` and
`ml-zoomcamp` families. It does not create 2024 or 2025 cohorts.

**Checkpoint** — `scripts/verify_local_dataset.py` already encodes this and exits
non-zero, but it forces local SQLite (`_configure` sets `DTC_ENVIRONMENT=local`
and `DTC_SQLITE_PATH`), so it is a rehearsal gate only until §11 C2 lands. The
portable form:

```
$TARGET uv run --frozen python manage.py shell -v 0 -c '
import sys
from django.db.models import Count
from courses.models import Cohort
EXPECTED = {"ml-zoomcamp-2026": (9, 105), "llm-zoomcamp-2026": (7, 72),
            "ai-dev-tools-2026": (4, 4)}
bad = False
for slug, (m, u) in EXPECTED.items():
    got = Cohort.objects.filter(slug=slug).aggregate(
        modules=Count("module", distinct=True), units=Count("module__unit", distinct=True))
    ok = (got["modules"], got["units"]) == (m, u)
    bad |= not ok
    print(("ok " if ok else "BAD"), slug, got, "expected", (m, u))
total = Cohort.objects.aggregate(m=Count("module", distinct=True),
                                 u=Count("module__unit", distinct=True))
print("total", total)
bad |= (total["m"], total["u"]) != (20, 181)
sys.exit(1 if bad else 0)'
```

Cohort slugs must be the **published family name plus year**. The AI Dev Tools
repository calls itself `ai-dev-tools-zoomcamp`; the published family is
`ai-dev-tools`, and `curriculum_import` rewrites that one prefix. The other five
families genuinely are `…-zoomcamp` and keep their slugs.

Re-run `make content-pull` and confirm each source reports `replayed`.

**Failure and recovery.** **Recoverable by re-run.** The projection is
transactional per repository (`curriculum_import`, `with transaction.atomic()`),
so a failure leaves the failing repository untouched and the earlier ones
complete. `replayed` re-asserts the projection still exists and raises
`idempotent_projection_missing` if a Course or Cohort has gone. Guards
`source_commit_checksum_conflict` and `source_import_identity_conflict` fire if
the same commit produces a different manifest — those mean *stop and
investigate*, not *re-run*.

**Duration.** Minutes. `make content-checkouts` is the only step that touches the
network and its time is git clone time.

### Step 4 — CMP course content

```
$TARGET uv run --frozen python scripts/prod/import_cmp_content.py \
    --database <target> --source $EXPORT
```

Reconciles homework onto step 3's rows, adds the CMP cohorts whose family already
exists, and copies registration campaign definitions.

**Expected counts, and why they are not the source totals.** The export holds 21
courses; five are deliberately not imported, each named with a reason in
`cmp_content_import.SKIPPED_COHORTS`: `ai-bootcamp-2025`, `ai-hero-2025`,
`ai-hero-2026` (need a reviewed family, title and publication state) and
`ai-buildcamp-2`, `ai-buildcamp-3` (the "2"/"3" is an edition number, not a year,
which the family+year model cannot express). So an earlier draft's "991 rows,
counts match the source table-for-table" is wrong in both halves. The correct
expectation:

| Table | In the export | Expected in the target |
| --- | ---: | ---: |
| cohorts (`courses_course`) | 21 | **16** |
| homework | 128 | **104** |
| questions | 616 | **494** |
| projects | 52 | **42** |
| review criteria | 169 | **117** |
| registration campaigns | 5 | **5** |

Those 16 include all three `sma-zoomcamp` editions, which this step creates
because step 1 put their family row there. If they are absent, step 1 did not run
or did not write `sma-zoomcamp` — do not work around it here.

Read the run's own `skipped_not_in_local_catalogue` list. It should be **empty**.
If it contains anything, a reviewed cohort found no family, which means step 1 is
incomplete. The five owner-skipped slugs appear under `skipped_by_owner`, which is
a different list and is expected to hold exactly those five.

**Checkpoint — counts**

```
$TARGET uv run --frozen python manage.py shell -v 0 <<'PY'
import sqlite3, sys, os
from courses.models import Cohort, Homework, Project, Question, ReviewCriteria
from courses.models.cohort import RegistrationCampaign
from courses.services.cmp_content_import import SKIPPED_COHORTS
src = sqlite3.connect("file:%s?mode=ro" % os.environ["EXPORT"], uri=True)
skip = tuple(SKIPPED_COHORTS); ph = ",".join("?" * len(skip))
keep = [r[0] for r in src.execute(
    f"select id from courses_course where slug not in ({ph})", skip)]
kq = ",".join("?" * len(keep))
s = lambda q, p=(): src.execute(q, p).fetchone()[0]
expected = {
  "cohorts":   len(keep),
  "homework":  s(f"select count(*) from courses_homework where course_id in ({kq})", keep),
  "questions": s(f"select count(*) from courses_question where homework_id in "
                 f"(select id from courses_homework where course_id in ({kq}))", keep),
  "projects":  s(f"select count(*) from courses_project where course_id in ({kq})", keep),
  "criteria":  s(f"select count(*) from courses_reviewcriteria where course_id in ({kq})", keep),
  "campaigns": s("select count(*) from courses_registrationcampaign"),
}
actual = {"cohorts": Cohort.objects.count(), "homework": Homework.objects.count(),
          "questions": Question.objects.count(), "projects": Project.objects.count(),
          "criteria": ReviewCriteria.objects.count(),
          "campaigns": RegistrationCampaign.objects.count()}
bad = False
for k in expected:
    ok = expected[k] == actual[k]; bad |= not ok
    print(("ok " if ok else "BAD"), k, "source=%s target=%s" % (expected[k], actual[k]))
sys.exit(1 if bad else 0)
PY
```

**Checkpoint — homework identity.** This is the one that guards §3.1, and it is
the checkpoint that currently fails (§10):

```
$TARGET uv run --frozen python manage.py shell -v 0 <<'PY'
import sqlite3, sys, os
from courses.models import Homework
from courses.services.cmp_content_import import SKIPPED_COHORTS
src = sqlite3.connect("file:%s?mode=ro" % os.environ["EXPORT"], uri=True)
skip = tuple(SKIPPED_COHORTS); ph = ",".join("?" * len(skip))
cmp_hw = {}
for cohort, slug in src.execute(
    "select c.slug, h.slug from courses_course c "
    f"join courses_homework h on h.course_id=c.id where c.slug not in ({ph})", skip):
    cmp_hw.setdefault(cohort, set()).add(slug)
bad = False
for cohort in sorted(cmp_hw):
    have = set(Homework.objects.filter(course__slug=cohort).values_list("slug", flat=True))
    missing = sorted(cmp_hw[cohort] - have)
    unreconciled = sorted(have - cmp_hw[cohort])
    if missing or unreconciled:
        bad = True
        print("BAD", cohort, "missing=", missing, "unreconciled=", unreconciled)
print("cohorts checked:", len(cmp_hw))
sys.exit(1 if bad else 0)
PY
```

`missing` is always fatal — CMP's slug did not win. `unreconciled` is a repository
homework row the reconciler could not pair to a CMP row by slug or by exact title,
and it is **reported, not guessed at**: the run's summary lists it under
`unpaired_repository_homework`. Each one must be resolved by a decision before
step 5, because after step 5 a submission may be attached to it.

**Checkpoint — inventory**

```
$TARGET uv run --frozen python manage.py shell -v 0 -c '
import sys
from courses.models import Cohort
c = {r.slug: r.finished for r in Cohort.objects.all()}
bad = ("mlops-zoomcamp-2026" in c) or c.get("de-zoomcamp-2026") is not True
print(sorted(c)); print("de-zoomcamp-2026 finished:", c.get("de-zoomcamp-2026"))
sys.exit(1 if bad else 0)'
```

**No MLOps 2026 edition exists**, and `de-zoomcamp-2026` is `finished`. Both are
confirmed in the export and both are owner decisions.

Re-run the import and confirm no count changes.

**Failure and recovery.** **Recoverable by re-run.** The transaction boundary is
**per cohort**, not per run, so a mid-run failure leaves earlier cohorts committed
and later ones untouched; a re-run is a no-op on the finished ones and completes
the rest. Two refusals mean *stop and think*, not *re-run*:
`cohort-family-mismatch` (a cohort sits under a family the reviewed mapping does
not name) and `homework_slug_collision` on the next repository pull (a homework
row owned by no import — that is the failure mode the rename-in-place design
exists to prevent).

**Duration.** Under a minute against the current 16 cohorts.

### Step 5 — CMP learner data

**No importer for this exists.** §11 A3. Everything below is the specification it
has to satisfy, and the checkpoints it has to pass.

The export is 38 tables and **664,806** rows. Every one of those tables needs a
declared fate; here is all 38, so nothing can be forgotten:

**Import (11 tables, 510,519 rows)**

| Table | Rows |
| --- | ---: |
| `courses_answer` | 218,157 |
| `courses_criteriaresponse` | 107,691 |
| `courses_projectevaluationscore` | 38,026 |
| `courses_submission` | 36,547 |
| `courses_courseregistration` | 27,656 |
| `courses_enrollment` | 20,907 |
| `accounts_customuser` | 20,009 |
| `account_emailaddress` | 20,005 |
| `courses_peerreview` | 13,041 |
| `courses_projectsubmission` | 4,261 |
| `courses_userwrappedstatistics` | 4,219 |

An earlier draft put this step at "~663,000 rows". That number is
664,806 − 991, i.e. *everything that is not course content*, most of which is
excluded on purpose. The learner set is **510,519**.

**Never import (5 tables, 92,864 rows)** — all present in the export:

`django_session` (71,095) · `socialaccount_socialaccount` (21,761) ·
`socialaccount_socialapp_sites` (3) · `socialaccount_socialapp` (3) ·
`accounts_token` (2). `socialaccount_socialtoken` is absent from this export and
must be named anyway, so a future export cannot introduce it quietly.

> **Correction, and it matters.** An earlier draft said to reuse
> `review_import`'s `SENSITIVE_TABLES` / `SENSITIVE_PREFIXES` for this. **Do not.**
> That list is the policy for building a *sanitized review database* and it
> excludes `accounts_customuser`, `courses_enrollment`, `courses_submission`,
> `courses_answer`, `courses_criteriaresponse`, `courses_peerreview`,
> `courses_projectsubmission`, `courses_courseregistration`,
> `courses_userwrappedstatistics` and `account_emailaddress` — the entire payload
> of this step. Reusing it here would import nothing and report success. Step 5
> needs its own explicit never-import set, and it is the five tables above.

**Do not import (16 tables, 60,432 rows)**

| Table | Rows | Why |
| --- | ---: | --- |
| `data_datamaileroutboxevent` | 33,529 | email delivery operations; payloads carry recipient data |
| `data_datamaileroutboxdispatchrun` | 15,374 | as above |
| `data_datamailersendaudit` | 10,174 | as above |
| `django_admin_log` | 641 | staff audit trail of a system being retired |
| `auth_permission`, `django_content_type`, `django_migrations`, `django_site` | 296 | owned by the target schema |
| `courses_homeworkstatistics` (110), `courses_projectstatistics` (43) | 153 | derived — recompute, never copy |
| `courses_projectvote` (250), `courses_systemevaluationcriteriaresponse` (11), `courses_emailcampaign` (1), `courses_leaderboardcomplaint` (1), `courses_systemprojectevaluation` (1), `courses_wrappedstatistics` (1) | 265 | **undecided** — §12 decision 4 |

That accounts for every table and every row: 6 content (step 4) + 11 import +
5 never-import + 16 do-not-import = **38 tables**, and
991 + 510,519 + 92,864 + 60,432 = **664,806 rows**. If a future export does not
add up, a table has appeared and needs a fate before the run.

**Transforms the importer must perform.** These are not checks on the source; they
are things the source will not do for you:

1. **Neutralise every password.** All **20,009** rows arrive with a usable hash.
   Write an unusable password; do not rely on the export. See §5.4 for why this
   is a tidy-up rather than an emergency, and for the one route that is genuinely
   open.
2. **Decide `is_staff` and `is_superuser` explicitly, for ten named rows.** The
   export contains **5 superusers and 5 staff accounts** (the same five). Copying
   those columns verbatim grants production administrator rights by import, and
   copying them *together with* a usable password hash is the one combination that
   matters — an admin is precisely the account CMP let sign in with a password.
   **The rule this plan sets:** import every account with `is_staff=False`,
   `is_superuser=False` and an unusable password, with **no exception**, and grant
   staff access afterwards through Studio to named people. That converts an
   invisible default into a deliberate, auditable act. Two of those five rows are
   the duplicate pair in transform 4, so this and that decision are the same
   person's problem.
3. **Recompute, never copy, statistics and leaderboards** — the same
   `calculate_homework_statistics` / `calculate_project_statistics` /
   `update_leaderboard` path the legacy importer uses.
4. **Consolidate accounts that share an address.** See §5.5 — it is a step of the
   import with its own checkpoint, not a footnote.
5. **Certificates ride on enrollments.** There is no certificate table:
   `courses_enrollment.certificate_url` is non-empty on **2,636** rows. If
   enrollments import and that column does not, 2,636 certificates disappear.

**Checkpoint — counts**

```
$TARGET uv run --frozen python manage.py shell -v 0 <<'PY'
import sqlite3, sys, os
from django.apps import apps
src = sqlite3.connect("file:%s?mode=ro" % os.environ["EXPORT"], uri=True)
PAIRS = {
  "accounts_customuser": "accounts.CustomUser",
  "courses_enrollment": "courses.Enrollment",
  "courses_submission": "courses.Submission",
  "courses_answer": "courses.Answer",
  "courses_courseregistration": "courses.CourseRegistration",
  "courses_criteriaresponse": "courses.CriteriaResponse",
  "courses_projectevaluationscore": "courses.ProjectEvaluationScore",
  "courses_peerreview": "courses.PeerReview",
  "courses_projectsubmission": "courses.ProjectSubmission",
  "courses_userwrappedstatistics": "courses.UserWrappedStatistics",
}
bad = False
for table, model in PAIRS.items():
    want = src.execute("select count(*) from %s" % table).fetchone()[0]
    got = apps.get_model(model).objects.count()
    ok = got >= want            # >= : step 2 added pre-2024 rows of its own
    bad |= not ok
    print(("ok " if ok else "BAD"), table, "source=%s target=%s" % (want, got))
sys.exit(1 if bad else 0)
PY
```

`>=` rather than `==` is deliberate: step 2 already put pre-2024 users,
enrollments and submissions in the database. Record the step-2 totals before this
step and assert the exact delta instead — that is a stronger check and the
rehearsal is where you obtain the numbers.

**Checkpoint — the five forbidden tables**

```
$TARGET uv run --frozen python manage.py shell -v 0 -c '
import sys
from django.db import connection
FORBIDDEN = ["django_session", "socialaccount_socialaccount",
             "socialaccount_socialapp", "socialaccount_socialapp_sites",
             "socialaccount_socialtoken", "accounts_token"]
bad = False
with connection.cursor() as c:
    for t in FORBIDDEN:
        try:
            c.execute("select count(*) from %s" % t); n = c.fetchone()[0]
        except Exception:
            print("absent", t); continue
        ok = n == 0; bad |= not ok
        print(("ok " if ok else "BAD"), t, n)
sys.exit(1 if bad else 0)'
```

Run this **before** the OAuth provider rows are configured (§5), or
`socialaccount_socialapp` will legitimately be non-zero and you will not be able
to tell a configured provider from an imported one. Configure providers only after
this checkpoint has passed once.

**Checkpoint — passwords, privileges, uniqueness, orphans**

```
$TARGET uv run --frozen python manage.py shell -v 0 -c '
import sys
from django.db.models import Count, Q
from accounts.models import CustomUser
from courses.models import Enrollment, Submission
usable = sum(1 for u in CustomUser.objects.only("password") if u.has_usable_password())
staff = CustomUser.objects.filter(Q(is_staff=True) | Q(is_superuser=True)).count()
dupes = (CustomUser.objects.exclude(email="").values("normalized_email")
         .annotate(n=Count("id")).filter(n__gt=1).count())
orphan_enrol = Enrollment.objects.filter(course__isnull=True).count()
orphan_sub = Submission.objects.filter(Q(homework__isnull=True) | Q(student__isnull=True)).count()
print("usable_passwords", usable, "staff_or_super", staff,
      "duplicate_emails", dupes, "orphan_enrollments", orphan_enrol,
      "orphan_submissions", orphan_sub)
sys.exit(1 if (usable or dupes or orphan_enrol or orphan_sub) else 0)'
```

`staff_or_super` is printed rather than asserted, because the right value is a
decision (transform 2). Whatever it is, it must be deliberate and written into the
run log.

The source itself is clean on referential integrity — **measured**: zero
enrollments without a user, zero submissions without a homework, zero submissions
without an enrollment. Any orphan in the target is therefore something the
importer did, most likely dropping a cohort the row pointed at. The known cases:
**171 enrollments and 227 submissions** belong to the five owner-skipped cohorts,
and **58 users appear only in those cohorts**. Decide before the run whether those
users import with no history or are excluded; do not let the importer decide by
accident.

**Failure and recovery.** Unknown until the importer exists, and this is the
question §11 A3 must answer explicitly. **The requirement:** per-table, keyed on
the source primary key, resumable, and reporting per-table written/skipped counts
— so a failure at 400,000 rows is a re-run and not a rebuild. If it cannot be
made resumable, say so, and then a failure here means dropping the database and
restarting from step 0, which given the duration is the difference between a
10-minute recovery and a whole maintenance window.

**Duration.** Unknown; 510,519 rows. Measure it in the rehearsal. This is almost
certainly the longest step and the one that sizes the maintenance window.

### Step 6 — Events

Identity manifest first, then the two registration sources. Counts only — **no
attendee row is ever written.**

```
$TARGET make import-events IMPORT_DATABASE=<target>
```

which runs `scripts/prod/import_events.py`. (`data-ingest.md` §11 and §13 say that
script does not exist; it landed on this branch on 2026-09-03 and
`scripts/prepare_local_data.py` already composes it. The reference needs
correcting, not the plan.)

Expected: 421 events, 1,684 aliases; Luma 174 events / 52,467 rows / 52,415
eligible / 52 excluded; Eventbrite 209 events / 24,001 rows / 1 unsupported
`.xlsx`. Those are the recorded safe facts in
`_docs/migration-data/event-registration-sources.json` and the adapters reconcile
against them.

**Checkpoint**

```
$TARGET uv run --frozen python manage.py shell -v 0 -c '
import sys
from events.models import Event, EventAlias, HistoricalEventMapping
states = dict(HistoricalEventMapping.objects.values_list("state").annotate(
    n=__import__("django.db.models", fromlist=["Count"]).Count("id")))
print("events", Event.objects.count(), "aliases", EventAlias.objects.count())
print("mapping states", states)
bad = Event.objects.count() != 421 or EventAlias.objects.count() != 1684
sys.exit(1 if bad else 0)'
```

**The number that must be said out loud.** The run reports
**"3 of 383 provider events activated, 380 awaiting mapping review"**. An earlier
draft wrote "3 of 421"; 421 is the count of canonical events, 383 is the count of
provider events (174 Luma + 209 Eventbrite) and therefore of mapping rows. The 380
sit at `mapping_review_required` and render no registration count at all. **This
is a decision backlog, not a script failure, and it must not read as success.**

**Failure and recovery.** **Recoverable by re-run.** The identity import is
atomic and reports `replayed`. Registration aggregates are staged revisions; a
failed parse writes nothing, because an unsupported schema fingerprint refuses to
parse rows at all.

**Duration.** Minutes.

### Step 7 — Content

Today this step is: rebuild the committed projection. It is **not** a database
import, and pretending otherwise is the biggest single misreading available in
this plan. The *objects* the projection points at are step 8.

```
uv run --frozen python scripts/build_public_projection.py \
    --content-root <DataTalksClub/content @ pin> \
    --legacy-main-root <datatalksclub.github.io @ pin> \
    --wiki-root <DataTalksClub/podwiki @ pin> \
    --output content/public_projection
```

Expected: wiki 282 · podcasts 203 (201 transcripts) · articles 55 · people 438 ·
books 98 · events 421 · media **index** 1,253. Plus the two hand-reviewed
projections this build does not produce: FAQ (6 courses / 70 sections / 1,401
questions / 99 assets) and docs (106 pages / 39 assets).

`media.json` is an *index*, not the bytes. This step produces it; step 8 makes the
1,253 objects it names actually exist in the CDN.

**Once §11 B3 and B8 land, this step changes shape entirely.** It becomes: push to
`DataTalksClub/content`, `DataTalksClub/faq` and `DataTalksClub/docs`, and let the
sync ingest them; `DataTalksClub/podwiki` stays the wiki's source and we keep
serving `/wiki`. Until then the projection build is what there is, and it needs
three pinned checkouts and is not reproducible (#253).

> **The import is not the goal; the view cutover is.** Every public content page
> is served from a checked-in JSON file. `content/models.py` has a full database
> pipeline — `ContentRelease`, `ContentDocument`, `ContentRelation`,
> `ContentAsset` — and **nothing writes it outside tests and nothing reads it at
> all**: `content/queries.py`'s `resolve_public_document` and
> `resolve_public_asset` have zero callers outside `content/tests/`. Writing an
> importer that fills `ContentDocument` would change **no page**. The owner's
> ruling that all content moves into the database therefore has two halves, and
> only the write half has ever been discussed. §11 B3 and B4 own them.

**Checkpoint**

```
uv run --frozen python -m ci.content_update          # committed artifacts vs manifest
$TARGET uv run --frozen python manage.py check       # content.E002 digest canary
```

`manage.py check` is a real checkpoint here: `content/public_data.py`
`_checked_public_projection()` raises `ImproperlyConfigured` if any per-artifact
digest, source revision or count canary drifts, wired in as system check
`content.E002`. **You cannot hand-edit a projection file and have the site boot.**

**Failure and recovery.** **Recoverable, but not by re-run.** A full projection
rebuild is currently *not reproducible* (issue #253) and requires three pinned
checkouts simultaneously; if the build fails, the committed projection is still
what is served, so the site is unaffected.

**Duration.** Minutes.

### Step 8 — Media to the CDN

**Owner ruling, two parts.** Images go to the CDN, and **we do not keep images in
a repository**. This step is where the first happens; the second is an end state
that this step starts and §11 B11 finishes.

The mechanism already exists and is landed under
[#301](https://github.com/DataTalksClub/website/issues/301). This step is about
running it deliberately and proving the result, not building anything:
`content/media_store.py` provides `local`, `s3` and `memory` backends selected by
`PUBLIC_MEDIA_STORE_BACKEND` (**production runs `s3`**), and three commands drive
it — `public_media_hydrate` (materialise the tree), `public_media_publish` (upload
it), `public_media_verify` (compare store against `media.json`, both directions).

**Destination.** The `dtc-website-media` bucket, already provisioned. Object keys
are `<PUBLIC_MEDIA_S3_PREFIX>/<record_key>` and are derived from the **matched
record only**, never from the request path.

Today, **measured** by running the checkpoint below against the local store:
**1,253 objects, 154,115,635 bytes** — that is ~154 MB decimal, **147.0 MiB**. The
two figures are the same number and both are quoted in different places; do not
spend time at 2am reconciling "154" against "147".

#### Where the images actually are right now

"The media tree is gitignored" is not the same as "images are not in a
repository", and the difference is the whole of the second ruling. Measured with
`git ls-files`:

| Repository | Image files tracked | Note |
| --- | ---: | --- |
| **This repository** | **0** | `content/public_projection/media/` is gitignored (`.gitignore:23`). Already compliant |
| `DataTalksClub/content` | **815** | `images/{posts,podcast,books}` — exactly the 815 records whose provenance names the content repo |
| `DataTalksClub/datatalksclub.github.io` | 1,290 | includes 444 under `images/authors` at HEAD; the build consumes **438** at the pin |

So the work the second ruling implies is **not here**. This repository already
keeps no images. The 815 in `DataTalksClub/content` are the ones that have to
leave, and the legacy repository's copies stop mattering when that repository
stops being a source at all. §11 B11.

That also settles what `--source github` is for. It fetches media **from a git
repository**, which is precisely the arrangement being ended. Its honest remaining
purpose is the **one-time seeding of the bucket** from wherever the bytes live
today; once the bucket is the origin of record it should not be the default, and
once images leave the content repository it should not exist. Until then, use
`--source checkout` against a local clone, or `--source store` from an
already-hydrated peer.

#### The one-file gap, resolved

The local tree holds **1,254** files against `media.json`'s **1,253** records.
Measured: nothing is missing — the extra file is
`media/podcast/s24e06-how-to-build-ai-that-actually-ships-in-production.jpg`, and
it is **stale local residue from an episode renumbering**. The projection's real
records are `s24e06-ai-adoption-in-enterprise-beyond-writing-code.jpg` and
`s24e07-how-to-build-ai-that-actually-ships-in-production.jpg`; that episode moved
from e06 to e07 and a developer's hydrated tree kept the old filename.

`media.json` is correct and the bucket is unaffected. **Delete the file**; do not
publish it. `public_media_verify` reports exactly this class as `extra`, which is
how it was found, so the resolution is "run the checkpoint and act on `extra`",
not a one-off cleanup to remember.

#### Ordering: media before content is public

**Media must land before content is public, and after the projection build.** The
projection build (step 7) produces `media.json`; the objects it names must exist
before anyone can look at a page.

The dependency is real and one-directional: Django resolves `/images/<path>`
against `media.json` and then reads the object from the store. **If content
arrives and its media has not, every page renders and every image is broken** — a
missing object is a failed read, not a fallback. On `/people/*` that is every
avatar; on `/blog/*`, `/podcast/*` and `/events/*` it is every author chip and
every hero image. The text is fine, which is what makes it easy to ship by
accident.

It is otherwise independent: nothing in steps 0–6 reads media, and step 9 does not
either. If step 8 must slip, the honest options are to delay the site going public
or to publish media first and content second — never content first.

```
# Materialise the tree. NOT --source github; see above.
$TARGET uv run --frozen python manage.py public_media_hydrate \
    --source checkout --checkout DataTalksClub/content=<path> \
                      --checkout DataTalksClub/datatalksclub.github.io=<path>

# Upload. Incremental: an object whose recorded checksum already matches is skipped.
PUBLIC_MEDIA_STORE_BACKEND=s3 $TARGET \
    uv run --frozen python manage.py public_media_publish

PUBLIC_MEDIA_STORE_BACKEND=s3 $TARGET \
    uv run --frozen python manage.py public_media_verify
```

#### Checkpoint

`public_media_verify` is the only true bidirectional set-diff in the codebase —
`missing` / `unreadable` / `mismatched` from the record side and `extra` from the
store side, non-zero exit. It is the shape every other drift check should copy.
Five things have to be true, and four of them it already answers:

```
$TARGET uv run --frozen python manage.py shell -v 0 <<'PY'
import subprocess, sys
from content.media_store import media_records, media_store
from content.media_tooling import verify_media

records = tuple(media_records())
store = media_store()
report = verify_media(store=store, records=records)
fail = []

# 1. Every record resolves to an object that exists, with the right bytes,
#    and the store holds nothing the index does not name.
print("total", report.total, "matched", report.matched,
      "missing", len(report.missing), "mismatched", len(report.mismatched),
      "unreadable", len(report.unreadable), "extra", len(report.extra))
if report.extra[:5]:
    print("  extra:", report.extra[:5])
for name in ("missing", "mismatched", "unreadable", "extra"):
    if getattr(report, name):
        fail.append(f"{name}={len(getattr(report, name))}")
if report.matched != report.total:
    fail.append("matched != total")

# 2. Object count and total bytes.
size = 0
for record in records:
    try:
        size += store.stat(record).size
    except Exception:
        pass
print("objects", report.total, "bytes", size, f"({size / 1024 / 1024:.1f} MiB)")

# 3. The 438 author images specifically.
authors = [r for r in records if r["record_key"].startswith("images/authors/")]
missing_authors = [r["record_key"] for r in authors if r["record_key"] in set(report.missing)]
print("author images", len(authors), "missing", len(missing_authors))
if len(authors) != 438 or missing_authors:
    fail.append(f"author images {len(authors)}, {len(missing_authors)} missing")

# 4. No image is tracked in git, in this repository.
tracked = subprocess.run(
    ["git", "ls-files", "content/public_projection/media"],
    capture_output=True, text=True).stdout.split()
print("media files tracked in git:", len(tracked))
if tracked:
    fail.append(f"{len(tracked)} media files tracked in git")

print("FAIL" if fail else "OK", fail)
sys.exit(1 if fail else 0)
PY
```

**5. A re-run uploads only what changed.** `publish_media` already compares each
object's stored checksum against the record's `provenance.checksum` and skips a
match, so this is a property to *confirm*, not to build. Run
`public_media_publish` a second time and read its report: `added` and `changed`
must both be **0** and `skipped` must equal `total` (1,253). If a second run
re-uploads 154 MB, something is rewriting checksums and that is a stop.

The first run's report should account for everything: `added + changed + skipped
== total`, `failed == 0`, and `orphan` naming anything in the store the index does
not — which on the first run, after deleting the stale file above, should be empty.

**Failure and recovery.** **Recoverable by re-run, and safe to re-run**, because
both hydrate and publish are idempotent and resumable: an object already present
with the recorded checksum is skipped. A partial publish leaves the bucket with a
subset, which is exactly the state a re-run completes. The one rule: **do not
declare the step done on a partial publish.** Run publish to completion, then
verify, and treat a non-empty `missing` as a stop rather than something to fix
later.

**Measured against the local store today**, so you know what a pass looks like
before you ever point it at `s3`:

```
total 1253 matched 1253 missing 0 mismatched 0 unreadable 0 extra 1
  extra: ['images/podcast/s24e06-how-to-build-ai-that-actually-ships-in-production.jpg']
objects 1253 bytes 154115635 (147.0 MiB)
author images 438 missing 0
media files tracked in git: 0
FAIL ['extra=1']
```

Everything passes except the stale file above. Delete it and this is clean.

**Duration.** 1,253 objects / ~154 MB on the first run; seconds on any re-run.

#### The reference-rewriting problem, and who owns it

This is the part most likely to be missed, and it is not solved by this step.

An article moving to `DataTalksClub/content` while its images move to the CDN
means **something must rewrite the image references**. Today that job is done at
projection-build time inside `scripts/build_public_projection.py` —
`_article_blocks(body, media_root=...)` rewrites the references and `_copy_media`
records provenance per asset. **There is no equivalent on the database ingest
path.** So the moment §11 B3 lands and content arrives through a sync rather than
through the builder, the rewriting stops happening and nothing notices until an
image 404s.

**Owner: §11 B3**, the content push-sync, and it is called out there as the real
new work rather than left implicit. AI Shipping Labs does exactly this inside its
sync (`github_sync/media.py`, `upload_images_to_s3`), which is the fourth thing
§11.1 says to copy. The thread is
[#301](https://github.com/DataTalksClub/website/issues/301); this step is its
migration-time half, B3 is its steady-state half, and B11 is what finally takes
the bytes out of git.

Two constraints anyone doing that work inherits, both already enforced at build
time and both easy to lose: `_copy_media` **content-sniffs every file** — JPEG,
PNG and GIF magic, and an SVG sanitizer that rejects `<script>`, `<style>`, event
handlers and remote `href`/`src`/`url()` — and every object is verified against
`provenance.checksum`, so **any move must preserve byte identity**.

### Step 9 — Sponsors and testimonials

**Owner ruling: both get an import script.** Both are database-backed surfaces
with no ingest path today, and both are edited in Studio afterwards — so this is a
one-time import that seeds what Studio then owns. **No script exists yet**;
§11 B9.

What it has to write into, read rather than invented:

- **Sponsors.** `core.models.Sponsor` is a `RevisionedModel` with
  `SponsorPlacementAssignment` and an append-only `SponsorRevision` trail. Do
  **not** write rows directly — go through `core/sponsors.py`
  (`create_sponsor`, `update_sponsor`, `archive_sponsor`), which is where
  validation, placement limits, revision conflict handling and the audit trail
  live. `export_sponsor_directory` already exists; this is its missing inverse.
  Two other things claim to hold sponsors and must be reconciled by whoever writes
  this: `core/sponsor_history.py`'s hardcoded `FEATURED_SUPPORTERS` tuple, and the
  legacy `_data/sponsors.yaml`, which is read by nothing at all. **Measured**: a
  freshly migrated database has **0** `core_sponsor` rows.
- **Testimonials.** `courses.models.Testimonial` — placement, optional cohort,
  name, attribution, quote, source URL, photo path, before/after role, elapsed
  time, position, published flag — read by `courses/services/testimonials.py`.
  This model is landing on this branch right now (migrations `0055`/`0056`), so
  read it rather than inventing a shape. **Measured**: `migrate` alone leaves
  **6** rows, seeded by `courses/migrations/0056_seed_homepage_testimonials.py`.
  The importer must not duplicate those six.

**Checkpoint**

```
$TARGET uv run --frozen python manage.py shell -v 0 -c '
import sys
from core.models import Sponsor
from courses.models import Testimonial
s = Sponsor.objects.count()
t = Testimonial.objects.count()
tp = Testimonial.objects.filter(published=True).count()
print("sponsors", s, "testimonials", t, "published", tp)
sys.exit(1 if (s == 0 or t < 6) else 0)'
```

Then re-run the import and confirm no count changes — in particular that the six
migration-seeded testimonials were matched, not duplicated.

**Failure and recovery.** **Recoverable by re-run**, provided the script is keyed
on a natural key (sponsor key, testimonial name + placement). Because sponsors go
through the revisioned service, a partial run leaves a valid append-only history
rather than a corrupt one — a re-run adds revisions, it does not rewrite them.

**Duration.** Seconds. Both sets are tens of rows.

---

## 5. OAuth: the highest-consequence check in this plan

Because `socialaccount_*` and `accounts_token` are never imported, all ~20,009
imported accounts arrive with **no OAuth link at all**. The first time a member
signs in with Google or GitHub, `accounts.auth.ConsolidatingSocialAccountAdapter`
must find their imported account by email and attach the provider to it.

### 5.1 What the code actually does

This path exists and is built for exactly this migration — it is not something to
build, it is something to prove.

- Matching happens **only on a provider-verified address**.
  `is_email_verified()` returns `False` unconditionally: this site never assumes,
  the provider asserts. An unverified claim is denied with
  `verified_email_required`.
- A candidate account is found by a **verified `EmailAddress` row** *or* by the
  account's own `email` column. The second is necessary: the export carries 20,005
  `EmailAddress` rows for 20,009 accounts, and the legacy importer creates none at
  all, so an `EmailAddress`-only match would lock those members out of every
  enrollment, submission, score and certificate they own.
- Exactly one candidate must match. Zero → `verified_owner_missing`. More than one
  → `verified_owner_ambiguous`.
- `is_open_for_signup()` returns `False`.

> **The failure signature is a denial, not a silent empty account.** Because
> social signup is closed, a member whose account is not matched is *refused*,
> with one of `verified_email_required`, `verified_owner_missing`,
> `verified_owner_ambiguous`, `verified_owner_unavailable`,
> `verified_owner_quarantined`, `verified_owner_claim_mismatch` or
> `provider_uid_conflict`. That is the safe direction — nobody is handed someone
> else's history — but it means the visible symptom of a botched learner import is
> **20,000 people who cannot log in**, and it will look like an auth bug rather
> than a data bug. Check the reason code; it names the cause.

### 5.2 Proving it, with a real account

The owner can do this properly, because their own account is in the export and
they hold the real Google and GitHub accounts on the other side.

1. **Configure the providers.** `SocialApp` rows for Google and GitHub with
   credentials valid for the local callback URL. The easy path is the admin API,
   which takes them without a restart: `GET`/`PUT /api/v1/admin/auth/providers`
   and `/api/v1/admin/auth/providers/<provider>` (`management_api/urls.py`); the
   client secret is write-only. On the provider side, register the local redirect
   URI — `http://web.dtcdev.click:<port>/accounts/google/login/callback/` and the
   GitHub equivalent — before trying.
2. **Run the forbidden-tables checkpoint first** (step 5), because configuring
   providers legitimately writes `socialaccount_socialapp` rows.
3. **Sign in with Google. Then sign in with GitHub**, from a separate browser
   profile. Both, not one: Google always asserts verification, GitHub reports the
   public profile address as `verified: false` and only the API-verified addresses
   as true. GitHub is the interesting case.
4. **Look at the history, not the landing page.** You will obviously land
   somewhere. The question is whether you landed on *your imported account with
   your history attached*:
   - the signed-in home page — enrollments and current cohorts;
   - `/courses/<cohort>/` for a cohort you were in — your homework submissions and
     scores;
   - the leaderboard for that cohort — your position;
   - your certificate, if you have one.
5. **The thing that looks like success and is not:** landing on a working, empty
   account with no history. Given `is_open_for_signup = False` that should be
   impossible, so if you see it, something has changed and it is more serious than
   a failed match.

### 5.3 What one sign-in does and does not prove

It proves the mechanism end to end, for an account shaped like the owner's. It
does not prove it for 20,009 accounts with the variations they will contain: a
different-cased address, a plus-tagged address, a provider that returns no email,
a member with two verified addresses at one provider, an account already linked to
another provider, and the **one pair of accounts that share an address** (§5.5) —
that person is `verified_owner_ambiguous` by construction and cannot sign in at
all until they are consolidated.

Those shapes are covered by the automated tests in `accounts/tests_auth.py`. Both
are needed and neither substitutes for the other: the tests cover the shapes, the
owner's sign-in covers the real provider round trip, real credentials, real
callback and real session.

### 5.4 The password hashes: what CMP enforced, and what travelled

Owner: members could only sign in through a provider; passwords were for admins.
That is right, and it makes 20,009 usable hashes much less alarming than a bare
count suggests. But "was unreachable in CMP" is a property of CMP's *routing*, not
of the data, so it has to be re-established here rather than assumed to travel
with the rows.

**What enforced it in CMP.** URL shadowing, not a flag.
`course_management/urls.py` includes CMP's own `accounts.urls` **before**
`allauth.urls`, and that module replaces three paths: `accounts/login/` with a
social-only `social_login_view`, and `accounts/email/` and
`accounts/password/reset/` with a view that returns **403**. allauth's password
login form was never reachable, so the hashes were never reachable. Nothing
disabled password *authentication*; there was simply no form pointed at it.

**What travelled.** All three, identically — this site's `accounts/urls.py` is the
adopted descendant of that module and `website/urls.py` includes it before
`allauth.urls`, so it still wins. **Measured** against a migrated database with
the development-owner login disabled, which is the production shape:

| Route | Result | Why |
| --- | --- | --- |
| `GET /accounts/login/` | 200, social buttons only | shadowed by `social_login_view` |
| `GET /accounts/password/reset/` | **403** | shadowed by `accounts/views/disabled.py` |
| `GET /accounts/email/` | **403** | same |

This site adds two defences CMP did not have. `accounts/backends.py`
`DurableAccountBackend._checked_candidate` **refuses password authentication for
any `is_staff` account** unless `DEVELOPMENT_OWNER_LOGIN_ENABLED` — and it still
calls `check_password` first, so it does not leak a timing oracle. And that flag
is `RUNTIME_ENVIRONMENT is RuntimeEnvironment.DEVELOPMENT`, i.e. **False in
production**, asserted by
`accounts/tests/test_auth_configuration.py::test_owner_login_is_local_and_development_only`.
Even when it is on, `social_login_view` authenticates only an account that is
staff *and* the exact development-owner `APIPrincipal` *and* holds
`MANAGE_API_CREDENTIALS`.

**What did not travel, and it is a real hole.** `/accounts/signup/` is allauth's
own view, unshadowed, and it is **open**. `ACCOUNT_ALLOW_REGISTRATION = False` in
`website/settings/base.py:194` **is not an allauth setting** — allauth's
`DefaultAccountAdapter.is_open_for_signup` returns `True` unconditionally, this
site configures no `ACCOUNT_ADAPTER`, and the only thing that reads that constant
is an inventory report in `accounts/identity_inventory.py`. With
`ACCOUNT_SIGNUP_FIELDS = ["email*", "password1*", "password2*"]`, that is a live
password form.

**Measured, in the production shape:** `POST /accounts/signup/` with a new address
returned 302, created an account with a **usable password**, and **signed it in**.

The blast radius is bounded, and the bound was also measured rather than assumed:
`POST /accounts/signup/` with an address that already belongs to an imported
account returned 200 with the form redisplayed, created nothing, signed nobody in,
and left the existing account's password unusable. `ACCOUNT_UNIQUE_EMAIL = True`
holds. So this is **not** account takeover and no imported member's history is
reachable through it. It is unwanted local password accounts on a site whose
sign-in is meant to be provider-only — which is worth closing before 20,009
accounts and 5 admin rows land, not after. §11 A5.

Note also that a member who signs in through a provider can still reach
`/accounts/password/change/` (200 for a signed-in user; allauth redirects to
`password/set/` when the account has no usable password). Whether a member may set
a local password at all is a product decision that this plan does not make — it
just says it is currently possible, so it should be a choice.

**Checkpoint — the password path behaves as intended after import.** Run this with
the target configured as production, after step 5 and before the site is public:

```
$TARGET uv run --frozen python manage.py shell -v 0 <<'PY'
import sys
from django.conf import settings
from django.test import Client
from accounts.models import CustomUser

fail = []
if settings.DEVELOPMENT_OWNER_LOGIN_ENABLED:
    fail.append("DEVELOPMENT_OWNER_LOGIN_ENABLED is on")

usable = CustomUser.objects.exclude(password="").exclude(password__startswith="!").count()
if usable:
    fail.append(f"{usable} accounts carry a usable password")
privileged = CustomUser.objects.filter(is_staff=True).count() + \
             CustomUser.objects.filter(is_superuser=True).count()
print("accounts", CustomUser.objects.count(), "usable_passwords", usable,
      "staff+superuser", privileged)

c = Client()
for path, expected in (("/accounts/password/reset/", 403), ("/accounts/email/", 403)):
    got = c.get(path).status_code
    print(f"GET {path} -> {got} (expect {expected})")
    if got != expected:
        fail.append(f"{path} returned {got}")

# A normal member: the password form must not authenticate anyone.
member = CustomUser.objects.filter(is_staff=False, is_superuser=False).first()
if member is not None:
    r = c.post("/accounts/login/", {"email": member.email, "password": "not-the-password"})
    print("member password POST ->", r.status_code, "authenticated",
          "_auth_user_id" in c.session)
    if "_auth_user_id" in c.session:
        fail.append("a member authenticated by password")

# An admin: same answer. Password sign-in is not how anyone gets in here.
admin = CustomUser.objects.filter(is_staff=True).first()
if admin is not None:
    c2 = Client()
    r = c2.post("/accounts/login/", {"email": admin.email, "password": "not-the-password"})
    print("admin password POST ->", r.status_code, "authenticated",
          "_auth_user_id" in c2.session)
    if "_auth_user_id" in c2.session:
        fail.append("an admin authenticated by password")
else:
    print("no staff account present — expected, per transform 2")

# A fresh address every run, so a second run does not collide with the first.
import uuid
probe = f"migration-probe-{uuid.uuid4().hex}@example.invalid"
c3 = Client()
before = CustomUser.objects.count()
try:
    c3.post("/accounts/signup/", {"email": probe,
                                  "password1": "Sup3r-Str0ng-Passphrase!x",
                                  "password2": "Sup3r-Str0ng-Passphrase!x"})
except Exception as error:                      # a refusal is a pass, not a crash
    print("signup raised", type(error).__name__)
after = CustomUser.objects.count()
created = CustomUser.objects.filter(email__iexact=probe)
print("signup created an account:", created.exists(), "accounts", before, "->", after)
if created.exists():
    fail.append("/accounts/signup/ is open (see A5)")
    created.delete()                            # leave the target as it was found

print("FAIL" if fail else "OK", fail)
sys.exit(1 if fail else 0)
PY
```

It uses a deliberately wrong password, so it proves the *route* is closed without
anyone typing a real credential into a shell, and it deletes the probe account it
creates so the target is left as it was found. Run it on the rehearsal database
first anyway.

### 5.5 Consolidating accounts that share an address

**Owner ruling: consolidate.** The export has 20,009 accounts and **20,008
distinct lower-cased addresses**, so exactly one address is claimed by two rows.
Left alone, that person matches two candidates at sign-in, is denied
`verified_owner_ambiguous`, and cannot get in at all. Consolidation is therefore
**a prerequisite for them signing in**, not a tidy-up afterwards.

**Build it as a general mechanism, not a fix for two ids.** The export is re-taken
before the real run and may contain a second collision; a script that hard-codes a
pair would silently miss it. The mechanism: group accounts by lower-cased trimmed
address, and for every group of more than one, pick a survivor and repoint
everything else. If a group cannot be resolved by the rule below, **refuse the
import** rather than guessing — an unresolved group is a person who cannot sign
in, and finding that out during the run is much cheaper than after it.

**What consolidation has to move.** Every learner-bearing reference to the
discarded account, or the person signs in successfully and sees half their
history, which is worse than being locked out because nobody notices:
`courses_enrollment` (and the certificate on it), `courses_courseregistration`,
`courses_submission`, `courses_answer` (reached through its submission),
`courses_criteriaresponse`, `courses_peerreview` — **both sides**, reviewer and
reviewee — `courses_projectsubmission`, `courses_projectevaluationscore`,
`courses_userwrappedstatistics`, and `account_emailaddress`.

**Survivor rule.** Prefer the account that carries history; if both do, prefer the
one with the more recent `last_login`, keep the earlier `date_joined` on the
survivor, and record both ids in the run log. Then recompute statistics and
leaderboards for every affected cohort — the merged enrollment's position is stale
by construction.

**Enrollment is `unique_together = ["student", "course"]`**, so a naive repoint
raises `IntegrityError` wherever both accounts were enrolled in the same cohort.
The mechanism must collapse such a pair into one row, keeping the higher
`total_score` and any non-empty `certificate_url`, and must not silently drop the
loser.

**The one instance in this export, by id — expected blast radius is one person.**

| | id 2 | id 15515 |
| --- | ---: | ---: |
| joined | 2024-01-18 | 2026-02-06 |
| last login | 2026-01-29 | **2026-08-28** |
| `is_staff` / `is_superuser` | yes / yes | yes / yes |
| `account_emailaddress` rows | **0** | 1 |
| enrollments | **15** | 8 |
| submissions | **11** | 2 |
| answers | **62** | 12 |
| project submissions | **5** | 1 |
| course registrations | 0 | 1 |
| wrapped statistics | 1 | 0 |
| certificates | **1** | 0 |

**Both carry history, so this is the harder case, and the owner should see it
before the run.** It is not an empty shell beside a real account. Three cohorts
overlap — `ai-bootcamp-2025`, `ai-buildcamp-2` and `de-zoomcamp-2026` — so three
enrollment pairs collide on the unique constraint; all six of those rows have
`total_score` 0 and no certificate, so collapsing them loses nothing, but the
mechanism has to handle it rather than crash. There is **no** homework-submission
overlap, so submissions repoint cleanly.

Two further things that make this pair the same problem as transform 2: **both are
superusers**, so two of the five privileged rows are this one person, and neither
survives as an admin under the "import everyone unprivileged" rule. And id 2 has
no `EmailAddress` row at all, which is why the matcher reads the account's `email`
column — see §5.1.

**Checkpoint**

```
$TARGET uv run --frozen python manage.py shell -v 0 -c '
import sys
from django.db.models import Count
from django.db.models.functions import Lower, Trim
from accounts.models import CustomUser
groups = (CustomUser.objects.exclude(email="")
          .annotate(k=Lower(Trim("email"))).values("k")
          .annotate(n=Count("id")).filter(n__gt=1))
total = CustomUser.objects.count()
distinct = (CustomUser.objects.exclude(email="")
            .annotate(k=Lower(Trim("email"))).values("k").distinct().count())
empty = CustomUser.objects.filter(email="").count()
print("accounts", total, "distinct addresses", distinct, "blank", empty)
print("collision groups", groups.count(),
      "ids", sorted(u.pk for u in CustomUser.objects.annotate(k=Lower(Trim("email")))
                    .filter(k__in=[g["k"] for g in groups])))
sys.exit(1 if groups.count() or distinct + empty != total else 0)'
```

And the totals check, which is the one that catches a merge that left rows behind.
Record the two source-side sums before the run and assert the survivor holds them:
for ids 2 and 15515 that is **23 enrollments − 3 collapsed = 20**, **13
submissions**, **74 answers**, **6 project submissions**, **1 course
registration**, **1 wrapped-statistics row** and **1 certificate**.

---

## 6. Before the site is public

- [ ] Full test suite green
- [ ] **OAuth matching proven end to end** — §5, both providers, history checked
- [ ] Zero rows in the five never-import tables, re-checked after providers are
      configured (the count should be exactly the SocialApps you created)
- [ ] **The password path behaves as intended** — §5.4's checkpoint exits 0: no
      account carries a usable password, `/accounts/password/reset/` and
      `/accounts/email/` are 403, neither a member nor an admin can authenticate by
      password, and `/accounts/signup/` creates nothing
- [ ] **No two accounts share an address** — §5.5's checkpoint exits 0, and the
      survivor of each consolidated group holds the group's whole history
- [ ] Staff and superuser access granted deliberately through Studio after the
      import, to named people, with **zero** privileged rows arriving by import
- [ ] A member's email address is visible to admins in Studio and nowhere else.
      *Not currently mechanised* — §11 C3
- [ ] In every log line, a person is identified by user id, never by email
- [ ] Spot-check a real learner end to end: one account, all their history
- [ ] Content sync verifiable: ingested commit against upstream HEAD, per-document
      digests, and detection of both a document that never arrived and one we still
      serve that upstream deleted. **None of this exists** — §11 C1
- [ ] Sponsors and testimonials imported and visible on the pages that show them —
      step 9
- [ ] `/faq/` and `/docs/` served by this application, and their CloudFront 302s
      retired — §11 B8, B10. Retire the redirects **after** the sync works
- [ ] **Media published and verified** — step 8's checkpoint exits 0: 1,253 objects
      matched, nothing missing/mismatched/extra, the 438 author images present, and
      no image tracked in git in this repository
- [ ] A second `public_media_publish` reports `added: 0, changed: 0` — it does not
      re-upload 154 MB
- [ ] `public_media_hydrate` succeeds on a fresh clone with no access to the legacy
      repository — §11 B7
- [ ] Row counts recorded per table, so the next run has a baseline to diff against
- [ ] The chosen export filename recorded in the run log

---

## 7. What the plan and the current local path do differently

`scripts/prepare_local_data.py` (`make production-prep-dataset`) is the closest
thing to a rehearsal that exists. Where it differs from this plan, **the plan is
the decision and the difference is a work item.**

What it actually does, in order: `migrate` → `import_event_identities` →
`seed_local_courses` → `seed_course_repository_sources` → `pull_course_repositories`
→ CMP content import → derive/stage/activate registration aggregates.

| # | Difference | Consequence |
| --- | --- | --- |
| 1 | It runs `manage.py seed_local_courses` as its bootstrap — a **placeholder seeder** reading `scripts/production_like_course_specs.json`, which invents copy ("Practice assignment for …") and writes 16 cohorts as well as the 6 families. The plan uses a structural seed that writes families only. | `seed_local_courses` refuses to run outside local SQLite, so it cannot be the production bootstrap. It is the right idea in the wrong place: step 1 is the production-capable, content-free version of it — §3.2, §11 A1. |
| 2 | It does **not** run `import_legacy_zoomcamp` at all. | The rehearsal, as it stands, never exercises step 2 or the user-matching in step 5. `make import-legacy-zoomcamp` exists and is run separately. |
| 3 | It imports event identities **before** the course steps; the plan puts events at step 6. | Harmless — events depend on nothing else — but the rehearsal will not detect an ordering error the plan cares about. Either is safe. |
| 4 | It uses `courses/services/local_cmp_content_import.py` (copies the protected snapshot, sanitizes, learner tables never read) rather than `scripts/prod/import_cmp_content.py` directly. | Two entry points onto one service. The plan uses the `scripts/prod/` one. |
| 5 | It has no step 5, no step 7, no step 8 and no step 9. | The largest step, both content steps and media are unrehearsed. |

**The one difference that is no longer a difference.** The premise that
`prepare_local_data.py` runs the CMP copy *before* the course-repository pull is
**out of date** — it was true and it was fixed. The file now pulls repositories
first and carries the reason in a comment: the CMP-first order worked only while
CMP had no cohort a repository also owned, and the first time both described one it
refused on a homework slug collision. The current order matches this plan.

---

## 8. The local rehearsal

The plan is executed against a disposable database before it is trusted against
production. This section is how.

### 8.1 Relationship to the existing machinery

`make production-prep-dataset` builds the *local development dataset*: a
placeholder catalogue with real CMP content on top, no learners, no legacy
history. It is a good target for a website and the wrong target for this
rehearsal, because its bootstrap (`seed_local_courses`) is the very thing
production cannot use.

**The rehearsal supersedes it for migration purposes and reuses its parts.**
`make content-sources`, `make content-checkouts`, `make content-pull`,
`make import-legacy-zoomcamp` and `make import-events` are all shared; only the
orchestrator differs, because the rehearsal must bootstrap the way production
bootstraps. `scripts/verify_local_dataset.py` remains useful as an extra gate
after step 4.

### 8.2 A disposable target

```
export REHEARSAL=.tmp/migration-rehearsal.sqlite3
export EXPORT=/data/tmp/rds-export/rds-prod-20260902-012536.db
export TARGET="DTC_ENVIRONMENT=local DJANGO_SETTINGS_MODULE=website.settings.local \
               DTC_SQLITE_PATH=$REHEARSAL"

rm -f "$REHEARSAL" "$REHEARSAL-shm" "$REHEARSAL-wal"
```

Everything lives under `.tmp/`. Tear-down is deleting those three files. Take a
copy after each step — `cp "$REHEARSAL" "$REHEARSAL.after-step-N"` — so a failed
step can be retried from the previous state instead of from zero. That is the
cheapest thing in this document and the most useful at 2am.

### 8.3 The sequence, with what each step substitutes

| Step | Rehearsal command | Source substitution | Does it weaken the rehearsal? |
| --- | --- | --- | --- |
| 0 | `$TARGET uv run --frozen python manage.py migrate --no-input` | SQLite instead of Postgres | **Yes, mildly.** SQLite will not catch a Postgres-only constraint or collation problem. The uniqueness and FK checks still run. |
| 1 | **does not exist** — §11 A1 | reviewed structure from `course_family_catalog.py` | No — the input is in this repository |
| 2 | `$TARGET make import-legacy-zoomcamp IMPORT_DATABASE=$REHEARSAL` | none — real `zoomcamp-scoring` clone | No |
| 3 | `$TARGET make content-sources` → `content-checkouts` → `content-pull` | none — real repositories | No |
| 4 | `$TARGET uv run … scripts/prod/import_cmp_content.py --database $REHEARSAL --source $EXPORT` | none — the real export, read in place | No |
| 5 | **does not exist** — §11 A3 | — | The rehearsal cannot run at all until this exists |
| 6 | `$TARGET make import-events IMPORT_DATABASE=$REHEARSAL` | Luma/Eventbrite archives from `.local/migration-data` | No |
| 7 | `manage.py public_media_verify`, `manage.py check`, `python -m ci.content_update` | the committed projection instead of a rebuild | **Yes.** A full rebuild needs three pinned checkouts and is not reproducible today (#253). The rehearsal checks the artifacts, not the build. |
| 8 | `manage.py public_media_hydrate` → `public_media_publish` → `public_media_verify` | a `local` store instead of `s3` | **Yes.** The rehearsal proves the counts and the incrementality, not the bucket |
| 9 | **does not exist** — §11 B9 | — | Rehearse without it; do not ship without it |
| OAuth | §5.2 | real Google and GitHub, local callback | No — this is the real thing |

Run each step's checkpoint from §4 immediately after the step, with `$TARGET` and
`$EXPORT` set as above. They were written to run unchanged in both places.

### 8.4 Record these numbers

The rehearsal is the only place some of them can be obtained. Leave them here.

| Measurement | Rehearsal value | Notes |
| --- | --- | --- |
| Step 2, per edition, wall time | `mlops-zoomcamp-2022`: **244.5 s** (measured); other six: _ | 7 editions total |
| Step 2, users created per edition | `mlops-zoomcamp-2022`: **569** (measured) | |
| Step 4, wall time | _ | expected < 1 min |
| Step 5, wall time | _ | 510,519 rows; sizes the maintenance window |
| Step 5, per-table written/skipped | _ | the baseline for every future diff |
| Step 5, consolidation groups found | _ | expected **1**; a second one means the export moved — §5.5 |
| Step 6, wall time | _ | |
| Step 8, first media publish wall time | _ | 1,253 objects, ~154 MB |
| Step 8, second publish `added`/`changed` | _ | must be **0/0**, `skipped` 1,253 |
| **Total** | _ | this is the maintenance window |

### 8.5 A number worth pre-computing

Step 5 then step 2 (or step 2 then step 5) must **match** learners by email rather
than duplicate them. The precise expectation is computable from the export, and it
is far better than "the total rose by strictly fewer than the number of legacy
users". **Measured** for `mlops-zoomcamp-2022`: 569 legacy users, of whom **120**
already exist in the CMP export by lower-cased email, so the account total should
rise by exactly **449** for that edition. Compute the same for the other six during
the rehearsal and assert the exact delta.

### 8.6 What the rehearsal cannot prove

Say these out loud rather than letting a green rehearsal imply them.

- **Postgres.** The rehearsal runs on SQLite. Constraint names, collation-sensitive
  uniqueness (the email collision in §4 is exactly this shape), transaction
  behaviour under concurrency, and index build times are all different.
- **Scale and time.** Local wall times are not production wall times, and the
  learner import is the step where that matters most.
- **A real cutover.** No traffic, no live CMP still accepting writes during the
  run, no DNS, no rollback under time pressure.
- **The export's edge cases at production scale.** The rehearsal reads the same
  file, so the data is real — but it reads *one* export. The export taken on the
  day will differ, and the row counts in this document will be stale by exactly
  that much.
- **The 380 unreviewed event mappings.** A rehearsal shows 3 activated, which is
  correct and is not success.
- **Step 7 as a rebuild.** See §8.3.
- **Step 8 against the real bucket.** The rehearsal publishes to a `local` store,
  which proves the counts, the 438 author images and the incrementality, but not
  S3 credentials, bucket policy or upload time for 154 MB.
- **Everything downstream of the content view cutover**, because it has not been
  built.

---

## 9. Failure and recovery, at a glance

| Step | Transaction boundary | Partial state after a failure | Recovery |
| --- | --- | --- | --- |
| 0 Schema | per migration | possibly mid-migration | **Drop and restart.** The only step where that is the answer |
| 1 Structural seed | per row (six rows) | some families written | Re-run. Cheapest step in the plan |
| 2 Legacy | none (per row) | that edition partial, statistics stale | Re-run the edition. Import one edition at a time |
| 3 Repositories | per repository | earlier repositories complete | Re-run; expect `replayed`. A checksum/identity conflict means stop |
| 4 CMP content | **per cohort** | earlier cohorts complete | Re-run; no-op on the done ones |
| 5 CMP learners | **to be decided — §11 A3** | unknown | Must be resumable per table. If it cannot be, a failure costs a full rebuild |
| 6 Events | atomic (identity); staged revisions (aggregates) | nothing half-written | Re-run |
| 7 Content | build writes files | committed projection unchanged, so the site is unaffected | Re-run the build; it needs three pinned checkouts and is not reproducible (#253) |
| 8 Media | per object | bucket holds a subset; every unpublished image is broken on the page | Re-run. Publish is incremental and skips a matching checksum, so a re-run completes rather than re-uploads |
| 9 Sponsors/testimonials | per service call (revisioned) | some rows written, history still valid | Re-run; must be keyed on a natural key |

**The step with no rollback that should have one is step 5.** Everything else is
either re-runnable in place or leaves the previous state serving. A half-imported
learner set is the one condition from which the only certain exit is a rebuild
from step 0 — which, at 510,519 rows, is the difference between a short recovery
and a lost maintenance window. Design A3 for resumability, or accept that and
budget the window for two full runs.

---

## 10. What the current database would pass today

`.tmp/production-prep-current.sqlite3` (7.6 MB, built 2026-09-02 by
`prepare_local_data.py`) is the nearest thing to a rehearsal we have. Run against
it, this plan's checkpoints give:

| Checkpoint | Result | Why |
| --- | --- | --- |
| Step 0 — empty target | **n/a** | It is a built database, not a fresh one |
| Step 1 — six reviewed families, no cohorts | **FAIL, and correctly** | All six families are present with the right titles — but they were written by `seed_local_courses` along with 16 cohorts and their invented copy, so the "structure only" half of the checkpoint fails. That is the difference the structural seed exists to remove |
| Step 2 — 7 legacy editions | **FAIL** | 0 present. `prepare_local_data.py` never runs that importer (§7 #2) |
| Step 3 — 20 modules / 181 units | **PASS** | `ai-dev-tools-2026` 4/4, `llm-zoomcamp-2026` 7/72, `ml-zoomcamp-2026` 9/105 |
| Step 3 — family-name cohort slugs | **PASS** | `ai-dev-tools-2026`, not `ai-dev-tools-zoomcamp-2026` |
| Step 4 — counts vs source | **FAIL, narrowly** | cohorts 16=16, projects 42=42, criteria 117=117, campaigns 5=5; **homework 107 vs 104**, **questions 500 vs 494** |
| Step 4 — homework identity | **FAIL** | every CMP slug is present (`missing=[]`), but `llm-zoomcamp-2026` still carries `homework-03`, `homework-06`, `homework-07` — three repository rows the reconciler could not pair, plus their 6 questions. That is the entire count delta above |
| Step 4 — inventory | **PASS** | no MLOps 2026 edition; `de-zoomcamp-2026` is `finished` |
| Step 5 — learner counts | **FAIL** | 1 account, 0 enrollments, 0 submissions. No importer exists |
| Step 5 — five forbidden tables | **PASS with a caveat** | `django_session` 6 and `socialaccount_socialapp` 3 are locally created, not imported. `socialaccount_socialaccount` and `accounts_token` are 0 |
| Step 6 — 421 events / 1,684 aliases | **PASS** | exactly |
| Step 6 — activation coverage | **PASS as designed** | 383 mapping rows: **3 mapped, 380 review_required** |
| Step 7 — projection | **PASS** | the database boots, so `content.E002` is satisfied |
| Step 8 — media | **FAIL, by one file** | The local tree holds 1,254 files against 1,253 records; `verify` reports one `extra`, `media/podcast/s24e06-how-to-build-ai-that-actually-ships-in-production.jpg`, stale residue from an episode renumbering. Nothing is missing. Delete it |
| Step 8 — no image tracked in git | **PASS** | `git ls-files content/public_projection/media` returns 0. The 815 in `DataTalksClub/content` are B11's problem, not this repository's |
| Step 9 — sponsors and testimonials | **FAIL** | 0 sponsors. `courses_testimonial` does not even exist in that database — it predates migrations `0055`/`0056`, which on a freshly migrated database seed 6 testimonials and 0 sponsors |
| §5.4 — password path | **FAIL** | `/accounts/password/reset/` and `/accounts/email/` are correctly 403, but `/accounts/signup/` creates an account with a usable password and signs it in (measured). §11 A5 |
| §5.5 — no shared addresses | **PASS, vacuously** | 1 account. It proves nothing; the check only becomes meaningful once step 5 exists |

Two conclusions worth carrying forward. First, the three unreconciled
`llm-zoomcamp-2026` homework rows are a **live defect in the current dataset**,
and they are exactly the condition §3.1 says must be resolved before any
submission lands. Second, the checkpoints work: they found it, they named it, and
they exited non-zero.

---

## 11. Build before the dry run

Ordered so someone can work down it. Sizes are honest estimates, not targets.

### A. Blocks the dry run — the rehearsal cannot start without these

**A1. The structural seed.** *Step 1. Blocks step 4. Small (hours), and it is the
cheapest item on this list.* Owner ruling: a migration-only seed script that sets
up the course structure before the imports, so the imports can be run safely. One
`scripts/prod/` script writing the six rows of `COURSE_FAMILY_TITLES` — slug and
reviewed title, nothing else. Not `seed_local_courses`, which invents content and
refuses to run outside local SQLite. Keyed on the family slug so a re-run is a
no-op. See step 1 for the full specification and the checkpoint.

This is what makes `sma-zoomcamp` work, per the second owner ruling: **it comes
from CMP.** It is not on the skip list, all three editions are already in
`COHORT_FAMILY_IDENTITIES`, and the only thing it ever lacked was a family row —
so no skip has to be lifted and no special case is needed. **Done looks like:**
step 1's checkpoint exits 0, and then step 4's inventory checkpoint lists 16
cohorts including all three `sma-zoomcamp` editions, on a database seeded only by
steps 1 to 3. Independent of everything else; nothing else can be rehearsed until
it exists.

**A2. Resolve the three unreconciled `llm-zoomcamp-2026` homework rows.**
*Blocks step 4's identity checkpoint. Small (hours), but it is a content
decision.* `homework-03`, `homework-06`, `homework-07` pair to no CMP row by slug
or exact title. Either CMP gains the matching rows, or the titles are aligned so
the existing pairing rule matches, or they are deliberately deleted. **Done looks
like:** the step 4 identity checkpoint exits 0. Must be settled **before** step 5
ever runs, because after it a submission may point at one.

**A3. The CMP learner importer.** *Blocks step 5 — i.e. everything. **Large
(1–2 weeks)**, and it is the single biggest item in this plan.* See §4 step 5 for
the full specification: 11 tables / 510,519 rows, 5 never-import tables, five
mandatory transforms, resumable per table, per-table written/skipped reporting,
PII-safe logging by user id. `scripts/load_rds_export.py` looks like a starting
point but its `main()` is disabled and only its internals survive, in two test
modules. **Done looks like:** all four step-5 checkpoints exit 0 against a
rehearsal database, and the run reports its own per-table counts. Depends on A1
(cohorts must exist) and A2 (homework identity must be final).

**A4. Decide the export-day questions the importer encodes.** *Blocks A3's
design. Small (hours), but they are owner decisions and cannot be guessed.*
Two of the four are now ruled and written into step 5's transforms: shared
addresses are **consolidated** (§5.5), and every account imports **unprivileged
with an unusable password**, staff being granted afterwards through Studio. Still
open: (i) the 58 users who exist only in the five owner-skipped cohorts, and the
171 enrollments and 227 submissions there; (ii) the six small undecided tables in
§12 decision 4. **Done looks like:** each written into this document as a rule the
importer implements. Can be answered in parallel with A3 starting.

**A5. Close `/accounts/signup/`.** *Small (hours). Do it before step 5 runs, not
after.* allauth's signup view is unshadowed and open;
`ACCOUNT_ALLOW_REGISTRATION = False` is not an allauth setting and nothing reads
it. **Measured**: in the production shape a signup POST creates an account with a
usable password and signs it in. It cannot take over an imported account —
`ACCOUNT_UNIQUE_EMAIL` refuses a duplicate address, also measured — so this is
unwanted local accounts rather than stolen history, which is why it is A5 and not
A1. Two honest fixes: shadow `accounts/signup/` the way `accounts/login/`,
`accounts/email/` and `accounts/password/reset/` already are, or configure an
`ACCOUNT_ADAPTER` whose `is_open_for_signup` returns `False` and have it read the
constant that already exists. **Done looks like:** the signup probe in §5.4's
checkpoint creates nothing, and a test asserts it. Independent of everything else.

**A6. The consolidation mechanism.** *Part of A3, named separately because it is
the part most likely to be skipped. 1-2 days inside A3.* Group accounts by
lower-cased trimmed address, pick a survivor, repoint every learner-bearing row,
collapse enrollments that collide on `unique_together = ["student", "course"]`,
recompute the affected cohorts, and refuse the import on any group the rule cannot
resolve. General, not a hard-coded pair — the export is re-taken before the run
and may carry a second collision. **Done looks like:** §5.5's checkpoint exits 0
and the survivor's history totals equal the sum of what the group held. One known
instance today, ids 2 and 15515, both carrying history.

### B. Blocks production — the rehearsal can run without these; the site cannot ship

**B1. A rehearsal orchestrator that bootstraps the way production does.**
*Medium (2–3 days).* `prepare_local_data.py` bootstraps with `seed_local_courses`,
which refuses to run outside local SQLite (§7 #1). Either add a mode that skips
the seeder and runs step 2 instead, or write a thin `scripts/prod/` orchestrator
that runs steps 0-8 in this document's order. The manual sequence in §8.3 is
the fallback and it works today, which is why this is not an A item. **Done looks
like:** one command
builds a rehearsal database with no placeholder rows in it —
`verify_local_dataset.py`'s `practice_assignment_homeworks` and
`generated_project_descriptions` both zero, for the right reason. Parallel with A3.

**B2. Fix the `DataTalksClub/content` ingest contract.** *Medium (2–3 days).*
`content_sync/dtc_content/contract.py` declares a flat `podcasts/*.yaml` /
`podcasts/transcripts/*.yaml` allowlist; at `PREFERRED_CONTENT_REVISION` the
repository is season-hierarchical (`podcasts/s12/e08.yaml`, 24 season
directories). **A push-sync built against that allowlist today matches nothing.**
Its `ACCEPTED_COUNTS` are also one migration generation stale (205/203 against the
built 203/201). Standing constraint: **do not re-pin `contract.py` casually** —
the pins are an acceptance record. Change the allowlist to the hierarchy the
builder already handles, and re-pin counts only with the reason recorded. **Done
looks like:** `manage.py verify_dtc_content` passes against a current checkout and
the adapter enumerates 203 podcasts. Must precede B3.

**B3. The `DataTalksClub/content` push-sync.** *Large (1–2 weeks).* Reuse the
course-repository shape — one implementation, two transports, repository list from
registered `ContentSource` rows — rather than a second entry point. The genuinely
new work is media: assets must go to the object store and **references must be
rewritten**, which today only happens at projection-build time inside
`_article_blocks` / `_copy_media`. **Done looks like:** a push to the content
repository lands articles, podcasts, people and books as `ContentDocument` rows
with their assets resolved, and a re-push replays. Depends on B2. See §11.1 for
which parts of the AI Shipping Labs sync to copy.

**B4. Switch the content views to read the database.** *Large (1–2 weeks).*
`content/queries.py` `resolve_public_document` / `resolve_public_asset` have zero
callers outside tests. Until this lands, **B3 changes nothing a visitor sees**.
This is the half of "all content moves into the database" that has never been
scheduled. Depends on B3.

**B5. Move `_people` to `DataTalksClub/content`.** *Medium (2–3 days).* The last
editorial folder with no destination and the only reason (with `events.yaml`) the
projection build still needs a legacy checkout. Carry the front-matter field
allowlist, the `short == filename stem` rule and the `picture` path pattern across
with it — they are enforced only inside the projection builder today, and moving
the folder without them silently loses the constraint. **Done looks like:** the
build takes people from `--content-root`. Independent.

**B6. Give event content a home.** *Medium (3–5 days).* All 421 events take
title, dates, description, speakers and links from `_data/events.yaml` in the
legacy repository. `events.Event` is deliberately thin. This is a one-off export
into the database or into the content repository. **Done looks like:** the
projection build no longer needs `--legacy-main-root` for events. Independent;
blocks §12 decision 1.

**B7. Close the `public_media_hydrate` legacy default.** *Small (hours).*
**A separate Codex run is fixing this now — do not duplicate it; check whether it
has landed before starting.* `--source github` is the default, and a fresh clone
or a bucket re-hydration therefore fetches 438 author images from the legacy
repository and reports `failed: 438` once it is gone. Production on `s3` and CI on
`memory` are unaffected, which is why it survived this long. **Done looks like:**
`public_media_hydrate` on a fresh clone succeeds with no network access to the
legacy repository. Independent.

Note the ruling makes this more than a default. `--source github` reads media
*from a git repository*, which is the arrangement being ended: once the bucket is
the origin of record the source has no steady-state purpose beyond the one-time
seeding in step 8, and once B11 lands it has none at all. Whether it survives that
is worth deciding rather than leaving as an unused branch.

**B8. FAQ and docs content sync.** *Medium (3–5 days), and it may collapse into
B3.* Per the owner's ruling, `DataTalksClub/faq` and `DataTalksClub/docs` are
git-synchronised into our database and served by us. Today neither has a builder,
a sync, a webhook or a `ContentSource` row in this repository — only committed
projections and a CI checker. Register both as `ContentSource` rows with their own
adapter types and dispatch them through the same ingest as B3. **Done looks like:**
a push to either repository updates `/faq/` or `/docs/` without a hand-reviewed
projection file, and a re-push replays. Depends on B4 for the read side; the write
side can start alongside B3.

**B9. The sponsor and testimonial import script.** *Small (1–2 days).* Step 9. One
`scripts/prod/import_*` entry point writing sponsors through `core/sponsors.py`'s
services and testimonials into `courses.models.Testimonial`, keyed on a natural key
so a re-run is a no-op and the six migration-seeded testimonials are matched rather
than duplicated. The input format is an owner decision — the obvious candidates are
the legacy `_data/sponsors.yaml` (currently read by nothing) and the existing
`export_sponsor_directory` output, which would make the script its exact inverse.
**Done looks like:** step 9's checkpoint exits 0 and a second run changes no
counts. Independent of everything else.

**B10. Retire the `/faq/` and `/docs/` CloudFront 302s.** *Small, but it is a
change in `DataTalksClub/aws-infra`, not here.* Once B8 works, the redirects are
shadowing pages we own. Leave `/podwiki/` and `/mediakit/` alone — those are still
deliberately not hosted. **Done looks like:** `/faq/` and `/docs/` resolve to this
application in production. Depends on B8. Do this *after*, never before.

**B11. Take the images out of `DataTalksClub/content`.** *Medium (2–3 days), and
it is the second half of the owner's ruling.* Measured: this repository tracks
**0** image files, `DataTalksClub/content` tracks **815**, and the legacy
repository tracks 1,290. Step 8 puts the bytes in the CDN; this removes them from
git, which is a separate act with a real ordering constraint — **the bucket must
be verified complete before anything is deleted**, because after deletion the
bucket is the only copy. Sequence: step 8 → its checkpoint exits 0 → B3's sync
resolves references against the CDN (not against repository paths) → only then
delete. **Done looks like:** `git ls-files "images/*"` in `DataTalksClub/content`
returns nothing, and every article still renders its images. Depends on step 8 and
on B3.

### C. Wanted, not blocking

**C1. A content drift check.** *Medium.* Nothing answers "is what we serve what
upstream says?" for `DataTalksClub/content`. All the parts exist:
`parity.py` is ~90% of it but is pinned to `ACCEPTED_CONTENT_COMMIT` and only
iterates projection → bundle; `public_media_verify` is the right report shape;
`ContentSource.last_reconciled_at`, `pending_follow_up` and
`freshness_target_minutes` are declared with zero readers and zero writers. §6's
"content sync verifiable" checkbox cannot be ticked without it.

**C2. Make `verify_local_dataset.py` point at a non-local target.** *Small.* It
forces `DTC_ENVIRONMENT=local` and `DTC_SQLITE_PATH`, so the best existing
checkpoint script cannot be run against production.

**C3. Mechanise "a member's email is visible to admins in Studio and nowhere
else."** *Small.* It is a stated policy with no test that would fail if a template
or a log line started rendering an address.

**C4. Record the run.** *Small.* One JSON file per production run: export
filename, per-step counts, per-step wall time, checkpoint results. It is the
baseline every future diff needs, and §6 already asks for it.

### 11.1 What to copy from AI Shipping Labs, and what not to

The owner pointed at AI Shipping Labs as the pattern for content sync. It is a
sibling Django application at `~/git/ai-shipping-labs` (and it is the same system
as the `rds-aisl_prod` export in §14). Its sync lives in
`integrations/services/github_sync/` behind a single webhook,
`POST /api/webhooks/github`.

**Our `content_sync/course_repository_ingest.py` is the right vehicle.** It is the
same architecture — one implementation, a signed push webhook and a pull command,
a repository list held as registered `ContentSource` rows, a transactional
projection, replay detection. Do not build a second pipeline. Four things AI
Shipping Labs does that we do not, and each is worth copying into B3/B8:

1. **Per-kind dispatchers under one orchestrator.**
   `github_sync/dispatchers/` has one module per content kind — articles, courses,
   events, instructors, projects, workshops, marketing pages and more — all driven
   by one classification pass. That is exactly the shape B3 and B8 need for
   articles, podcasts, people, books, FAQ and docs, and it is why they should be
   one pipeline rather than six.
2. **A durable per-run record with a `deleted` count.** `SyncLog` stores
   `items_created` / `updated` / `unchanged` / `deleted`, `commit_sha` and a
   per-item detail blob. Our `CourseCurriculumImportRun` records curriculum runs
   only, and nothing anywhere records a *deletion*. C1's drift question (b) — "do
   we still serve something upstream deleted?" — is answerable for free if the
   sync records it.
3. **Upstream HEAD resolution.** `repo.fetch_remote_head_sha` answers "are we
   behind upstream?". **Nothing in our tree resolves an upstream ref at all**;
   that is C1's question (d) and its hardest missing part.
4. **Media handled inside the sync.** `github_sync/media.py`
   `upload_images_to_s3` uploads assets during the sync run. In our tree that job
   exists only inside the projection builder (`_copy_media`, `_article_blocks`),
   which is precisely the gap §2 flags as the least obvious part of B3.

Also worth taking: the sync lock (`ContentSource.sync_locked_at` with a timeout)
and `max_files` as a per-source ceiling.

**Do not copy its transport.** AI Shipping Labs clones or pulls a working checkout
(`repo.clone_or_pull_repo`) and walks the tree. We deliberately read a `git
archive` tar on both sides, because `git archive` honours `.gitattributes`
`export-ignore` and `export-subst` and a working-tree walk does not — a pull that
walked the checkout would import files the push route never sees. Ours is the
stronger choice; keep it.

---

## 12. Open decisions

These block nothing today but must be closed before the migration runs.

1. **Event content has no home.** §11 B6 is the work; the decision is *database or
   content repository*.

2. **`_conferences` (2 records) has no fate**, reaches no page, and **6 event rows
   carry links to conference pages that do not exist on our site** — the links are
   dropped at build time and the drop is count-asserted at
   `build_public_projection.py:1891`, so it is deliberate and visible rather than
   silent. The two conference programmes are nested multi-track agendas with
   talks, speakers and abstracts, much richer than an `events.yaml` row. Two
   honest options: fold them into B6's event-content export, or delete them and
   accept 6 broken links being 6 removed links. **Doing nothing is the one option
   that is not available**, because the links currently point at pages we do not
   serve.

3. **Legacy certificates have names but no URLs.** The `mlops-zoomcamp-2022`
   import produced 82 certificate names and 0 certificate URLs. Is a certificate
   without a URL acceptable for pre-2024 cohorts, or does something have to
   produce one?

4. **Six small CMP tables have no declared fate**: `courses_projectvote` (250),
   `courses_systemevaluationcriteriaresponse` (11), `courses_emailcampaign` (1),
   `courses_leaderboardcomplaint` (1), `courses_systemprojectevaluation` (1),
   `courses_wrappedstatistics` (1). Small enough to ignore and small enough to
   import; either is fine, neither by accident.

5. **380 of 383 event mappings are unreviewed.** The scripts work; the mappings
   are a decision backlog.

6. **The five owner-skipped CMP cohorts.** `ai-bootcamp-2025`, `ai-hero-2025`,
   `ai-hero-2026` need a reviewed family, title and publication state.
   `ai-buildcamp-2` and `ai-buildcamp-3` need *design*, not a mapping entry — "2"
   is an edition number, not a year, and the family+year model cannot express it.
   They carry 171 enrollments, 227 submissions and 58 users who appear nowhere
   else.

7. **The sponsor/testimonial import format.** §11 B9 needs an input. The legacy
   `_data/sponsors.yaml`, the existing `export_sponsor_directory` output, or a
   hand-written file — an owner decision, not an engineering one.

### 12.1 Decisions that are now closed

Recorded so they are not reopened.

| Was open | Ruling |
| --- | --- |
| How do the importers land against an empty database? | **A migration-only structural seed runs first** — step 1, §11 A1 |
| Where does `sma-zoomcamp` come from? | **CMP.** It is not skipped and needs no special case; it only ever lacked a family row, which step 1 writes — §3.2 |
| Are 20,009 usable password hashes an emergency? | **No.** Members signed in through a provider; passwords were for admins. The enforcement was URL shadowing and it travelled — §5.4. Every account still imports unprivileged with an unusable password |
| The two accounts sharing one address | **Consolidate them**, as a general mechanism with one known instance — §5.5, §11 A6 |
| Public media objects had no fate (`data-ingest.md` §2 row 18) | **Step 8.** Images go to the `dtc-website-media` bucket **and out of every git repository** — §11 B11 |
| The one-file gap between the tree (1,254) and `media.json` (1,253) | **Stale local residue** from an episode renumbering. Nothing is missing; delete it. `public_media_verify` reports the class as `extra` |
| Do FAQ and docs stay at the legacy site, or come to us? | **They come to us via content sync.** The CloudFront 302s are transitional — §11 B8, B10 |
| Sponsors and testimonials have no source | **They get an import script** — step 9, §11 B9 |
| Is `rds-aisl_prod` in scope? | **No.** §14 |
| Do books and people exist in two repositories? | **No dual ownership.** podwiki's `_people`, `_books` and `_podcast_summaries` are never opened by the builder — all four `wiki_root` joins read `_wiki/`, `graph/`, `search/` and one asset. Nothing to reconcile |
| Are the projection count deltas silent drops? | **No.** Every one is pin drift (additions after the pin; zero deletions), a `_template.md` scaffold, or 2 podcast episodes removed under a signed manifest (`content/migration/podcast-removals.yaml`) |

---

## 13. Constraints

Non-negotiable, and every one of these has a reason behind it.

- `/data/tmp/rds-export/` is read **in place**, read-only. Never copied into the
  worktree, never committed.
- **Never import** `django_session`, `socialaccount_socialaccount`,
  `socialaccount_socialapp`, `socialaccount_socialapp_sites`,
  `socialaccount_socialtoken`, `accounts_token`. Five of the six are present in
  the export. Step 5 owns this list; **do not** reuse `review_import`'s
  `SENSITIVE_TABLES` for it — that list is the sanitized-review-database policy and
  excludes the whole payload of step 5.
- A member's email address is visible to admins in Studio and nowhere else.
- In a log, identify a person by user id, never by email address.
- Do not hand-edit anything in `content/public_projection/` — the startup digest
  check (`content.E002`) will refuse to boot.
- Do not add a second entry point for course-repository ingest. There is one and
  both transports share it deliberately.
- Plain scripts, direct ORM, no framework. Call the existing services rather than
  reimplementing them — in particular the CMP reconciliation, which encodes
  decisions that took two migrations to get right.

---

## 14. Explicitly out of scope

Written down so nobody rediscovers these and re-raises them as gaps.

**`rds-aisl_prod` — the second production database.**
`/data/tmp/rds-export/rds-aisl_prod-*.db`, refreshed daily alongside the CMP
export: **108 tables, 151,402 rows**, with its own `events`, `content`,
`payments`, `plans`, `questionnaires`, `bookclub`, `crm` and `analytics` apps,
5,142 accounts and 1,508 event registrations. It is the database of **AI Shipping
Labs** (`~/git/ai-shipping-labs`). **Owner ruling: "this is a different website."**
Nothing in it is migrated here, no script in this repository reads it, and the
files in `/data/tmp/rds-export/` matching `rds-aisl_prod-*` are to be ignored by
every step of this plan. Its content-sync design is still worth borrowing —
§11.1 — but its data is not ours.

**`/podwiki/` and `/mediakit/` redirects.** Pages we deliberately do not host.
Redirecting is not ingesting. Unlike `/faq/` and `/docs/`, these two stay
redirected.

**`scripts/sync_course_platform.py`.** It syncs *application code* from
`DataTalksClub/course-management-platform` against a 768-row ledger. Not data
ingest.

**`scripts/production_like_course_specs.json` and the `courses.json` artifact it
produces.** The specs seed the *local* catalogue only; `courses.json` (12 records)
is built, committed and digest-verified, and read by no view. `/courses` is
database-served. Neither is migrated. `courses.json` could be deleted — that is
worth doing eventually and is not worth doing now.

**`backfill_event_qna` and `retry_event_qna`.** Zero callers. Not a migration
concern; noted so the next survey does not re-find them.

**`content/review_projection.json`'s six `edit_url` values** pointing at legacy
`_posts` paths. Loaded at startup, rendered by no template. Dead links in data,
not on a page. Not worth a change.

**Already-deleted legacy machinery — verify it stays gone.**
`scripts/build_pinned_legacy_sources.py` (which cloned the legacy repository into
`.tmp/`), `scripts/build_legacy_manifest.py`,
`scripts/validate_github_editorial_source_projection_inventory.py`,
`compatibility/source_config.py`'s `PINNED_LEGACY_SOURCES` and
`_docs/compatibility/legacy-manifest.jsonl` are deleted on this branch and nothing
live imports them. Untracked on-disk residue —
`.tmp/legacy-compatibility-sources/`, `.tmp/legacy-main-pinned/` and a stale
`.pyc` — is harmless and is not tracked.

**`_docs/design/specs/script-inventory.md` is stale.** It still documents the four
pre-consolidation script paths. Do not base a survey on it.
