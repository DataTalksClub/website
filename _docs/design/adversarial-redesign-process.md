# Adversarial UI/UX redesign process

This is the repeatable process used for issue #237. It is intended for substantial
page-family or design-system work where visual quality, interaction quality, and
responsive behavior need an independent acceptance gate. Routine styling fixes do
not need this full loop.

## 1. Freeze scope and exclusions

Create a dedicated branch and write down:

- the page families and user tasks being redesigned;
- exact routes or templates that must not change;
- product behavior, destinations, content, and compatibility contracts to preserve;
- representative desktop, mobile, dark-mode, zoom/reflow, keyboard, and reduced-motion
  states;
- the evidence location below the project-local `.tmp/` directory.

Translate subjective goals into observable properties. Examples include one clear H1,
no document overflow, a 44px interactive-target floor, visible 3px focus, readable
desktop measure, primary content before secondary navigation on mobile, semantic table
markup, honest CTA wording, and valid destinations.

## 2. Build realistic review data

Audit real interfaces with realistic but fully synthetic data. For each page, document:

- route and actor;
- lifecycle, permission, content-density, empty, error, and denial states;
- why the state is needed;
- the synthetic records and persona that enable it;
- exact build, launch, and authenticated-browser setup;
- safety boundaries for any allowed interaction.

Course reviews must distinguish curriculum presentation, URL compatibility, and content
provenance instead of calling every older-looking route “legacy.” Cover legacy and module
curricula, active and completed cohorts, registration, dashboards, modules and units,
assignments, peer review, leaderboards, and empty states when the product supports them.

Review databases, browser storage, screenshots, and manifests stay under `.tmp/`. Use
reserved synthetic identities, deny provider/network/job side effects, and allow only the
exact bounded mutations needed to render validation or interaction states.

## 3. Establish an independent acceptance contract

Before implementation, an adversarial designer inspects the current rendered product,
design-system authority, representative content, and task flows. The designer publishes a
finite contract covering:

- hierarchy, alignment, spacing, density, typography, palette, and components;
- navigation clarity, information architecture, action prominence, feedback, and state
  comprehension;
- desktop/mobile reflow, content extremes, overflow, and reading order;
- keyboard focus, target sizes, contrast, reduced motion, and dark mode;
- functional destinations and content fidelity;
- consistency across every in-scope page family.

The reviewer remains independent from implementation and cannot approve source-only
evidence or the implementer's description.

## 4. Implement against rendered evidence

Give one implementer ownership of overlapping source files. The implementer:

1. inspects existing tokens, shells, primitives, routes, and tests;
2. reuses shared design-system structures where appropriate;
3. fixes the finite findings without expanding product behavior;
4. renders every affected viewport and state with representative data;
5. opens and inspects the artifacts;
6. runs focused functional, accessibility, and browser checks;
7. leaves a frozen candidate for independent review.

Read-only parallel investigations may map routes, fixtures, invariants, and regression
points, but they do not share implementation ownership.

## 5. Iterate with exact verdicts

The designer reassesses the whole contract and attempts to falsify the candidate. Every
round ends with exactly one verdict:

- `REVISE`: a prioritized finite list. Each failure includes route, viewport/state,
  rendered evidence, why it matters, and the observable correction.
- `ACCEPT`: explicit confirmation that all criteria were checked and no blocking or
  material UI/UX issue remains.

Send every `REVISE` verdict to the same implementer. Re-render all affected states and
reassess prior passes for regressions. Continue until `ACCEPT`; “looks better” or approval
with material caveats is not completion.

## 6. Commit accepted slices and integrate safely

Commit coherent slices after independent acceptance, using focused subjects and issue
references. Before merging:

- reconcile the branch with current `main` if it has advanced;
- resolve overlapping changes in favor of the combined current contracts, not either
  historical side wholesale;
- rerun the affected Django, browser, accessibility, formatting, system, and migration
  checks on the integrated source;
- confirm explicit exclusions have no unintended diff;
- record the final reviewer verdict, artifact paths, commands, counts, commits, and
  intentionally unchanged scope.

Merge locally with `--no-ff` only after the integrated candidate remains accepted. Push or
deploy only when separately authorized.

## Issue #237 reference implementation

The durable route/state/data contract is
`_docs/testing/issue-237-review-state-matrix.md`. Scratch evidence is below
`.tmp/adversarial-design-review/`. The final independent gate checked legacy and module
course states, docs, individual-event Q&A, Slack, representative public/auth regressions,
desktop/mobile/dark/reflow/focus behavior, and explicit homepage and events-list
exclusions.
