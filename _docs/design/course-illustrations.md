# Course-learning illustration

The planned course catalogue and course-family artwork is a shared robot reading
a book. It carries the existing Zoomcamp learning motif into the website's hand-drawn
style. Course names remain ordinary page text; the artwork contains no titles,
course-specific symbols, or registration details.

This document is the production brief. An asset is ready only after the checks
below pass. A plausible-looking preview is not sufficient.

## Deliverables and references

| Theme | Asset under `core/static/core/illustrations/` | Canvas |
| --- | --- | --- |
| Light | `course-learning.webp` | 1024 × 1024 |
| Dark | `course-learning-dark.webp` | 1024 × 1024 |

Every pass must attach actual reference images through the built-in imagegen
tool. Do not rely on filenames in prompt text and do not use an API key.
Create and accept the light version first, then tune that light version for dark
mode through a separate imagegen pass.

Use `home-step-2.webp` as the drawing-style reference for the first light pass.
The historical subject reference is the robot in
`DataTalksClub/datatalksclub.github.io:images/courses/zoomcamp.jpg`.
That reference includes banner text: use only its robot-reading motif.
Keep all existing homepage assets unchanged.

Generate the dark companion in a separate pass using the accepted light file
as its composition reference. Follow [illustration-assets.md](illustration-assets.md):
image generation produces the finished drawing. Prefer native transparency; if
the output is opaque, generate a solid magenta outer backdrop and remove only
that key locally. The lilac watercolor blur is part of the artwork and must stay,
along with the robot, book and white interiors. Encode the accepted PNG as
lossless WebP, preserving alpha. Proportional canvas sizing is allowed; recoloring,
CSS filtering and mechanically derived dark companions remain prohibited. The
linked guide provides the finishing review and 1024 × 1024 encoding checks.

## Light-generation prompt

```text
Create a finished light-theme DataTalks.Club course-learning illustration.
Reference 1 is the approved website drawing STYLE; reference 2 is the historical
robot-reading SUBJECT. Do not reproduce any banner typography.

One calm friendly robot with a softly squared head, a short antenna and green
side panels holds and looks down at an open blank indigo book with both hands.
Draw small simple rounded fingers, an opaque warm-white face and book pages,
forest-green forearms, confident slightly irregular navy ink outlines, and
restrained hand-painted watercolor texture. Match reference 1's simple visual
language. Place a quiet irregular pale lavender watercolor cloud behind the figure.

Square 1024x1024 canvas. Center one compact silhouette occupying approximately
85 percent of the canvas, with the entire antenna, book and hands visible.
No extra props, characters, letters, symbols, logos or watermark.

Use cream #fdfaf3, navy ink #1e2136, green #2b7a35, indigo #5a62c4 and pale
lavender #c9cdf2. Outside the cloud and figure, pixels must have real transparent
alpha. Keep white drawing fills opaque. Let the watercolor cloud fade irregularly
to transparency. No painted checkerboard, white rectangle, hard cutout, shadow,
glow, halo, luminous rim or colored fringe. Output a finished drawing with genuine
alpha transparency in the native PNG.
```

## Dark-generation prompt

Replace `<page-color>` with the computed background of the consuming header.
Use the live stylesheet as the authority if a prose token table differs.

```text
Create the dark-theme companion to the attached accepted light-theme
course-learning illustration as a fresh generation. The light file is the
composition anchor. Preserve its exact robot pose, expression, full antenna,
book and hand positions, proportions, crop, square 1024x1024 canvas, ink line
weight, hand-drawn watercolor style and blank book. Add no props or text.

Render natively for the DataTalks.Club dark page <page-color>. Retain opaque
warm-white robot face and book pages, readable green forearms and a muted indigo
book. Paint a subdued indigo watercolor cloud following the light anchor's own
cloud silhouette. Keep foreground colors close to the light anchor.

The irregular cloud edge dissolves softly into actual alpha transparency so the
page surface shows through. No light rim, colored fringe, glow, shine, halo,
aura, shadow, hard cutout, rectangular or oval plate, opaque background, or
painted checkerboard. Produce a finished drawing with genuine alpha transparency
in the native PNG. This is an independent generation, not a filtered, inverted or
tinted version of the light bitmap.
```

For an opaque result, use the candidate and the relevant style/composition
references in a new imagegen pass. Replace the native-transparency background
instructions with this outer-key request:

```text
Preserve the entire drawing, including its lilac or indigo watercolor cloud and
soft edge, and all white robot and book interiors. Change only the outer backdrop
to perfectly uniform saturated magenta #ff00ff. This key color must occur nowhere
inside the artwork. No checkerboard, gradient, added decoration or changed pose.
The watercolor cloud is artwork; do not remove it with the surrounding backdrop.
```

Remove only the outer magenta locally and inspect the cloud and interior fills
on the actual page surfaces. Keep the accepted light drawing as the anchor for
the separately generated dark companion.

## Accepted finishing recipe

The accepted light source is a built-in imagegen edit of the reviewed robot
composition: only the outer white backdrop was changed to magenta, while the
lilac watercolor cloud stayed. Its PNG is 1254 × 1254 RGB, so transparency comes
from local key removal. The [generation record](references/course-learning-generation.json)
contains the exact white-source, keyed-light and dark-companion prompts with
their actual reference and output hashes. The source chain is opaque composition
→ white light source → magenta light source → accepted light → generated dark.
Raw images remain under ignored `.tmp/`; image generation is not deterministic.
Preserve the keyed light source under
`.tmp/illustration-sources/course-learning-key-light.png` before running:

```bash
uv run --with pillow==12.3.0 --with numpy==2.5.1 --with scipy==1.18.1 python \
  scripts/finish_illustration_key.py \
  .tmp/illustration-sources/course-learning-key-light.png \
  .tmp/course-key-finishing/light \
  --fringe-chroma 12 --solid-key-chroma 100 --fringe-radius 20 --size 1024

uv run --with pillow==12.3.0 --with numpy==2.5.1 --with scipy==1.18.1 python \
  scripts/finish_illustration_key.py \
  .tmp/illustration-sources/course-learning-key-dark.png \
  .tmp/course-key-finishing/dark \
  --fringe-chroma 12 --solid-key-chroma 100 --fringe-radius 20 --size 1024
```

The helper identifies exterior magenta and its nearby fringe, estimates edge
coverage from neighboring paint and backdrop colors, then encodes a 1024 × 1024
lossless WebP. It preserves source RGB outside that fringe before resizing.
Fringe coverage is estimated from an opaque composite, not recovered original
alpha; resizing also resamples pixels. The helper rejects non-square sources
and inputs that already contain transparency. It uses transient dependencies
without adding image-processing packages to the website runtime.

Review `candidate.webp`, `alpha.png`, `preview-cream.png`, `preview-navy.png` and
`evidence.json` in the output directory before copying `candidate.webp` to the
static asset path. The accepted light has 31.28% fully transparent pixels,
25,357 pixels with partial alpha; the dark companion has 31.23% fully transparent
pixels and 20,199 with partial alpha. Both retain opaque face/book probes and
decode from WebP to exactly the normalized RGBA. These checks accompany visual
review of the retained cloud and interior fills; they do not replace it.

| Artifact | SHA256 |
| --- | --- |
| Keyed light source PNG | `4b72ccb0738ac0413daeb5d7b8c0287c51b4048ce893a2bfe576151fd7774158` |
| Accepted `course-learning.webp` | `e69d680752baae0b0d2efc0c9edc5574586c6a26c3e715c4c04b58a356d17826` |
| Keyed dark source PNG | `6ffd6a19f805e3d89a787ac5c398327eedc55dd86bb4acb6ac5f337c76539263` |
| Accepted `course-learning-dark.webp` | `39dfe89caf30f76265fe7083f366d90a7fef42dc667dbad80b681196c51dd14d` |

The dark companion was generated through imagegen using the exact accepted light
as its sole reference. Its own source passed the same finishing and edge checks;
the light mask was not reused.

## Acceptance and placement

Inspect the actual file format, dimensions and alpha channel before wiring either
image into a page. An RGB file with a visible checkerboard is an opaque image and
must be rejected. A filename or generation prompt claiming transparency is not
evidence of alpha. Keep rejected drafts under `.tmp/illustration-sources/`.

Inspect temporary flattened previews against each actual consuming surface.
Check the cloud boundary at 200% for seams, key-colored fringe, bright rims,
clipped ink and missing watercolor. Revise outer-key removal or regenerate if
this fails; preserve the cloud instead of hiding damage with filters or blur.

The course include must own both theme variants in one contained, square layout
slot. Both must use empty alt text, async decoding and explicit dimensions.
Above-the-fold images must load eagerly so changing theme cannot reveal a blank illustration.
At mobile widths, reduce the artwork before sacrificing the title, registration
actions or the visible course choices. Preserve database-owned copy and existing
campaign images.

After the pair passes the file and edge checks, inspect the catalogue and a course
family page at 1440 × 900 and 390 × 844 in both themes. Wait for image completion,
positive natural dimensions and `decode()` before screenshots. Where artwork is
shown, verify exactly one visible variant and a full robot/book silhouette. Where
artwork is omitted on mobile, preserve readable copy and course actions. Check for
horizontal overflow and useful access to the course choices in every state.
Screenshots belong under `.tmp/`.

## Banner reuse

The `dtc-social` template in `banner-generator` accepts `course_image_url` and
`course_image_dark_url` independently of a speaker's `image_url`. Copy only the
accepted pair into that repository's bundled template assets, preserving the
files. Record the source website commit when copying them so the two packages can
be updated together.

At 1200 × 630, use a contained illustration beside the course title. Keep the
artwork out of the title and footer areas. Titles stay rendered HTML text.
When artwork is absent, retain a full-width text layout. Articles use their own
editorial hierarchy, events use speaker portraits, and books use book covers.
Check the final course banner at native size and at feed scale in both themes.
