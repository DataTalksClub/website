# Site feedback ideas: what changed, why, and where it stands

Source: Valeriia's review, `_docs/audits/2026-11-19-site-feedback.md` (section
numbers below refer to it), implemented by several Hub intakes on 24–25 Nov 2026.

On 25 Nov 2026 Alexey reviewed the result: many ideas are directionally good, but
they landed all at once, changed the whole site and undid a lot of UI polish. The
rule from now on: **the polished design (as it was on localhost:8015 before these
merges) is the base. Ideas come back one at a time, in that design's style, each
reviewed on the rendered page before the next.**

Status: **kept** = live now; **idea** = reverted, waiting its turn; **review** =
live, still to be judged one by one.

## Home page

| # | Idea | Why (feedback) | Tried in | Status |
|---|---|---|---|---|
| 1.1 | One neutral ground for most sections; colour only for big blocks; softer transitions; green only on buttons | Background changes on almost every band, so it is hard to see what is important; hard edges make the page feel like separate plates; green on decoration makes buttons less visible | #552 | idea |
| 1.2 | Simpler "one idea per picture" illustrations (staircase, reading, helping, laptop with a check) | Current pictures have many details and the meaning is not instant; the old site's simple "climbing the stairs" read at once; small detail turns into noise on mobile | #552 (assets `home-simple-*` kept in the repo) | idea. Alexey: pictures not good enough yet |
| 1.3 | Landing-page type scale; climb cards titled "Learn the fundamentals / Learn with the community / Build your own project" with one short line each | Text feels like an article, not a landing page; small grey text under detailed pictures; card titles written as learner thoughts are slower to scan | #552 | idea |
| 1.4 | Section order: benefits higher, testimonials near the end | Visitors first need to know what this is and what they get; social proof after that | #570 | idea |
| 1.5 | "What's inside DataTalks.Club" overview of activities with links; "Free courses" instead of "Active courses" with format and topics; one sentence explaining the Wiki | It is not obvious that courses are free; the Wiki graph looks like a tech demo without a sentence on what it is | #570 | idea. Alexey: this block was "very bad" as built |
| 1.6 | One label per action (Create free account, Subscribe, Join Slack); a one-line hint under CTAs; no Slack promise in the newsletter | Several similar CTAs with different words; unclear what "join" means | #549 | kept. Hint spacing under the hero buttons fixed |
| 1.7 | "Free courses vs Academy" comparison | Academy stripe at the bottom said nothing about why to click | #546 | kept. Eyebrows removed, free card titled "DataTalks.Club Zoomcamps", funding line "Bildungsgutschein (coming soon)" |
| 1.8 | Hero kicker naming the audience and 130,000+; course grid instead of carousel | Audience only visible in the subtitle; carousels hide content; the 130,000 figure was buried at the end | #570 | idea. Alexey crossed out the kicker (no eyebrows) |
| Top-5 #2 | "What is DataTalks.Club?" band: About intro, "Who's here" figures, founder story | The tour page already answers "what is this community"; reuse it on home | #554 | kept, placed after the three promises |

## Site-wide

| # | Idea | Why | Tried in | Status |
|---|---|---|---|---|
| 2.1 | Masthead in three groups (Learn / Community / Read); theme toggle as an icon | Ten equal links in one row; the "Dark" pill competed with "Sign up free" | #550 | kept. Alexey likes it |
| 2.2 | Footer columns (Learn / Community / Read / Stay in touch) | Footer was only legal links | #550 | review. Alexey: footer is ugly; redesign it in the polished style next |
| 2.3 | Softer borders, no shadow on archive list cards | Heavy borders everywhere flatten the hierarchy | #548 | kept |
| 2.4 | Hide empty states ("No show notes…") | Dead placeholder blocks | #532, #547 | kept |
| Wiki | One name, "Wiki", everywhere | Five names for one thing | #557 | kept |
| Top-5 #2 | /tour becomes /about | Clearer page name | #554 | kept |

## Courses and Academy

| # | Idea | Why | Tried in | Status |
|---|---|---|---|---|
| 3.1 | /courses as a landing page plus a filterable catalogue, benefits, reviews, one-column FAQ | Page was neither a catalogue nor a landing page; only 4 of 7 courses at the top | #551 | idea. Page back to the polished design; ideas listed one by one under "Courses catalogue (/courses)" |
| 3.2 | Course family landing: 11-block structure, guests do not see the LMS editions list | Landing and LMS were mixed | #563, #566 | review |
| 3.3 | Cohort page: one primary button, workspace links as tabs, personal statuses hidden from guests | Too many equal buttons | #563, #566 | review |
| Academy | Landing blueprint, week-by-week syllabus, facts strip, guides | Landing-page best practice (`_docs/design/landing-pages.md`) | 24 Nov redesign | review. Alexey: design is bad but the ideas are good; keep the ideas, rework the look |

## Courses catalogue (/courses)

On 25 Nov 2026 Alexey judged that #551 "changed too much" at once. The /courses
template and view went back to the pre-#551 design (as at `a7d8fa24^1`). The data
side stays: migration 0038 with the `Course.catalog_*` fields (topic, level, weeks,
hours a week, tools, project, fits), `courses/catalog_facets.py` and
`scripts/prod/import_course_catalog_facets.py`. The page does not show these fields
yet. A `?topic=...` link still loads the full page; the filter is ignored.

| # | Idea | Why (Valeriia, 3.1) | Tried in #551 | Status |
|---|---|---|---|---|
| C1 | One generic hero illustration ("learning a course") instead of 4 family covers | Only 4 of 7 courses were shown at the top, for no clear reason; one picture can show what our courses are about | `course-learning` illustration in the hero | idea |
| C2 | Subtitle names the outcome: finish with your own portfolio project | The headline is good; the subtitle should name the result, not only the format | New hero subtitle; CTAs to the catalogue and to the course-fit helper | idea |
| C3 | Benefits section (free forever, learn by doing, cohort, certificate, practitioners, open on GitHub) instead of the "7 zoomcamps / €0 / 100% open" tiles | The tiles do not say why our courses are good; numbers need context ("100% open" means nothing alone) | Six benefit rows with the 130,000+ community figure | idea |
| C4 | "How our zoomcamps work" moved up, right after the benefits | It answers "how does this work", which people need before they pick a course | Section moved above the catalogue | idea |
| C5 | Filter pills above the grid: topic, level, length, format/status, tools; selection kept in the URL; active chips with "Clear all"; hint when nothing matches | People choose a course by topic, length, tools, level; with 7 courses a row of pills is simpler than a sidebar; a shareable link like "ML for beginners" (Baymard) | Server-rendered pills plus JS, `?topic=mlops&level=beginner`, OR within a filter and AND across filters | idea |
| C6 | Sort by nearest start date | The soonest cohort is what most visitors can act on | Dated open registration first (soonest first), then running, then undated, then self-paced | idea |
| C7 | "Which course fits me?" helper (new to data / developer / already work with models), each button sets a filter | The question is hidden in the FAQ; a short guide helps people pick | Buttons above the catalogue naming the matching courses | idea |
| C8 | Every card has the same fields in the same order: status and start date, topic and level, length and hours a week, 3–4 tools, "You will build", "View course" | Cards are hard to compare when they show different things | Unified card field order, read from the `catalog_*` fields | idea |
| C9 | Reviews band with people's stories about our courses, results first | Stories with a result ("found my first job") work better than general praise; they are already in the site data | All published homepage and course testimonials, de-duplicated, in the shared story card | idea |
| C10 | FAQ in one column of folds | Two columns are harder to read in order; folds take less space, like on the course page | One column of shared `faq-fold` disclosures | idea |
| C11 | Closing CTA: "Did not find your course? Subscribe to hear about new cohorts" | The page ends without a next step for people who found nothing | Digest subscribe band at the end | idea |

## How to bring an idea back

1. Pick one row. Write down the smallest change that carries the idea.
2. Build it with the existing components and tokens; no new visual language.
3. Screenshot it (desktop and phone, light and dark) next to the current page.
4. Alexey decides; only then the next row.
