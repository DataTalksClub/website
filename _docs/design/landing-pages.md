# Landing page blueprint (Academy programs, course family landings)

Source: Maven's cohort-course landing guidance (help.maven.com/en/articles/6815473,
maven.com/resources/nail-your-hero-section, maven.com/resources/winning-landing-page),
adapted to DataTalks.Club's rules (frozen H1s/URLs, no price, no testimonials until we have
real ones, no job promises, one CTA per page).

Global: one goal per page (the waitlist / register action), repeated identically at hero,
mid-page and end; nothing else competes as a primary button. Active voice, concise, no
superlatives, no "-ing" headline verbs, no "Unleash/Supercharge/Bootcamp". Every section uses
existing design-system primitives at the shared column width.

## Program landing page, top to bottom
1. **Hero** — H1 (frozen). Subhead 2–3 sentences: the outcome, the transformation, who it's for.
   Credibility line with photo (name + "taught 100,000+ students"). The short waitlist form
   (fewest fields, one column) or the primary CTA. No stacked link lists, no status pills.
2. **Key facts** — spec strip: Start, Format, Where, Language, Seats, Funding (short values).
3. **Who it's for / not for** — at most 2 closely related personas, specific enough to
   disqualify. Two parallel cards.
4. **Overview** — the challenge the reader faces, what the program covers, how the format
   (full-time, live, project every week) delivers the outcome.
5. **Outcomes** — "You'll be able to [action] so you can [outcome]." Concrete deliverables,
   Bloom's higher-order verbs, never "learn/understand".
6. **Syllabus, week by week** — "Week N: action-oriented title" + one line on the tangible
   output. No module numbers that disagree with weeks.
7. **Final project (capstone)** — what you ship, who uses it, how you show it.
8. **Interview preparation** — its own block, separate from the capstone.
9. **Instructor** — relevant experience + why I built this program + who it's for. Short.
10. **Schedule** — daily rhythm (Mon–Fri 09:00–16:30 Berlin), start date, where.
11. **(Testimonials)** — only when real ones exist; never a placeholder.
12. **FAQ** — prerequisites, time commitment, missed sessions, deliverables, who it's NOT
    for, funding, location; ends with the contact line.
13. **Closing CTA** — same waitlist action as the hero.

## Academy home
Hero (what the Academy is in 2–3 sentences + form) → Choose a program (two cards, secondary
buttons, link to the comparison page) → Key facts → Funding (English + German guide links) →
Free sessions → Articles (recaps, guides, comparison) → FAQ excerpt → Closing CTA.

## Article pages (recaps, guides, comparison)
Same reading layout as a blog post: breadcrumb, H1, byline/date, prose at the reading width,
related links, one closing CTA.

## As built on the Academy (2026-11-24)
- The waitlist form sits once per page, in the closing mint panel; the hero
  carries the primary action that scrolls to it (the blueprint's "form or the
  primary CTA"), because a form beside the copy squeezed the heading and lede
  at the content width.
- The key facts close the hero on the home page as on the program pages (one
  place for facts across the Academy), rather than as a chapter after the
  program cards.
- "Limited seats" and "How you get in" sit beside the closing form, where the
  decision is made, instead of a chapter of their own.
- Implementation: `academy/templates/academy/` and `_docs/design/design-system.md`
  ("The Academy pages").

## Course family landing (`/courses/<family>`)

The free-course landing follows the same blueprint, adapted to what the course records
hold (`courses.views.course_page_context.course_family_page_context`). The
implementation contract is `_docs/design/design-system.md`, "Course landings".

1. **One width.** The whole page sits in the ordinary content column
   (`--content-width`, 56rem), like the Academy pages. Every block fills it; a block
   that would leave an empty strip on its right is laid out to the column instead.
2. **Hero** — the frozen course title as H1, the lede (`Course.outcome`), one primary
   action, the course drawing scaled to the column. No secondary text links beside the
   button: the docs link is a fact, the syllabus is on the page.
3. **Key facts** — the `.spec-strip` under the hero: next cohort start, format (live
   cohort / self-paced), syllabus size, weekly time, docs. Only facts the data backs.
4. **About** — the overview (`Course.description`) at the column width, then the
   live outcome numbers (cohorts, sign-ups, projects, graduates) as the credibility
   marker.
5. **Join** — only when a cohort is open or approval-gated: the cohort slab and, beside
   it, the start-on-your-own route. A family with only its materials has no second
   choice, so the hero's action is the whole join step and no card repeats it.
6. **Journey** — the three `progression` stages from `course.yaml`, on the homepage's
   stage cards. The copy is the course repository's.
7. **Syllabus** — one row per module: action-oriented title + one line on the tangible
   output (both from the course repository's module metadata), projects at their
   chronological slot with their deadlines.
8. **Who it's for**, **cohort editions** (one row each; the current one carries its
   status), **stories** (only real ones), **what people built**, **FAQ**.
9. **Closing CTA** — the mint panel with the hero's action, identical label and
   destination, and one line saying where it leads. No Academy or tour stripe after it.

Primary action, by what the data holds: *Register for the YYYY cohort* (open
registration) → *Apply for this course* (approval-gated) → *Start the self-paced
course* → *Start module 1* (curriculum on the platform) → *Open the course on GitHub*.

Copy lives at its source, never in the template: title, lede, overview, starting
point, prerequisites, progression and module titles/summaries come from each course
repository's `course.yaml` and module metadata (synced by the course repository
ingest, `_docs/runbooks/data-ingest.md` §3).
