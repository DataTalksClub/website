# Course journey starting-state illustration

Date: 2026-09-16. Generated with the built-in `image_gen.imagegen` tool; no
API key or CLI. The tool exposed no model selector or reliable backend version,
so the actual backend is unverified.

The reusable scene replaces the technology-specific `RAG? / Agents? / K8s?`
homepage artwork on course landing pages. It deliberately shows three neutral,
disconnected system pieces so each course's source-owned copy can describe its
own starting point without the image contradicting it.

## Light

- References: `home-stuck.webp`, `home-step-2.webp`
- Tool output: `/home/alexey/.codex/generated_images/01a0a479-3d2f-7991-9ca7-bf120c70bac4/exec-312c3800-47de-47b3-8c55-80cda8f00b0d.png`
- Raw: `.tmp/course-journey-step1-20260916/light/source-keyed.png`
- Raw SHA256: `02386e547a42c3f5d8748ef892ccde14d43a9ba4db38d7ee640f52d301544242`
- Final PNG SHA256: `6a493ac64ba3d214ad68a4dd9afebdd6169fc57ecfb6bd35e9af8322c5a48800`
- Installed WebP: `core/static/core/illustrations/course-journey-start.webp`
- Installed SHA256: `c9b54ac83078b371fb684739a61d38e9a00c3ce36fab7de5ab56a45af4760da2`

```text
Use case: illustration-story
Asset type: reusable Step 1 illustration for course landing-page learning journeys.
Input images: Image 1 is only the approved learner-at-laptop composition and watercolor style reference; remove all its written labels and technology-specific symbols. Image 2 guides the established DataTalks.Club drawing style, character proportions, palette, and clean watercolor edge.
Primary request: Show one friendly learner at a laptop, curious but uncertain about how several disconnected building blocks fit together. Above the learner are exactly three separate warm-white tiles: a simple database cylinder/source icon, a small navy-and-indigo gear/transformation icon, and a simple output card with three abstract horizontal lines. Use short broken/dotted connector fragments between the tiles so they clearly do not form a working system yet. The scene should communicate “I know some basics, but I cannot yet connect the whole system” and remain applicable to data engineering, machine learning, LLM, MLOps, AI development, and analytics courses.
Style/medium: clean friendly hand-drawn editorial watercolor; confident slightly irregular dark-navy outlines, opaque warm-white fills, forest green and indigo accents, broad restrained pale-lavender watercolor wash. Match the references without copying their exact cloud shape.
Composition/framing: compact centered square/near-square scene with generous margins; learner in the lower third, three tiles across the upper half, all shapes large and legible at 250px wide. Learner uses the laptop with both hands and does not point.
Scene/backdrop: Return the complete illustration with its own irregular pale-lavender watercolor blurb on a perfectly uniform opaque saturated #ff00ff exterior chroma backdrop reaching all four borders. No transparency simulation, checkerboard, white rectangle, gradient, or texture outside the artwork.
Constraints: exactly one learner, one laptop, three tiles, two connected arms/hands. No robot, no readable text, no letters, no acronyms, no numbers, no brand or technology logos, no Kubernetes mark, no RAG label, no presentation gesture, no checkmark, no completed end-to-end arrow, no extra limbs. Clean flat foreground fills; no JPEG blocks, ringing, grain, speckles, cellular facets, polygon networks, mottled fills, malformed hands, halos, or magenta inside the artwork.
```

## Dark

- References: accepted light candidate, `home-stuck-dark.webp` for palette only
- Tool output: `/home/alexey/.codex/generated_images/01a0a479-3d2f-7991-9ca7-bf120c70bac4/exec-23a59660-8171-4fc1-8281-063fc8964121.png`
- Raw: `.tmp/course-journey-step1-20260916/dark/source-keyed.png`
- Raw SHA256: `2f6d108174a99e32f86bab93a306e6564a695aa00c83840e51c8f315f199ce08`
- Final PNG SHA256: `fbac3d292f952d7c734e62dbd401c101c77ad54856c009ff5206f874b4a9e033`
- Installed WebP: `core/static/core/illustrations/course-journey-start-dark.webp`
- Installed SHA256: `b8c39754378e2d3395c05ce9dc77d61e157d867041d7305a93ea4dc1ed16ff51`

```text
Use case: precise-object-edit
Asset type: dark-theme companion for the reusable Step 1 course-journey illustration.
Input images: Image 1 is the exact accepted composition and registration anchor. Image 2 guides only the established dark-theme palette; do not copy its written labels, symbols, edge artifacts, or cloud silhouette.
Primary request: Redraw Image 1 as its true dark-theme companion while preserving the exact scene, positions, scale, three icon meanings, learner pose, laptop, broken connectors, canvas, and margins. Keep the database tile, gear tile, and abstract output-card tile exactly text-free. Use a deep indigo/navy watercolor wash that reads naturally on the site's dark page; use muted dark green for the shirt, clean warm-white face/tile/laptop interiors, navy ink, and restrained indigo/green accents.
Style/medium: clean friendly hand-drawn editorial watercolor matching Image 1, with crisp slightly irregular navy outlines and opaque clean foreground fills. Broad restrained watercolor variation only in the background wash.
Scene/backdrop: Return the complete dark illustration on a perfectly uniform opaque saturated #ff00ff exterior chroma backdrop reaching all four borders. No transparency simulation, checkerboard, dark rectangle, gradient, or texture outside the artwork. The dark watercolor edge must thin naturally into the key without a glow, hard contour, horizontal streak, or copied artifact.
Constraints: exactly one learner, one laptop, three tiles, two connected arms/hands. Preserve Image 1's subject and layout. No readable text, letters, acronyms, numbers, logos, technology marks, robot, presentation gesture, checkmark, completed end-to-end arrow, extra limbs, compression blocks, JPEG noise, ringing, grain, speckles, cellular facets, polygon networks, mottled foreground fills, halos, or magenta inside the artwork.
```

Both sources were finished with:

```sh
uv run --script .agents/skills/website-illustrations/scripts/finish_chroma.py \
  SOURCE_KEYED.png FINAL_DIR \
  --solid-key-chroma 230 --key-noise-alpha 0.05
```

The retained PNG and installed lossless WebP decode identically, contain both
opaque and transparent pixels, and have transparent pixels on all four borders.
