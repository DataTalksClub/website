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
image generation produces the finished drawing and its transparency. Encode the
accepted native PNG as lossless WebP for the site, preserving alpha. Proportional
canvas sizing is allowed; recoloring, CSS filtering, background removal, alpha
manufacture, and mechanically derived dark companions remain prohibited. The
linked guide provides the 1024 × 1024 encoding and verification commands.

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

## Acceptance and placement

Inspect the actual file format, dimensions and alpha channel before wiring either
image into a page. An RGB file with a visible checkerboard is an opaque image and
must be rejected. A filename or generation prompt claiming transparency is not
evidence of alpha. Keep rejected drafts under `.tmp/illustration-sources/`.

Inspect temporary flattened previews against each actual consuming surface.
Check the cloud boundary at 200% for seams, bright rims and clipped ink. Generate
again if this fails; do not repair the bitmap with filters or background removal.

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
