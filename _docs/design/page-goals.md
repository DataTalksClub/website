# Page goals

Every public page on DataTalks.Club has **one primary goal** and a clear set of
**roles** it plays to serve that goal. Before creating or changing any page,
check its entry here (or add one if introducing a new page type):

- **One primary goal per page.** Everything on the page exists to help the
  visitor complete that goal.
- **One primary CTA per page** (`.cta-primary`, green). It may repeat at the
  hero and closing band with the same label and destination, so a reader who
  scrolls does not have to scroll back.
- **Everything else is secondary or moved elsewhere.** Supporting paths
  (community join, self-paced materials, LMS workspace tools, archive links,
  cross-sell) use secondary styling (`.cta-secondary`, `.band-link`, tabs) or
  live on their own dedicated page — never as equal-weight buttons in one
  cluster with the primary CTA.

This document complements `_docs/design/design-system.md` (visual primitives and
page shell) and `_docs/design/landing-pages.md` (layout blueprints for Academy
and course landings).

---

## Page types

### 1. Homepage (`/`, anonymous landing)

The homepage is a **landing page** for first-time and returning signed-out
visitors. It is not a full directory or an LMS dashboard.

- **Primary goal:** Orient a visitor to what DataTalks.Club is and convert them
  into taking the first step — **exploring the free courses** (or **creating a
  free account** to join the community and cohorts).
- **Primary CTA:** One primary action in the hero and closing band; secondary
  actions (newsletter digest, Academy comparison, individual content links) must
  not compete as equal-weight primary buttons.
- **Roles the homepage plays:**
  1. **Immediate positioning (hero):** State who the community is for (data &
     AI practitioners and learners), what it offers (free hands-on courses,
     events, community), and scale (130,000+ community members) within the first
     screen.
  2. **Learning-by-doing narrative:** Show the progression from following
     tutorials to building and sharing real projects with community support.
  3. **Community activity overview:** Introduce what exists in the community
     (free courses, live events, podcast, articles, Wiki) so a visitor sees the
     breadth without being overwhelmed.
  4. **Free courses showcase:** Highlight active and upcoming free zoomcamps so
     learners can jump straight into a course.
  5. **Free courses vs Academy routing:** Clarify when to take free
     community-led courses versus the full-time intensive Academy, without
     making the free courses look second-class.
  6. **Social proof & trust:** Real graduate stories and sponsors, placed after
     the visitor understands what is offered.
  7. **Low-friction fallback:** Weekly newsletter digest for visitors who are
     not ready to join yet (clearly distinct from joining the community/Slack).
- **Signed-in homepage (`/`, `templates/core/member_home.html`):** Plays a
  different role — a personal workspace hub showing enrolled courses, due-soon
  homework/projects, and upcoming events.

---

### 2. Course pages

Course pages have three distinct layers. Mixing them on one page confuses
first-time visitors.

#### 2a. Course catalogue (`/courses`, `courses/templates/courses/course_list.html`)

- **Primary goal:** Help the visitor **choose the right free course** and open
  its course landing page.
- **Primary CTA:** `Explore the courses` (scrolls to the filterable catalogue).
  The closing fallback for visitors who did not find a match is a single
  newsletter subscribe action (`Subscribe to the digest`), with `Join Slack`
  strictly secondary.
- **Roles the page plays:**
  1. **Value proposition:** Make immediately clear that all zoomcamps are free,
     practical, project-based, and community-supported.
  2. **How zoomcamps work:** Explain the 4-step flow (register → watch & build
     weekly → submit homework → ship a peer-reviewed project for a certificate)
     before asking the visitor to pick a course.
  3. **Guided matching & filtering:** "Which course fits me?" shortcuts plus
     topic/level/tool/length/status filters and uniform course cards.
  4. **Social proof & objection handling:** Learner project gallery pointer,
     course-specific graduate reviews, and a single-column FAQ.

#### 2b. Course family landing (`/courses/<family>`, `courses/templates/courses/course_family.html`)

- **Primary goal:** **Get the person to register for the course** (register for
  the open cohort, apply when approval-gated, or register interest for the next
  cohort). For signed-in learners already enrolled in a cohort, the primary goal
  becomes returning to their cohort workspace (`Continue in the <id> cohort`).
- **Primary CTA:** One primary registration button (`courses/_family_primary_action.html`),
  repeated identically in the hero and closing panel (and in the cohort card of
  the join section).
- **What is secondary or moved elsewhere:**
  - **Course materials / self-paced route:** Secondary option (not equal-weight
    with cohort registration in the hero or closing cluster). Lives as a
    secondary card/link in the "How the course works" / "Join the course"
    section.
  - **LMS links (cohort editions, submission deadlines, homework/project
    submission links, leaderboard, dashboard):** Belong on the cohort LMS page,
    not mixed into the public landing for anonymous visitors.
  - **Community join (Slack):** Mentioned as a benefit of joining the cohort;
    not a competing CTA button in the course hero or closing cluster.
  - **Materials access policy:** Whether course materials stay public or go
    behind free registration is an open product decision (Hub intake `a05132cb`,
    PR #563). Materials are currently public; do not gate them without an
    explicit decision.
- **Roles the page plays:**
  1. **Outcome & key facts (hero):** Course title, concrete outcome, key facts
     strip (free, next cohort date, format, syllabus size, weekly time, docs),
     and the single primary registration CTA.
  2. **Fit check:** Starting point and prerequisites ("Who it's for") so the
     visitor can quickly decide if the course matches their level.
  3. **Progression & overview:** 3-stage journey, overview, and live outcome
     numbers (cohorts, sign-ups, projects, graduates).
  4. **How the course works:** Clear distinction between joining a live cohort
     (primary) and studying self-paced (secondary).
  5. **Syllabus overview:** Module titles and tangible outputs without LMS
     submission clutter.
  6. **Project, certificate & graduate proof:** Final project summary, passed
     learner projects, certificate preview, and graduate stories for this
     course.
  7. **Objection handling & closing conversion:** Single-column FAQ and closing
     registration panel.

#### 2c. Cohort LMS page (`/courses/<family>/<cohort>`, `courses/templates/courses/course.html`)

- **Primary goal:**
  - For **unregistered visitors** landing on an open or archived cohort:
    **register for the cohort** (or the next edition).
  - For **enrolled learners**: **complete the cohort's modules, homework, and
    projects** on schedule.
- **Primary CTA:** At most **one** primary button in `.course-actions`
  (register for the cohort / next edition when not enrolled, or open course
  materials when already enrolled/registered).
- **What is secondary or moved elsewhere:**
  - **Workspace sections** (Overview, Course materials, Leaderboard, Dashboard,
    Project submissions, Edit course profile, Download Certificate) sit in a
    secondary navigation strip (`.cohort-tabs`), never as a wrapping cluster of
    5–7 equal-weight buttons alongside registration.
  - **Personal status badges** (`Not submitted`, `Already scored`, status
    column) belong to signed-in learners only, never shown to anonymous guests.
- **Roles the page plays:**
  1. **Enrollment / registration entry** for unregistered visitors.
  2. **Learner progress summary** (score, submitted modules) for signed-in
     learners.
  3. **Cohort schedule & assignments** (modules, homework deadlines, projects,
     timezone/calendar feed).
  4. **Workspace navigation** via tabs to materials, leaderboard, dashboard,
     and submissions.

---

### 3. Events

#### 3a. Events index (`/events`, `templates/public/events.html`)

- **Primary goal:** Help the visitor **find an upcoming live event** and open
  its detail page to register (or browse past event recordings).
- **Primary CTA:** Opening an upcoming event card; view toggles
  (Upcoming / Past) and calendar subscription are secondary controls.
- **Roles the page plays:**
  1. **Upcoming schedule:** Chronological list of upcoming live streams,
     webinars, and workshops with date, speakers, and event type.
  2. **Archive discovery:** Switch to past events and recordings.
  3. **Calendar subscription:** Secondary link to subscribe to the events
     calendar.

#### 3b. Event detail (`/events/<slug>`, `templates/public/event_detail.html`)

- **Primary goal:** **Get the visitor to register for the upcoming event** (or
  **watch the recording** for a past event).
- **Primary CTA:** `Register` submit button on the event registration form
  (`.cta-primary`).
- **What is secondary:** Event Q&A link, add-to-calendar, YouTube/recording
  links, and related content use secondary controls (`.cta-secondary` /
  `.band-link`).
- **Roles the page plays:**
  1. **Event overview:** Title, date/time in the visitor's timezone, event type,
     and what will be covered.
  2. **Registration conversion:** Embedded registration form with minimal
     friction.
  3. **Speaker credibility:** Speaker bios and links.
  4. **Participation & follow-up:** Q&A link and recording embed once available.

---

### 4. Podcast

#### 4a. Podcast hub (`/podcast`, `templates/public/podcast_hub.html`)

- **Primary goal:** Help the visitor **pick an episode to listen to** or
  **subscribe on their preferred podcast platform**.
- **Primary CTA:** Platform subscription links / opening an episode row.
- **Roles the page plays:**
  1. **Show positioning:** What the podcast is about (conversations with data &
     AI practitioners who ship) and where to subscribe.
  2. **Episode browsing:** Season navigation and clean episode list.

#### 4b. Episode detail (`/podcast/<slug>`, `templates/public/podcast_detail.html`)

- **Primary goal:** **Play/listen to the episode** (or read its summary,
  timestamps, and transcript).
- **Primary CTA:** Listen/watch on the visitor's platform (embedded player /
  platform links).
- **What is secondary:** Show notes, transcript expansion, guest bio, related
  episodes, and Wiki knowledge-graph connections. Empty sections (e.g. missing
  show notes or missing Wiki connections) are omitted rather than rendered as
  empty placeholders.
- **Roles the page plays:**
  1. **Playback & platform access.**
  2. **Scannable reference:** Summary, show notes, clickable timestamps, and
     searchable transcript.
  3. **Guest context & discovery:** Guest bio, related episodes, and Wiki
     connections.

---

### 5. Academy

The Academy is the paid / Bildungsgutschein-funded, full-time intensive program
arm of DataTalks.Club. Its layout blueprint is in `_docs/design/landing-pages.md`.

#### 5a. Academy home (`/academy/`, `academy/templates/academy/home.html`)

- **Primary goal:** **Get the prospective student onto the Academy waitlist**
  (or into the right program page to evaluate fit).
- **Primary CTA:** Waitlist CTA (`.cta-primary`, repeated at hero and closing
  waitlist panel). Program track cards use `.cta-secondary`.
- **Roles the page plays:**
  1. **Positioning & key facts:** Explain what the full-time intensive Academy
     is, who it is for, and key facts (start, format, language, funding).
  2. **Program selection:** Present available tracks with secondary links and
     program comparison.
  3. **Funding & trust:** Explain Bildungsgutschein funding (hiding any
     unfinished sections), free info sessions, guides/recaps, and FAQ.
  4. **Waitlist conversion:** Single closing waitlist form.

#### 5b. Academy program landing (`/academy/<program>`, `academy/templates/academy/program.html`)

- **Primary goal:** **Get the visitor to join the waitlist for that program.**
- **Primary CTA:** Single waitlist action (`.cta-primary`) at hero (scrolls to
  the closing waitlist form) and closing panel.
- **Roles the page plays:**
  1. **Outcome & credibility (hero + key facts):** Frozen H1, transformation
     lede, instructor credibility, and spec strip.
  2. **Qualification:** Explicit "Who it's for" and "Who it's not for" cards.
  3. **Curriculum proof:** Overview, concrete outcomes, week-by-week syllabus,
     capstone project, and interview preparation.
  4. **Logistics & conversion:** Instructor bio, daily schedule, FAQ, and
     closing waitlist form.

---

### 6. Supporting community & content pages

- **About / Tour (`/tour` or `/about`):** Primary goal = explain what
  DataTalks.Club is and route newcomers to either free courses or creating a
  free account.
- **Blog / Articles (`/blog`, `/blog/<slug>`):** Primary goal = deliver
  practical technical reading; secondary closing pointer to related courses or
  newsletter.
- **Book of the Week detail (`/books/<slug>.html`, `templates/public/book_detail.html`):**
  Primary goal = **get the book**, with the author's answers to the community's
  questions as the proof. Primary CTA: `Get the book` (`.cta-primary`, the
  record's first link) in the head and again after the Q&A. The book's other
  links are compact `.cta-secondary` under the summary. Roles: (1) the book —
  cover, title, author chips, date and answered count; (2) the summary; (3) the
  Q&A read as an interview — asker and question, then the author's answer under
  the author's person chip, at the reading width.
- **Wiki (`/wiki/...`, canonical name "Wiki"):** Primary goal = let visitors
  explore concepts, episodes, and practitioners in the knowledge graph.
- **Slack (`/slack`) and Newsletter (`/newsletter`):** Single-purpose
  conversion pages with one action each (`Join Slack` and `Subscribe to the
  digest`), kept clearly distinct from each other and from `Create free
  account`.

---

## Audit of Valeriia's 19 Nov 2026 site feedback (page-goal lens)

Source: `_docs/audits/2026-11-19-site-feedback.md` (PR #531, merged as
`cefa8ef`, parent triage `77ae0a61`, close-out tracker `5de5486a`).

Every item is mapped below to its owning Hub intake, PR/status, and page-goal
consistency check. Work owned by in-flight intakes is not duplicated here;
page-goal gaps are tracked via Hub intake notes.

| Feedback item | Summary | Hub intake | PR / status | Page-goal consistency check |
|---|---|---|---|---|
| **1.1–1.3** | Home: fewer background colours, softer band transitions, green reserved for actions, simpler illustrations, landing-style typography & direct step titles | `ecbf122a` | PR #552 (open, blocked on external CI fix) | Consistent with homepage landing goal; reserving accent green strictly for primary actions reinforces single-CTA hierarchy. Note appended on `ecbf122a` to keep one primary CTA destination across the homepage. |
| **1.4, 1.5, 1.8** | Home: section order (benefits up, testimonials down), community activity overview, "Free courses" framing, Wiki blurb, "130,000 people" higher, hero audience clarity | `12bb4d60` | Merged (`c5bdb49e`, PR #570) | Consistent with homepage landing goal: single `.cta-primary` destination (`Explore free courses`) in hero and closing band; featured cohort, Academy comparison, and closing account signup use `.cta-secondary`. |
| **1.6 / Top-5 #1** | One canonical name per action across the site; separate newsletter from Slack; single closing CTA | `d9f4b1aa` | PRs #549, #553 (merged) | Consistent with page-goal rule (one clear label per action, no mixed newsletter+Slack promise, single closing CTA). Live check tracked by `5de5486a`. |
| **1.7** | Academy on home: "Free courses vs Academy" comparison section | `cef19730` | PR #546 (merged); CTA aligned in `c5bdb49e` | Resolved: `#academy-comparison-heading` Academy card now uses `.cta-secondary` (`Explore the Academy`), matching `Explore free courses`. |
| **2.1–2.2 / Top-5 #5** | Grouped top navigation, icon theme toggle, selective Academy/Tour stripes, wider footer | `90c2df89` (+ `cef19730`) | PRs #550, #546 (merged) | Consistent: grouping nav and removing stacked Academy+Tour stripes prevents competing CTAs at the foot of landing pages. |
| **2.3, 2.5** | Softer borders/shadows on archive list cards; consistent illustration style | `d06e75aa` | PRs #548, #543 (merged) | Consistent: lowers visual weight of archive lists so primary actions stand out. |
| **2.4 / Top-5 #4** | Hide empty states (podcast show notes, Wiki KG empty blocks, Academy Bildungsgutschein "coming soon") | `fbc09ce1` | PR #547 (merged); podcast empty-state strings removed | Consistent: no dead placeholder blocks distracting from page goals. |
| **Top-5 #2** | Make `/tour` the "About" page and reuse its blocks on home | `2eea31bb` | PR #554 (open) | Consistent: clarifies homepage orientation role and gives About a single newcomer-onboarding goal. |
| **Top-5 #3** | One canonical name ("Wiki") across the site | `a373202f` | PR #557 (merged) | Consistent: removes naming confusion across homepage, nav, and episode pages. |
| **3.1** | `/courses` catalogue: benefits, single hero illustration, "How our zoomcamps work" higher, filters + uniform cards, reviews, 1-col FAQ | `bedef4ea` | PR #551 (merged) | Consistent with `/courses` goal (pick a course). Hero has 1 primary CTA (`Explore the courses`) + 1 secondary (`Which course fits me?`); closing has 1 primary fallback (`Subscribe to the digest`) + 1 secondary (`Join Slack`). |
| **3.2–3.3** | Course landing (`/courses/<family>`) vs cohort LMS (`/courses/<family>/<cohort>`) split; single primary header button + tabs on cohort page; hide guest personal statuses; materials access policy undecided | `a05132cb` | PR #563 (open; materials stay public — Alexey undecided) | **Directly addressed with AC2:** registration is the single primary CTA on course pages; materials/community/LMS links are secondary or moved to tabs/cohort layer; materials remain public (not decided here). |
| **Close-out** | Live conformance close-out across all 19 Nov items | `5de5486a` | Open | Updated via Hub note with this mapping and the `home.html` multi-`cta-primary` follow-up. |
