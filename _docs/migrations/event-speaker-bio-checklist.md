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

## A new event now has a path, and two places it waits for a person

This checklist is closed for the 421 events it covers, and it never grows: the
bridge that described them matches on the legacy `_data/events.yaml` tuple, and
a Luma-discovered event does not have one. That is why the fix is not a bigger
bridge. A new event's description goes through its own additive artifact,
applied after the bridge and keyed on identity id, with the same Markdown and
link policies and its own provenance saying the description came straight from
an export.

That artifact exists now. `_docs/runbooks/data-ingest.md` §14.4 is the runbook;
this section says what it means for the bio decision specifically.

**The removal rule is code, and it generalises — so there is no second plan to
write.** `normalize_description_html` finds a bio section by the markers in the
plan's `rules.bio_section_markers` -- "about the speaker", "about the guests",
"speaker bio", "bio", "biography" -- and drops that block through to the end of
the section. Run it over a description Luma produced this morning and the bio
goes; it needs no plan entry to do that. The same call removes the
"DataTalks.Club is the place to talk about data" footer and rewrites absolute
self-links.

`scripts/staging/luma_event_descriptions.py` calls exactly that function and
records the outcome -- `removed_speaker_bio`, `removed_platform_boilerplate`,
`normalized_internal_links` -- in each record's own `description_provenance`,
rather than in a replay plan. There is nothing to replay it against: unlike the
421, whose descriptions are rebuilt from the bridge each time, this artifact
*is* the reviewed result and the database reads it once. So the "the plan
refuses an unknown event" blocker recorded here is not worked around; it simply
does not apply on this path, and `apply_event_speaker_bio_normalization` and its
421-event plan are left exactly as they are.

Measured over the real export on 2026-09-05, with a scratch type file standing
in for the review nobody has done yet: 160 of the 166 description pairs built,
154 speaker bios removed, 134 platform-boilerplate blocks removed, 154 internal
links rewritten. What the removal does is settled; what is not settled is the
type, below.

**The link policy is still the human gate, and it still works.** A destination
with no reviewed decision stops that event, and the builder now reports it *by
URL* so a person knows what to look at. Over the real export that is 4 events
and 6 distinct destinations: `http://bol.com` and `https://Fly.io` (hosts not in
`REVIEWED_EXTERNAL_HOSTS`), and four URLs on already-approved hosts that are not
in `REVIEWED_RENDERED_LINKS` -- two GitHub paths, one YouTube video, and
`https://pythoninvest.com/`, whose reviewed literal is the no-slash spelling.
Approving any of them is an edit to
`scripts/projection_build/event_description_link_policy.py` by a person. Nothing
infers or auto-approves one, and host approval alone stays deliberately
insufficient.

**What still requires a person, and it is not the bio.**

1. *The event's type.* An `EventContent` row needs one, and nothing anywhere in
   a Luma export says whether an event is a webinar, a workshop, a podcast or a
   conference. It comes only from
   `_docs/migration-data/local-event-type-input.json`, which a person maintains
   and which ships empty. Until somebody fills it in, the builder reports every
   export under `no_reviewed_type` and prepares nothing -- 164 of them today.
   Its start time is not a person's job: the export states it, and the builder
   reads it from the `_json` checkpoint beside each description.

2. *The link approvals above.* 4 events.

Everything else is wired: `scripts/prod/import_events.py --discover-new-events-only`
creates the `Event` row (§14.3), `scripts/build_luma_event_descriptions.py --write`
builds the artifact once both reviews are clean, and the same import script's
`new_event_content` leg lands it as `EventContent` with its speakers and links.
A description export carries no reviewed speaker list and no reviewed event
links -- the bio block is removed and the links stay inside the description
copy -- so those two collections land empty, which is the honest value rather
than a gap.
