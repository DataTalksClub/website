# Accessibility manual evidence — candidate template

Candidate fingerprint:

Base commit:

Worktree and branch:

Tester and date:

This template is intentionally blank until an independent tester performs the checks. Do not mark a
row passed from automation or from screenshot existence alone.

Automated prerequisite (not a manual screen-reader result): the full lane writes the named local
Chromium/HeadlessChrome version, Blink accessibility-tree protocol, and checked representative
states to `.tmp/screenshots/issue-65/named-browser-engine-evidence.json`. Record that artifact in the
engineering report only. A real screen-reader/browser pairing is still mandatory below and remains
pending when no named screen reader is available.

| Area | Browser / OS | Assistive technology and version | Viewport / zoom / mode | Route and state | Result | Evidence path | Defect or bounded exception |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Keyboard: skip, order, activation, Escape, restoration, no trap |  |  |  |  |  |  |  |
| Screen reader: landmarks, headings, navigation, tables |  |  |  |  |  |  |  |
| Screen reader: form instructions, errors, status, copy result |  |  |  |  |  |  |  |
| Normal desktop and mobile visual review |  |  |  |  |  |  |  |
| 200% browser zoom and 320 CSS-pixel reflow |  |  |  |  |  |  |  |
| WCAG text-spacing override |  |  |  |  |  |  |  |
| Contrast, focus visibility, and target spacing |  |  |  |  |  |  |  |
| Reduced motion and forced colors |  |  |  |  |  |  |  |
| Long, empty, success, validation, stale/conflict, denied, table/code states |  |  |  |  |  |  |  |
| HTML/plain email at narrow/wide widths and with images disabled |  |  |  |  |  |  |  |

Screenshot inspection log:

| Original image path | Shared pattern or changed surface | Synthetic/private-data check | Visual result | Notes |
| --- | --- | --- | --- | --- |
|  |  |  |  |  |

Unperformed or blocked checks:

Named real screen-reader/browser status (must say pending until actually performed):

Defects filed:

Tester conclusion:
