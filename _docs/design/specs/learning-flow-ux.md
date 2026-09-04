# Course learning flow — UX analysis

Walked 2026-09-02 against `http://localhost:8000` (curl, signed-out, no JavaScript). Compared LLM Zoomcamp 2026 and ML Zoomcamp 2026 (the live current edition; the brief’s `/2025` path is not the active cohort). Cited paths are from this repo. Where live HTML disagrees with current templates, that is called out.

This is a journey analysis, not a visual spec. It does not prescribe pixels.

---

## The loop (normative)

A Zoomcamp learner is not browsing a catalogue of homework forms. They are repeating:

```
pick a cohort
  → enter module N
    → work units 1…K in order
    → do homework N
  → enter module N+1
```

Projects and peer review sit on that same spine when the curriculum places them there (`courses/services/curriculum_flow.py:46-108`). Optional modules are labelled in titles; they are still in the numbered flow.

**Next** means the next step on that spine. **Previous** means the previous step. Homework-to-homework and “next assignment due” are syllabus/admin views, not the learning path.

---

## 1. What the learner is doing at each step

Observed signed-out. Authenticated behaviour is inferred from templates and tests.

### `/courses` — choose a cohort

**Intent:** “Which course do I join / return to?”

**Helps:** Active cards, dates, a due panel (`courses/templates/courses/course_list.html:393-400`). Clicking the card goes to the current edition.

**Obstructs:** The due panel is the next *assignment by due date*, not the next *lesson*. Live LLM card today: “Next assignment … Homework 2: Vector Search” while Module 1 content is still the work. That is `current_assignment_info()` returning the soonest future homework (`courses/views/course_homepage.py:60-74`). A returning learner is pointed at a form, not at unread units.

No personal “continue” for a signed-in student. I did not log in; this is from the template: the assignment panel is the same for everyone.

### Cohort page (`/courses/llm-zoomcamp/2026`, `/courses/ml-zoomcamp/2026`)

**Intent:** “Where am I in this edition, and how do I start / resume?”

**Helps:** Module list with each module’s terminal homework, due date, Open/Closed pill (`courses/templates/courses/course.html:502-548`). Specs for dates and homework count. This is a decent syllabus.

**Obstructs:** There is no Start or Continue. Live hero actions were only Leaderboard, Dashboard, Project submissions. Dashboard is cohort statistics, not “your next lesson.” Units are not listed; the clickable homework title is as strong as the module title, so skipping lessons is the path of least resistance. Signed-in “Your work” is homework-submission progress, not unit progress (`course.html:444-476`).

### Module page

**Intent:** “What is in this week, and which lesson do I open?”

**Helps:** Ordered lesson list, copy that homework comes after lessons (`module.html:172-205`), rail with the same list plus homework link.

**Obstructs:** The main list and the rail are the same ten/sixteen links twice. No due date or status on the homework card (those exist only on the cohort page). No Start/Continue on the first unread unit; the learner must pick a row. No next/previous *module*. Guest sees “Sign in to keep track…” with no progress numbers.

### Unit page

**Intent:** “Watch / read this lesson, then go to the next one.”

**Helps (correction to the brief):** Sequential unit nav **does exist** at the foot of the lesson (`unit.html:163-177`; live LLM `01-intro` → “Environment →”; last unit → “Continue to homework →”). Adjacent units are computed inside the module (`courses/views/unit.py:39-51`). Last-unit → homework is the right loop step (`courses/tests/test_unit_pages.py:190-202`). The rail marks the current lesson.

**Obstructs:**

- Next is easy to miss: it sits under a long article, labelled with the raw upstream title, not “Next lesson.” First unit has no Previous, so the row is a single right-aligned title button.
- Next never leaves the module except into *this* module’s homework. There is no “next module.”
- Rail sign-in `?next=` is the **module** URL, not the unit (`_module_rail.html:42-43`). Verified live: `next=/courses/llm-zoomcamp/2026/modules/01-agentic-rag` from the intro unit.
- Signed-in “Mark as read” under every rail row POSTs and redirects to the module overview (`courses/views/module.py:125`; `courses/tests/test_module_pages.py:157-158`). Marking the lesson you are on kicks you out of it. Inferred as painful; I did not authenticate.
- ML units also ship an upstream “Navigation” list in the markdown body (live `01-what-is-ml` ends with Course / Lesson / Next). Product nav and source nav compete.

### Homework page

**Intent:** “I finished the lessons; now submit this module’s work. Then start the next module.”

**Helps:** Deadline, questions, “Log in to submit” with `next=` back to this homework (live). Instructions on GitHub.

**Obstructs — this is the owner’s bug:**

Footer **Next homework →** goes to the next homework by due date (`courses/views/homework_context.py:75-97`, `_submission_page.html:186-204`). Live HW1 → `/homework/homework-02` (Vector Search), skipping Module 2’s units. Previous homework (on HW2) skips backward the same way.

The page has **no link to its module or any unit** (live HTML: zero `/modules/` URLs). Breadcrumbs are Courses / family / identifier (`_submission_page.html:147-161`) — the module is missing. No rail, no “lessons in this module,” no submitted/unsubmitted state for guests beyond the login callout.

GitHub instructions are a second, external homework. Fine as a reference; they should not be the only “how to do this” path.

---

## 2. Navigation model — what “Next” should mean

Treat the published `curriculum_flow` as one ordered tape:

`Module N overview? → Unit N.1 → … → Unit N.K → Homework N → (project if the flow says so) → Module N+1 …`

| You are here | Next | Previous |
| --- | --- | --- |
| Cohort syllabus | First incomplete step (module overview or first unread unit of the current module) | Courses index |
| Module overview | First unread unit, else first unit | Cohort syllabus |
| Unit (not last) | Next unit in this module | Previous unit, or module overview if first |
| Last unit | **This module’s homework** (already built) | Previous unit |
| Homework | **Next module’s first unit** (or that module’s overview). If the flow’s next item is a project, go there. If this is the last item, cohort page with a done state | **Last unit of this module**, not previous homework |
| After last homework / project | Cohort “you’re done” / certificate / wrapped — not a fake next | Last real item |

**Do not use homework-to-homework as primary Next.** Keep a way to jump the syllabus (cohort page already lists every homework). If operators still want homework hopping, make it a quiet secondary control (“All homework”) — not the primary pager.

**Primary control:** one pager at the end of the work, same placement on unit and homework: Previous (subtle) · Next (primary when it continues the loop). Label the action, then the destination: “Next lesson: Environment,” “Continue to homework: Homework 1,” “Continue to Module 2: Vector Search.” Raw titles alone do not say “this is sequential nav.”

**Secondary map:** the module rail (units + this homework + “N of K lessons”). It is a map, not a substitute for Next. It should appear on **module, unit, and homework** so the loop never drops its context. The cohort page remains the course-wide map (modules + dues + projects).

**Cohort page vs learning path:** keep homework rows for deadlines; add one primary “Continue” that follows the table above. Do not make the homework title the only obvious click inside a module row.

Honest note: the brief said unit pages have no next/previous. On this server they do. The real break is the **module boundary**: unit nav stops correctly at homework, then homework nav abandons the spine.

---

## 3. State the learner cannot see

| State | Where it exists today | Where it is missing |
| --- | --- | --- |
| Which unit I’m on | Rail `aria-current` / `is-current` | Homework page; cohort page (no units) |
| Units read / remaining | Rail “N of K lessons read” if signed in (`_module_rail.html:4-5`) | Guests; cohort page; homework page; module main list (Read/Unread only if signed in, `module.html:183-185`) |
| Homework due | Cohort row + homework hero | Module homework card; unit rail homework link; courses-index is due-only, not “this module” |
| Homework submitted / score | Cohort pills; homework page if signed in | Module/unit/rail; guests on homework only see login |
| What is next | Nothing persistent | Every page. Index “next assignment” is the wrong object |
| Resume point | Nowhere | Returning learners |

`.progress` already exists for “modules submitted” (`_design_system.html:2067-2080`, `course.html:460-475`). It counts homework submissions, not lessons. A learner who watched 15/16 units and has not submitted still looks like they have not started the module.

---

## 4. Sign-in

Progress tracking is the reason to sign in **while learning**. Submitting is the reason **on homework**. Those are two jobs; they should not share copy, and they should not be repeated.

**Today (guest, live):**

- Masthead: Log in / Sign up free (every page).
- Module + unit rail: sentence “Sign in to keep track of what you have read.” **and** button “Sign in to track progress” after the full unit list (`_module_rail.html:7, 41-44`). ML module 1 has 10 units, so the button lands “after 10 items”; LLM has 16. It is “after the list,” not a special 10-item rule.
- Homework: “Log in to submit this homework” (`homework.html:85-87`) — correct job, correct `next=` on the callout button.

**Recommendation:**

1. **One progress prompt**, in the progress line of the rail (replace the sentence with a short control). Do not repeat it under the list. Masthead stays for account; do not add a third course-page banner unless the person is trying to mark progress.
2. **`?next=` = current URL.** Rail must return to the unit, not `module_url`. Homework already mostly does this.
3. **Homework keeps a submit-specific prompt.** Do not reuse “keep track of what you have read.”
4. After sign-in, the progress line becomes “3 of 16 lessons read” and read-state is a small control on the current row, not a 2.75rem button on every row (that density problem is real; inferred from `_module_rail.html:24-35` and tests, not from a logged-in session).

---

## 5. Shared patterns (consistency)

All four surfaces extend `core/content_page.html` (cream header, lavender content, `.content-shell`). That is the only shared skeleton. Below the header they fork:

| | Cohort | Module | Unit | Homework |
| --- | --- | --- | --- | --- |
| Map | `row-list` + status pills + due | Custom `lesson-list` **and** rail | Rail only | None |
| Sequential nav | None | None | `.unit-navigation` | `.submission-navigation` (wrong target) |
| Deadline | Homework rows | Copy only | No | `homework-deadline` |
| Progress | Submission bar if signed in | Rail count if signed in | Rail count | Login callout |
| Primary CTA | Leaderboard / dashboard | Open homework | Next title / Continue to homework | Log in / Next homework |
| Breadcrumb parent | Family (repo); live still shows identifier as current) | Edition | Module | Edition — **skips module** |

**Share these, do not invent a fifth look:**

- Breadcrumb contract already in the design system: ancestors only, h1 names this page, muted 0.78rem trail (`_design_system.html:1009-1020`). Homework’s trail should include the **module**. (Breadcrumb size/redundancy is a known parallel fix; see below.)
- One **Continue** primary CTA using `core/_button.html`.
- One **module rail** on module, unit, homework (homework current-state on the homework row).
- **Status pills + spec/deadline** for homework wherever it appears (cohort row, module card, rail, homework hero).
- **`.progress` + `.mono-note`** for “k of n lessons” and, separately, “submitted / not submitted.”
- Sequential pager styles: unit and homework already almost share a dashed top rule + space-between; they should share a partial and the **same destination rules**.

Module/unit `shell-breakout` for the two-column reader is justified. Homework can use the same layout once it has a rail; until then it should not feel like a different product.

---

## 6. Known issues (others own — not solved here)

- Upstream markdown as escaped literal HTML (YouTube embeds, tables). Seen in spirit on ML units (thumbnail markup / competing source chrome). Do not mix with nav work.
- Relative image paths 404; some images are rewritten to GitHub raw, some are not.
- Breadcrumbs oversized and redundant. Live module/unit trails still include a current-page crumb (`aria-current="page"`). Current repo templates and tests already trim that (`courses/tests/test_module_pages.py:111-124`, `test_unit_pages.py:204-210`). Treat remaining trail work as that issue, except **add the module ancestor on homework** — that is navigation, not chrome cleanup.

---

## 7. Recommendations, by value

### P0 — Fix the spine (the owner’s complaint)

**What:** Replace primary homework Next/Previous with curriculum next/previous as in the table in §2. Last unit → homework stays. After homework → next module (first unit or overview). Previous on homework → last unit of *this* module.

**Why:** “Next homework” is a working control that teaches the wrong loop. This is the only change that matches how people actually take the course.

**Hard:** Medium. `homework_navigation_context` is due-date ordered (`homework_context.py:81-96`). Need a flow-aware neighbour helper from `CurriculumFlowItem` / `Module.terminal_homework`. Tests in `courses/tests/test_homework.py:154-189` currently lock the wrong behaviour and must change.

**Owner decision:** After homework, land on the **next module overview** or its **first unit**? Overview orients; first unit reduces clicks. Recommend **first unit** plus a crumb/rail back to the overview.

### P0 — Put homework back on the module

**What:** Homework breadcrumbs: Courses / family / edition / **module**. Footer Previous = last lesson. A rail (or a compact “In this module” list) on the homework page, with homework current. Optional one-line “You are submitting Homework 1 for Module 1.”

**Why:** Today homework is a dead-end form. Live page has no `/modules/` link at all.

**Hard:** Small–medium. Context already has `course` / `homework`; need `module` via `terminal_homework` reverse relation (`courses/models/curriculum.py:25-29`).

### P1 — One Continue on the cohort page

**What:** Primary “Continue” / “Start Module 1” resolving to the first incomplete spine step. Keep the syllabus rows, but do not let the homework title outrank the module.

**Why:** Signed-out live hero has no learning CTA. Index “next assignment” already sends people to homework.

**Hard:** Medium. Resume needs read-state + submission (signed-in) or “first unit of module 1” (guest). Guest Continue is easy; smart resume is the work.

### P1 — Same pager language on units

**What:** Keep foot nav; label “Next lesson: …”; previous on first unit → module overview; keep last-unit homework primary. Optionally a compact next at the end of the *video*, not only after the article — inferred: LLM intro is a long read after the embed.

**Why:** Nav exists but does not read as a lesson player. Owner thought it was missing.

**Hard:** Small.

### P1 — Sign-in lives with progress

**What:** Single rail prompt; `next=` = this page; homework keeps submit copy. Stop redirecting mark-as-read to the module (`module.py:125`).

**Why:** Duplicate copy, wrong return URL, and a read action that unloads the lesson.

**Hard:** Small.

### P2 — One map, not two, on the module overview

**What:** Overview: lesson list in the main column, homework card with due + pill, no duplicate rail — **or** rail only and a short intro. Prefer main list + homework card; rail is for *reading* pages (unit, homework).

**Why:** 10–16 links twice, and the rail’s sign-in block is noise on a TOC page.

**Hard:** Small. Owner decision: which of the two lists to keep.

### P2 — Show lesson progress on the cohort syllabus

**What:** Per module, “4/16 lessons” and homework pill. Use existing `.progress` / `.mono-note`.

**Why:** The syllabus already answers “when is homework due?” It does not answer “have I done the lessons?”

**Hard:** Medium (signed-in query per module; guests omit numbers).

### P2 — Courses index: do not pretend homework is the course

**What:** If the card shows a next step, prefer “Continue: Module 1, Introduction” for a learner, and keep due as secondary metadata. Or split “Next lesson” vs “Due soon.”

**Why:** LLM index currently promotes Homework 2.

**Hard:** Medium. `current_assignment_info` is assignment-only by design (`course_homepage.py:60-74`). Changing it affects every active card.

**Owner decision:** Is the index for **joining** (due dates, registration) or **resuming**? If joining, leave the due panel and put Continue only on the cohort page.

### P3 — Quiet source “Navigation” in unit markdown

**What:** Product owns prev/next. Hide or strip upstream Navigation sections when they duplicate the pager (ML `01-what-is-ml` has both).

**Why:** Two next links, different labels.

**Hard:** Medium; content-policy, easy to get wrong. Do not block P0.

---

## 8. Owner decisions

1. **Next after homework:** first unit of next module vs module overview vs project when the flow item is a project. (Recommend first unit; project if `curriculum_flow` says so.)
2. **Optional modules** (LLM 6–7): in the default Next chain, or skip until chosen? Titles already say Optional.
3. **Index card:** due-date assignment vs resume lesson. Product tension, not an engineering default.
4. **Module overview:** list in main vs rail vs both.
5. **Mark as read:** manual per lesson (today) vs auto-mark on view. Manual is honest; auto needs a decision. Either way, stay on the unit.
6. **Homework hopping:** delete primary Next homework, or keep as a secondary “Skip to next homework”? Recommend delete from the pager; syllabus remains.

Visual rules being debated elsewhere (accent rails, rail-as-card vs unframed chrome) are out of scope here. Do not wait on them to fix Next.

---

## 9. What I did not verify

- Signed-in session, mark-as-read, score, enrollment pills (templates + tests only).
- JavaScript deadline countdown (`.time-left`).
- Layout at phone width (CSS says rail stacks below content at `max-width: 52rem`, `_module_rail_styles.html:134-144` — so Next is above the rail on small screens, which is good; the duplicate sign-in is then *after* a long list).
- Project / peer-review insertion in the live LLM/ML 2026 flow (templates support it; I did not hit a project URL).
- Legacy non-module cohorts (homework tables without modules, `course.html:619+`).

---

## 10. One-line diagnosis

The course already knows the loop (modules own units and a terminal homework). Unit pages already walk units then homework. The homework page, the cohort CTA, and the courses-index due panel all walk **assignments by due date** instead. Align Next with the curriculum spine and keep homework inside its module; everything else is supporting chrome.
