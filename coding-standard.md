# Coding standard

## Scope

These rules apply to new and materially changed handwritten code. Adding this
standard does not require retroactive cleanup of untouched code.

Everything under Required practices, Templates, Refactoring, and Tests and
repository tools is mandatory. Design targets guide how those requirements
are met. Generated files, vendored code, migrations, and framework-mandated
signatures may follow the constraints of their owning tool or framework.

## Required practices

### Design and ownership

- Keep code DRY. Search for similar logic, services, helpers, template tags,
  components, and partials before creating an implementation.
- Reuse or extend the existing owner when behavior has the same semantics.
  Consolidate repeated business rules and presentation patterns, but do not
  force behavior with materially different semantics into one abstraction.
- Give each business rule one clear domain owner. Views, APIs, jobs, and Studio
  must call that owner rather than duplicate its policy.
- Keep views and API handlers thin: parse input, authorize, call domain logic,
  and format the response.
- Put related validation, writes, and side effects inside an explicit
  transaction boundary when they form one state transition.

### Readability and control flow

- Prefer explicit control flow over clever expressions.
- Do not use ternary expressions.
- Limit a comprehension to one simple, single-loop transformation with no
  filter or nesting. Use a named loop or helper for anything more complex.
- Use guard clauses and extracted helpers to avoid deeply nested conditionals.
- Choose names that state intent. Write comments to explain constraints or
  reasons; do not comment what the code already says.

### Size limits

- Keep new handwritten functions at or below 30 lines and source files at or
  below 300 lines.
- Do not grow an existing function or file that already exceeds its limit.
  Split it along cohesive responsibilities when materially changing it.
- Make an exception only when tool, framework, or cohesion constraints require
  one, and document the reason.

### Configuration, errors, and trust boundaries

- Read runtime-configurable values through the typed parse helpers in
  `website/settings/`. Do not introduce direct runtime configuration reads.
- Catch specific exceptions. If a broad defensive catch is unavoidable at an
  external boundary, explain why and log enough context and traceback to
  diagnose it.
- Validate and authorize on the server. Treat external and user-provided data
  as untrusted, escape or sanitize HTML, and never log secrets.
- Prevent avoidable N+1 queries and paginate collections that can grow without
  a fixed bound.

## Templates

- Keep business logic and data shaping in Python. Templates may render prepared
  values and use simple presentation conditionals and loops.
- Search for an existing owning partial or template tag before adding markup
  for an established pattern.
- Use `{% include %}` for repeated visual patterns so one partial owns the
  design and behavior stays consistent across pages.
- Pass explicit context to reusable partials. Keep feature-specific differences
  outside the shared partial when their semantics differ.
- Follow the component owners and template rules in
  [`_docs/design/design-system.md`](_docs/design/design-system.md).

## Refactoring

- Identify the current behavior and ensure one authoritative test covers it
  before changing structure.
- When coverage is missing, add the behavior test first and prove it passes
  before and after the refactor.
- Apply the same rule to template refactors. Cover the user-visible behavior or
  rendered contract at the appropriate Django or Playwright layer before
  extracting, moving, or consolidating template markup.
- Keep behavior-preserving refactors separate from intentional behavior changes
  so failures and review scope remain clear.

## Tests and repository tools

- Write one authoritative behavior test at the cheapest layer that proves the
  contract. Every assertion must fail when the behavior is broken.
- Use `uv` for Python commands and dependency management.
- Run the blocking lint gate with `make lint`, and the staged advisory report
  with `make lint-advisory`.
- Run the relevant test subset with
  `uv run python scripts/ci.py test-ci` (or `test-core`), as defined by the
  repository workflow.

## Design targets

- Meet the size limits by extracting cohesive responsibilities into clear
  helpers or modules. Do not create arbitrary fragments just to meet a number.

## Operational references

- Contributor and repository rules: [`AGENTS.md`](AGENTS.md)
- Issue lifecycle and delivery process: [`_docs/PROCESS.md`](_docs/PROCESS.md)
- Lint commands and policy: [`_docs/lint.md`](_docs/lint.md)
- App and service boundaries:
  [`_docs/architecture/app-boundaries.md`](_docs/architecture/app-boundaries.md)
