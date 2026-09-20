# Course illustration dark companions

> Superseded on 2026-09-17 by
> [`dark-counterpart-audit-20260917.md`](dark-counterpart-audit-20260917.md).
> The assets recorded below were technically transparent but their saturated
> blue washes read as pasted plates on the real dark page. Keep this document as
> historical prompt evidence only; do not use its outputs as dark-style anchors.

Generated 2026-09-16 with the built-in `image_gen.imagegen` tool, using each
installed light course illustration as the sole composition reference. The tool
exposed no model selector or reliable backend identifier, so the backend version
is unverified. No API key or CLI image generation was used.

The six candidates were finished with:

```sh
uv run --script .agents/skills/website-illustrations/scripts/finish_chroma.py \
  SOURCE_KEYED.png OUTPUT_DIRECTORY \
  --solid-key-chroma 230 --key-noise-alpha 0.05
```

The installed WebP files are the finish script's lossless candidates. Decoded
RGBA matches the retained PNG, alpha spans 0–255, and every canvas border is
fully transparent.

| Family | Light reference | Tool output | Raw SHA-256 | Final PNG SHA-256 | Installed WebP SHA-256 |
| --- | --- | --- | --- | --- | --- |
| DE | `course-de-zoomcamp.webp` | `exec-1f06a370-596b-4a2e-9ca2-349bc68b2ea5.png` | `15366954fa74dbf2aabfa0294fb113e846091a0ab8b1bf80175f38ea002efd9a` | `d0558e7a94b673007d5d8908abc135da9f059edfc57db1dc8cc4a9fc81a35d5f` | `d2a2e0e7d79c3c199491e55dbe0d2ebb8e98bba2cb0c66471874403223e650ef` |
| AI Dev Tools | `course-ai-dev-tools-zoomcamp.webp` | `exec-a01f1316-9d7b-43fd-8f3f-faa88e79f521.png` | `e4168fb33a000bfa9f9b7db9b9755cc0696836b74ca941531cb63784b246b0ef` | `5f483d2ea3e92e11b0a4ea9e76c913c219d1fc1511c4f3f0146849046cdbd0ef` | `3983484ab5ef1079aa5a24f1aec0794dd6fd5f206aaf5793f381f7b07a09b506` |
| LLM | `course-llm-zoomcamp.webp` | `exec-32060a05-1d10-4ce5-9957-f29b8d3d6337.png` | `0fb335151eb26747f297a41ab58290a15f3b2f8a08ec773cd7bcca3f16aecba8` | `8f86d5e42d2add17a0565abacbd3242964fc3efa4c466fcda2bd1e73054bca14` | `a267d4935eca47d0952fce604b8d69d4565e2d7f0a02fd62c7c9f81228d0ea81` |
| ML | `course-ml-zoomcamp.webp` | `exec-e6fc58d8-fd5b-4047-b2d0-bc9642d25721.png` | `eb4803a8b84a1a7f8d34b66b1aba6c33f2dcb8008b004e6ac6cc917055cf5d0a` | `7feb9f107467dfbe0b7db10e471257c8395e57fdf24d04bcfcce52d979a2119e` | `954302f02d1363d9cc59499bb25a1e0d852604a89620f297ac04fd4c57ebeca9` |
| MLOps | `course-mlops-zoomcamp.webp` | `exec-a9b1af3e-cbba-4a3a-b8cc-16976291fa36.png` | `808a7fc0e987d49266712cd44d28275c017e5441a5a28b3caa237fcbdc80b2e0` | `d2643a52a9898068383f4719d168920f05e19a65a9454845afc8130392879055` | `7541430310041116edf0308513746f7a46c5a7fbac294c0cbda6a1bbd16bb510` |
| SMA | `course-sma-zoomcamp.webp` | `exec-e0fecb08-a68e-494a-82b3-ecc44567365d.png` | `54d825c91cc96427a1b1c11e8dcdf6ff09c919c656fe83f9fbdcf0d72ead9558` | `0e095b58bccf9a83a29612b47e8a1d23fa769c6f322e592ea297e8c457f4325d` | `9c122a5e63411449c50326d4918fac4c2dd6d9f77ecd1f26dd3d8234a2d416ee` |

All tool outputs are below
`/home/alexey/.codex/generated_images/01a0a479-3d2f-7991-9ca7-bf120c70bac4/`.
Raw and finished retained files are below
`.tmp/course-dark-companions-20260916/`.

## Prompt

Each call used the following exact prompt, with the bracketed scene sentence
replaced by the matching entry in the next section. The first three calls omit
the words `orientations`, `money, calculator,` and `upside-down objects`; those
extra constraints were included for the ML, MLOps, and SMA batch.

```text
Use case: precise-object-edit
Asset type: dark-theme companion for a course-specific landing-page illustration.
Input image: the exact accepted light-theme course illustration and composition anchor.
Primary request: Redraw the input as its true dark-theme companion while preserving the exact scene, subject count, poses, object identities, [orientations,] positions, scale, framing, square canvas, and generous margins. The scene is [SCENE]. Replace only the pale-lavender watercolor wash with a deep indigo/navy watercolor wash suitable for the site's dark page. Keep faces, paper, screens, cards, [money, calculator,] and machine interiors clean warm white; use muted dark green clothing, navy ink, and restrained indigo/green accents. Preserve the meaning and all connections.
Style/medium: clean friendly hand-drawn editorial watercolor matching the source, with crisp slightly irregular navy outlines, opaque clean foreground fills, and [a smooth] broad restrained watercolor [variation only in the background wash / wash only in the background]. [Foreground surfaces must be flat and clean.]
Scene/backdrop: Return the complete dark illustration on a perfectly uniform opaque saturated #ff00ff exterior chroma backdrop reaching all four borders. No transparency simulation, checkerboard, dark rectangle, gradient, texture, or shadows outside the watercolor artwork. The dark watercolor edge must thin naturally into the key without a glow, hard contour, streak, or copied artifact.
Constraints: preserve the input exactly; do not add, remove, mirror, rotate, relabel, or rearrange any person, hand, object, connector, icon, document, device, [chart, calculator, notepad,] [pipeline, or money / or money] element. No new text, letters, acronyms, numbers, logos, presentation gesture, extra limbs, malformed hands, [upside-down objects,] compression blocks, JPEG noise, ringing, grain, speckles, cellular facets, polygon networks, mottled foreground fills, halos, or magenta inside the artwork.
```

Scene insertions:

- DE: `data-engineering learner repairing a leaking pipeline from source blocks through gears to a database`
- AI Dev Tools: `AI-assisted software-development scene`
- LLM: `learner building a clear retrieval-augmented generation flow from documents through retrieval into an answer`
- ML: `machine-learning learner training and evaluating a model, with data, chart, and model system`
- MLOps: `learner operating a repeatable machine-learning lifecycle with experiment, deployment, and monitoring elements`
- SMA: `stock-market analytics learner counting money beside a chart, correctly oriented notepad, stacks, and calculator`
