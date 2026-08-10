# Accessibility verification

The website accessibility baseline combines shared server-rendered patterns, a deterministic
Playwright/axe gate, and an independent manual review. Automated results are necessary, but they do
not establish WCAG conformance by themselves.

## Register a route or state

Every product issue that adds or materially changes a user-facing state must add a `CriticalState`
to `core/accessibility_registry.py`. Give it a stable identifier, product group, rendered fixture
surface, and an executable scenario key that proves the route, authorization, privacy, and business
contract. Set `js_required`, `core_smoke`, or `route_contract` only when those properties apply.

Add the corresponding deterministic `Surface` and state assertion to the scenario executor in
`playwright_tests/test_accessibility.py`. The complete matrix resolves every scenario key, runs each
real workflow, and compares the exact set of reported states with the registry. A state is reported
only after its distinct setup, behavioral assertion, and comprehensive page scan; route contracts
are reported only after their status and destination are asserted. The test never deduplicates by
rendered URL.

Fixtures must use isolated SQLite records, source-derived public slugs, synthetic copy, and the
repository's factory/runtime namespace. They must not call email, provider, AWS, or other network
services. A denial or redirect whose accepted response is deliberately plain text can remain a
route contract without pretending it is a rendered page.

The registry and fixture map fail closed: duplicate identifiers, missing rendered surfaces,
unclassified code-owned templates, missing behavior-test references, and incomplete exception
records fail the test suite.

## Run the gates

Use the uv-backed targets from the repository root:

```console
make test-accessibility
make test-playwright-core
```

`make test-accessibility` selects both the representative core smoke and the complete scheduled/full
matrix. For focused diagnosis, use:

```console
DJANGO_SETTINGS_MODULE=website.settings.test DJANGO_ALLOW_ASYNC_UNSAFE=true \
  uv run --frozen pytest playwright_tests/test_accessibility.py \
  -m 'accessibility and core' -v

DJANGO_SETTINGS_MODULE=website.settings.test DJANGO_ALLOW_ASYNC_UNSAFE=true \
  uv run --frozen pytest playwright_tests/test_accessibility.py \
  -m 'accessibility and full' -v
```

The harness injects the locally installed axe-core 4.12.1 payload from the pinned
`axe-playwright-python==0.1.8` package. It never downloads test code at runtime. The explicit checks
cover landmarks/headings, skip behavior, keyboard order/traps, computed focus, status/error
relationships, value preservation, target size/spacing, overflow, dates/timezones, media
alternatives, text spacing, zoom/reflow, reduced motion, forced colors, and JavaScript-off public
reading. The home-page event selection is frozen at `DEFAULT_FROZEN_AT`, and each evidence PNG is
accepted only when a second immediate capture has identical bytes.

The full lane also reads the platform accessibility tree from the named local
Chromium/HeadlessChrome 145 browser through Blink's `Accessibility.getFullAXTree` protocol. It
checks representative landmarks, headings, navigation, table, form-error, date/timezone,
transcript/media, and polite copy-status semantics. The exact runtime version and checked states are
written to `.tmp/screenshots/issue-65/named-browser-engine-evidence.json`. This is reproducible
browser-engine evidence, not screen-reader evidence and not a WCAG conformance claim.

The transactional-email fixture renders every active definition in
`course_management/datamailer_templates/definitions/registry.py` with that definition's maintained
example context. It wraps the exact current subject/HTML fragment for document-level checks and
validates the exact current plain-text alternative. It does not add a message purpose or send an
email.

## Add a bounded axe exception

Fix the page whenever possible. If axe is demonstrably wrong or remediation would break a safer
accepted contract, add one `AxeException` in `core/accessibility_registry.py` with all of:

- the exact axe rule;
- the exact selector emitted for the affected node;
- one exact registered state identifier;
- a concrete reason automation is wrong or remediation is unsafe;
- an accountable owner; and
- a future expiry date.

Blanket selectors, impact filtering, disabled WCAG rules, and unexplained exclusions are not
accepted. The exception test rejects missing fields, broad selectors, unknown states, and expired
records. There are no accepted exceptions in the issue-65 baseline.

## Manual gate and evidence

A tester who did not implement the candidate must complete
`_docs/accessibility/manual-evidence-template.md`. Record the exact candidate fingerprint, date,
browser/OS, viewport or zoom, assistive technology and version, route/state, result, evidence path,
and defect or approved exception.

The named real screen-reader/browser rows must remain unpassed until a tester actually performs
them. In particular, the Chromium/Blink tree artifact above must never be copied into the assistive
technology column as if it were NVDA, JAWS, VoiceOver, Orca, or another real screen reader.

Store screenshots only under `.tmp/screenshots/issue-65/`. Use synthetic data, open every original
image, and reject evidence containing an email address, profile value/link, provider payload,
registration row, token, credential, or one-time secret. The engineer report must say that keyboard,
screen-reader, contrast, and visual inspection remain unperformed until the independent tester has
actually recorded them.
