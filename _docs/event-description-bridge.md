# Event description bridge

The checked event projection remains authoritative for all 421 canonical event identities, routes,
titles, times, types, speakers, ordering, podcast lineage, and non-Luma event links. The version-1
event description bridge supplies only a reviewed, sanitized description for 159 exact matches.
The other 262 events intentionally have no description region.

The bridge is a build artifact, not a synchronization path. Only the offline projection build
reads it, from the committed `temporary/content/event_description_bridge.json`; public requests and
Django startup read the database and never see it. Nothing here needs the exporter checkout, makes a
network request, reads a CSV, or inspects a guest record.

## Reconciliation and review

An authorized operator starts from the exact clean exporter revision recorded in the bridge and
runs the command with local paths supplied explicitly:

```console
uv run python scripts/build_event_description_bridge.py \
  --exporter-root /operator/path/to/exporter \
  --source-root /operator/path/to/exporter/luma-events
```

Without `--write`, the command prints only safe digests, aggregate counts, and bounded decision
codes. It validates 168 JSON/Markdown pairs, the checked projection baseline, the reviewed source
and URL-inventory digests, and all Markdown/link policies. Matching uses only the normalized exact
provider host and byte-exact one-segment path. Title and UTC start comparisons are diagnostics and
never select a target.

The reviewed baseline must remain exactly 159 one-to-one matches and nine opaque source gaps. The
local reviewer may keep a richer crosswalk only under `.tmp/`; it is never committed or pasted into
an issue. After reviewing the complete candidate, repeat the command with `--write`. Replacement is
atomic, and any drift or validation failure leaves the prior artifact unchanged.

All 540 URL occurrences and 118 distinct literals are bound by the reviewed link-inventory digest.
Provider, registration, join, form, unsafe, unresolved internal, and unreviewed short-link actions
are omitted. Valid DataTalks.Club links resolve to final HTTPS canonical routes, useful reviewed
external resources receive accessible new-tab markup, and the one remote image is omitted without
fetching or copying it.

Markdown policy version 2 also removes copy that depends on a stripped action destination: exact
orphaned `form` link labels and the unsupported “Use this link to submit them in advance”
instruction. Builder and runtime corpus validation reject those dangling link/form remnants, so a
future source or artifact change fails closed instead of rendering a phantom action.

The builder and the projection checker share the code-owned policy in
`scripts/projection_build/event_description_link_policy.py`. It pins the exact seven decision kinds and counts and
the literal set of 80 reviewed destinations that may appear in rendered HTML; host approval alone
is not sufficient. Both boundaries also resolve internal paths and fragments against the committed
route registry and reject registration/action paths, provider links, meeting/join hosts, unknown
hosts, and non-reviewed literals. Consequently, recomputing entry, bridge, projection, and
provenance digests cannot authorize a new destination. A content update must review the changed
literal, decision inventory, policy code, schema, and regenerated bindings together.

## Projection build and rollback

Run the ordinary projection builder only after the safe bridge is reviewed. The builder verifies
the old 421-record event artifact digest before applying the bridge, removes exactly 168 Luma
top-level links, preserves the 682 existing non-Luma link objects, and writes event-record schema
version 2. The outer projection manifest remains schema version 1 and binds the bridge schema,
content/source/link digests, policy versions, reconciliation counts, resulting event artifact, and
complete tree digest.

Rollback is also projection-only. A rollback candidate must retain all 421 canonical records and
682 non-Luma links while either selecting a previously reviewed Luma-free bridge or applying a
reviewed empty-description bridge. The code-owned
`apply_empty_description_rollback_to_events` transformer accepts only the exact pinned legacy
artifact, removes the same 168 Luma links, and emits record-schema-v2 empty description fields.
Its exhaustive test compares every identity, provenance value, and retained link against both the
legacy baseline and the active projection. The old event artifact containing Luma actions is never
a valid rollback target.

## Where the reviewed descriptions end up

The projection this builds is a **staging layer**, not a serving path. Nothing on a public request
path reads it. Its one consumer is `scripts/prod/import_events.py`, which writes the reviewed
records into `EventContent` with its speakers and links, and the public event pages read those rows
(`events/queries.py`). See `_docs/architecture/database-only-content.md`.

That import is why the artifact is checked in at all. The description a visitor reads -- stripped of
the "about the speaker" biography and the platform boilerplate, with every link bound to a reviewed
destination -- exists in that form only here: rebuilding it needs the exporter checkout an
authorized operator holds locally, and the legacy repository it was originally derived from is
retired.

`events.content_import` resolves every record by the exact legacy tuple preserved on it: repository
`DataTalksClub/datatalksclub.github.io`, revision `ee43d3fa0929faf691178d79f19528e6f15a83e5`, source
path `_data/events.yaml`, source key, and source checksum
`7eac8bcc9bfb3ec5f0b35434343a58eb766f8cc8451dca8a4a82ac4674aa213d`. It re-checks that tuple, the
title and the slug against the identity row already in the database and refuses the whole candidate
on any mismatch, so a missing, duplicated, or changed tuple blocks the import rather than landing a
description on the wrong event. It also refuses a description that arrives without this bridge's
provenance behind it. It never rereads the exporter and never creates an event -- including from any
of the nine source gaps; identity import (`events.identity`) is the only thing that creates events.
