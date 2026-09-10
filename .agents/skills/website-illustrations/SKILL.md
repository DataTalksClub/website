---
name: website-illustrations
description: Generate or repair this website's watercolor raster illustrations with imagegen, a removable chroma backdrop, and real-page edge checks. Use for homepage blurbs and related illustration assets; not for SVG icons or routine CSS changes.
---

# Website illustrations

Use the available `imagegen` skill and built-in image-generation tool. This skill
owns the repository's production workflow; asset dimensions, theme contracts and
the correction ledger remain in
[`_docs/design/illustration-assets.md`](../../../_docs/design/illustration-assets.md).
Paths below are relative to the repository root.

## Inputs and scope

- Inspect each exact target and the approved style reference with `view_image`.
  Attach them as actual tool inputs using `referenced_image_paths`. A filename in
  the prompt does not attach an image. If a reference has no local path, use the
  smallest applicable `num_last_images_to_include`; never provide both methods.
- Identify the target as the composition and registration anchor. For homepage
  edge repairs, light `home-step-2.webp` is the approved edge/style reference only.
  Each illustration keeps its own integrated blurb, silhouette and texture.
  Generate or edit the **complete illustration**, not a separate reusable cloud.
  A separate background may be a visual reference, never a shared production
  background, copied cloud, or borrowed alpha mask.
- Preserve the requested scope. Repairing light Steps 1 and 3 leaves dark
  counterparts, the hero, Step 2 and unrelated assets unchanged. A new light/dark pair starts
  with the light illustration; generate the dark companion from the accepted
  light composition only when the task includes it. Do not derive it with a
  filter or recolouring operation.
- Save original copies, hashes, exact prompts, raw outputs and working evidence
  in a task directory under `.tmp/`. Copy built-in output into that directory
  after generation; the tool's default save location is outside the repository.

## Generate against chroma

The repository default is an opaque, uniform saturated chroma backdrop, followed
by local removal of that backdrop. This follows the user's chroma preference:
do not request native transparency first or substitute a pure-white key. Earlier
transparency requests returned painted checkerboards, and white keying makes
pale watercolor and white drawing fills difficult to distinguish.

Use `#ff00ff` when absent from the illustration; select another saturated key
only if the actual artwork contains magenta. State these requirements explicitly:

```text
Edit Image 1, the exact composition and registration anchor. Image 2 guides
only the soft watercolor edge treatment; do not copy its background silhouette.
Return the complete illustration with its own distinct integrated blurb.
Change only the requested outer blurb border. Preserve all foreground drawing,
text, white fills, interior watercolor texture, colours, scale and coordinates.
Keep the complete original canvas and the blurb's own broad shape.
Outside the artwork use one perfectly uniform saturated #ff00ff chroma backdrop:
no transparency simulation, checkerboard, white paper, gradient or texture.
The watercolor edge thins naturally into that key through diminishing pigment
coverage, with no white underpainting, hard contour, halo or key-coloured details
inside the drawing. It must look smooth and clean on the actual light page,
without coarse speckles, jagged pixel noise, disconnected dots or clipped paint.
```

Adapt the edit clause to the requested scope; do not invent changes to the
composition. Generate each target independently using its matching anchor. For a
dark asset, request the approved dark watercolor palette and a clean edge on its
actual dark page/card background. The chroma-to-transparency process is the same
in both themes; a dark final asset does not retain an opaque dark backdrop.

## Preserve everything outside a border repair

An image-generation prompt alone cannot guarantee unchanged pixels. Before
finishing a border-only edit, define the permitted border band from that target's
original cloud geometry and protect its original foreground **and interior
watercolor**. Restore protected original RGBA at the exact source coordinates,
including partially transparent ink and antialiasing. Do not resize, translate,
trim or regenerate the preserved drawing.

The selection limits which pixels may change; it must not become the generated
watercolor alpha or manufacture its fade. Within the permitted band, the edge
comes from imagegen and chroma recovery. Retain a diff against the original and
verify exact equality of protected pixels, unchanged canvas dimensions and
foreground registration. Reject a visible join between the retained interior
and regenerated border; revise the candidate or selection within scope.

## Remove the exterior key and inspect

Inspect the raw file's dimensions, alpha and actual key uniformity first. A
checkerboard preview, transparent corners, or `opaque=false` alone proves little.
Measure all four outer borders: nominal magenta can vary and leave a rectangular
plate after simplistic removal.

Recover only the surrounding key connected to the exterior and its contaminated
fringe. Preserve enclosed whites, pale watercolor, foreground RGB and partial
alpha. Do not globally remove white/lilac pixels, borrow another image's alpha,
blur the boundary or recolour the drawing. Keep the generated gradual fade:
historical recipes with a `0.25` alpha cutoff are not a reusable default and can
harden the edge. Base any residual-key tolerance on the actual source and inspect
its effect on the faint pigment; do not remove visible noise by cutting off the
whole soft fade. If recovery damages the artwork, revise it or regenerate.

Encode the accepted result as lossless WebP at the original declared dimensions.
Inspect genuine alpha, all four canvas borders and the complete cloud boundary
on the actual consuming surfaces, including enlarged views: light assets on
their light page/card/band backgrounds, dark assets on their dark equivalents.
Reject key spill, white/coloured halos, dark contours, rectangular plates,
clipping, jagged edges and pixel noise. An alpha check cannot replace this visual
gate.

## Verify in the running page

Use the user's running host when supplied (for the homepage repair,
`http://localhost:8000/`); otherwise resolve the development target. Capture and
read before/after screenshots at desktop `1440x900` and mobile `390x844` and
`320x844` in the affected theme. Wait for image loading and `decode()` before
capture. Compare image dimensions/positions, foreground scale, clipping and the
edge against the real page background. Keep evidence under `.tmp/`.

Confirm unrelated assets remain unchanged. After acceptance, record the exact
references, prompts, raw/final paths, hashes, key-removal commands and parameters,
protected-pixel comparison and browser evidence in the owning generation record.
Historical recipes describe previous outputs; do not label a current attempt
accepted until its pixel and rendered checks pass.
