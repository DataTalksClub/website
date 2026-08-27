# Adding illustration assets

Homepage and other design-system illustrations are stored in
`core/static/core/illustrations/` as transparent WebP files. The drawing keeps its
own white fills; only the outer background is transparent. The consuming template
must treat decorative artwork as `alt=""`, with `decoding="async"`. Below-the-fold
artwork normally uses `loading="lazy"`; the homepage's paired light/dark files use
`loading="eager"` so a theme switch or a mobile scroll cannot expose an empty slot.

## Requirements

- ImageMagick, available as either `magick` or `convert`.
- The repository's `uv` environment.
- A source PNG (or another format ImageMagick can read), saved under `.tmp/` when
  it came from an issue or another external source.

## Prepare the image

Run the reusable processor from the repository root:

```bash
uv run python scripts/process_illustration.py \
  .tmp/illustration-sources/new-step.png \
  core/static/core/illustrations/home-new-step.webp
```

The source argument may also be a public HTTP(S) URL. URL downloads are cached under
`.tmp/illustration-sources/`:

```bash
uv run python scripts/process_illustration.py \
  "https://github.com/user-attachments/assets/<attachment-id>" \
  core/static/core/illustrations/home-new-step.webp
```

The default `5%` fuzz is intentional. Issue attachments can contain isolated,
nearly-transparent edge pixels. A strict `-trim` treats those pixels as artwork and
leaves a large transparent margin. The processor uses fuzz only to find the crop
rectangle; it does not threshold, recolour, or otherwise alter pixels inside that
rectangle. Lower `--fuzz` if a source has intentionally faint artwork at its edge;
increase it only after visually checking the result.

The output is written atomically, so an ImageMagick failure does not replace an
existing asset with a partial file.

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
hero's pointing gesture, and Step 3's open separated-finger hand.
```

For the dark pass, append:

```text
Render natively for the dark page: use a deep navy page-compatible ground and a
readable indigo watercolor cloud while keeping the foreground colours close to the
light anchor. The pixels outside the irregular cloud and figures must be
transparent, so the page surface shows through. No light-mode background,
checkerboard, rectangular canvas, edge seam, glow, halo, coloured fringe, or blue
aura around hands, bodies, or feet. Preserve the hero pointing gesture, exact Step
1 labels, and Step 3's open separated-finger hand.
```

Generate at the slot's aspect ratio. If the raw output needs a final canvas
normalisation, preserve its alpha channel and centre it without stretching:

```bash
RAW=.tmp/illustration-sources/home-step-2-dark.png
TARGET=core/static/core/illustrations/home-step-2-dark.webp
magick "$RAW" -alpha on -background none -resize 957x532 \
  -gravity center -extent 957x532 -define webp:lossless=true "$TARGET"
```

Replace the names and dimensions for the other slots. If the raw output has an
opaque background, a rectangular edge, a glow, or a coloured fringe, reject it and
regenerate it; do not flood-fill, filter, or mechanically recolour it into shape.
The final asset must pass this check:

```bash
identify -format '%f %wx%h %[channels] opaque=%[opaque]\\n' \
  core/static/core/illustrations/home-*-dark.webp
```

The expected dark output is `srgba`, `opaque=false`, with the dimensions in the
table above. Wire the pair through
`templates/core/_home_illustration.html`; the include owns the variant map and the
shared CSS owns visibility, so both themes keep one layout slot.

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
`_docs/design/design-5a.md`.
