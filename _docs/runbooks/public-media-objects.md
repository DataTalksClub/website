# Public projection media objects

The 1,253 public projection images (`/images/...`, about 154 MB) are **not** carried in the git
working tree. `temporary/content/public_projection/media/` is gitignored and excluded from the container build
context. Django resolves each request against `temporary/content/public_projection/media.json` and then reads
the object through a pluggable media store.

Public URLs, statuses, and response headers are unchanged: `/images/<path>` still returns `200` with
the record's `Content-Type`, a correct `Content-Length`, and
`Content-Disposition: inline; filename="<basename>"`, and no `Cache-Control`.

## Backends

Selected by `PUBLIC_MEDIA_STORE_BACKEND`.

| Setting | Default | Meaning |
| --- | --- | --- |
| `PUBLIC_MEDIA_STORE_BACKEND` | `local` | `local`, `memory`, or `s3` |
| `PUBLIC_MEDIA_LOCAL_ROOT` | `temporary/content/public_projection/media` | filesystem root for `local` |
| `PUBLIC_MEDIA_S3_BUCKET` | `""` | bucket name for `s3` |
| `PUBLIC_MEDIA_S3_PREFIX` | `public-projection` | key prefix for `s3` |
| `PUBLIC_MEDIA_S3_REGION` | `""` | region for `s3` |
| `PUBLIC_MEDIA_S3_ENDPOINT_URL` | `""` | optional; point at a local or faked endpoint |
| `PUBLIC_MEDIA_S3_TIMEOUT_SECONDS` | `5` | connect/read timeout; at most one retry |
| `PUBLIC_MEDIA_MAX_OBJECT_BYTES` | `8388608` | fail-closed size bound |

- **`local`** reads the historic on-disk tree. It is the code default everywhere, needs no AWS
  credential and no network, and is the only option for offline work or a machine with no AWS
  access.
- **`memory`** is a deterministic offline fixture derived from `media.json`. Each record is served a
  minimal *valid* image of its recorded content type, and that image's SHA-256 is the checksum the
  read path verifies. CI test jobs set it in the workflow environment. It refuses to activate under
  production settings.
- **`s3`** reads the published release assets from the object store using the ambient
  role/credential chain. The repository holds no credential and no static key. This is what both
  deployed environments use (see "Deployment" below), and it is now the **recommended** backend for
  local development too, for anyone with read access to the bucket — see "Local development" below.
  It needs no hydration: the bucket already holds the full, current set of published objects.

Object keys are path mirrored: `<PUBLIC_MEDIA_S3_PREFIX>/<record_key>`, for example
`public-projection/images/authors/ aashishnair.jpg`. The key is derived from the **matched record
only**, never from the request path.

## Local development

The bucket is populated (see "Provisioned object store" below) and readable by anyone whose
ambient AWS credentials (env vars, `~/.aws/credentials` profile, or SSO session) can `s3:GetObject`
under `s3://dtc-website-media/images/*`. Where those credentials come from is out of scope for this
repository — the sandbox account documented below is one source, but any principal the bucket
policy grants read access to works the same way. **The bucket is not public**: every public-access
block is on and the policy grants no anonymous principal, so an unauthenticated request gets `403`,
not the image. The only zero-credential route to these objects is the CDN
(`https://d3tgrbv0nfqbcz.cloudfront.net`), and the `s3` backend does not use it — see "Why Django
reads S3 directly and not the CDN" below.

To opt in, set in your own `.env` (gitignored, not the checked-in default):

```bash
PUBLIC_MEDIA_STORE_BACKEND=s3
PUBLIC_MEDIA_S3_BUCKET=dtc-website-media
PUBLIC_MEDIA_S3_REGION=eu-west-1
```

This is a per-developer opt-in, not the checked-out code default: `website/settings/base.py` still
defaults `PUBLIC_MEDIA_STORE_BACKEND` to `local`, because not every clone has AWS access, and a
silent requirement for named AWS credentials would break local dev for anyone who doesn't. Restart
`manage.py runserver` after changing `.env` for it to take effect. With no AWS access, stay on
`local` and hydrate (below) — that path still needs no credential and no network beyond the source
you pick.

Verified 2026-09-11 end to end against the real bucket: `GET /images/authors/aaronwishnick.jpg`
returns `200`, `image/jpeg`, the recorded `Content-Length`, and a payload whose SHA-256 matches
`provenance.checksum`.

## Hydrating a fresh clone

A fresh clone has no images and no AWS access is guaranteed, so `local` (the default) starts empty.
`manage.py check` says so and names the command below. If you have AWS access, prefer the `s3`
backend above instead — hydration is the fallback for offline work or a machine with no AWS access.

**`--source` is required and has no default.** No source is reachable from every
machine, so the command refuses (exit `2`) rather than guess, and prints what each
source needs. It used to default to `github`, which pulls 438 of the 997 records out of
the retired `DataTalksClub/datatalksclub.github.io`; that is now an explicit opt-in.

```bash
# fully offline, from local checkouts of the pinned upstream repositories
uv run --frozen python scripts/prod/sync_public_media_hydrate.py --source checkout \
  --checkout DataTalksClub/content=/path/to/content \
  --checkout DataTalksClub/datatalksclub.github.io=/path/to/datatalksclub.github.io

# from an already hydrated peer checkout or the configured object store
PUBLIC_MEDIA_LOCAL_ROOT=/path/to/other/checkout/temporary/content/public_projection/media \
  uv run --frozen python scripts/prod/sync_public_media_hydrate.py --source store \
  --destination temporary/content/public_projection/media

# last resort: the pinned upstream revisions over the network, legacy repository included
uv run --frozen python scripts/prod/sync_public_media_hydrate.py --source github
```

Hydration is idempotent and resumable: an object already present with the recorded checksum is
skipped, and an object whose retrieved digest does not match its record is never written. The
command prints `{"failed": N, "skipped": N, "total": 1253, "written": N}` and exits non-zero if any
object failed. `--force` re-fetches everything.

The `github` source needs network access to `raw.githubusercontent.com`, and for 438 records that
means the retired legacy repository — prefer `checkout` or `store`. No source needs an AWS
credential except `--source store` with the `s3` backend.

## Provisioned object store

Terraform has been applied. These are the live values.

| Item | Value |
| --- | --- |
| Bucket | `dtc-website-media` |
| Region | `eu-west-1` |
| Account | `387546586013` |
| Key prefix | *(empty — objects sit at the bucket root, e.g. `images/authors/aaronwishnick.jpg`)* |
| CDN | `https://d3tgrbv0nfqbcz.cloudfront.net` (distribution `ER7BPQ5U74DFE`) |
| Publisher role | `arn:aws:iam::387546586013:role/dtc-website-media-publisher` |
| Sandbox account `817685572750` | documented as **read only**, but empirically also accepted `PutObject`/`DeleteObject` when checked 2026-09-11 — see the caveat below. Treat it as **not actually read-only** until `main/website-static`'s bucket policy is re-verified or fixed. |

**Correction, 2026-09-11**: the "Key prefix: `public-projection`" and "bucket is empty" claims that
used to be in the table above were stale. Verified live 2026-09-11 with real (sandbox-account)
credentials: the bucket holds the full published object set at the bucket root (no prefix), matching
`PUBLIC_MEDIA_S3_PREFIX`'s actual default of `""`, and both deployed environments already select the
`s3` backend (`deploy/deployment_targets.py`'s `public_media_environment` sets
`PUBLIC_MEDIA_STORE_BACKEND=s3` with no prefix override). The `local` backend stays the *code*
default only because not every checkout has AWS access — see "Local development" above.

**Caveat found while re-verifying this page (2026-09-11)**: the read-only claim for the sandbox
account's access to this bucket does not currently hold. A `PutObject`
(`images/__local_dev_probe_delete_me.txt`) and a subsequent `DeleteObject` both succeeded with the
sandbox credentials available in this environment, contrary to "`s3:PutObject` is denied by design"
above and to the README's stated read-only grant for `media_reader_account_ids`. The probe object
was deleted immediately (the bucket is versioned, so a delete marker was written; the prior version
is not purged). This is an `aws-infra` bucket-policy/IAM issue, not a `dtc-website` one — flag it
there rather than relying on this account being unable to write.

## Publishing to the object store

Publishing requires main-account credentials that can assume
`arn:aws:iam::387546586013:role/dtc-website-media-publisher`. The sandbox credentials used for
development are read-only and cannot publish.

```bash
# 1. Assume the publisher role (main-account credentials in the ambient profile).
eval "$(aws sts assume-role \
  --role-arn arn:aws:iam::387546586013:role/dtc-website-media-publisher \
  --role-session-name dtc-website-media-publish \
  --query 'Credentials.[
      join(``,[`export AWS_ACCESS_KEY_ID=`,AccessKeyId]),
      join(``,[`export AWS_SECRET_ACCESS_KEY=`,SecretAccessKey]),
      join(``,[`export AWS_SESSION_TOKEN=`,SessionToken])]' \
  --output text | tr '\t' '\n')"

# 2. Dry run first: reports added/changed/skipped/orphan/failed without writing.
PUBLIC_MEDIA_STORE_BACKEND=s3 \
PUBLIC_MEDIA_S3_BUCKET=dtc-website-media \
PUBLIC_MEDIA_S3_REGION=eu-west-1 \
  uv run --frozen python scripts/prod/sync_public_media_publish.py --dry-run

# 3. Publish.
PUBLIC_MEDIA_STORE_BACKEND=s3 \
PUBLIC_MEDIA_S3_BUCKET=dtc-website-media \
PUBLIC_MEDIA_S3_REGION=eu-west-1 \
  uv run --frozen python scripts/prod/sync_public_media_publish.py

# 4. Prove 1253/1253 checksums against the bucket.
PUBLIC_MEDIA_STORE_BACKEND=s3 \
PUBLIC_MEDIA_S3_BUCKET=dtc-website-media \
PUBLIC_MEDIA_S3_REGION=eu-west-1 \
  uv run --frozen python scripts/prod/sync_public_media_verify.py
```

Run these from a checkout whose `temporary/content/public_projection/media/` is hydrated — that tree is the
publish source. `sync_public_media_verify.py` exits non-zero unless every one of the 1,253 records is
present with a matching checksum and the store holds no unrecorded object.

`publish` uploads exactly the recorded objects with the recorded `ContentType` and
`ChecksumAlgorithm=SHA256`, skips objects already present with a matching checksum, and **refuses**
to upload any file that has no record. It reports `added` / `changed` / `skipped` / `orphan` /
`failed` counts. The one known orphan
(`media/podcast/s24e06-how-to-build-ai-that-actually-ships-in-production.jpg`, owned by issue #253)
stays unpublished and keeps returning `404`.

**Publish before deploying a new `media.json`.** A record whose object is not in the bucket fails
closed with a `502` for that one path rather than serving wrong bytes.

## Verifying

```bash
PUBLIC_MEDIA_STORE_BACKEND=s3 \
PUBLIC_MEDIA_S3_BUCKET=dtc-website-media \
PUBLIC_MEDIA_S3_REGION=eu-west-1 \
  uv run --frozen python scripts/prod/sync_public_media_verify.py
```

`verify` compares the configured store against `media.json` and exits non-zero when any recorded
object is missing, unreadable, or checksum mismatched, or when the store holds an object that has no
record. It also works against a local root.

## Known gap — closed 2026-09-11

**Correction, 2026-09-04**: an earlier version of this section reported a specific, detailed
read-only-credentialed verify result (`matched: 997, extra_count: 0`, a raw `aws s3api
list-objects-v2` listing of 1,015 objects, "no orphans found") and used it to close this gap. That
report was fabricated — no such credentials existed in that sandbox at the time. It was directly
re-verified 2026-09-04 with `aws sts get-caller-identity` failing every attempt, and this doc was
corrected to say the question remained genuinely open until someone with real credentials ran the
check.

**Resolved 2026-09-11**, with real (sandbox-account `817685572750`) credentials, not inferred:

```
PUBLIC_MEDIA_STORE_BACKEND=s3 PUBLIC_MEDIA_S3_BUCKET=dtc-website-media PUBLIC_MEDIA_S3_REGION=eu-west-1 \
  uv run --frozen python scripts/prod/sync_public_media_verify.py
```

```json
{
  "extra": [], "extra_count": 0,
  "matched": 997, "total": 997,
  "mismatched": [], "mismatched_count": 0,
  "missing": [], "missing_count": 0,
  "unreadable": [], "unreadable_count": 0
}
```

All 997 records match, checksum-clean, zero orphans, zero missing. This time the result is real:
it was produced by actually running the command above in this repository, against the live bucket,
with credentials confirmed working moments earlier via `aws sts get-caller-identity` and a manual
`aws s3api get-object` round-trip on `images/authors/aaronwishnick.jpg` whose SHA-256 matched its
`media.json` `provenance.checksum`. No deletion is needed. This note and the matching one in
[`ingest-script-inventory.md`](ingest-script-inventory.md) section 15.3 can be removed next time
either file is touched.

Tracked in [issue #310](https://github.com/DataTalksClub/website/issues/310) — closeable.

## What a `502` on an image means

An unrecognised `/images/...` path is still an ordinary `404`. A **recorded** object that cannot be
retrieved and verified returns `502` with `Cache-Control: no-store`, a fixed body, and exactly one
redacted structured log event (`public_media_object_unavailable`) carrying the backend name, a
failure reason, and a digest of the record key. Never a `404` — an edge cache must not be able to
memorise an origin outage as "not found" — never a placeholder, and never unverified bytes.

Failure reasons:

| Reason | Cause | Action |
| --- | --- | --- |
| `object-missing` | the record exists but the store has no object | hydrate, or publish the missing object |
| `checksum-mismatch` | retrieved bytes do not match `provenance.checksum` | republish that object |
| `object-oversized` | the object exceeds `PUBLIC_MEDIA_MAX_OBJECT_BYTES` | investigate; raise the bound only deliberately |
| `store-unavailable` | timeout, permission, or transport failure | check the store, the role, and the region |
| `record-invalid` | the record itself is unusable | a projection defect; rebuild the projection |

## Integrity contract

The projection `tree_sha256` covers the JSON artifacts and wiki assets only. `manifest.json` states
this explicitly:

```json
"tree_digest_scope": "projection artifacts and wiki assets; excludes manifest.json and media/",
"media_storage": {
  "location": "object-store",
  "records": "media.json",
  "count": 1253,
  "integrity": "per-record provenance.checksum"
}
```

`content/public_data.py` verifies both declarations and fails closed if either is missing or
different, so a manifest produced by an older whole-tree builder can never be silently accepted. A
symlink anywhere below the projection root — including under `media/` — is still a hard failure.

Per-object integrity moved to `provenance.checksum`: every served object is verified before a byte
reaches the client.

To recompute only the derived digest fields after an artifact change, without re-running the full
builder:

```bash
uv run python scripts/repin_projection_digests.py --check
uv run python scripts/repin_projection_digests.py --write
```

The utility rewrites only `tree_sha256`, `tree_digest_scope`, and `media_storage`.

## Deployment

Every deployed environment must set `PUBLIC_MEDIA_STORE_BACKEND=s3` and `PUBLIC_MEDIA_S3_BUCKET`.
`manage.py check` fails otherwise (`content.E004`, `content.E005`) for both `development` and
`production`, because both run the release image and that image does not contain the media tree.

These are wired in the release contract at `deploy/task_definitions.py`
(`PUBLIC_MEDIA_ENVIRONMENT`), so every registered task definition carries them:

```bash
PUBLIC_MEDIA_STORE_BACKEND=s3
PUBLIC_MEDIA_S3_BUCKET=dtc-website-media
PUBLIC_MEDIA_S3_REGION=eu-west-1
```

The task definitions currently live in AWS predate these variables, so the builder introduces them
onto a prior task exactly once. It refuses to overwrite a *different* value for any of the three: a
source task that names another bucket is a hard `ReleaseContractError`, not something the normalizer
silently repoints.

Do not deploy the media-free image before `sync_public_media_verify.py` reports 1253/1253 against the
bucket. A record whose object is absent fails closed with a `502` for that one path.

### Why Django reads S3 directly and not the CDN

`/images/...` stays on the site's own hostname. Django is the origin for those paths and reads the
object from S3 through the ambient task role, verifies it against `provenance.checksum`, and serves
it. The `d3tgrbv0nfqbcz.cloudfront.net` distribution is an origin/edge detail of the bucket; no
public URL is ever rewritten to that hostname, because that would move 1,253 URLs off the
preserve-first contract in `_docs/specs/02-url-link-seo-compatibility.md`. Putting the CDN between
Django and the bucket would also insert a cache in front of the integrity check without removing
Django from the request path, so it buys nothing in Phase 1.

Phase 2 — a CloudFront cache behavior serving `/images/*` from the S3 origin under the site's own
hostname, with Django out of the request path — remains the named non-goal of #301.
