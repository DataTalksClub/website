# Cross-sell after registration

After someone registers on a campaign page (`/courses/register/<slug>/`), the
success panel and the confirmation email suggest up to three other open
courses. One-click rows register the visitor for a suggested course with the
same details, with no new form to fill in. Code:
`courses/services/cross_sell.py`; panel:
`courses/templates/courses/_registration_cross_sell.html`.

## Relevance rule

The rule is deterministic, and everything it reads lives in the database.

1. **Candidates.** A candidate is an active `RegistrationCampaign` that passes
   all of these:
   - it is not the campaign just registered for, and not another cohort of
     the same course family;
   - it does not have `exclude_from_suggestions` set, and its slug is not in
     `CROSS_SELL_EXCLUDED_CAMPAIGNS`;
   - its cohort, if it has one yet, is visible, its family is visible, and
     the cohort is not finished. A "next cohort" campaign without a cohort is
     allowed;
   - the visitor is not already registered for it (by account or by email)
     and not enrolled in its cohort.
2. **Order.** The campaign's curated `suggested_campaigns` come first, then
   every other candidate. Within each group, campaigns are sorted by cohort
   start date (soonest first, undated last), then by slug.
3. **Limit.** The first `CROSS_SELL_MAX_SUGGESTIONS` (default 3) are shown.

The fallback means the block is never empty just because a campaign has not
been curated yet.

## Configuration

- **Per course:** Django admin → Registration campaigns → *Cross-sell*.
  - `suggested_campaigns` is the curated list for this course ("after
    registering here, also suggest …"). It is one-directional.
  - `exclude_from_suggestions` keeps a campaign out of every suggestion
    list.
- **Environment:**
  - `CROSS_SELL_MAX_SUGGESTIONS` (int, default 3).
  - `CROSS_SELL_EXCLUDED_CAMPAIGNS` (comma-separated slugs).

## One-click registration

`POST /courses/register/<slug>/also/` registers the visitor for the
suggestions they picked. It takes either `campaign=<slug>` (the per-row
button) or `campaigns=<slug>` repeated (the checkbox list).

- **Source registration.** It reuses the visitor's own registration on
  `<slug>`: the signed-in account's, or, when the sign-in gate is off, the
  one stored in the session.
- **Fields copied:** name, email, company, country, region, role, years of
  experience, newsletter consent.
- **Never copied:** course-specific consents and application answers.
- **Validation.** Only slugs from the freshly recomputed one-click suggestions
  are honoured. Repeat posts are no-ops because of the unique
  campaign+email constraint.
- **Application campaigns** (`requires_approval`, or a cohort that requires
  registration approval) are never auto-registered. Their row links to the
  campaign's own form instead.
- **Confirmation.** A copy of a confirmed registration is confirmed at once,
  since the address is already proven. A copy of an unconfirmed one gets no
  email of its own: the source's confirmation link confirms it too
  (`cascade_confirmation`). "Wrong email? Fix it" moves unconfirmed copies to
  the corrected address. Deleting the source deletes its unconfirmed copies.

## Tracking and conversion

`CourseRegistration.source` records where each registration came from:

| source | meaning |
| --- | --- |
| (empty) | the plain registration form |
| `xsell` | one-click from the success panel (`referred_by` = the source registration) |
| `xsell-link` | registered after following an *Apply* link on the success panel (`?src=xsell`) |
| `xsell-email` | registered after following a link in the confirmation email (`?src=xsell-email`) |

The funnel is recorded through `record_event`. Event properties contain slugs
and ids only, never email addresses.

- `cross_sell.impression`: each render of the block. Properties:
  `from_campaign` and the comma-separated `suggested` slugs.
- `cross_sell.click`: each campaign page visit with a recognised `src`.
- `cross_sell.registered`: one per one-click registration.

**Measuring conversion.**

- Registrations per source: filter by *source* in the admin, or run
  `CourseRegistration.objects.values("source", "campaign__slug").annotate(n=Count("id"))`.
- One-click conversion = `cross_sell.registered` ÷ `cross_sell.impression`.
- Link conversion = registrations with source `xsell-link`/`xsell-email` ÷
  `cross_sell.click`.
