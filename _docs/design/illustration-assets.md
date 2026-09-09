# Adding illustration assets

Homepage and other design-system illustrations are stored in
`core/static/core/illustrations/` as transparent WebP files. The drawing keeps its
own white fills; only the outer background is transparent. The consuming template
must treat decorative artwork as `alt=""`, with `decoding="async"`. Below-the-fold
artwork normally uses `loading="lazy"`; the homepage's paired light/dark files use
`loading="eager"` so a theme switch or a mobile scroll cannot expose an empty slot.

## How assets are produced

### Always start from reference images

Every illustration generation and edit must receive reference images as actual
tool inputs. A filename mentioned in the prompt alone does not attach an image.
Use the built-in `imagegen`; this workflow does not use an API key or the imagegen
CLI.

1. Inspect the approved site artwork and the subject reference with `view_image`.
   Generate the **light version first**, passing those files through
   `referenced_image_paths`. Identify their roles in the prompt: drawing-style
   reference, subject reference, or composition anchor. For a course robot, use
   `home-step-2.webp` for style and the historical robot-reading course image for
   the subject; exclude the historical banner's lettering.
2. Review the light result's composition, dimensions and actual alpha channel.
   If its outer canvas is opaque or the returned alpha is unreliable, use the
   outer-background workflow below before accepting it. Do not simulate a soft
   watercolor edge by blurring, recolouring, or inventing an alpha mask locally.
   The lilac watercolor cloud is part of the drawing and stays. Keep that
   accepted light file as the fixed anchor. For corrections, pass the previous
   candidate as an edit target and state what must remain unchanged.
3. Generate the **dark companion second**, passing the accepted light file as
   the reference image. Tune the cloud and palette for the actual dark page
   through imagegen while preserving the light composition. Do not independently
   invent a new dark composition or manufacture one with a CSS filter.
4. Inspect both files on their real consuming surfaces, then capture the page
   in both themes. Save the reference paths, exact prompts, raw outputs and
   validation results under the project's `.tmp/illustration-sources/` while
   working; record the accepted recipe and final asset paths in the owning doc.

Use `referenced_image_paths` when all references are local files. Use
`num_last_images_to_include` only for references without local paths, choosing
the smallest recent-image count that includes the required references. Never
provide both mechanisms, and never run an illustration pass without either one.

### Verify transparency in the file, not the preview

A checkerboard in a preview can be painted into an opaque image. Asking for
transparency in a prompt does not prove that the returned file has alpha. Inspect
the raw file immediately, before generating the dark companion or wiring it into
the page:

```bash
identify -format '%f %wx%h %[channels] opaque=%[opaque]\n' \
  .tmp/illustration-sources/course-learning-light.png
```

Prefer native transparency. The file is ready for encoding only when it has an
alpha channel and `opaque=false`, the outer canvas is transparent, and the white
drawing fills remain opaque. Renaming a PNG to WebP, adding transparent padding,
or enabling an all-opaque alpha channel does not satisfy this check. Keep the
raw source and record whether transparency came from imagegen or local finishing;
a transparent final WebP alone does not establish native alpha generation.

### Remove only the outer backdrop when needed

If imagegen cannot produce reliable transparency, ask it for a solid chroma-key
backdrop and remove that backdrop locally. This is the fallback for a fully
opaque result, a fake checkerboard, or an unstable/haloed alpha edge. Do not
replace a generated edge with a blur or a hand-made alpha mask. The lilac
watercolor cloud is artwork: retain its shape, color, texture and soft edge.
Remove only the surrounding backdrop.

1. Use imagegen with actual reference images to produce the same drawing against
   one perfectly uniform key color absent from the artwork. Ask explicitly for
   "a single, perfectly uniform chroma-key background, #ff00ff, with no
   checkerboard, gradient, texture, or key-colored details inside the drawing".
   Saturated magenta (`#ff00ff`) is suitable for the green course robot; choose
   another key color only when magenta occurs in the artwork. Keep the cloud and
   all interior white fills unchanged.
2. Save the untouched output under `.tmp/illustration-sources/`. Work on a copy
   and remove only the key-colored pixels connected to the outer canvas. Preserve
   the watercolor cloud, robot, book, white interiors and their intended soft or
   crisp edges. Do not globally threshold the image, treat pale lilac or white
   as disposable background, blur the boundary, or copy alpha from another image.
3. Inspect the result on the actual light and dark page surfaces, including the
   cloud boundary at 200%. Check for remaining key-colored fringe, clipped ink,
   missing watercolor, holes in white fills, halos and visible canvas edges.
   Verify that no key-colored pixels remain, the outside is genuinely transparent,
   and the foreground registration matches the edit target. If key removal
   damages the artwork, revise the finishing or regenerate the keyed source; do
   not disguise damage by blurring or recoloring the drawing.
4. Accept the light image only after these checks, then generate its dark
   companion from that accepted light through imagegen. Finish the dark image's
   own outer key if needed and repeat the checks. Record the exact accepted
   command, parameters, input/output paths and inspection results in the owning
   illustration document; no particular finishing command is accepted merely
   because it worked on another image.

**`imagegen` produces the finished drawing.** Native outer transparency is
preferred; the keyed-background workflow above is the fallback. The built-in tool
saves PNG output; the site serves WebP. Lossless encoding and canvas sizing that
preserves alpha are allowed after the transparency and edge checks. Prefer the
requested canvas so resizing is unnecessary.

**Nothing recolours, filters, or derives a dark asset from a light one.** A dark
companion that was produced by inverting, tinting, or filtering a light bitmap is
wrong and must be regenerated, not corrected.

### Repairing a homepage cloud edge without redrawing the foreground

An explicitly requested homepage edge repair may replace the defective cloud behind an
approved drawing. Use that exact theme's original illustration as the registration
anchor and `home-step-2.webp` as the granular edge reference. Generate a separate
cloud for each composition; neither cloud pixels nor watercolor alpha may be
borrowed from another illustration. Keep the hero pair and the light Step 2
reference unchanged.

Whole-image edits can move lettering, people or props. For this repair, imagegen
may instead return only the anchored cloud layer. Preserve the original target's
foreground pixels separately, including its ink, enclosed fills and antialiasing,
and copy them back at the original canvas coordinates. A foreground selection
mask protects existing drawing pixels; it must never manufacture the watercolor
edge. Preserve original partial alpha as well as RGB. Do not resize or translate
the foreground, trim the source canvas, or regenerate its text or icons.

Prefer native alpha. For a generated chroma backdrop, remove only that backdrop
and its contaminated fringe. Check the actual key color: nominal magenta output
can vary and leave a faint rectangular plate after naive key removal. Neither
`opaque=false` nor transparent corners alone proves a clean edge. Inspect all
four borders and the complete boundary on the actual desktop and mobile surfaces,
including enlarged views. Reject pooled dark contours, bright rims, key spill and
clipping. Keep raw sources, prompts and finishing commands with the
[edge-repair generation record](references/blurb-edge-repair.json).

ImageMagick may encode the accepted drawing as lossless WebP, resize it without
stretching, center it on a transparent canvas, or trim surplus transparent margins
from an external source. It may also flatten throwaway edge-review previews.
Local removal of a generated outer key is also allowed as described above. These
operations must not recolor the artwork, remove the watercolor cloud, replace
interior fills, or borrow another drawing's alpha mask.

## Requirements

- The repository's `uv` environment.
- ImageMagick for encoding and inspection. This environment provides ImageMagick
  6's `convert` and `identify`; use `magick` in place of `convert` on ImageMagick 7.
  Existing examples below using `magick` also work with `convert` here.
- For an externally supplied source image, a copy saved under `.tmp/`.

## Prepare the image

First inspect the generated PNG. Continue after native transparency or approved
outer-key finishing passes review, with the cloud preserved and white drawing
fills opaque. For an accepted course image already at 1024 × 1024, encode without
resizing:

```bash
identify -format '%f %wx%h %[channels] opaque=%[opaque]\n' \
  .tmp/illustration-sources/course-learning-light.png

convert .tmp/illustration-sources/course-learning-light.png \
  -define webp:lossless=true \
  .tmp/illustration-sources/course-learning-light.webp

identify -format '%f %wx%h %[channels] opaque=%[opaque]\n' \
  .tmp/illustration-sources/course-learning-light.webp
```

If an otherwise accepted source needs the declared canvas size, use proportional
resizing and transparent padding. Do not use `!`, which can stretch the drawing:

```bash
convert .tmp/illustration-sources/course-learning-light.png \
  -background none -resize 1024x1024 -gravity center -extent 1024x1024 \
  -define webp:lossless=true \
  .tmp/illustration-sources/course-learning-light.webp
```

Lossless encoding avoids compression loss; resizing still resamples pixels.
Recheck the dimensions, alpha and actual page-edge previews after sizing. Copy
the reviewed WebP to its final static path only after those checks pass. Apply
the same encoding to the separately generated dark PNG; never derive its colors
or alpha from the light bitmap.

For an externally supplied image with excess transparent margins, trim from the
local copy and encode as lossless WebP:

```bash
magick .tmp/illustration-sources/new-step.png \
  -alpha on -background none -fuzz 5% -trim +repage \
  -define webp:lossless=true \
  core/static/core/illustrations/home-new-step.webp
```

Save sources fetched from an issue or another external location under
`.tmp/illustration-sources/` first, then trim from that local copy.

The `5%` fuzz is intentional. Issue attachments can contain isolated,
nearly-transparent edge pixels; a strict `-trim` treats those as artwork and leaves a
large transparent margin. Fuzz only finds the crop rectangle — it does not threshold,
recolour, or otherwise alter pixels inside it. Lower it if a source has intentionally
faint artwork at its edge; raise it only after checking the result visually.

Write to a temporary path and move it into place if you want the same atomicity the
retired `scripts/process_illustration.py` wrapper provided, so a failed conversion
cannot replace an existing asset with a partial file.

## Reproduce the homepage light/dark pairs

The homepage has four illustration slots. The approved light files are the
composition anchors; each dark file is a separate image-generation pass, not a CSS
filter or a recolour of the light bitmap.

| Slot | Light anchor | Dark companion | Canvas |
| --- | --- | --- | --- |
| Hero | `home-hero.webp` | `home-hero-dark.webp` | `1470x834` |
| Step 1 (`stuck`) | `home-stuck.webp` | `home-stuck-dark.webp` | `957x532` |
| Step 2 (`learning`) | `home-step-2.webp` | `home-step-2-dark.webp` | `957x532` |
| Step 3 (`shipping`) | `home-step-3.webp` | `home-step-3-dark.webp` | `957x532` |

### Review correction ledger

These decisions came from the illustration review and are part of the asset
specification. They apply to the regenerated dark companions unless explicitly
marked as a light-anchor constraint. The approved light files must not be edited
as part of that dark-companion generation. The explicit cloud-edge repair above
separately permits changes to light Step 1 and Step 3 backgrounds while preserving
their foregrounds.

- **Scope:** regenerate the hero and Steps 1–3 as four dark assets. Keep the
  approved light assets byte-for-byte unchanged and use them as the composition
  anchors. Do not roll back or redesign the previously approved hero composition.
- **Geometry:** keep the hero at `1470x834`; keep every step at the same
  `957x532` canvas as Step 2. Steps 1–3 must have equal width and height, with
  equal figure scale and no squashing or stretching.
- **Composition and density:** preserve each light anchor's shapes, forms,
  object positions, proportions, labels, and crop. Keep the composition calm and
  no busier than the light anchor; add no props, people, text, or decorative
  elements.
- **Hands:** use the approved Step 2/Step 3 hand-drawing style consistently in
  the hero and all steps. Avoid any fist-like hand, closed-fist silhouette,
  outlined/open hand substitution, or disappearing hand. In particular, Step 3
  must use the small filled black hand from the approved light Step 3 anchor in
  both themes (review reference:
  `/home/alexey/.pocketshell/attachments/git-dtc-website-4/20260827-135641-01-clipboard.png`).
  The hero's second hand must remain visible and connected to the figure.
- **Step 1 expression:** keep the approved relatable, calm expression. It must
  not look scared or frightened, and its hand style must match the other slots.
- **Step 3 figures:** keep the figures the same visual size as Steps 1 and 2;
  do not squash the scene. Match the approved Step 1/2 background treatment in
  the light anchor and the hero-like treatment in the dark companion.
- **Dark treatment:** generate natively for the dark navy page. Preserve the
  light anchor's cloud shape and form, but make the cloud subdued and blended
  into the dark page like the approved dark hero. It must not look like a
  light-background image placed on navy.
- **Cloud-to-page connection:** use the same soft watercolor blur/blending
  treatment as the approved dark hero for all four dark assets. The cloud edge
  should dissolve irregularly into the navy with no hard cutout or bright rim
  (review references:
  `/home/alexey/.pocketshell/attachments/git-dtc-website-4/20260827-151805-01-clipboard.png`
  and
  `/home/alexey/.pocketshell/attachments/git-dtc-website-4/20260827-151820-01-clipboard.png`).
  Repeat this treatment independently for each step; do not copy one identical
  cloud/blurb between Steps 1–3 or replace any step's own cloud shape.
  Recheck the circled left-edge transitions in the annotated comparison
  `/home/alexey/.pocketshell/attachments/git-dtc-website-4/20260827-152005-01-annotated-clipboard-20260827-132005.png`;
  those transitions must blend into the page without a luminous border or a
  hard dark cutout.
- **Transparency and effects:** keep the irregular cloud/figures on a
  transparent outer canvas. No glow, shine, halo, aura, drop shadow, blue/white
  fringe, bright plate, rectangular background, or visible edge seam around the
  cloud, figures, hands, or feet.
- **Dark accents:** use the approved dark palette as the visual target:
  `--green: #2f6d59`, `--green-bright: #3a795e`, `--green-deep: #9bc9ad`,
  `--indigo: #b0b7f5`, and `--indigo-soft: #3e4778`. Keep the foreground colors
  close to the light anchor while making the dark companion feel native to the
  dark palette.

Use the built-in `imagegen` tool with the matching light file supplied as the
reference image. Save raw outputs under `.tmp/illustration-sources/`; do not use a
browser screenshot, a previous dark render, or an unrelated illustration as the
anchor. Image generation is not bit-for-bit deterministic, so the anchor, prompt
contract, dimensions, alpha check, and screenshot review are the reproducibility
requirements.

Use this shared prompt, replacing `<slot>` and `<anchor>`:

```text
Create the <slot> illustration as a fresh companion to the attached approved
light-theme anchor <anchor>. Preserve the anchor's exact composition, crop, canvas
ratio, character poses, object positions, proportions, labels, line weight, and
hand-drawn watercolor brush style. Keep the white paper/screens, green clothing and
green accents, and dark ink crisp and readable. This is a fresh generation, not a
generic redraw and not a flat recolour. Do not add props, text, people, glow, halo,
aura, drop shadow, busy decoration, or a rectangular panel.
```

For the light pass, append:

```text
Render for the light page: a pale lavender watercolor cloud on a transparent outer
canvas. Keep the cloud irregular and softly painted, with no opaque white plate or
light rectangle outside it. Preserve the approved calm Step 1 expression, the
hero's previously approved composition with its visible second hand, and Step 3's
small filled black hand exactly as it appears in the approved light anchor.
```

For the dark pass, append:

```text
Render natively for the dark page: use a deep navy page-compatible ground and a
subdued indigo watercolor cloud that blends into the dark page like the approved
dark hero while keeping the foreground colours close to the light anchor. Use the
approved dark green and indigo accents from the correction ledger. The pixels
outside the irregular cloud and figures must be transparent, so the page surface
shows through. No light-mode background, checkerboard, rectangular canvas, edge
seam, shine, glow, halo, coloured fringe, or blue aura around hands, bodies, or
feet. Preserve the hero's full previously approved composition and visible second
hand, the relatable Step 1 expression and exact labels, and Step 3's small filled
black hand matching the light anchor.
```

Generate at the slot's aspect ratio. If the raw output needs a final canvas
normalisation, preserve its alpha channel and centre it without stretching:

```bash
RAW=.tmp/illustration-sources/home-step-2-dark.png
TARGET=core/static/core/illustrations/home-step-2-dark.webp
magick "$RAW" -alpha on -background none -resize 957x532 \
  -gravity center -extent 957x532 -define webp:lossless=true "$TARGET"
```

Replace the names and dimensions for the other slots. An opaque raw backdrop may
use the outer-key workflow above. The finished image must have no rectangular
edge, glow or coloured fringe; correct the key removal or regenerate the source
if it fails. Do not filter or mechanically recolour the drawing into shape.
The final asset must pass this check:

```bash
identify -format '%f %wx%h %[channels] opaque=%[opaque]\\n' \
  core/static/core/illustrations/home-*-dark.webp
```

The expected dark output is `srgba`, `opaque=false`, with the dimensions in the
table above. Wire the pair through
`templates/core/_home_illustration.html`; the include owns the variant map and the
shared CSS owns visibility, so both themes keep one layout slot.

### Dark cloud-edge review gate

Every dark-generation iteration must pass an edge review before it is wired into
the page. Do not judge the raw transparent file against the image viewer's white
or checkerboard background; judge it on the actual dark surfaces where it will
render. The hero sits on dark `--cream`/`--page` (`#13162a`); the step artwork sits
inside dark cards (`--card`, `#1b1f3a`) on the dark lavender band
(`--lavender`, `#232a52`).

For each target, create a temporary flattened preview on the relevant surfaces:

```bash
EDGE_DIR=.tmp/illustration-edge-review
mkdir -p "$EDGE_DIR"

magick core/static/core/illustrations/home-hero-dark.webp \
  -background '#13162a' -alpha background -flatten \
  "$EDGE_DIR/hero-on-page.png"

for NAME in home-stuck home-step-2 home-step-3; do
  magick "core/static/core/illustrations/${NAME}-dark.webp" \
    -background '#1b1f3a' -alpha background -flatten \
    "$EDGE_DIR/${NAME}-on-card.png"
  magick "core/static/core/illustrations/${NAME}-dark.webp" \
    -background '#232a52' -alpha background -flatten \
    "$EDGE_DIR/${NAME}-on-band.png"
done
```

Open the full-size previews and inspect the left cloud-to-page transitions marked
in the comparison reference
`/home/alexey/.pocketshell/attachments/git-dtc-website-4/20260827-152005-01-annotated-clipboard-20260827-132005.png`.
Zoom those areas to at least `200%` (or inspect the browser screenshot at native
size) before accepting an iteration.

| Acceptable | Reject and regenerate |
| --- | --- |
| The cloud keeps its own step-specific shape and the watercolor edge fades irregularly into the exact navy/card surface. | A bright rim, white/blue fringe, halo, shine, bloom, or aura surrounds the cloud or figures. |
| Transparency outside the artwork lets the page surface show through; there is no visible canvas boundary. | A hard cutout, straight seam, dark outline, rectangular/oval plate, or opaque background appears at the edge. |
| The cloud remains visible but subdued, with the same visual blending idea as the dark hero repeated independently for each slot. | The cloud looks like a light-mode asset placed on navy, disappears into the page, or uses one copied cloud/blurb for all steps. |
| Ink, white screens, hands, and feet stay crisp at the edge; the approved hand shapes remain intact. | Edge processing softens or clips the ink, creates a glow around hands/feet, or changes a hand into a fist/open outlined substitute. |

If any red-circled transition fails, reject the candidate. Revisit outer-key
removal if it caused the defect, or regenerate the source. Do not disguise a
damaged cloud edge with a CSS filter, recolour, crop or blur.
Repeat the flattened-surface check after every new generation and again after
the real-site screenshot check below.

Finally, take and read real browser screenshots at `1440x900` and `390x844` in both
themes. Wait for each image's `complete`, `naturalWidth > 0`, and `decode()` before
capturing; otherwise a screenshot can falsely show an undecoded blank below the
fold. Check the hero and all three steps for composition, equal step dimensions,
transparent edges, no glow/halo/rectangle, and exactly one visible theme variant.
Keep screenshots below `.tmp/`, then run:

```bash
DJANGO_ALLOW_ASYNC_UNSAFE=1 uv run pytest .tmp/luna-visual-check/verify.py -q
uv run pytest core/tests/test_homepage.py -q
```

## Wire it into a page

1. Choose a stable filename under `core/static/core/illustrations/`.
2. Add or update the variant map in the owning illustration include, such as
   `templates/core/_home_illustration.html`.
3. Keep decorative-image attributes and the surrounding semantic copy unchanged.
4. Do not commit the downloaded source or generated previews from `.tmp/`.

## Verify the result

Inspect the dimensions, format, and alpha channel:

```bash
file core/static/core/illustrations/home-new-step.webp
identify -verbose core/static/core/illustrations/home-new-step.webp \
  | rg 'Geometry|Type:|Alpha:'
```

Read the image against both a light and dark background, then run the focused page
tests. For homepage artwork, also check the desktop and mobile layouts because
natural image aspect ratios affect the climb-card rhythm:

```bash
uv run pytest core/tests/test_homepage.py -q
make test-playwright-core
```

The full illustration convention and accessibility contract are documented in
`_docs/design/design-system.md`.
