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
Adopting them means adding the faces to the site's own static assets and deciding how
they are served; until then the system stacks stand in, and the rendered type is close
but not identical to the export.

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
