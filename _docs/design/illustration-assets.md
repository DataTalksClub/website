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

Trim the transparent margin and write the WebP directly with ImageMagick, from the
repository root:

```bash
magick .tmp/illustration-sources/new-step.png \
  -alpha on -background none -fuzz 5% -trim +repage \
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
marked as a light-anchor constraint. The approved light files must not be edited.

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

If any red-circled transition fails, reject the whole asset and regenerate it;
do not repair the edge with a CSS filter, flood fill, recolour, crop, or blur.
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
`_docs/design/design-5a.md`.
