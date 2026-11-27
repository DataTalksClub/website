# Email audience merge

The owner asked for the course platform's (CMP) emails and the emails in Relay
to become one newsletter audience, and for the result to answer "who is
subscribed now". Mailchimp, which sent the newsletter until Relay took over, is
the third source.
This page holds the rules. `scripts/prod/import_email_audience_merge.py` is
their only implementation, and `_docs/runbooks/data-ingest.md` §27 is how it is
run. Account identity itself is in
[`single-durable-account.md`](single-durable-account.md).

## Where each source keeps its mail state

| Source | Newsletter consent | Opt-outs | Course mail |
| --- | --- | --- | --- |
| **CMP** (`courses_courseregistration`) | `accepted_newsletter`, with the registration's `created_at` and campaign (course). The form field is `required=True` and `clean_accepted_newsletter` refuses an unticked box, so **every registration made through the form carries consent**. The box reads "I agree to be added to the DataTalks.Club newsletter and receive course updates". | The retired Datamailer contact events in `data_datamailercontactevent` (`subscription.unsubscribed`, `contact.hard_bounced`). | The three category opt-outs left CMP's account in migration 0010 and live in Relay's `category_preferences` (audience `dtc-courses`). |
| **Mailchimp** (list `97178021aa`) | The subscribed export, with `OPTIN_TIME` and `CONFIRM_TIME` (double opt-in). A Marketing API members dump (`<audience>_subscribed.csv`) has `timestamp_opt` instead. | The unsubscribed export (`UNSUB_TIME`, reason) and the cleaned export (`CLEAN_TIME`, i.e. bounced). The API dump has neither time, so the member's `last_changed` stands in for it. | none |
| **Relay** (org `datatalksclub`) | `subscriptions.status = subscribed` in audience `dtc`: client `dtc-newsletter` holds the imported Mailchimp list and the public double-opt-in list behind the website's newsletter form; client `dtc-website` holds course registrations the website syncs. `pending` means the double opt-in was not confirmed. | `subscriptions.unsubscribed_at` per audience/client, and on the contact `global_unsubscribed_at`, `hard_bounced_at` and `complained_at`. | Audience `dtc-courses` (client `dtc-courses`), with `category_preferences`. |
| **Website** | `EventRegistration.newsletter_consent` (with version, source and `newsletter_consented_at`), and `CourseRegistration.accepted_newsletter` for registrations made on the site (`courses.services.registration_contacts` pushes those to Relay `dtc`/`dtc-website`). The newsletter page posts straight to Relay's public list and stores nothing locally. | `User.newsletter_subscribed`, plus `newsletter_preference_changed_at` when the member decided it. | `User.email_submission_confirmations`, `email_deadline_reminders` and `email_course_updates` (`None` means allowed). |

### Inventory (2026-11-26, counts only)

- **CMP** export `rds-prod-20261126-153117`: 43,609 registrations for 37,609
  addresses.
  - 22,771 consented through the form with a confirmed address, 116 consented
    with an unconfirmed address, and 20,722 carry no box.
  - All 20,722 of the no-box rows came through the bulk registration API added
    on 2026-08-24, which defaults `accepted_newsletter` to false. Nearly all of
    them were created on 2026-08-25 for five 2026 campaigns.
  - 27,190 CMP accounts, of which 16,875 have no registration.
  - Datamailer remnants: 23 unsubscribe events (13 addresses) and 49
    hard-bounce events (46 addresses). The 6,000 `relay240-loadtest` rows are a
    synthetic load test and are ignored.
- **Mailchimp**, two captures, both read in place under `~/prod/dtc-data`:
  - `mailchimp-export/` (UI export, latest change 2026-09-03): 130,854
    subscribed, 7,247 unsubscribed and 1,354 cleaned.
  - `mailchimp-export-2026-11-26/` (Marketing API members dump, taken
    2026-11-26, latest change that day): 131,228 subscribed, 11,627
    unsubscribed and 2,365 cleaned. It is the one the merge reads. It holds all
    but 3 of the September addresses, and both captures' unsubscribes start on
    2026-04-29, so September holds no opt-out the fresh one lacks. Since
    September, 4,000 subscribers unsubscribed and 854 were cleaned; of the
    addresses added since, 391 unsubscribed and 157 were cleaned; 11
    resubscribed.
- **Relay**: no production export exists on this machine.
  `rds-export/relay/*.db` is 0 bytes, and `~/relay-exports/` (2026-10-02) is
  the sandbox (two contacts). Either read-only export works: the SQL export
  specified in `~/prod/dtc-data/requests/relay-audience-export-2026-11-26.md`,
  or one `relay operator contacts csv-export --full` file per audience/client
  scope (Relay #163). The CLI export has the contact's suppressions as booleans
  without their times, which only affects the `consent_newer_than_latest_opt_out`
  count.
- **Website**, local dataset copy of 2026-11-26:
  - 30,251 accounts, and **all 30,251 have `newsletter_subscribed = true`**,
    because the field defaults to true and no import has written false.
  - None carries a decision stamp.
  - Two event registrations recorded a newsletter answer (one yes).
  - All three course category opt-outs are unset on every account. The Relay
    category preferences have never been imported.

## Rules

1. **Match key.** The address, trimmed and lower-cased; this is the same key
   as the account's `normalized_email` and Relay's `normalized_email`. The
   merge is per address, never per person: addresses are not joined across
   accounts.
2. **Consent evidence.** These count as consent, each kept with its source and
   timestamp:
   - a CMP registration with the box ticked and a confirmed address (consent
     time is the registration time; the course is recorded);
   - Mailchimp subscribed (confirm time);
   - a Relay `subscribed` subscription in audience `dtc`;
   - a website event-registration consent;
   - a member's own stamped "yes" on the website.
3. **Opt-outs always win.** Any of these, in any source, takes the address off
   the newsletter: an unsubscribe (Mailchimp, a Relay subscription in any
   audience, a Relay global unsubscribe, or a CMP Datamailer unsubscribe), a
   bounce (Mailchimp cleaned, Relay hard bounce, CMP hard bounce) or a
   complaint. The merge never resubscribes such an address. Consent given
   after the opt-out does not reverse it either; it is counted as
   `consent_newer_than_latest_opt_out` for the owner to decide. Relay's own
   import also keeps existing opt-outs.
4. **No evidence, no newsletter.** These are not consent:
   - an account with no consent anywhere, including CMP accounts that never
     registered;
   - a CMP registration without the box (the bulk-imported rows);
   - a consent given with an unconfirmed address;
   - a Relay `pending` (double opt-in not confirmed);
   - a subscription to the course audience `dtc-courses`.

   They are counted, never added.
5. **Course mail and newsletter are separate categories.** The newsletter is
   audience `dtc`. Course and transactional mail stays governed by the three
   category fields and Relay's `dtc-courses` preferences. A course-category
   opt-out is recorded but does not unsubscribe from the newsletter, and the
   merge never touches transactional mail. An unsubscribe from a whole Relay
   audience (including `dtc-courses`) does count as an opt-out under rule 3.
6. **The website's "no" is not an unsubscribe.** A stamped
   `newsletter_subscribed = false` cannot tell an account-page opt-out from an
   event registration whose box was left unticked. It is recorded as
   `site_declined`: the account keeps it, and an address that is subscribed
   elsewhere is counted as `site_declined_but_subscribed_elsewhere` for the
   owner to decide.
7. **Absorbed duplicates.** Per `single-durable-account.md`, mail consent is
   never merged across accounts. An absorbed or quarantined account is never
   written, and the survivor gets the verdict for its own address. Several CMP
   registrations for one address collapse into one key, and the evidence keeps
   each one.
8. **No accounts are created.** An address with consent but no website account
   (Mailchimp- or Relay-only) stays a newsletter-only contact in Relay.
9. **Writes.**
   - Relay gets `relay-import-dtc.csv` for `import_audience_csv --organization
     datatalksclub --audience dtc --client dtc-newsletter`. Subscribed rows
     carry `consent-<source>` tags. Opted-out rows are `unsubscribed`, with
     `hard_bounced`/`complained` where they apply.
   - The website gets `User.newsletter_subscribed` set to the verdict through
     `bulk_update`, so `newsletter_preference_changed_at` is not stamped. A
     stamped member decision is kept unless rule 3 applies.
   - The per-address evidence (`evidence.jsonl`) stays in the run directory
     under `~/prod/dtc-data/email-audience/`, next to the counts-only
     `report.json`. It is never put in the repository or the database.

## Ownership after cutover, and sync direction

- **Relay owns send-time truth**: who is subscribed to the newsletter, every
  unsubscribe, bounce and complaint, and the per-category preferences. Its
  unsubscribe links and SES events write there first.
- **The website DB owns** accounts, and the consent *events* captured on the
  site: event registration consent with its notice version, course
  registration consent, and the signup onboarding consent being built now.
  It also owns the member's own account-page decision.
- **Direction: site → Relay, immediately, for consent.** A new consent on the
  site is pushed as a Relay upsert. Relay refuses to resubscribe an opted-out
  address, which keeps rule 3 true at runtime. Course registrations already do
  this (`registration_contacts`); event registration consent and onboarding
  consent do not yet.
- **Relay → site only for opt-outs.** The site does not keep a second copy of
  subscription state. Where it shows the state, it should ask Relay
  (`contact_status`). Until then, `User.newsletter_subscribed` is the value
  this merge wrote, and the account page's own changes.
- **Mailchimp** stops being a source once its sending is retired. One final
  full export (subscribed, unsubscribed and cleaned) is merged before that,
  per Relay's `docs/mailchimp-migration.md`.

## Signup onboarding consent (website, 2026-11-26)

The post-signup profile step (`/accounts/welcome/?onboarding=1`) says that
signing up means emails about the course and the community (the weekly
newsletter) and shows a pre-ticked box. Account states
(`accounts.services.newsletter_consent.newsletter_state`):

| State | Stored as | Meaning |
| --- | --- | --- |
| off | `newsletter_subscribed = false`, no `newsletter_confirmation_sent_at` | No consent (the default for every new account), unticked at signup, or unsubscribed on the account page |
| pending | `newsletter_subscribed = false`, `newsletter_confirmation_sent_at` set | A social signup left the box ticked; the `newsletter-confirm` email went out and its link was not clicked yet. **Not consent** (rule 4) |
| active | `newsletter_subscribed = true`, with `newsletter_consent_version`, `newsletter_consent_source = signup onboarding` (or `account settings`) and `newsletter_consented_at` = the confirmation time | Confirmed: the emailed link was clicked, or, for an email + password signup, its account verification link (which comes before onboarding) |

The account page shows the state, unsubscribes at once, and sends turning it
back on through the same confirmation. Like event consent, onboarding consent
is not yet pushed to Relay (open decision 4 applies to it too); when it is,
only the *active* state is pushed, and Relay's refusal to resubscribe an
opted-out address keeps rule 3.

## Open decisions for the owner

1. **The 20,722 bulk-imported CMP registrations without the box (11,796
   addresses with no other evidence).** Did their original forms require
   consent? If they did and that can be shown, they become consent evidence
   with their registration time. Until then they stay out.
2. **Consent after an opt-out (88 addresses in the 2026-11-27 dry run, 83 on
   2026-11-26).** These registered for a course, ticking the box, after
   unsubscribing from Mailchimp (85) or bouncing there (3). Rule 3 keeps them out. The alternative is to accept the newer
   consent.
3. **Resolved 2026-11-26: `User.newsletter_subscribed` defaults to false**
   (`accounts` migration 0012). A new account is not subscribed until it
   confirms; see "Signup onboarding consent" below.
4. **Event registration consent is not pushed to Relay.** It is stored only on
   the site.
5. **Course category opt-outs.** Import Relay's `dtc-courses` category
   preferences onto the three account fields (`import_mail_category_optouts`
   needs a JSON export) before the site sends course mail itself.
6. **Unconfirmed addresses (116 registrations).** They are kept out until the
   address is confirmed.
