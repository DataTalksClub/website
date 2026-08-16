# Design 5a / 6x mockup source

The owner supplied the DataTalks.Club redesign as `DataTalks.Club UI mockups.zip`
(issue #179, re-shared 2026-08-16 with the HTML added). The zip holds seven PNG page
exports plus two bundler-produced HTML files.

The two HTML files are single-file bundles: the real document is embedded inside a
`<script>` as a JSON-escaped string, and every font and image is a separate base64
resource keyed by UUID. Opening them directly shows a loader, not the design.

What is checked in here is the **decoded document** from each bundle, which is the
authoritative source for markup and CSS:

| File | What it is |
| --- | --- |
| `datatalks-homepage.source.html` | Decoded homepage bundle — markup, inline CSS, component scripts |
| `datatalks-homepage.source.css` | The design stylesheet lifted out of that bundle, without the `@font-face` block |
| `datatalks-pages.source.html` | Decoded bundle for the courses, course, events and podcast pages |
| `datatalks-pages.source.css` | The design stylesheet from that bundle |

The PNG exports and the original zip are not checked in; they live under the
gitignored `.tmp/design-mockups/` on the build machine. Re-extract them from the
issue attachment when needed.

## Typefaces

The mockups use real webfonts, which the first homepage implementation approximated
with system stacks:

| Role | Mockup | First implementation |
| --- | --- | --- |
| Sans / headings | **Nunito** | `ui-rounded, "Segoe UI", system-ui, …` |
| Mono | **JetBrains Mono** | `ui-monospace, SFMono-Regular, …` |
| Serif | **Source Serif 4** | not used |

The bundles reference the font binaries by UUID, so the files themselves are not here.

**Decision (owner, 2026-08-16): the site keeps the typefaces it already ships.** That is
self-hosted Quicksand as `--font-sans` (`core/static/core/fonts/quicksand-latin-var.woff2`
with its OFL licence, added in `034c8b4`) over a rounded system fallback, and a system
mono stack. The mockup's Nunito, JetBrains Mono and Source Serif 4 are **not** adopted and
are recorded above for provenance only.

The consequence is accepted: rendered type is close to the export without matching it
exactly, and no measurement should be tuned in pursuit of a match that a different
typeface makes impossible.

## Reproducing the decode

The payload is the largest `<script>` body that JSON-decodes to a string beginning with
`<!DOCTYPE`:

```python
import json, re, pathlib

source = pathlib.Path("datatalks-homepage.html").read_text(errors="replace")
best = None
for match in re.finditer(r"<script[^>]*>", source):
    body = source[match.end() : source.find("</script>", match.end())].strip()
    if not body.startswith('"'):
        continue
    try:
        document = json.loads(body)
    except ValueError:
        continue
    if document.lstrip().startswith("<!DOCTYPE") and (best is None or len(document) > len(best)):
        best = document
pathlib.Path("homepage.source.html").write_text(best)
```
