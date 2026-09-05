# Event speaker-bio normalization checklist

This checklist covers every event in the canonical public projection. Each row records the
deterministic outcome of the review: an event with no description, an event with no duplicate
material, or an event whose speaker bio and/or external-platform footer was removed.

The authoritative replay data is
`_docs/migrations/event-speaker-bio-normalization.json`; its transform is
`scripts/projection_build/event_speaker_bio_normalization.py`. The projection builder applies the checked
bridge first, then replays this migration transform, retaining the bridge source hashes and
adding normalization provenance to each changed event. The original bridge and external source
data remain unchanged for export or audit use.

To rebuild, use the pinned clean source checkouts and run:
`uv run python scripts/build_public_projection.py --content-root <content-checkout> --legacy-main-root <legacy-checkout> --wiki-root <wiki-checkout>`

Coverage: 421 events reviewed (159 described, 262 without descriptions), 155 speaker-bio
sections removed, 136 external-platform boilerplate blocks removed across 136 events, 154
absolute internal self-links rewritten to root-relative paths, and 17 linked person bios updated.
Two source conflicts are recorded in the replay plan: a
mismatched speaker name was discarded, and a conflicting current affiliation retained the
checked person profile. No checklist rows remain open.

Canonical projection at checklist creation: `temporary/content/public_projection/events.json`
(421 events, 2026-08-29).

## A new event does not pass through this automatically

This checklist is closed for the 421 events it covers. A Luma event that
arrives tomorrow does not flow through it, and the reason is structural rather
than a missing command. Each stage below was checked against the real export in
`.local/migration-data/events/luma/descriptions/` (166 files).

**The removal rule is code, and it does generalise.** `normalize_description_html`
finds a bio section by the markers in the plan's `rules.bio_section_markers` --
"about the speaker", "about the guests", "speaker bio", "bio", "biography" --
and drops that block through to the end of the section. Run it over a
description Luma produced this morning and the bio goes; it needs no plan entry
to do that. The same call removes the "DataTalks.Club is the place to talk about
data" footer and rewrites absolute self-links.

**The link policy is the human gate, and it works.** Rendering one real
undescribed event's Luma Markdown classified twelve URLs without help: six Luma
registration links and two course/Slack actions removed, one internal link
rewritten, two GitHub links kept -- and two refused, `https://Fly.io` (host not
in `REVIEWED_EXTERNAL_HOSTS`) and one GitHub URL on a reviewed host that is not
in the 80-literal `REVIEWED_RENDERED_LINKS`. Rendering fails closed with
`description rendered link is not reviewed`. That gate should stay: approving a
destination is a person's decision, and the bridge's own rules say host approval
alone is not enough.

**Two things then block the result from surviving.**

1. *The plan refuses an unknown event.* `apply_event_speaker_bio_normalization`
   replays a reviewed decision per event and checks the set is exactly the 421 it
   names. A 422nd fails with `event speaker-bio projection count mismatch`
   (verified). Nothing in this repository writes that plan file -- it has readers
   only -- so recording a new decision is unbuilt work.

2. *A projection rebuild erases the description anyway.* `apply_bridge_to_events`
   is authoritative: an event with no bridge entry has its description set to
   `""` and its provenance to `null`. And the bridge matches entries on the
   **legacy `_data/events.yaml` tuple**, which a Luma-discovered event does not
   have -- `create_event_identity` gives it title and path only. So the bridge
   cannot key a new event's description even if someone added one to it.

**What this means for the shape of the fix.** Extending the bridge is the wrong
move: its matching key is legacy provenance that new events will never carry. A
new event's description needs its own additive artifact, applied after the
bridge and keyed on identity id, with the same Markdown and link policies and
its own provenance kind saying the description came straight from a Luma export
rather than through the bridge. The plan then needs a generator so the bio
decision for that event is recorded and replayable.

Until that exists, a new Luma event reaches the database with an identity and no
description: `scripts/prod/import_events.py --discover-new-events-only` creates
the `Event` row (see `_docs/runbooks/data-ingest.md` §14.3), and no path carries
its description in.
