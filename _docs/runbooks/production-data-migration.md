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

Every count here was measured on `main` on 2026-09-03 against
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
every source `data-ingest.md` §2 enumerates, with its fate, plus one that document
does not list at all — row 22, the site assets in `core/static/core/`, which were
never treated as a migratable source because they travel the staticfiles pipeline
rather than the media one. Nothing is left out; where the answer is "we
deliberately do nothing", it says so.

| # | Source | Model | Fate and owner | Step |
| --- | --- | --- | --- | --- |
| 1 | Course repositories (3) | git-synchronised | `content_sync/course_repository_ingest.py` | 2 |
| 2 | `DataTalksClub/content` — articles, podcasts, books | git-synchronised after migration | push-sync — **to build**, §11 B3 | 6 |
| 3 | `DataTalksClub/podwiki` — wiki, graph, search | pinned build → git-synchronised | source stays at podwiki; **we serve `/wiki`** | 6 |
| 4 | `DataTalksClub/faq` (6 courses / 70 sections / 1,401 questions) | **git-synchronised** | **owner ruling: comes into our database via content sync** — to build, §11 B8 | 6 |
| 5 | `DataTalksClub/docs` (106 pages) | **git-synchronised** | **owner ruling: same as FAQ** — to build, §11 B8 | 6 |
| 6 | CMP repo `production_like_course_specs.json` | pinned build | local seeding only; produces the unused `courses.json`. **Not migrated** — §14 | — |
| 7 | Legacy `_people` (443) | pinned build | push-sync — move to `DataTalksClub/content`, §11 B5 | 6 |
| 8 | Legacy `_data/events.yaml` (421) | pinned build | one-off export — **no home yet**, §11 B6 | 6 |
| 9 | Legacy `images/authors/` (438) | pinned build | one-off export → CDN; **live defect**, §11 B7 | 6 |
| 10 | Legacy `_data/faqs/` article FAQ (159 pairs) | one-time seed → git-synchronised | **owner ruling: pairs move to article frontmatter `faq:` in `DataTalksClub/content` and enter through the content ingest; no separate model** | 6 |
| 11 | CMP export — course content (991 rows) | one-time | `scripts/prod/import_cmp_content.py` | 3 |
| 12 | CMP export — learner data (510,519 rows) | one-time | **to build**, §11 A3 | 4 |
| 13 | `zoomcamp-scoring` — pre-2024 history | one-time | `scripts/prod/import_legacy_zoomcamp.py` | 1 |
| 14 | Event identity manifest (421 / 1,684) | one-time | `scripts/prod/import_events.py` — **no longer a migration** | 5 |
| 15 | Event description bridge | one-time | committed capture; `scripts/build_event_description_bridge.py` | 6 |
| 16 | Luma aggregates | one-time | `scripts/prod/import_events.py` → `events/importers.py` | 5 |
| 17 | Eventbrite aggregates | one-time | as above | 5 |
| 18 | Public media objects (1,253 → 997, measured) | already CDN-resident; verify, flatten, delete | **owner ruling: to the CDN and out of git**, renamed on ingest, cards and covers deleted — §11 B11, B14, B15 | **7** |
| 19 | **Sponsors** | one-time then Studio | **owner ruling: give it an import script** — to build, §11 B9 | 8 |
| 20 | **Testimonials** | one-time then Studio | `scripts/prod/import_testimonials.py` — **no longer a migration** | 8 |
| 21 | `rds-aisl_prod` | — | **explicitly out of scope** — §14 | — |
| 22 | Site assets in `core/static/core/` (18 + 14 orphaned) | one-time publish, then per-release | **owner ruling: no assets in the repository** — §11 B12, B13 | **7** |

The owner's original list had ten sources. `DataTalksClub/podwiki`, `faq`,
`docs`, the CMP repository's course specs, public media, sponsors and testimonials
were folded into "the content repository" and are not in it and never were.

---

## 2. Where content lives after the migration

`DataTalksClub/datatalksclub.github.io` stops being a content source. Taking
content from it **in the interim is fine**; depending on it at the end is not.

**Owner ruling: the legacy repository lives only in temporary one-time
prod-ingest scripts, never anywhere else.** No runtime view, service, check,
builder, adapter, or synchronized sync reads it. The committed snapshot it
produced now sits at `temporary/content/` as explicit ingest input, excluded
from the release image. The remaining legacy reads are the violation list in
`_docs/architecture/database-only-content.md`; each is removed by its own move:
people and article FAQ pairs into `DataTalksClub/content`, events as a one-off
export, images to the CDN.

Copied to `DataTalksClub/content`, then push-synchronised from there:

| Legacy folder | Files at HEAD | Becomes | State |
| --- | --- | --- | --- |
| `_posts` | 55 | articles | **already in `content`** as `articles/*.md` |
| `_podcast` | 209 | podcast episodes | **already in `content`** |
| `_books` | 100 | books | **already in `content`** as `books/*.yaml` |
| `_people` | 443 | people | **not moved. This is the work** |
| `_data/faqs` | 10 | article FAQ pairs | **moving to article frontmatter `faq:`, then push-synced with the article** |
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
This is **step 7**, with its own checkpoint; the summary here is so the fate is
visible from the content section too.

- `images/` → the `dtc-website-media` bucket in `eu-west-1`. **Measured**: 1,253
  objects, 154,115,635 bytes today. The `public-projection/` prefix is removed and
  `images/` and `site-assets/` become siblings at the root; 256 objects are deleted
  along the way. See
  [#301](https://github.com/DataTalksClub/website/issues/301).
- **No assets in the repository, not just no content images.** Beyond the content
  media there are **18** design assets in `core/static/core/` that also move —
  8 homepage illustrations, 4 sponsor logos, 6 testimonial portraits — plus 14
  orphaned mediakit files to delete. §11 B12, B13.
- **This repository keeps no *content* images already.** The remaining content work
  is in `DataTalksClub/content`, which tracks **815**. §11 B11.
- **We rename on ingest.** The CDN key is assigned by us, never inherited from an
  upstream filename. Step 7 says why, and the homepage illustrations are the
  clearest example.
- **Consequence:** moving an article to the content repository while its images
  move to the CDN means something has to rewrite the image references. Today that
  job is done at build time inside `scripts/build_public_projection.py`
  (`_article_blocks`, `_copy_media`). There is **no equivalent on the database
  ingest path**. §11 B3 owns it; step 7 says why it is easy to miss.

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

### 3.2 Which importers bootstrap, and why CMP runs last

An importer either **bootstraps** — it can populate a database with no prior rows
of its own domain — or it **reconciles**, matching upstream rows against rows that
are already there. Running a reconciler first is not an error; it is a *silent
no-op*, which is the more dangerous failure because it reports success having
written nothing.

`scripts/prod/__init__.py` records which is which in `BOOTSTRAPPING_ENTRY_POINTS`,
and `scripts/tests/test_prod_imports.py` checks the declaration against each
module's `BOOTSTRAPS_EMPTY_DATABASE`. Today four bootstrap —
`import_legacy_zoomcamp`, `import_cmp_content`, `import_testimonials` and
`sync_content` — and only `import_events` reconciles.

**Measured**: against a database that had only just been migrated, importing
`mlops-zoomcamp-2022` created the `mlops-zoomcamp` family, the cohort, 6 homeworks,
2 projects, 569 users and 569 enrollments. `Cohort.save()` resolves a cohort's
family from its slug through `canonical_family_slug`, so that importer needs no
catalogue at all.

**`import_cmp_content` now bootstraps too, and that closed a real gap.** It used to
refuse to mint a course family — it would create a cohort named in the reviewed
`COHORT_FAMILY_IDENTITIES` mapping only if the family row already existed — so
against an empty database it wrote nothing and reported every cohort under
`skipped_not_in_local_catalogue`. It now creates the family as well, taking the
title from the reviewed `COURSE_FAMILY_TITLES` catalogue so the fact stays reviewed
rather than derived from a source value.

That matters because of `sma-zoomcamp`, which had no other way in:

| Family | Created by |
| --- | --- |
| `de-zoomcamp`, `ml-zoomcamp`, `mlops-zoomcamp` | `import_legacy_zoomcamp` |
| `ai-dev-tools`, `llm-zoomcamp`, `ml-zoomcamp` | `scripts/prod/sync_course_repositories.py` (was `pull_course_repositories`) |
| `sma-zoomcamp` | **`import_cmp_content`** — nothing else has it |

`sma-zoomcamp` has no course repository (no `course.yaml`, so neither transport can
ingest it) and no pre-2024 edition. It was previously supplied locally by
`manage.py seed_local_courses`, which **refuses to run outside local SQLite**, so
on production nothing would have created it and the **1,081 enrollments** CMP holds
against `sma-zoomcamp-2024`, `-2025` and `-2026` would have had nowhere to land.

**Owner ruling: `sma-zoomcamp` comes from CMP.** It now does, directly, with no
intermediate step. An earlier revision of this plan specified a separate
migration-only structural seed to write the six reviewed families before anything
else ran; **that step is gone and is not needed** — the reconciler minting its own
reviewed families is a smaller change in a better place. Worth keeping the
diagnosis, because the obvious one is wrong:

- It is **not** one of the five owner-skipped cohorts. `SKIPPED_COHORTS` is
  `ai-bootcamp-2025`, `ai-hero-2025`, `ai-hero-2026`, `ai-buildcamp-2`,
  `ai-buildcamp-3`. **No skip has to be lifted.** `sma-zoomcamp-2026` was on that
  list and was already removed, with the reason recorded in the service: it is
  visible and active in CMP and its family is already reviewed.
- All three editions are already in `COHORT_FAMILY_IDENTITIES`.
- The **only** thing missing was the family row, and that is now minted.

**CMP still runs last**, and for the reason in §3.1 rather than for bootstrapping:
it reconciles homework against what the repositories wrote. `COURSE_CATALOGUE_ORDER`
in `scripts/prod/__init__.py` states the order as data —
`import_legacy_zoomcamp`, `sync_course_repositories`, `import_cmp_content` — so it
is checkable rather than remembered.

### 3.3 The consequence for ordering

Legacy history first, because nothing else has the pre-2024 editions; then the
repositories, which own module and unit curricula; then CMP, which reconciles
against both. Users still come from CMP — just at step 4 rather than step 0, and
nothing before then needs them.

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

**Schema only. `migrate` now produces no business rows at all.** That is a change,
and it moves work into later steps rather than removing it: the event identity
manifest and the homepage testimonials used to arrive as data-bearing migrations
and are now explicit imports — step 5 and step 8. If you have run this migration
before and remember 421 events appearing for free, they do not any more.

> **Precondition: every existing local database must be rebuilt.** The migration
> set has been collapsed from 93 files to 10 — one per app, except `core`, which
> needs the standard two-part split for the `core ↔ management_auth ↔
> AUTH_USER_MODEL` circular foreign key. The old chain was deleted and regenerated
> rather than squashed with `replaces`, because `replaces` exists to let a
> partially-migrated database catch up and no such database exists. So
> `django_migrations` in any database built before the collapse no longer matches
> the files on disk, and the only correct move is to delete it and re-run from
> zero. For this plan that is a simplification, not a cost: the production target
> is from-zero by definition, and the rehearsal in §8 is now the same shape as the
> real thing rather than an approximation of it.

Schema equivalence was proved rather than assumed — two databases built from the
old and new chains and compared structurally across 100 tables (columns, types,
nullability, defaults, primary keys, indexes, foreign keys with `on_delete`, named
constraints and CHECK expressions), with zero differences.

> **This document names no migration by number.** The owner has ruled that nothing
> may pin a migration by number, and a runbook that says "migration such-and-such
> seeds the testimonials" is a pin with extra steps — it goes stale silently and sends the
> next reader looking for a file that is not there. Where a migration matters here,
> it is described by what it does. Where a *behaviour* matters, it is named by the
> code that owns it.

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

### Step 1 — Bootstrap: pre-2024 Zoomcamp history

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

### Step 2 — Course repositories

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
        m_count=Count("modules", distinct=True), u_count=Count("modules__units", distinct=True))
    ok = (got["m_count"], got["u_count"]) == (m, u)
    bad |= not ok
    print(("ok " if ok else "BAD"), slug, got, "expected", (m, u))
total = Cohort.objects.aggregate(m_count=Count("modules", distinct=True),
                                 u_count=Count("modules__units", distinct=True))
print("total", total)
bad |= (total["m_count"], total["u_count"]) != (20, 181)
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

### Step 3 — CMP course content

```
$TARGET uv run --frozen python scripts/prod/import_cmp_content.py \
    --database <target> --source $EXPORT
```

Reconciles homework onto step 2's rows, adds the CMP cohorts whose family already
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
step 4, because after step 4 a submission may be attached to it.

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

### Step 4 — CMP learner data

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
> of this step. Reusing it here would import nothing and report success. Step 4
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

That accounts for every table and every row: 6 content (step 3) + 11 import +
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
    ok = got >= want            # >= : step 1 added pre-2024 rows of its own
    bad |= not ok
    print(("ok " if ok else "BAD"), table, "source=%s target=%s" % (want, got))
sys.exit(1 if bad else 0)
PY
```

`>=` rather than `==` is deliberate: step 1 already put pre-2024 users,
enrollments and submissions in the database. Record the step-1 totals before this
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

### Step 5 — Events

**First, sync new events against the current export — before anything else in
this step runs.** The reviewed identity manifest (`events/event_identity_manifest.json`)
is frozen at the moment it was built; Luma keeps moving. Confirmed today: four
real events dated 2026-09-08 through 2026-09-15 exist on Luma but were in
neither the prepared export available at the time nor the manifest — not a
bug, the normal gap between a periodic export and Luma's live state, but one
this playbook must close explicitly on migration day rather than leave to
chance. Owner's instruction, verbatim: "first import new events, make sure
they are up to date, then start the imports."

```
$TARGET uv run --frozen python scripts/prod/import_events.py \
    --database <target> \
    --luma-source /data/tmp/luma-eventbrite-export/luma-aggregate-v1 \
    --discover-new-events-only
```

Point `--luma-source` at whatever export is current on migration day — the
durable copy today lives at `/data/tmp/luma-eventbrite-export/luma-aggregate-v1/`
(`chmod 700`/`600`, the same protected-export handling as `/data/tmp/rds-export/`
and `/data/tmp/mailchimp-export/`), not the gitignored, worktree-local default
path. `--discover-new-events-only` deliberately does not require the export to
match the pinned checksum in `event-registration-sources.json` — that pin
protects registration *counts* from drift, and this leg writes no count — so it
is safe to run against an export newer than the one the rest of this step's
numbers below were measured against.

This runs the reviewed-manifest import first (replayed, as always), then
`discover_new_luma_event_identities()`: any Luma event with no existing
identity — checked against both this database's own `Event` rows and every
`HistoricalEventMapping`, in any state, so an event already sitting in the
380-event mapping-review backlog under a different source key is never
double-created — gets a real `Event` row via `create_event_identity()`. Title
and canonical path only; this never creates or activates a
`HistoricalEventMapping`, so it cannot manufacture a registration count.
Report shape: `new_event_identities.luma.created_total` and
`.created_events` (each with `title`, `start_at`, `eligible_count`,
`public_id`, `canonical_path`), `.already_tracked_total`, and
`.no_metadata_total` for a zero-registration Luma event this export cannot
even name. `--discover-new-events-only` deliberately stops at identity —
mapping activation is a separate concern and runs later, when the main import
below runs without the flag: it now also calls `activate_unambiguous_mappings()`
automatically (reported under `mapping_auto_activation` in that run's report),
which activates a `review_required` mapping only when exactly one canonical
`Event` shares its provider event's date and normalized title — no fuzzy or
ranked match. Everything else, including every mapping this sync step's new
identities have not yet been reconciled into, stays `review_required` and
renders no registration count, exactly as gated as it is today (§16/17 below).

**Done** when `created_total` accounts for every genuinely new event an
operator expected (cross-check against Luma directly if in doubt) and nothing
unexpected shows up in `no_metadata_events`. Re-run is safe and idempotent —
a second pass against the same export reports everything under
`already_tracked_total` and creates nothing.

Identity manifest first, then the two registration sources. Counts only — **no
attendee row is ever written.**

```
$TARGET make import-events IMPORT_DATABASE=<target>
```

which runs `scripts/prod/import_events.py`. (`data-ingest.md` §11 and §13 say that
script does not exist; it landed on this branch on 2026-09-03 and
`scripts/prepare_local_data.py` already composes it. The reference needs
correcting, not the plan.)

**This step is now load-bearing in a way it was not.** The 421 events and 1,684
aliases used to be inserted by a data-bearing migration, so they appeared as a
side effect of `migrate`. They do not any more: seeding has moved out of
migrations entirely and this import is the only thing that puts them there. Skip
it and the site has no events at all, rather than a subset.

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

**Checkpoint — the public ID allocator is parked above the imported IDs**

This one exists because of a real bug that the old migration-based seeding hid,
and a from-zero production database is precisely the case that would have hit it.

`import_identity_manifest` wrote `public_id` values the allocator never issued and
never advanced `EventPublicIdSequence`. It worked only by accident of ordering:
the singleton row happened to be created *after* the seeding migration ran, so
`max + 1` came out at 422. On a database built from zero — the production case —
the singleton would be created at 1, and the next event creation would raise
`event_public_id_allocator_invalid`. Fixed by `ensure_public_id_sequence()`
(`events/identity.py:290`), which parks the allocator above every ID that already
exists and belongs with the code that writes events rather than in a migration.

```
$TARGET uv run --frozen python manage.py shell -v 0 -c '
import sys
from django.db.models import Max
from events.models import Event, EventPublicIdSequence
highest = Event.objects.aggregate(v=Max("public_id"))["v"] or 0
row = EventPublicIdSequence.objects.filter(pk=1).first()
nxt = row.next_public_id if row else None
print("events", Event.objects.count(), "highest public_id", highest,
      "allocator next", nxt)
sys.exit(0 if row is not None and nxt > highest else 1)'
```

**Measured**, from a bare `migrate` through the import on a scratch database:

```
after migrate:   events 0    highest public_id 0    allocator next None   -> exit 1
after import:    events 421  highest public_id 421  allocator next 422    -> exit 0
```

The first line is the new normal and is worth seeing once: `migrate` alone leaves
no events and **no allocator row at all**. The second is a pass.

**An allocator sitting at or below the highest imported ID is a stop**, not
something to correct by hand: it means the import ran without the fix and the next
event created on the site will fail.

**A repair capability was lost here, and it is worth knowing before it is needed.**
A migration used to repair PostgreSQL collation divergence in `public_id`; it no
longer exists in any form. The reasoning is sound for the case at hand — on a
from-zero database the IDs come from the manifest by construction, and the
importer still refuses renumbering — but **production is PostgreSQL and the
divergence was a real observed problem there**, so the mitigation is now "it
cannot arise" rather than "it is repaired if it does".

If divergence ever appears, the symptoms are ordering-dependent: events resolving
to the wrong route, or the allocator's `max()` disagreeing with what a sorted
listing shows. Do not hand-edit `public_id` values — they are public URLs. The
recovery is to re-run the identity import, which is authoritative and refuses
renumbering, and then re-run the checkpoint above. Record the occurrence; a second
one would justify rebuilding the repair rather than relying on construction.

**Failure and recovery.** **Recoverable by re-run.** The identity import is
atomic and reports `replayed`. Registration aggregates are staged revisions; a
failed parse writes nothing, because an unsupported schema fingerprint refuses to
parse rows at all.

**Duration.** Minutes.

### Step 6 — Content

Today this step is: rebuild the committed projection. It is **not** a database
import, and pretending otherwise is the biggest single misreading available in
this plan. The *objects* the projection points at are step 7.

```
uv run --frozen python scripts/build_public_projection.py \
    --content-root <DataTalksClub/content @ pin> \
    --legacy-main-root <datatalksclub.github.io @ pin> \
    --wiki-root <DataTalksClub/podwiki @ pin> \
    --output temporary/content/public_projection
```

Expected: wiki 282 · podcasts 203 (201 transcripts) · articles 55 · people 438 ·
books 98 · events 421 · media **index** 1,253. Plus the two hand-reviewed
projections this build does not produce: FAQ (6 courses / 70 sections / 1,401
questions / 99 assets) and docs (106 pages / 39 assets).

`media.json` is an *index*, not the bytes. This step produces it; step 7 makes the
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
make content-update-check CONTENT_UPDATE_FAMILY=all  # committed artifacts vs manifest, one run per family
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

### Step 7 — Media and assets to the CDN

**Owner ruling, three parts.** Images go to the CDN; **we keep no assets in the
repository**; and **we rename them on ingest** — the CDN key is ours to assign, not
one we inherit from whatever the file happens to be called upstream.

The mechanism exists and is landed under
[#301](https://github.com/DataTalksClub/website/issues/301):
`content/media_store.py` provides `local`, `s3` and `memory` backends selected by
`PUBLIC_MEDIA_STORE_BACKEND` (**production runs `s3`**), driven by
`sync_public_media_hydrate.py`, `sync_public_media_publish.py` and `sync_public_media_verify.py`. What is
*not* built is the key assignment and the site-asset half — §11 B12 and B13.

**Destination.** The `dtc-website-media` bucket, `eu-west-1`, already provisioned
and already holding the content media. **Measured 2026-09-03** with
`aws s3api list-objects-v2`, not inferred:

```
objects: 1253        bytes: 154,115,635
public-projection/images/authors/   438      2,108,878 B    2.0 MiB
public-projection/images/books/     196      9,050,873 B    8.6 MiB
public-projection/images/podcast/   212      7,390,915 B    7.0 MiB
public-projection/images/posts/     407    135,564,969 B  129.3 MiB
```

That is the state **before** the flattening and the two deletions below; it matches
`media.json` exactly, object for object and byte for byte. The target state is:

| Prefix | Objects | Bytes | Change |
| --- | ---: | ---: | --- |
| `images/authors/` | 438 | 2,108,878 | — |
| `images/books/` | 196 | 9,050,873 | — |
| `images/posts/` | **357** | **133,108,882** | −50 social cards, −2,456,087 |
| `images/podcast/badges/` | **6** | **112,316** | badges kept |
| `images/podcast/` covers | **0** | **0** | −206, −7,278,599 |
| `site-assets/` | **18** | ~2,083,635 | new |
| **Total** | **1,015** | **146,464,584** | −238 objects, −7,651,051 bytes |

The content half falls from 1,253 to 997 objects; `site-assets/` adds 18.

**The AWS gate is open from the workstation**, so every claim in this step is
measurable rather than assumed — `head-bucket` succeeds and a full recursive
listing completes. Measure rather than infer, and re-measure on the day.

#### Prerequisites — settle these before the first upload

Not prose to be found later. Each is a configuration decision that is cheap now and
expensive after 1,015 objects are in place.

| # | Prerequisite | Why it cannot wait |
| --- | --- | --- |
| 1 | **CloudFront cache key must include the query string** | The whole cache-busting scheme is `?v=<digest>`. A distribution that strips query strings serves stale bytes forever and the version does nothing. Verify before publishing, not after a stale image is reported |
| 2 | **Decide S3 object versioning, explicitly** | Stable key + regeneration = **overwrite**. With versioning off there is no rollback: the previous bytes are gone and recovery means re-deriving from source. Either turn it on or write down that restore-from-source is the accepted recovery |
| 3 | **Scope `existing_keys()` to `RECORD_KEY_PREFIX`** | With the prefix flattened, `verify` sees `site-assets/*` as `extra` — 18 permanent false failures |
| 4 | **`PUBLIC_MEDIA_S3_PREFIX` set to empty** | The flattening is this setting plus the object move. Miss it and keys are written back under `public-projection/` |

Prerequisites 1 and 2 compound: **a stable key, an overwrite, and versioning off
together mean no rollback of any kind.** That combination should be a decision
somebody made, not a default nobody looked at.

#### What this step places, deletes and keeps

| Group | Objects | Bytes | Fate |
| --- | ---: | ---: | --- |
| `images/authors/` | 438 | 2,108,878 | **keep** — people faces |
| `images/books/` | 196 | 9,050,873 | **keep** — book covers |
| `images/posts/` illustrations | 357 | 133,108,882 | **keep** — real artwork |
| `images/podcast/badges/` | 6 | 112,316 | **keep** — platform badges, not covers |
| homepage illustrations | 8 | ~2,012,078 | **place** — site asset, from `core/static/` |
| sponsor logos | 4 | 26,839 | **place** — site asset |
| testimonial portraits | 6 | 44,718 | **place** — site asset |
| **Bucket after this step** | **1,015** | **146,464,584** | |
| `images/posts/*/cover.jpg` | **50** | **2,456,087** | **delete** — generated social cards, §11 B15 |
| `images/podcast/` covers | **206** | **7,278,599** | **delete** — being regenerated, §11 B14 |
| `core/static/core/mediakit/` | 14 | 7,803,244 | **delete** — orphaned, §11 B12 |

**Measured** on 2026-09-03 against both the local tree and the bucket. What this
step *uploads* is only the 18 site assets — the content media is already published.
Everything else is a deletion or a verification.

#### The two deletions, and why records go first

Both deletions are the **regenerated path** from further down, and both hinge on
the same ordering. `verify_media` compares the store against the *record*, so:

> **Rewrite the records, then delete the objects.** Delete first and every page
> holding a reference renders a broken image, and `verify` reports `missing` for
> rows that are supposed to be gone — noise that hides real failures.

**Podcast covers — 206 objects, 7,278,599 bytes.** Owner ruling: delete them, they
are being regenerated. **Measured**: 212 podcast records, of which **6 are
`images/podcast/badges/`** — Apple, Spotify, Google, Anchor and friends. Those are
platform badges, nothing is regenerating them, and they are **kept**. The 206
covers go.

*What a podcast page renders in the interval*, which is the part worth getting
right: `templates/public/_episode_artwork.html` already guards on
`episode.media_available` and falls through to
`<span class="player-art-missing mono-note">Artwork unavailable.</span>`. So an
episode with its record rewritten to `media_available: false` renders **a deliberate
note in the player frame**, not a broken image — the degradation was designed for.
That only holds if the record is rewritten first; delete the object while the record
still says `media_available: true` and the same page renders a broken `<img>`.

**Post social cards — 50 objects, 2,456,087 bytes.** Owner ruling: keep the
illustrations, remove the social card. The card is the generated
white-background image carrying title, subtitle and author headshot.

> **Correction to the rule as stated.** "Stem exactly `cover`" is one file too
> broad. **Measured**: 53 records have stem `cover` — **50 `.jpg` and 3 `.png`** —
> and the three `.png` are **artwork, not cards**. Three independent signals agree:
> they are the only cover records **embedded inside article bodies**; their median
> size is **2,149,542 bytes against 48,740** for the `.jpg` cards (whose whole range
> is 30,811–78,586, exactly the profile of a flat generated card); and they sit in
> posts whose bodies reference them inline. **The rule is stem exactly `cover` *and*
> extension `.jpg`.** `cover-start.png` was already excluded by the `cover-` prefix
> rule and stays excluded.

*Does anything claim these images?* **Yes, and this is the part that must not be
skipped.** Of the 50 cards, **44 are an article's `image_path`** and 6 are
referenced by nothing. `content/public_views.py:708` turns that field into the
page's Open Graph image:

```python
"og_image_url": _canonical(article["image_path"]) if article["image_path"] else "",
```

which `templates/public/article_detail.html:28,31` renders as `og:image` and
`twitter:image`. Delete the objects without touching the records and **44 of 55
articles advertise a social preview image that 404s** — invisible on the page
itself, and visible everywhere the link is shared.

The fix falls out of the guard that is already there. `_canonical(...) if
... else ""` and the template's `{% if og_image_url %}` mean an article whose
`image_path` is emptied emits **no `og:image` tag at all**, which is correct rather
than broken. So: **clear `image_path` on those 44 records, rebuild the projection,
then delete the objects.** Nothing ever claims an image that does not exist.

The 3 `.png` keep both their `image_path` and their inline body references, because
they are staying.

**The mediakit assets are dead.** `/mediakit/` is a 301 to `DataTalksClub/mediakit`,
which hosts its own copies. **Measured**: all 14 files under
`core/static/core/mediakit/` are referenced by nothing — no template, no view, no
stylesheet. The only `mediakit` references in the tree are the redirect in
`website/urls.py` and `core/tests/test_mediakit.py`. 7.4 MiB out of the container
image and out of the staticfiles manifest.

#### Two kinds of object, two prefixes

The split is structural, not filing. **Content media** belongs to records, arrives
through ingest, and changes when content changes. **Site assets** belong to the
design, are deployed rather than ingested, and change when the site changes.

**Owner ruling: `public-projection/` goes.** It named the artifact that produced
the objects rather than the objects themselves, and it bought nothing — there was
never a second thing at the top level for it to disambiguate from. The bucket gets
**two prefixes at the root**, siblings:

```
dtc-website-media/                        eu-west-1
  images/                                 content media
    authors/                              438
    posts/                                407 → 357 after the social cards go
    books/                                196
    podcast/                              212 → 6 (badges only) after the covers go
      badges/                             6     platform badges — KEPT
  site-assets/                            site assets, by surface
    home/                                 8     homepage illustrations
    courses/                              0     destination for #311; empty today
    sponsors/                             4     logos
    testimonials/                         6     portraits
```

**Why `site-assets/`.** `static/` would collide with Django's staticfiles, which is
precisely the pipeline these are leaving, and would confuse the next reader.
`assets/` is vague. `design/` is too narrow — a sponsor logo is not design. It says
what the objects are, and it reads correctly beside `images/`. The reasoning for a
sibling rather than a child is unchanged by the flattening; only what it is a
sibling *of* has changed.

**The flattening is a setting change, not a code change.**
`PUBLIC_MEDIA_S3_PREFIX` is an operational setting
(`core/operational_settings.py:441`, key `public_media.s3_prefix`, default
`public-projection`), and `object_key()` already returns the bare record key when
the prefix is empty:

```python
return f"{normalized}/{key}" if normalized else key
```

So the prefix becomes `""`, the objects move, and no key-construction code changes.
Record keys already begin with `images/` (`RECORD_KEY_PREFIX`), so the content half
lands at exactly the tree above.

> **One consequence to handle rather than discover.** With the prefix empty,
> `existing_keys()` lists the **whole bucket**, so `verify_media` will count every
> `site-assets/*` object as `extra` — 18 permanent false failures the moment site
> assets land. Either scope the listing to `RECORD_KEY_PREFIX`, or have `verify`
> ignore keys outside it. The checkpoint below filters on the reporting side so it
> is honest today, but the tooling is the right place to fix it.

Flattening the bucket and moving 1,253 objects is being executed separately,
alongside the code side. This document describes the destination and the checks;
it does not perform the move.

**`site-assets/courses/` is empty today and that is expected.** Every file in
`core/static/core/illustrations/` is `home-*`; there are no course illustrations in
the tree. It is a prefix that **artwork the owner supplies** will land in, not a
migration of existing files. Per the owner's ruling, producing that artwork is not
our work — [#311](https://github.com/DataTalksClub/website/issues/311) is therefore
about *receiving* assets in the new style rather than generating them, and
`_docs/design/illustration-assets.md` describes the style they arrive in.

**Who publishes each folder, and when — the answers differ, which is why the
subdivision is worth having:**

| Prefix | Trigger | Publisher |
| --- | --- | --- |
| `content/*` | a content change upstream | the content sync (§11 B3), as part of ingest |
| `site-assets/home/` | a site design change | the deployment pipeline, at release |
| `site-assets/courses/` | a course is added or restyled | **the owner supplies the artwork** (#311); we ingest and publish it |
| `site-assets/sponsors/` | a sponsor is added or changes | neither — Studio, when the sponsor record changes (§11 B13) |
| `site-assets/testimonials/` | a testimonial is added | Studio, with the row (step 8) |

Only `site-assets/home/` is genuinely deployment-shaped. Publishing site assets at deploy
time is **a new step in the deployment pipeline, not in this migration** — this
step performs the one-time upload; B13 owns making it repeatable. Until it is,
a changed sponsor logo is a manual upload, and that must be written down where the
person changing it will look, not only here.

**Why sponsor logos and testimonial portraits get their own folders rather than
living under `home/`.** Because neither is homepage-only, which is checkable and
was checked:

- Sponsor logos render on **two** surfaces — `templates/core/home.html:1387` and
  `templates/core/sponsors.html:64`, from `core/views.py:121` and `:133`.
- Testimonial portraits render from `templates/core/home.html:991`, but
  `TestimonialPlacement` has **two** choices, `HOMEPAGE` and `COURSE`, so a course
  testimonial renders on a course page by design.

**Where testimonial portraits belong, and why site assets.** They back a database
model, which argues content media. They are six fixed editorial images that shipped
with the design and change when the design changes, which argues site assets. They
go under `site-assets/testimonials/` — because the deciding question is *what triggers a
change*, and it is an editor adding a testimonial through Studio, never a content
sync. A `Testimonial` row referencing a `site-assets/` object is normal: the row owns
*which* portrait, the CDN owns the bytes. **That is already how the model works**:
`Testimonial.portrait_asset_key` stores `testimonials/nevenka-lukic.jpg` — no
`core/`, no `site-assets/`, no bucket, no scheme — with a `RegexValidator` refusing
anything absolute, escaping or scheme-prefixed. See step 8.

#### The key is ours: `<surface>/<name>-<theme>.<ext>`

Owner ruling, and the naming is explicit rather than implied:

```
site-assets/home/home-hero-light.webp     site-assets/home/home-hero-dark.webp
site-assets/home/home-step-1-light.webp   site-assets/home/home-step-1-dark.webp
site-assets/home/home-step-1-light.webp   site-assets/home/home-step-1-dark.webp
site-assets/home/home-step-2-light.webp   site-assets/home/home-step-2-dark.webp
```

Two renames in one, and both are only possible because the key is ours:
`home-hero.webp` gains an explicit `-light`, and **`home-stuck` becomes
`home-step-1`**.

Today the tree holds `home-hero.webp` beside `home-hero-dark.webp`: the dark file
announces its theme and the light one is inferred from the *absence* of a suffix.
That asymmetry is invisible in code and obvious in a listing, and it is the
clearest illustration in this document of why assigning keys beats inheriting
them — **the CDN key can be the name the scheme wants instead of the name the file
happens to have.** No file is renamed on disk to achieve it; the rename happens at
ingest.

**State it as a rule, not a list**, so `site-assets/courses/` and every future surface
follows without another decision:

> `site-assets/<surface>/<name>-<theme>.<ext>`, where `<theme>` is `light` or `dark`.
> **The theme segment is mandatory for any asset a template selects by theme, and
> absent for a theme-neutral one.** A sponsor logo is `site-assets/sponsors/dlthub.png`; a
> homepage illustration is `site-assets/home/home-hero-light.webp`. If a themed asset ever
> ships without a dark variant it is still `-light`, and the template falls back to
> the light URL — that is a rendering decision, not a naming one, so the key scheme
> never has to be guessed at.

The pairing becomes a property of the key rather than an accident of it: `-light`
and `-dark` are the same name in two themes, which is exactly what
`_home_illustration.html` selects between.

**`home-stuck` is the step 1 drawing, and the rename fixes a real inconsistency
rather than inventing one.** The partial's own comment settles what it is: *"The
climb cards use the issue's reworked step 1 drawing and its step 2 and step 3
drawings."* The three climb cards are the `stuck`, `learning` and `shipping`
variants, mapping to `home-stuck`, `home-step-1` and `home-step-2` — one named by
role, two by position, which is exactly why a listing looked like it was missing
its first step. Nothing was missing; the naming was inconsistent. Assigning keys is
the moment that is free to fix, so `home-stuck` is published as `home-step-1`.

The template's `variant` names (`hero`, `stuck`, `learning`, `shipping`) are a
separate vocabulary and do not have to change with the asset names — but leaving
`variant="stuck"` selecting `home-step-1-*` re-creates the same mismatch one layer
up. Rename both, in the same edit as the `{% static %}` removal.

**Content-media keys** follow the same principle, assigned at ingest from the
record's **normalised** identity rather than its upstream filename: lowercased,
trimmed, spaces to hyphens, one extension. That is not theoretical tidying — six
records today carry names that would otherwise be permanent CDN keys *and*
permanent public URLs:

| Today's key | Problem |
| --- | --- |
| `images/authors/ aashishnair.jpg` | leading space |
| `images/podcast/production-ml-...-hybrid search.jpg` | inner space |
| `images/podcast/hiring-...-skills.md.jpg` | `.md.jpg`, from a Markdown filename |
| `images/posts/2023-11-18-data-engineering-zoomcamp/Image7.jpg` | uppercase |

**What changes when the thing the key derives from changes:**

| Change | Effect |
| --- | --- |
| upstream filename changes | **nothing** — the key does not derive from it. This is what stranded a podcast cover when an episode was renumbered from e06 to e07 |
| file moves between upstream directories | nothing |
| bytes change (regeneration, re-encode) | same key, new `?v=` — see caching below |
| identical bytes re-ingested | same key, same version; publish reports `skipped` |
| the record's logical identity changes | **the key changes** — a re-key, and the old object becomes `extra` |

That last row is the residual case and it is named rather than hidden: full
immunity would need a persisted assignment ledger that survives rebuilds, which is
real state and a real cost. §12 decision 8.

#### Caching: a stable key needs an explicit version

This is the one thing the move *loses*, and it must not be discovered in
production. `CompressedManifestStaticFilesStorage` gives hashed filenames, so a
changed asset gets a new URL for free and can be cached for a year. **A stable CDN
key has no such property.** Replace `home-hero-light.webp` in place and every
browser and edge cache that holds the old bytes keeps serving them until their TTL
expires.

**The rule:** the object key is stable and readable; **the emitted URL carries
`?v=<sha256[:8]>` of the bytes.** The mapping record already holds the checksum —
`provenance.checksum` for content media, the site-asset manifest for site assets —
so the version is derived, never stored twice and never hand-maintained.

Two consequences to configure rather than assume:

- **The CDN cache key must include the query string.** On CloudFront that is not
  the default; a distribution that strips query strings will serve stale bytes and
  the `?v=` will do nothing at all. Verify it as part of B13.
- **A stable key means an overwrite**, so the previous bytes are gone. That is the
  deliberate trade for readable keys: rollback is restoring from source, not
  pointing at an older object. Bucket versioning is the cheap mitigation if the
  owner wants one — §12 decision 8.

#### Two paths: carried across, and newly supplied

The constraint that any move must preserve byte identity applies to objects that
are **moved**. Objects that arrive **new** — the owner's replacement artwork — have
new bytes and new checksums by definition, and the plan must not imply the old
digests survive them.

**The shape has changed since the deletions landed.** The new artwork is no longer
part of the carried migration at all: the old covers and cards are deleted at
cutover, and the replacements arrive afterwards as a **fresh ingest**. So the
bucket is *deliberately short* those objects when the site goes live, and step 7's
checkpoint asserts the short state rather than flagging it. Nothing is waiting on
the artwork; the artwork is waiting on the owner.

| | Carried across | Newly supplied |
| --- | --- | --- |
| Bytes | unchanged | new |
| `provenance.checksum` | the upstream digest, matched | **written fresh** from the new bytes |
| `provenance` origin | upstream repository + revision | who supplied it and when — the owner for the new artwork |
| Verify behaviour | store checksum equals record checksum | same — *because the record is built from the file that exists* |
| At cutover | `authors/` 438, `posts/` 357, `books/` 196, `podcast/badges/` 6, the 18 site assets | **nothing yet** — arrives after handover |

**How a re-run tells them apart: it does not have to.** `verify_media` compares
the store against `store.expected_checksum(record)`, which reads the record. So the
invariant is:

> **Regeneration is a projection change first and an upload second.** The record is
> the source of truth for what the bytes should be. Rewrite the record, then
> publish.

Upload regenerated bytes *without* rewriting the record and `verify` reports
`mismatched` — and that is correct behaviour, not a false alarm to suppress. It is
the check working.

**The content sniffing applies to regenerated images too.** `_copy_media`
content-sniffs every file — JPEG, PNG and GIF magic — and runs an SVG sanitizer
that rejects `<script>`, `<style>`, event handlers and remote `href`/`src`/`url()`.
That gate is on the **ingest boundary, not on the origin**: "we generated it" is
not a reason to skip it, because a generator is software that can be wrong, and an
image pipeline emitting an SVG with a script element is exactly the case the
sanitizer exists for. **The same holds for artwork a person supplies** — a design
tool exports whatever it exports — so owner-supplied files get no exemption
either. Note that all five SVGs in the projection are podcast covers,
so the carried set contains none — the sanitizer matters here purely for what
comes *next*, which is the easiest kind of protection to drop by accident.

#### Static assets are a third pipeline, and that is the hard part

The 18 site assets are not media objects today. They are **static assets**, and
they travel a completely different road: `collectstatic`,
`whitenoise.storage.CompressedManifestStaticFilesStorage`
(`website/settings/base.py:237`), hashed filenames, and templates resolving them
through `{% static %}`. This is not "copy 18 files to a bucket".

**The reference surface, measured — 10 call sites in 3 files:**

| Asset group | Where referenced | Count |
| --- | --- | ---: |
| homepage illustrations | `templates/core/_home_illustration.html:20-30` | 8 `{% static %}` calls |
| testimonial portraits | `templates/core/home.html:991`, `{{ story.portrait_url }}` | 1, resolved by the model — **no longer a raw manifest lookup** |
| sponsor logos | `core/sponsor_history.py:92`, `static(f"core/sponsors/{...}")` | 1, from a hardcoded tuple |

**The rename and the `{% static %}` removal are the same edit**, not two. Each of
the eight illustration references moves to a CDN URL under its new `-light`/`-dark`
name in one change; doing the rename first and the URL swap later means a window
where the template asks the manifest for a file that is no longer there.

**A stale reference under manifest storage is a hard failure, not a soft 404.**
Once a file leaves `core/static/`, `collectstatic` no longer sees it and the
manifest no longer carries it — which is the point — but `{% static 'core/…' %}`
for a missing entry raises `ValueError: Missing staticfiles manifest entry`, i.e.
a **500 on the page**, not a broken image. This project already knows that failure.

The testimonial case used to be the sharpest — `{% static story.photo_static_path %}`
applied the manifest lookup to a **database value**, so one bad row took the
homepage down rather than losing one portrait. **That is now closed.** The field is
`portrait_asset_key`, the template reads a `portrait_url` property that resolves
through `staticfiles_storage.url()` and **returns `""` on any failure**, and the
template falls through to the decorative-avatar branch — the same designed empty
state a testimonial with no portrait already uses. It is verified against the real
`whitenoise` class over a temporary manifest rather than a mock, and a full
`client.get("/")` with one row's manifest entry missing returns **200 with the other
five portraits intact**.

The general rule stands and is what the other groups still need: **never apply a
manifest lookup to a value that can change after the manifest was built.** An
audit found one other non-literal site, `core/sponsor_history.py:92`
(`static(f"core/sponsors/{supporter['logo']}")`), and it is **not** the same defect
— every value comes from a module constant, so `collectstatic` checks it at build
time. It matters here only because `core/sponsors/` relocates in this same move, so
B13 has to carry it.

**Do they become `ContentAsset` rows?** No, and B13 should resist it.
`ContentAsset` is part of the content pipeline that nothing writes and nothing
reads (§11 B4). Site assets need a URL resolver and a small manifest — name,
surface, theme, checksum — not a content model. Testimonial portraits already have
an owning record; sponsor logos and homepage illustrations have none and do not
need one invented.

#### Ordering: media before content is public

**After the projection build, before the site is public.** The build (step 6)
produces `media.json`; the objects it names must exist before anyone looks at a
page.

The dependency is one-directional and unforgiving: Django resolves `/images/<path>`
through `media.json` and then reads the object. **If content arrives and its media
has not, every page renders and every image is broken** — a missing object is a
failed read, not a fallback. The text is fine, which is what makes it easy to ship
by accident. Site assets are worse: under manifest storage a missing one is a 500.

Otherwise independent: nothing in steps 0-5 reads media, and step 8 needs only the
testimonial portraits this step places. If step 7 must slip, delay going public or
publish media first and content second — never content first.

```
# Materialise the tree. NOT --source github; see above.
$TARGET uv run --frozen python scripts/prod/sync_public_media_hydrate.py \
    --source checkout --checkout DataTalksClub/content=<path> \
                      --checkout DataTalksClub/datatalksclub.github.io=<path>

# Upload. Incremental: an object whose recorded checksum already matches is skipped.
PUBLIC_MEDIA_STORE_BACKEND=s3 $TARGET \
    uv run --frozen python scripts/prod/sync_public_media_publish.py

PUBLIC_MEDIA_STORE_BACKEND=s3 $TARGET \
    uv run --frozen python scripts/prod/sync_public_media_verify.py
```

#### Checkpoint

`sync_public_media_verify.py` is the only true bidirectional set-diff in the codebase —
`missing` / `unreadable` / `mismatched` from the record side and `extra` from the
store side, non-zero exit. It is the shape every other drift check should copy.

```
$TARGET uv run --frozen python manage.py shell -v 0 <<'PY'
import subprocess, sys
from content.media_store import media_records, media_store
from content.media_tooling import verify_media

# Expected content media after the two deletions: 997 objects.
# posts/ drops the 50 generated social cards (stem "cover", .jpg only --
# cover.png is artwork and stays); podcast/ keeps only badges/.
EXPECTED = {"authors": 438, "books": 196, "posts": 357, "podcast": 6}

def group(key):
    return key.split("/")[1]

records = tuple(media_records())
store = media_store()
report = verify_media(store=store, records=records)
fail = []

counts = {}
for record in records:
    counts[group(record["record_key"])] = counts.get(group(record["record_key"]), 0) + 1
print("records by group:", counts)
if counts != EXPECTED:
    fail.append(f"group counts {counts} != {EXPECTED}")

print("total", report.total, "matched", report.matched,
      "missing", len(report.missing), "mismatched", len(report.mismatched),
      "unreadable", len(report.unreadable))
for name in ("missing", "mismatched", "unreadable"):
    if getattr(report, name):
        print(f"  {name}:", getattr(report, name)[:5])
        fail.append(f"{name}={len(getattr(report, name))}")

size = 0
for record in records:
    try:
        size += store.stat(record).size
    except Exception:
        pass
print("bytes", size, f"({size / 1024 / 1024:.1f} MiB)")
if size != 144380949:
    print("  note: byte total moved; re-baseline deliberately, do not just edit this")

# No record may name a social card or a podcast cover any more.
cards = [r["record_key"] for r in records
         if r["record_key"].startswith("images/posts/")
         and r["record_key"].rsplit("/", 1)[-1] == "cover.jpg"]
covers = [r["record_key"] for r in records
          if r["record_key"].startswith("images/podcast/")
          and not r["record_key"].startswith("images/podcast/badges/")]
print("social cards still in records:", len(cards),
      "podcast covers still in records:", len(covers))
if cards or covers:
    fail.append(f"deleted objects still referenced: {len(cards)} cards, {len(covers)} covers")

# Three kinds of `extra`, and only the third is a failure.
#   site-assets/  -- shares the bucket root now that PUBLIC_MEDIA_S3_PREFIX is
#                    empty; goes away when verify_media is scoped to
#                    RECORD_KEY_PREFIX (prerequisite 3).
#   swept         -- objects the deletions removed from the index but not yet
#                    from the store. Expected between the rebuild and the sweep;
#                    must be zero once the sweep has run.
#   unexplained   -- anything else. Always a stop.
def swept(key):
    return (key.startswith("images/podcast/")
            and not key.startswith("images/podcast/badges/")) or (
        key.startswith("images/posts/") and key.rsplit("/", 1)[-1] == "cover.jpg")

site = [k for k in report.extra if k.startswith("site-assets/")]
pending = [k for k in report.extra if swept(k)]
other = [k for k in report.extra
         if not k.startswith("site-assets/") and not swept(k)]
print("extra:", len(report.extra), "site-assets", len(site),
      "awaiting sweep", len(pending), "unexplained", len(other))
if other:
    print("  unexplained:", other[:5])
    fail.append(f"unexplained extra={len(other)}")
if pending:
    print("  NOTE: the store still holds deleted objects. Sweep before cutover.")

tracked = subprocess.run(["git", "ls-files",
                          "temporary/content/public_projection/media", "core/static/core"],
                         capture_output=True, text=True).stdout.split()
# core/static/core/vendor holds FontAwesome webfonts, three of which are .svg.
# They are font files, not assets, and they stay with the CSS that names them.
images = [p for p in tracked
          if p.rsplit(".", 1)[-1].lower() in ("jpg", "jpeg", "png", "gif", "svg", "webp")
          and "/vendor/" not in p]
print("image files tracked in git:", len(images))
if images:
    print("  e.g.", sorted(images)[:5])
    fail.append(f"{len(images)} image files tracked in git")

print("FAIL" if fail else "OK", fail)
sys.exit(1 if fail else 0)
PY
```

And the bucket itself, which is measurable now that the AWS gate is open:

```
aws s3api list-objects-v2 --bucket dtc-website-media \
  --output json --query 'Contents[].{K:Key,S:Size}' \
| python3 -c '
import collections, json, sys
d = json.load(sys.stdin)
top = collections.Counter(x["K"].split("/")[0] for x in d)
grp = collections.Counter(); size = collections.Counter()
for x in d:
    parts = x["K"].split("/")
    g = "/".join(parts[:3]) if len(parts) > 3 else "/".join(parts[:-1])
    grp[g] += 1; size[g] += x["S"]
print("objects", len(d), "bytes", sum(x["S"] for x in d))
print("top-level:", dict(top))
for g in sorted(grp):
    print(f"  {g:44} {grp[g]:>5}  {size[g]:>12,} B")
'
```

Expected when this step is complete: **two prefixes at the root**, `images` at 997
objects / 144,380,949 bytes and `site-assets` at 18 — and **no `public-projection`
prefix at all**. Its continued existence is itself a failure.

**Run against today's state**, with the record deletions already applied and the
site-asset move still outstanding. Verbatim:

```
records by group: {'authors': 438, 'books': 196, 'posts': 357, 'podcast': 6}
total 997 matched 997 missing 0 mismatched 0 unreadable 0
bytes 144380949 (137.7 MiB)
social cards still in records: 0 podcast covers still in records: 0
extra: 257 site-assets 0 awaiting sweep 257 unexplained 0
  NOTE: the store still holds deleted objects. Sweep before cutover.
image files tracked in git: 32
FAIL ['32 image files tracked in git']
```

The content half is **done and passing**: 997 records, the exact target byte total,
no record naming a deleted card or cover. Two things remain and both are honest:
the store still holds the 256 deleted objects plus the one stale podcast file
(**257 awaiting sweep** — they disappear on the next hydrate, and the sweep must
happen before cutover), and the **32 site assets are still in git**, which is B12
and B13. Nothing is unexplained, which is the number that matters.

Then the site assets, which the first command does not cover because they are not
media records yet — until B13 lands this is by hand:

- all 18 objects exist under `site-assets/`, at their assigned `-light`/`-dark` names;
- no template contains `{% static 'core/illustrations/`, `core/sponsors/` or a
  a raw manifest lookup on a testimonial's `portrait_asset_key`;
- `core/static/core/mediakit/` is gone;
- the homepage and `/sponsors` render with every image, in **both** themes;
- a `?v=` change on one asset reaches a browser holding the old bytes.

**A re-run uploads only what changed.** `publish_media` compares each object's
stored checksum against the record's and skips a match, so this is a property to
*confirm*, not to build: run `sync_public_media_publish.py` twice and read the report —
`added: 0`, `changed: 0`, `skipped` equal to `total`. If a second run re-uploads
147 MB, something is rewriting checksums and that is a stop.

**Failure and recovery.** **Recoverable by re-run, and safe to re-run**: hydrate
and publish are both idempotent and resumable, and an object already present with
the recorded checksum is skipped. A partial publish leaves a subset, which a re-run
completes. The one rule: **do not declare the step done on a partial publish.** Run
publish to completion, then verify, and treat a non-empty `missing` as a stop.

The site-asset half is *not* as forgiving, because it is a cutover rather than an
upload: between removing a file from `core/static/` and the template pointing at
the CDN, the page raises. Do those two together, per asset group, and keep the
`core/static/` copies until the CDN objects verify.

**Duration.** The 18 site assets are the only upload; the deletions are 256 objects
and the flattening is a move of 997. Seconds to minutes, not the hours a 147 MB
upload would take — the content bytes are already in the bucket.

#### Receiving the owner's artwork

**Owner ruling: the owner produces the new banner and cover artwork — podcast
episodes, articles and the rest. Generating it is not our work.** What is our work
is receiving it, and that is now the deliverable rather than a follow-on.

This section answers one question: *the owner has a folder of finished images, what
happens next?*

**The ingest matches on a declared path, not on a convention.** That is the fact
everything else follows from, and it is worth stating because the tidy assumption
is wrong. Each record's own source file names its image explicitly:

| Record | Source file | Field |
| --- | --- | --- |
| Podcast episode | `podcasts/sNN/eNN.yaml` | `image: images/podcast/sNNeNN-<episode-slug>.jpg` |
| Article | `articles/<year>/<file>.md` front matter | `image: images/posts/<post-directory>/cover.jpg` |

**Measured**: all 55 articles carry an `image:` key; the episode YAML carries the
same. Nothing is inferred from the filename, so there is no naming convention the
ingest enforces — there is a *declaration* that has to agree with a *file*.

**So the rule the owner can follow without reading any code:**

> **Deliver each file at exactly the path its record already declares.** For a
> replacement — which is what all of this is — that path already exists in the
> source, so the filename is not a choice: it is `images/podcast/sNNeNN-<slug>.jpg`
> for an episode and `images/posts/<post-directory>/cover.jpg` for an article. Put
> the file there, commit it to `DataTalksClub/content`, and the record picks it up
> with no source edit at all.

If the owner would rather name files their own way, that is allowed but it is a
second edit: the `image:` field in the episode YAML or the article front matter has
to change to match. **Prefer the first.** A replacement that reuses the declared
path is one change with nothing to keep in step; a rename is two changes that can
disagree, and the disagreement is silent (below).

**This does not conflict with keys being ours.** The delivered filename identifies
*which record* the bytes belong to. The CDN key is still assigned at ingest from
normalised record identity, so an awkward incoming filename never becomes a
permanent CDN key or a public URL.

**How records get repopulated: the same projection build, and nothing else.** This
is the part that closes the loop on the deletions, and it is simpler than it looks
because the state is *derived* rather than stored. `build_public_projection.py:2904`:

```python
record["image_path"] = candidate if candidate in public_paths else ""
record["media_available"] = bool(record["image_path"])
```

The record declares an image; the projection records whichever one actually exists.
So the same line does both halves of the job:

| Event | What the build produces |
| --- | --- |
| Cards and covers deleted | `image_path: ""`, `media_available: false` — automatically |
| Owner's artwork arrives at the declared path | `image_path` set, `media_available: true` — automatically |

**No front-matter edit is needed for the deletion**, and none for the restoration.
The dangling `image:` declaration is left in place deliberately: it records the
intent, and the projection reflects reality. And because the value is derived from
what exists rather than accumulated, **rebuilding is inherently replay-safe** —
there is no state to reconcile and no "already imported" flag to get wrong. Run it
as many times as you like.

**A file that matches no record, and a record that gets no file.** Both are
reported, and the two directions fail very differently:

| Case | What happens |
| --- | --- |
| File present, no record declares it | It becomes a media object with no consumer. `sync_public_media_verify.py` reports it as `extra` — the same detector that found the stale podcast file |
| Record declares an image, file absent — **primary/listing image** | Soft: `image_path: ""` and `media_available: false`. The page degrades as designed |
| Record declares an image, file absent — **inline body image** | **Hard: the build fails** with `article image is missing from the pinned source` (`build_public_projection.py:1029`) |

That last row is why the `cover.jpg` / `cover.png` distinction mattered beyond
tidiness: the three `cover.png` are embedded in article bodies, so deleting them
would not have produced a blank image — **it would have broken the projection
build outright**. Keeping them was not a nicety.

**The interim is a deliberate state, not a defect.** Between the deletions and the
owner's handover:

- **44 articles emit no `og:image` and no `twitter:image` at all.** The template
  guard omits the tags rather than pointing them at a 404. Correct behaviour.
- **206 podcast episodes render "Artwork unavailable."** in the player frame, from
  `_episode_artwork.html`'s existing `media_available` branch. Correct behaviour.

Neither is to be "fixed" by restoring deleted objects. If someone reports either
as a bug, the answer is that the artwork is pending, not that the deletion was
wrong.

**Owner-supplied files are validated exactly like everything else.** `_copy_media`
content-sniffs every file — JPEG, PNG and GIF magic — and the SVG sanitizer rejects
`<script>`, `<style>`, event handlers and remote `href`/`src`/`url()`. **There is
no exemption for artwork the owner produced**, and the argument is the same one
that applies to a generator: the gate is on the *ingest boundary*, not on the
origin. A human exporting from a design tool can ship an SVG with an embedded
script exactly as easily as a script can. Anything that fails the sniff is
rejected at ingest and reported; it is not waved through on provenance.

**One consequence of the deletions that will otherwise stop the build.** The
builder asserts the content-repository media count:
`EXPECTED_PREFERRED_CONTENT_MEDIA_COUNT = 815`
(`build_public_projection.py:118`, asserted at `:2828`). **Measured**, the content
repository tracks exactly 815 images — posts 407, podcast 212, books 196. Removing
50 cards and 206 covers takes it to **559**, and the build will **refuse** until
that constant is re-baselined. When the owner's artwork lands it moves again, by
however many files arrive. Re-baseline deliberately, with the reason recorded —
these canaries exist to make an unexplained change loud, and editing one without a
note is exactly the silent drift they are there to prevent.

#### The reference-rewriting problem, and who owns it

This is the part most likely to be missed, and this step does not solve it.

An article moving to `DataTalksClub/content` while its images move to the CDN means
**something must rewrite the image references**. Today that happens at
projection-build time inside `scripts/build_public_projection.py` —
`_article_blocks(body, media_root=...)` rewrites them and `_copy_media` records
per-asset provenance. **There is no equivalent on the database ingest path.** The
moment §11 B3 lands and content arrives through a sync instead of the builder, the
rewriting stops and nothing notices until an image 404s.

**Assigning keys makes B3's job strictly larger**: it is no longer "point at the
CDN" but "point at the CDN under a key we assign". How the two stay in step is a
design property, not a discipline:

> **Nothing outside the media layer may ever contain an object key.** Content
> references a *public path* (`/images/posts/…`); the media layer maps that path to
> a key and a version. Rename the key and no content changes, because no content
> ever named it.

That invariant holds today — **measured**: the object-key prefix appears in **zero**
projection artifacts, and article bodies carry `/images/...` public paths only. It
is what makes assigned keys safe, and it is exactly what a build step baking a CDN
URL into HTML would destroy. B3 must rewrite references to the **public path**,
never to the object key.

This step is the migration-time half of
[#301](https://github.com/DataTalksClub/website/issues/301); B3 is the steady-state
half; B11 takes the bytes out of git. AI Shipping Labs does the ingest-time upload
inside its sync (`github_sync/media.py`, `upload_images_to_s3`), which is the
fourth thing §11.1 says to copy.

### Step 8 — Sponsors and testimonials

**Owner ruling: both get an import script.** Both are database-backed surfaces
edited in Studio afterwards, so this is a one-time import that seeds what Studio
then owns.

**Testimonials now have theirs — `scripts/prod/import_testimonials.py`.** The six
quotes used to be inserted by a data-bearing migration and are now an explicit
import, reading the reviewed set from `courses/homepage_testimonials.json`. Every
row is keyed on its `source_url`, so a replay reports `replayed`, creates nothing,
and never touches a testimonial an editor added by hand. **Sponsors still have no
script**; §11 B9.

What it has to write into, read rather than invented:

- **Sponsors.** `core.models.Sponsor` is a `RevisionedModel` with
  `SponsorPlacementAssignment`. Do **not** write rows directly — go through
  `core/sponsors.py` (`create_sponsor`, `update_sponsor`, `archive_sponsor`),
  which is where validation, placement limits, revision conflict handling and
  the audit trail live. `export_sponsor_directory` already exists; this is
  its missing inverse.
  Two other things claim to hold sponsors and must be reconciled by whoever writes
  this: `core/sponsor_history.py`'s hardcoded `FEATURED_SUPPORTERS` tuple, and the
  legacy `_data/sponsors.yaml`, which is read by nothing at all. **Measured**: a
  freshly migrated database has **0** `core_sponsor` rows.
- **Testimonials.** `courses.models.Testimonial` — placement, optional cohort,
  name, attribution, quote, source URL, `portrait_asset_key`, before/after role, elapsed
  time, position, published flag — read by `courses/services/testimonials.py`.
  Read the model rather than inventing a shape. **`migrate` alone now leaves zero
  testimonials**: seeding has moved out of migrations, so the six arrive only when
  `import_testimonials` runs. If you remember them appearing for free, that has
  changed.

**The portraits are step 7's, and the field that names them is already ready for
the move.** This was a hazard when the plan was first written and it has been
closed since, so the argument is worth restating in its settled form rather than
its alarming one.

`Testimonial.photo_static_path` is now **`Testimonial.portrait_asset_key`**, and
the change is not just a name. The stored value is **origin-free and layout-free**:
the six rows hold `testimonials/nevenka-lukic.jpg` and the like — **no `core/`, no
`site-assets/`, no bucket, no scheme** — with a `RegexValidator` refusing anything
absolute, containing `..`, backslashed or scheme-prefixed. Resolution happens
outside the row: `INTERIM_SITE_ASSET_STATIC_PREFIX = "core/"` names where portraits
live *today*, and the CDN move changes that one constant.

> **This is the "nothing outside the media layer may ever contain an object key"
> invariant, applied to a database column.** The key names *which* portrait; it
> encodes no part of the old layout or the new one. So moving the six objects to
> `site-assets/testimonials/` is a prefix-constant change that touches **zero
> rows** — no data migration, no backfill, no window where a stale row can 500.

The manifest hazard is closed too. The template reads `{{ story.portrait_url }}`, a
model property that resolves through `staticfiles_storage.url()` and **returns `""`
on any failure**, and the template falls through to the decorative-avatar branch —
the designed empty state a testimonial with no portrait already uses. Verified
against the real `whitenoise` class over a temporary manifest rather than a mock,
and a full `client.get("/")` with one row's manifest entry deliberately missing
returns **200 with the other five portraits intact**.

So B13 owns the six objects and the prefix constant. It no longer owns a field
rename or a risky cutover, which is the difference between this being a careful
piece of work and a nervous one.

**Measured**, from a bare `migrate` on a scratch database:

```
after migrate:              testimonials 0                        -> checkpoint exit 1
after import_testimonials:  created 6, replayed false, total 6
                            sample key: testimonials/nevenka-lukic.jpg
                            keys carrying a layout prefix: []     -> checkpoint exit 0
re-run:                     created 0, replayed true, total 6
```

Three things confirmed at once: `migrate` really does seed nothing now, no stored
key carries `core/`, `site-assets/` or a leading `/`, and the import is replay-safe.

**Checkpoint**

```
$TARGET uv run --frozen python manage.py shell -v 0 -c '
import sys
from core.models import Sponsor
from courses.models import Testimonial
s = Sponsor.objects.count()
t = Testimonial.objects.count()
tp = Testimonial.objects.filter(published=True).count()
# Every published testimonial must name a portrait that resolves. After B13 this
# is a CDN reference; until then it is a static path.
missing = [x.pk for x in Testimonial.objects.filter(published=True)
           if not x.portrait_asset_key]
print("sponsors", s, "testimonials", t, "published", tp,
      "without a portrait", missing)
sys.exit(1 if (s == 0 or t < 6 or missing) else 0)'
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
2. **Run the forbidden-tables checkpoint first** (step 4), because configuring
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
the target configured as production, after step 4 and before the site is public:

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
      step 8
- [ ] `/faq/` and `/docs/` served by this application, and their CloudFront 302s
      retired — §11 B8, B10. Retire the redirects **after** the sync works
- [ ] **Content media verified** — step 7's checkpoint exits 0: group counts
      `authors` 438, `books` 196, `posts` 357, `podcast` 6; nothing
      missing/mismatched/unreadable; no record naming a deleted card or cover
- [ ] **The bucket has no `public-projection/` prefix** — `images/` and
      `site-assets/` are siblings at the root
- [ ] **Site assets published** — 18 objects under `site-assets/`, at their assigned
      `-light`/`-dark` names; homepage and `/sponsors` render every image in both
      themes; a `?v=` change reaches a browser holding the old bytes
- [ ] **No asset tracked in git** outside `core/static/core/vendor/` — B12 and B13
- [ ] A second `sync_public_media_publish.py` reports `added: 0, changed: 0` — it does not
      re-upload 147 MB
- [ ] `sync_public_media_hydrate.py` succeeds on a fresh clone with no access to the legacy
      repository — §11 B7
- [ ] **The public ID allocator sits above the highest imported event ID** — step 5
- [ ] **Testimonials imported** — `migrate` no longer seeds them
- [ ] Interim artwork state understood and accepted: 44 articles emit no `og:image`
      and 206 episodes render "Artwork unavailable." until the owner's artwork lands
- [ ] Row counts recorded per table, so the next run has a baseline to diff against
- [ ] The chosen export filename recorded in the run log

---

## 7. What the plan and the current local path do differently

`scripts/prepare_local_data.py` (`make production-prep-dataset`) is the closest
thing to a rehearsal that exists. Where it differs from this plan, **the plan is
the decision and the difference is a work item.**

What it actually does, in order: `migrate` → event identity import (`import_events.py`'s
`import_identities()`) → `seed_local_courses` → course-repository source registration
(`scripts/prod/sync_course_repository_sources.py`) → course-repository pull
(`scripts/prod/sync_course_repositories.py`) → CMP content import → derive/stage/activate
registration aggregates.

| # | Difference | Consequence |
| --- | --- | --- |
| 1 | It runs `manage.py seed_local_courses` as its bootstrap — a **placeholder seeder** reading `scripts/production_like_course_specs.json`, which invents copy ("Practice assignment for …") and writes 16 cohorts as well as the 6 families. Production has no seeder at all. | `seed_local_courses` refuses to run outside local SQLite, so it never could be the production bootstrap. It is no longer needed anywhere: `import_cmp_content` mints its own reviewed families — §3.2. The rehearsal should stop using it so it exercises the production path. |
| 2 | It does **not** run `import_legacy_zoomcamp` at all. | The rehearsal, as it stands, never exercises step 1 or the user-matching in step 4. `make import-legacy-zoomcamp` exists and is run separately. |
| 3 | It imports event identities **before** the course steps; the plan puts events at step 5. | Harmless — events depend on nothing else — but the rehearsal will not detect an ordering error the plan cares about. Either is safe. |
| 4 | It uses `courses/services/local_cmp_content_import.py` (copies the protected snapshot, sanitizes, learner tables never read) rather than `scripts/prod/import_cmp_content.py` directly. | Two entry points onto one service. The plan uses the `scripts/prod/` one. |
| 5 | It has no step 4, no step 6, no step 7 and no step 8. | The largest step, both content steps and media are unrehearsed. |

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
after step 3.

### 8.2 A disposable target

```
export REHEARSAL=.tmp/migration-rehearsal.sqlite3
export EXPORT=/data/tmp/rds-export/rds-prod-20260902-012536.db
export TARGET="DTC_ENVIRONMENT=local DJANGO_SETTINGS_MODULE=website.settings.local \
               DTC_SQLITE_PATH=$REHEARSAL"

rm -f "$REHEARSAL" "$REHEARSAL-shm" "$REHEARSAL-wal"
```

**Any database built before the migration collapse must be deleted, not migrated
forward.** `django_migrations` no longer matches the files on disk. That is not a
cost here — the production target is from-zero by definition, so a from-zero
rehearsal is now the same shape as the real run rather than an approximation of
it.

Everything lives under `.tmp/`. Tear-down is deleting those three files. Take a
copy after each step — `cp "$REHEARSAL" "$REHEARSAL.after-step-N"` — so a failed
step can be retried from the previous state instead of from zero. That is the
cheapest thing in this document and the most useful at 2am.

### 8.3 The sequence, with what each step substitutes

| Step | Rehearsal command | Source substitution | Does it weaken the rehearsal? |
| --- | --- | --- | --- |
| 0 | `$TARGET uv run --frozen python manage.py migrate --no-input` | SQLite instead of Postgres | **Yes, mildly.** SQLite will not catch a Postgres-only constraint or collation problem. The uniqueness and FK checks still run. |
| 1 | `$TARGET make import-legacy-zoomcamp IMPORT_DATABASE=$REHEARSAL` | none — real `zoomcamp-scoring` clone | No |
| 2 | `$TARGET make content-sources` → `content-checkouts` → `content-pull` | none — real repositories | No |
| 3 | `$TARGET uv run … scripts/prod/import_cmp_content.py --database $REHEARSAL --source $EXPORT` | none — the real export, read in place | No |
| 4 | **does not exist** — §11 A3 | — | The rehearsal cannot run at all until this exists |
| 5 | `$TARGET make import-events IMPORT_DATABASE=$REHEARSAL` | Luma/Eventbrite archives from `.local/migration-data` | No — **verified end to end**: 421 events, allocator at 422 |
| 6 | `scripts/prod/sync_public_media_verify.py`, `manage.py check`, `python -m ci.content_update` | the committed projection instead of a rebuild | **Yes.** A full rebuild needs three pinned checkouts and is not reproducible today (#253). The rehearsal checks the artifacts, not the build. |
| 7 | `scripts/prod/sync_public_media_hydrate.py` → `sync_public_media_publish.py` → `sync_public_media_verify.py` | a `local` store instead of `s3` | **Yes.** The rehearsal proves the counts and the incrementality, not the bucket |
| 8 | `$TARGET uv run … scripts/prod/import_testimonials.py --database $REHEARSAL` (sponsors: §11 B9) | none — the reviewed set is in this repository | No for testimonials; sponsors cannot be rehearsed yet |
| OAuth | §5.2 | real Google and GitHub, local callback | No — this is the real thing |

Run each step's checkpoint from §4 immediately after the step, with `$TARGET` and
`$EXPORT` set as above. They were written to run unchanged in both places.

### 8.4 Record these numbers

The rehearsal is the only place some of them can be obtained. Leave them here.

| Measurement | Rehearsal value | Notes |
| --- | --- | --- |
| Step 1, per edition, wall time | `mlops-zoomcamp-2022`: **244.5 s** (measured); other six: _ | 7 editions total |
| Step 1, users created per edition | `mlops-zoomcamp-2022`: **569** (measured) | |
| Step 3, wall time | _ | expected < 1 min |
| Step 4, wall time | _ | 510,519 rows; sizes the maintenance window |
| Step 4, per-table written/skipped | _ | the baseline for every future diff |
| Step 4, consolidation groups found | _ | expected **1**; a second one means the export moved — §5.5 |
| Step 5, wall time | _ | |
| Step 7, site-asset upload wall time | _ | 18 objects; the content media is already published |
| Step 7, second publish `added`/`changed` | _ | must be **0/0**, `skipped` equal to `total` |
| Step 7, bucket after the run | _ | `images` 997 / 144,380,949 B, `site-assets` 18, no `public-projection` |
| Step 7, objects deleted | _ | 50 social cards + 206 podcast covers = **256** |
| **Total** | _ | this is the maintenance window |

### 8.5 A number worth pre-computing

Step 4 then step 1 (or step 1 then step 4) must **match** learners by email rather
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
- **Step 6 as a rebuild.** See §8.3.
- **Step 7's site-asset half.** The rehearsal can verify the content media against
  a `local` store, but the `{% static %}` cutover, the manifest failure mode and the
  `?v=` cache behaviour are all release-shaped and only meaningful against a real
  deployment. The bucket itself *is* measurable from the workstation — the AWS gate
  is open — so bucket counts are evidence, not assumption; edge caching is not.
- **Everything downstream of the content view cutover**, because it has not been
  built.

---

## 9. Failure and recovery, at a glance

| Step | Transaction boundary | Partial state after a failure | Recovery |
| --- | --- | --- | --- |
| 0 Schema | per migration | possibly mid-migration | **Drop and restart.** The only step where that is the answer |
| 1 Legacy | none (per row) | that edition partial, statistics stale | Re-run the edition. Import one edition at a time |
| 2 Repositories | per repository | earlier repositories complete | Re-run; expect `replayed`. A checksum/identity conflict means stop |
| 3 CMP content | **per cohort** | earlier cohorts complete | Re-run; no-op on the done ones |
| 4 CMP learners | **to be decided — §11 A3** | unknown | Must be resumable per table. If it cannot be, a failure costs a full rebuild |
| 5 Events | atomic (identity); staged revisions (aggregates) | nothing half-written | Re-run |
| 6 Content | build writes files | committed projection unchanged, so the site is unaffected | Re-run the build; it needs three pinned checkouts and is not reproducible (#253) |
| 7 Media | per object | bucket holds a subset; every unpublished image is broken on the page | Re-run. Publish is incremental and skips a matching checksum, so a re-run completes rather than re-uploads |
| 7 Site assets | per asset group | **worse — a page can 500**, because a template still asking the manifest for a removed file raises | Not a re-run: keep the `core/static/` copies until the CDN objects verify, and move each group's files and references together |
| 8 Sponsors/testimonials | per service call (revisioned) | some rows written, history still valid | Re-run; must be keyed on a natural key |

**The step with no rollback that should have one is step 4.** Everything else is
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
| Step 1 — 7 legacy editions | **FAIL** | 0 present. `prepare_local_data.py` never runs that importer (§7 #2) |
| Step 2 — 20 modules / 181 units | **PASS** | `ai-dev-tools-2026` 4/4, `llm-zoomcamp-2026` 7/72, `ml-zoomcamp-2026` 9/105 |
| Step 2 — family-name cohort slugs | **PASS** | `ai-dev-tools-2026`, not `ai-dev-tools-zoomcamp-2026` |
| Step 3 — counts vs source | **FAIL, narrowly** | cohorts 16=16, projects 42=42, criteria 117=117, campaigns 5=5; **homework 107 vs 104**, **questions 500 vs 494** |
| Step 3 — homework identity | **FAIL** | every CMP slug is present (`missing=[]`), but `llm-zoomcamp-2026` still carries `homework-03`, `homework-06`, `homework-07` — three repository rows the reconciler could not pair, plus their 6 questions. That is the entire count delta above |
| Step 3 — inventory | **PASS** | no MLOps 2026 edition; `de-zoomcamp-2026` is `finished` |
| Step 4 — learner counts | **FAIL** | 1 account, 0 enrollments, 0 submissions. No importer exists |
| Step 4 — five forbidden tables | **PASS with a caveat** | `django_session` 6 and `socialaccount_socialapp` 3 are locally created, not imported. `socialaccount_socialaccount` and `accounts_token` are 0 |
| Step 5 — 421 events / 1,684 aliases | **PASS** | exactly |
| Step 5 — public ID allocator | **n/a here, PASS on a fresh build** | Measured on a from-zero database: `migrate` leaves 0 events and no allocator row; after the import, 421 events, highest `public_id` 421, allocator 422 |
| Step 5 — activation coverage | **PASS as designed** | 383 mapping rows: **3 mapped, 380 review_required** |
| Step 6 — projection | **PASS** | the database boots, so `content.E002` is satisfied |
| Step 7 — content media integrity | **PASS** | 997 records, 997 matched, 0 missing/mismatched/unreadable, 144,380,949 bytes — the exact target |
| Step 7 — group counts | **PASS** | `authors` 438, `books` 196, `posts` 357, `podcast` 6. The record deletions have been applied |
| Step 7 — sweep | **PENDING** | The store still holds 257 objects the index no longer names — the 256 deleted plus the stale podcast file. Expected between rebuild and sweep; must be zero before cutover |
| Step 7 — bucket layout | **FAIL** | Measured: 1,253 objects / 154,115,635 bytes, all under `public-projection/`. The prefix is being removed and `site-assets/` does not exist yet |
| Step 7 — no asset tracked in git | **FAIL** | 32 image files tracked under `core/static/core` — 8 illustrations, 4 sponsor logos, 6 testimonial portraits, 14 orphaned mediakit. B12 and B13. The 3 `vendor/` SVGs are FontAwesome webfonts and are excluded by the checkpoint |
| Step 8 — sponsors and testimonials | **FAIL** | 0 sponsors, and `courses_testimonial` does not exist in that database at all — it predates the model. On a freshly migrated database today both are **0**, because seeding has left migrations: the six testimonials now arrive from `scripts/prod/import_testimonials.py` |
| §5.4 — password path | **FAIL** | `/accounts/password/reset/` and `/accounts/email/` are correctly 403, but `/accounts/signup/` creates an account with a usable password and signs it in (measured). §11 A5 |
| §5.5 — no shared addresses | **PASS, vacuously** | 1 account. It proves nothing; the check only becomes meaningful once step 4 exists |

Two conclusions worth carrying forward. First, the three unreconciled
`llm-zoomcamp-2026` homework rows are a **live defect in the current dataset**,
and they are exactly the condition §3.1 says must be resolved before any
submission lands. Second, the checkpoints work: they found it, they named it, and
they exited non-zero.

---

## 11. Build before the dry run

Ordered so someone can work down it. Sizes are honest estimates, not targets.

### A. Blocks the dry run — the rehearsal cannot start without these

**A1. The structural seed — DONE, and not the way this plan specified.** *No work
remains; kept for provenance because earlier revisions pointed at it.* This item
called for a migration-only script writing the six reviewed course families before
any import ran, so that `import_cmp_content` had something to reconcile against.
It has been solved better and smaller: **the CMP importer now mints the family
itself**, taking the title from the reviewed `COURSE_FAMILY_TITLES` catalogue when
a cohort named in `COHORT_FAMILY_IDENTITIES` has no family row. No new script, no
new step, and the reviewed fact stays reviewed rather than derived.

That closes `sma-zoomcamp` too, which was the reason the item existed: it is not on
the skip list, all three editions were already in the identity mapping, and the
only thing it ever lacked was a family row. **Verify by:** step 3's inventory
checkpoint listing 16 cohorts including all three `sma-zoomcamp` editions, on a
database built only by steps 0 to 2.

**A2. Resolve the three unreconciled `llm-zoomcamp-2026` homework rows.**
*Blocks step 3's identity checkpoint. Small (hours), but it is a content
decision.* `homework-03`, `homework-06`, `homework-07` pair to no CMP row by slug
or exact title. Either CMP gains the matching rows, or the titles are aligned so
the existing pairing rule matches, or they are deliberately deleted. **Done looks
like:** the step 3 identity checkpoint exits 0. Must be settled **before** step 4
ever runs, because after it a submission may point at one.

**A3. The CMP learner importer.** *Blocks step 4 — i.e. everything. **Large
(1–2 weeks)**, and it is the single biggest item in this plan.* See §4 step 4 for
the full specification: 11 tables / 510,519 rows, 5 never-import tables, five
mandatory transforms, resumable per table, per-table written/skipped reporting,
PII-safe logging by user id. `scripts/load_rds_export.py` looks like a starting
point but its `main()` is disabled and only its internals survive, in two test
modules. **Done looks like:** all four step-4 checkpoints exit 0 against a
rehearsal database, and the run reports its own per-table counts. Depends on A1
(cohorts must exist) and A2 (homework identity must be final).

**A4. Decide the export-day questions the importer encodes.** *Blocks A3's
design. Small (hours), but they are owner decisions and cannot be guessed.*
Two of the four are now ruled and written into step 4's transforms: shared
addresses are **consolidated** (§5.5), and every account imports **unprivileged
with an unusable password**, staff being granted afterwards through Studio. Still
open: (i) the 58 users who exist only in the five owner-skipped cohorts, and the
171 enrollments and 227 submissions there; (ii) the six small undecided tables in
§12 decision 4. **Done looks like:** each written into this document as a rule the
importer implements. Can be answered in parallel with A3 starting.

**A5. Close `/accounts/signup/`.** *Small (hours). Do it before step 4 runs, not
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
the seeder and runs step 1 instead, or write a thin `scripts/prod/` orchestrator
that runs steps 0-7 in this document's order. The manual sequence in §8.3 is
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

**B7. Close the `sync_public_media_hydrate.py` legacy default.** *Small (hours).*
**A separate Codex run is fixing this now — do not duplicate it; check whether it
has landed before starting.* `--source github` is the default, and a fresh clone
or a bucket re-hydration therefore fetches 438 author images from the legacy
repository and reports `failed: 438` once it is gone. Production on `s3` and CI on
`memory` are unaffected, which is why it survived this long. **Done looks like:**
`sync_public_media_hydrate.py` on a fresh clone succeeds with no network access to the
legacy repository. Independent.

Note the ruling makes this more than a default. `--source github` reads media
*from a git repository*, which is the arrangement being ended: once the bucket is
the origin of record the source has no steady-state purpose beyond the one-time
seeding in step 7, and once B11 lands it has none at all. Whether it survives that
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

**B9. The sponsor and testimonial import script.** *Small (1–2 days).* Step 8. One
`scripts/prod/import_*` entry point writing sponsors through `core/sponsors.py`'s
services and testimonials into `courses.models.Testimonial`, keyed on a natural key
so a re-run is a no-op and the six migration-seeded testimonials are matched rather
than duplicated. The input format is an owner decision — the obvious candidates are
the legacy `_data/sponsors.yaml` (currently read by nothing) and the existing
`export_sponsor_directory` output, which would make the script its exact inverse.
**Done looks like:** step 8's checkpoint exits 0 and a second run changes no
counts. Independent of everything else.

**B10. Retire the `/faq/` and `/docs/` CloudFront 302s.** *Small, but it is a
change in `DataTalksClub/aws-infra`, not here.* Once B8 works, the redirects are
shadowing pages we own. Leave `/podwiki/` and `/mediakit/` alone — those are still
deliberately not hosted. **Done looks like:** `/faq/` and `/docs/` resolve to this
application in production. Depends on B8. Do this *after*, never before.

**B11. Take the images out of `DataTalksClub/content`.** *Medium (2–3 days), and
it is the second half of the owner's ruling.* Measured: this repository tracks
**0** content image files, `DataTalksClub/content` tracks **815**, and the legacy
repository tracks 1,290. Step 7 puts the bytes in the CDN; this removes them from
git, which is a separate act with a real ordering constraint — **the bucket must
be verified complete before anything is deleted**, because after deletion the
bucket is the only copy.

**That constraint applies to the carried set and not to the regenerated one**, and
the distinction matters because "we are regenerating them anyway" is exactly the
reasoning that loses data. For `authors/`, `posts/` and `books/` the upstream copy
is the only fallback and the constraint holds in full. For the podcast covers the
old bytes are being discarded, so the old *outputs* do not need protecting — but
the **inputs to regeneration do**. If a cover is produced by re-encoding the
upstream image, deleting upstream destroys the ability to regenerate; if it is
produced from a source asset or a prompt, upstream can go. Establish which before
deleting anything, and treat the regenerated set as protected until the
regeneration is proven repeatable. Sequence: step 7 → its checkpoint exits 0 → B3's sync
resolves references against the CDN (not against repository paths) → only then
delete. **Done looks like:** `git ls-files "images/*"` in `DataTalksClub/content`
returns nothing, and every article still renders its images. Depends on step 7 and
on B3.

**B12. Delete `core/static/core/mediakit/`.** *Small (minutes), zero risk,
independent of everything.* 14 files, 7.4 MiB, orphaned when `/mediakit/` became a
301 to `DataTalksClub/mediakit`, which hosts its own copies. **Measured**: no
template, view or stylesheet references any of the 14; the only `mediakit` strings
in the tree are the redirect in `website/urls.py` and `core/tests/test_mediakit.py`.
**Done looks like:** the directory is gone, 7.4 MiB leaves the container image and
the staticfiles manifest, and the mediakit redirect test still passes.

**B13. Move the 18 site assets to `site-assets/`.** *Medium (3–5 days), and it is
harder than it sounds because it crosses two pipelines.* 8 homepage illustrations,
4 sponsor logos, 6 testimonial portraits leave `core/static/` for the CDN under
`site-assets/<surface>/<name>-<theme>.<ext>`. What it has to carry, none of it
optional:

- **Rename on ingest, in the same edit as the URL change.** `home-hero.webp` →
  `home-hero-light.webp`, `home-stuck` → `home-step-1`, and the template's
  `variant="stuck"` with it. Ten `{% static %}` call sites in three files —
  `templates/core/_home_illustration.html` (8), `templates/core/home.html:991`
  (testimonials, from a database value), `core/sponsor_history.py:92` (sponsors).
- **A URL resolver and a small manifest** — name, surface, theme, checksum. **Not**
  `ContentAsset` rows: that model belongs to the content pipeline nothing writes and
  nothing reads (B4), and site assets need a resolver, not a content model.
- **`?v=<sha256[:8]>` on every emitted URL**, because a stable key does not
  cache-bust the way a hashed static filename does. Verify the CloudFront cache key
  includes the query string; if it strips query strings the version does nothing.
- **Testimonial portraits move by changing one constant.**
  `INTERIM_SITE_ASSET_STATIC_PREFIX` in `courses/models/testimonial.py` is where
  portraits resolve from; `portrait_asset_key` already stores a layout-free key, so
  **zero rows change**. Do not reintroduce a prefix into the column.
- **Fail soft.** A missing static entry is a 500 under manifest storage; a missing
  CDN object must be a broken image. That is the point of moving them.
- **Keep the `core/static/` copies until the CDN objects verify.** This is a
  cutover, not an upload: between removing a file and repointing its template, the
  page raises.

**Done looks like:** step 7's site-asset checks pass, the homepage and `/sponsors`
render every image in both themes, `git ls-files` finds no image under
`core/static/core` outside `vendor/`, and a `?v=` change reaches a browser holding
the old bytes. Depends on B12 only for tidiness. Connects to
[#311](https://github.com/DataTalksClub/website/issues/311) and
[#301](https://github.com/DataTalksClub/website/issues/301).

**B14. Delete the podcast covers, then regenerate.** *Small to delete, medium to
regenerate, and it is content work rather than engineering.* Owner ruling: remove
them, they are being replaced. **206** covers go; the **6** objects under
`images/podcast/badges/` are platform badges, nothing is regenerating them, and they
stay. Order, and it is not negotiable: **rewrite the records first**
(`media_available: false`, drop the image reference), rebuild the projection, *then*
delete the objects. In the interval `_episode_artwork.html` renders its designed
"Artwork unavailable." note; delete first and the same page renders a broken
`<img>`.

**The replacement artwork is the owner's, not ours to produce.** Our half is the
ingest path in step 7 — the owner commits each cover at the path its episode YAML
already declares, and the projection build repopulates `image_path` and
`media_available` on its own. Nothing extra runs and there is no second importer.
**Done looks like:** the covers are gone and every episode renders its
"Artwork unavailable." note without a broken image; and later, when the artwork
lands, `images/podcast/` holds badges plus the new covers with `verify` clean.

Both this and B15 trip the same guard: `EXPECTED_PREFERRED_CONTENT_MEDIA_COUNT`
(815, `build_public_projection.py:118`) is asserted at `:2828`, so the build
**refuses** until it is re-baselined — 815 → 559 for the two deletions together.
Re-baseline with the reason recorded; that canary exists to make an unexplained
change loud.

**B15. Delete the post social cards.** *Small, but the record rewrite is the whole
job.* Owner ruling: keep the illustrations, remove the generated social card.
**50 objects, 2,456,087 bytes** — stem exactly `cover`, **extension `.jpg` only**.
The 3 `cover.png` are artwork embedded in article bodies and **stay**; see step 7
for the three measurements that separate them. **44 of the 50 are an article's
`image_path`**, which `content/public_views.py:708` turns into `og:image` and
`twitter:image`, so the records must be rewritten first — clear `image_path`, and
the existing `{% if og_image_url %}` guard omits the tag entirely rather than
advertising a 404 — and because `image_path` is *derived* from what exists, the
rewrite is the projection build itself, not a hand edit. New article artwork, when
the owner supplies it, comes back the same way. Then delete. **Done looks like:**
no record names a
`posts/*/cover.jpg`, `images/posts/` is 357 objects / 133,108,882 bytes, and no
article emits an `og:image` pointing at a deleted object.


### C. Wanted, not blocking

**C1. A content drift check.** *Medium.* Nothing answers "is what we serve what
upstream says?" for `DataTalksClub/content`. All the parts exist:
`parity.py` is ~90% of it but is pinned to `ACCEPTED_CONTENT_COMMIT` and only
iterates projection → bundle; `sync_public_media_verify.py` is the right report shape;
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

8. **Assigned keys have one residual case and one lost property.** *(a)* If a
   record's **logical** identity changes — the podcast renumbering, not a filename
   edit — the assigned key changes and the old object becomes `extra`. Full
   immunity needs a persisted assignment ledger that survives rebuilds, which is
   real state with a real cost. Accept the re-key, or fund the ledger. *(b)* A
   stable key means regeneration **overwrites**, so the previous bytes are gone;
   rollback becomes restore-from-source rather than point-at-the-old-object. S3
   bucket versioning is the cheap mitigation if that is not acceptable.

9. **Who publishes site assets, and when.** Step 7 does the one-time upload. After
   that `site-assets/home/` changes with a release, `site-assets/courses/` with a
   course, and `site-assets/sponsors/` when a sponsor changes — three different
   triggers. Until B13 makes it repeatable, every one of them is a manual upload,
   and *that* has to be written where the person making the change will look, not
   only in this runbook.

### 12.1 Decisions that are now closed

Recorded so they are not reopened.

| Was open | Ruling |
| --- | --- |
| How do the importers land against an empty database? | **`import_cmp_content` mints its own reviewed families.** An earlier plan added a structural-seed step; it is not needed — §3.2, §11 A1 |
| Where does `sma-zoomcamp` come from? | **CMP**, directly. Not skipped, no special case; it only ever lacked a family row, which the CMP importer now mints — §3.2 |
| Are 20,009 usable password hashes an emergency? | **No.** Members signed in through a provider; passwords were for admins. The enforcement was URL shadowing and it travelled — §5.4. Every account still imports unprivileged with an unusable password |
| The two accounts sharing one address | **Consolidate them**, as a general mechanism with one known instance — §5.5, §11 A6 |
| Public media objects had no fate (`data-ingest.md` §2 row 18) | **Step 7.** Images go to the `dtc-website-media` bucket **and out of every git repository** — §11 B11 |
| The one-file gap between the tree (1,254) and `media.json` (1,253) | **Stale residue on one developer's disk**, from an episode renumbering. Measured: the bucket holds 212 podcast objects, so it never reached production. Moot once the covers go — it disappears on the next re-hydrate |
| Do CDN keys come from upstream filenames? | **No — we assign them on ingest.** `site-assets/<surface>/<name>-<theme>.<ext>`, content media from normalised record identity |
| Do sponsor logos and homepage illustrations stay as static assets because they are design? | **No.** No assets in the repository; all 18 go to the CDN — §11 B13 |
| Where do site assets live in the bucket? | **`site-assets/`, a sibling of `images/` at the root**, subdivided by surface: `home/`, `courses/`, `sponsors/`, `testimonials/` |
| Is the light variant implicit? | **No.** `-light` is explicit for any themed asset; theme-neutral assets carry no theme segment |
| Is `home-step-1` missing? | **No.** `home-stuck` *is* the step 1 drawing, per the partial's own comment; it is published as `home-step-1` |
| Is a testimonial portrait a static path or a CDN key? | **Neither is stored.** `portrait_asset_key` holds a layout-free key like `testimonials/nevenka-lukic.jpg`; the prefix lives in one constant, so the CDN move touches zero rows |
| Does `migrate` still seed data? | **No.** Testimonials and the event identity manifest are explicit imports now — steps 8 and 5 |
| May a runbook name a migration by number? | **No** — owner ruling. Describe what it does, or name the code that owns the behaviour |
| Who produces the new banner and cover artwork? | **The owner.** Generation is off our plate; receiving and ingesting it is the deliverable — step 7 |
| Are the podcast covers deleted now? | **Yes** — owner ruling. 206 covers go, the 6 `badges/` stay. Records first, objects second, so the player frame shows its "Artwork unavailable." note rather than a broken image — §11 B14 |
| Are the post social cards deleted? | **Yes.** 50 `cover.jpg`, keeping the 3 `cover.png`, which are artwork. 44 are an article's `og:image`, so records are rewritten first — §11 B15 |
| Is `public-projection/` kept as a prefix? | **No** — owner ruling, it names the producing artifact rather than the objects and disambiguates nothing. `images/` and `site-assets/` become siblings at the root |
| Was "stem exactly `cover`" the right deletion rule? | **Not quite** — one file too broad. `.jpg` only: the 3 `.png` are 2 MB artwork embedded in article bodies, against 49 KB generated cards |
| Do FAQ and docs stay at the legacy site, or come to us? | **They come to us via content sync.** The CloudFront 302s are transitional — §11 B8, B10 |
| Sponsors and testimonials have no source | **They get an import script** — step 8, §11 B9 |
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
  the export. Step 4 owns this list; **do not** reuse `review_import`'s
  `SENSITIVE_TABLES` for it — that list is the sanitized-review-database policy and
  excludes the whole payload of step 4.
- A member's email address is visible to admins in Studio and nowhere else.
- In a log, identify a person by user id, never by email address.
- Do not hand-edit anything in `temporary/content/public_projection/` — the startup digest
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
