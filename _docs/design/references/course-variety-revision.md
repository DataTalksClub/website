# Course illustration variety revision

Date: 2026-09-15. Built-in `image_gen.imagegen`; no API key or CLI. The tool
exposed no model selector or reliable backend version, so the actual backend is
unverified. The user replaced three repetitive presentation poses with distinct
actions: repairing a data pipeline, retrieving sources for a grounded LLM answer,
and counting money while reviewing market analysis.

All raw tool outputs are native 1254 × 1254 RGB PNGs. The finishing helper
removed only the connected magenta key and wrote native transparent PNG plus
lossless WebP. No JPEG or other lossy intermediate was used. The installed WebPs
decode exactly to their retained PNG counterparts, and all four canvas borders
are transparent.

## Data Engineering Zoomcamp

- Raw output: `.tmp/course-illustration-variety-20260915/raw/de.png`
- Raw SHA256: `2101094b4cf6fd8035352e2ae0cb67233d34c69c4ae69765c750d81fb3441d7e`
- Original tool output: `/home/alexey/.codex/generated_images/01a0a479-3d2f-7991-9ca7-bf120c70bac4/exec-e77d2ae6-db34-4145-94d4-065958106f18.png`
- Final PNG SHA256: `8986c13ab7fe7110cd51f039df8ee7269134f481c913fa3162e5d69064215634`
- Installed WebP SHA256: `daa84aaf6f0ae75d17681507b8db16351fa48a704085909ab00610511cc0d6a9`

The first people-free factory draft was rejected as too diagram-like. The
accepted revision uses a learner crouching over a leaking joint with both hands
on a wrench; it is a repair pose, not another pointing/presentation pose.

```text
Use case: stylized-concept
Asset type: square light-theme course illustration for Data Engineering Zoomcamp.
Input images: Image 1 is the approved DataTalks.Club watercolor drawing style. Image 2 is the new pipeline composition anchor. Preserve its clear left-to-right data-pipeline idea and palette, but simplify the machine and make the story about actively repairing the pipeline.
Primary request: Show one friendly learner crouching beside the lower-left pipe joint, tightening the joint with a clearly recognizable small navy wrench held in both hands. A tiny gap or leak at that joint is being fixed; two or three small indigo data blocks wait just before the repair. After the repaired joint, the connected pipe runs through one compact transformation box and into a neat cylindrical data warehouse on the right. The story must read instantly as fixing a data pipeline.
Style/medium: simple hand-drawn editorial watercolor matching Image 1; confident irregular navy outlines, opaque warm-white fills, forest-green clothing and pipe collars, indigo pipe/data accents, broad calm pale-lavender wash. Clean solid foreground fills with restrained organic watercolor only.
Composition/framing: compact centered square silhouette. Person is kneeling/crouching and looking at the pipe joint, not presenting. Hands stay on the wrench; no raised hand, no pointing finger. Keep the pipeline components large and uncluttered.
Scene/backdrop: soft irregular pale-lavender watercolor wash behind the complete scene. Outside it, one perfectly uniform saturated magenta #ff00ff chroma backdrop.
Constraints: exactly one person, two arms, two hands visibly connected, one wrench, one repair joint, one transformation box, one warehouse. All pipes connect plausibly except the small joint being repaired. No laptop, screen, presentation board, speech bubble, text, letters, numbers, logos, cursor, floating arrows, checkmark, robot, or extra limbs. No compression blocks, ringing, grain, speckles, cellular facets, polygon texture, malformed hands, detached wrench, hard cutout, white halo, or key-colored details inside the art.
```

Finishing command:

```sh
uv run --script .agents/skills/website-illustrations/scripts/finish_chroma.py \
  .tmp/course-illustration-variety-20260915/raw/de.png \
  .tmp/course-illustration-variety-20260915/final4/de \
  --solid-key-chroma 230 --key-noise-alpha 0.05
```

## LLM Zoomcamp

- Raw output: `.tmp/course-illustration-variety-20260915/raw/llm.png`
- Raw SHA256: `85917ea8cfd0d7eb6fc72b54518f8be6e24adf2a3dcf178de844243aba8ffae0`
- Original tool output: `/home/alexey/.codex/generated_images/01a0a479-3d2f-7991-9ca7-bf120c70bac4/exec-d3cfb1ca-9376-4b7f-a623-c5b72edb7141.png`
- Final PNG SHA256: `fa8eccdc04137d629d9a54ad0c10e352a162e24f852a16fd4ddaed0564146c01`
- Installed WebP SHA256: `bc3ff1aece5f03e73bd39f6d255b22a7ea8d049fcb2b8a0e9ffc5a6764d68c9c`

```text
Use case: stylized-concept
Asset type: square light-theme course illustration for LLM Zoomcamp.
Input images: Image 1 is the approved DataTalks.Club drawing style. Image 2 is the accepted current LLM composition and character reference. Keep its friendly learner-plus-robot feeling and palette, but make the retrieval-augmented-generation connection far clearer and remove the ambiguous loose-document handoff.
Primary request: Show a clear left-to-right RAG chain with no labels. On the left, one learner types a question on a laptop with both hands on the keyboard. In the center, a compact stack of three documents sits inside a large simple magnifying-glass/search frame; one document is visibly selected with a green outline. A thick continuous indigo connector flows from the laptop question into the document search, then from the selected document into the friendly robot on the right. The robot composes one clean answer card, and two tiny document-source badges are visibly attached beneath the answer card to suggest grounded citations. The question -> retrieve documents -> grounded answer sequence must be unmistakable without words.
Style/medium: friendly hand-drawn editorial watercolor matching Image 1 and Image 2; confident irregular navy outlines, opaque warm-white fills, forest green and indigo accents, broad calm pale-lavender wash. Clean flat foreground color with restrained organic watercolor only.
Composition/framing: balanced compact square silhouette. Learner remains seated with hands on keyboard, not pointing. Robot uses both hands on the answer card, not pointing. Search/document node is central and visually connects both sides.
Scene/backdrop: soft irregular pale-lavender watercolor wash behind the complete scene. Outside it, one perfectly uniform saturated magenta #ff00ff chroma backdrop.
Constraints: exactly one learner and one robot; exactly two learner arms/hands and two robot arms/hands, visibly connected; one laptop; three source documents; one magnifying glass; one answer card; two small source badges. No extra limbs, no raised pointing fingers, no presentation board, no loose arrows that do not connect nodes, no database cylinder, no text, letters, numbers, logos, watermark, checkmarks, speech text, or cursor. Keep robot anatomy coherent. Avoid compression blocks, ringing, grain, speckles, cellular facets, polygon texture, malformed hands, detached parts, hard cutout, white halo, or key-colored details inside the artwork.
```

Finishing command:

```sh
uv run --script .agents/skills/website-illustrations/scripts/finish_chroma.py \
  .tmp/course-illustration-variety-20260915/raw/llm.png \
  .tmp/course-illustration-variety-20260915/final5/llm \
  --solid-key-chroma 210 --key-noise-alpha 0.05
```

## Stock Market Analytics Zoomcamp

- Raw output: `.tmp/course-illustration-variety-20260915/raw/sma.png`
- Raw SHA256: `38bc52c84a2efd5c659e40e0271637b5068e7de2c542dc031ae842384fc1eafc`
- Original tool output: `/home/alexey/.codex/generated_images/01a0a479-3d2f-7991-9ca7-bf120c70bac4/exec-f9e5c15e-b9fc-4d72-8cc7-e8205ada1879.png`
- Final PNG SHA256: `302ba1004fff1efb05e6d687db6a240e0cb14869ef9781b0c3c9e521a440ef44`
- Installed WebP SHA256: `43eeeb6f94346e3aee7c8475284ed34882bb187445eb06baf109eba6622b363b`

```text
Use case: stylized-concept
Asset type: square light-theme course illustration for Stock Market Analytics Zoomcamp.
Input images: Image 1 is the approved DataTalks.Club watercolor drawing style. Image 2 is the current Stock Market Analytics palette/character reference. Replace its pointing-at-a-chart composition completely.
Primary request: Show one friendly learner seated at a simple table, carefully counting and sorting paper money with both hands. One hand holds a small fan of three plain banknotes; the other places one banknote onto one of two tidy stacks. A simple calculator and a small notebook with a tiny unlabeled green-and-indigo line chart sit on the table, making this about analysis and accounting rather than celebration. The action must read instantly as counting money.
Style/medium: friendly hand-drawn editorial watercolor matching Image 1; confident slightly irregular navy outlines, opaque warm-white fills, forest-green shirt, indigo and green banknote accents, broad calm pale-lavender wash. Clean flat foreground fills with restrained organic watercolor only.
Composition/framing: compact centered square silhouette. The learner looks down at the money and uses both hands; no pointing, no raised presenting hand, no large screen or presentation board. Tabletop and money are large enough to remain legible in a small card.
Scene/backdrop: soft irregular pale-lavender watercolor wash behind the complete scene. Outside it, one perfectly uniform saturated magenta #ff00ff chroma backdrop.
Constraints: exactly one person, two arms, two hands visibly connected, six or fewer banknotes total, two money stacks, one calculator, one notebook. Banknotes are generic rectangles with simple green marks only—no currency symbols, flags, portraits, readable text, numbers, logos or real-world currency design. No coins flying, cash rain, trophy, profit arrow, celebratory pose, laptop, presentation screen, pointing finger, robot, watermark or extra limbs. Avoid compression blocks, ringing, grain, speckles, cellular facets, polygon texture, malformed fingers, fused bills, hard cutout, white halo, or key-colored details inside the artwork.
```

Finishing command:

```sh
uv run --script .agents/skills/website-illustrations/scripts/finish_chroma.py \
  .tmp/course-illustration-variety-20260915/raw/sma.png \
  .tmp/course-illustration-variety-20260915/final4/sma \
  --solid-key-chroma 230 --key-noise-alpha 0.05
```

Pixel verification uses:

```sh
uv run --script .agents/skills/website-illustrations/scripts/verify_assets.py \
  .tmp/course-illustrations-20260915/png core/static/core/illustrations \
  --prefix course-
```
