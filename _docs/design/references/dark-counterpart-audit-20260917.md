# Dark illustration companion correction — 2026-09-17

## Scope and outcome

The filename audit found all 13 light WebPs paired, but visual review on
`#13162a` showed that nine non-home companions were not acceptable dark assets.
Their saturated cobalt washes read as closed pasted plates. The four accepted
homepage companions remained unchanged and were the visual authority for nine
fresh replacements:

- `course-ai-dev-tools-zoomcamp-dark.webp`
- `course-de-zoomcamp-dark.webp`
- `course-journey-start-dark.webp`
- `course-learning-dark.webp`
- `course-llm-zoomcamp-dark.webp`
- `course-ml-zoomcamp-dark.webp`
- `course-mlops-zoomcamp-dark.webp`
- `course-sma-zoomcamp-dark.webp`
- `tour-community-dark.webp`

Campaign banners, portraits, sponsor logos, media-kit examples and lesson
screenshots are not theme-switched watercolor illustrations and remain outside
this pairing contract.

## Tool and model

Generated with the built-in `image_gen.imagegen` tool, without an API key or
CLI. Official documentation checked before generation described GPT Image 2 as
the state-of-the-art API image model and ChatGPT Images 2.5 as the newer ChatGPT
image experience. The built-in tool exposed no model selector or reliable
backend version, so the actual backend is **unverified**.

- <https://developers.openai.com/api/docs/models/gpt-image-2>
- <https://openai.com/index/introducing-chatgpt-images-2-5/>

## References and prompt

Each generation attached its own accepted light WebP as Image 1 and
`home-hero-dark.webp` as Image 2. Image 1 was the composition/registration
anchor; Image 2 guided only low-saturation dark palette and edge integration.

The eight first-pass accepted replacements used this exact prompt, substituting
the recorded subject sentence:

```text
Use case: style-transfer
Asset type: dark-theme website illustration companion.
Input images: Image 1 is the exact approved light composition anchor. Image 2 is the accepted homepage dark illustration and the PRIMARY authority for smoky low-saturation wash color, low contrast, compact geometry, and the way pigment disappears into the page. Do not copy Image 2's scene, objects, geometry, cloud silhouette, labels, or horizontal artifacts.
Create a native dark companion to Image 1. Preserve [SUBJECT]. Preserve exact meaning, subject count, anatomy, relative positions, scale, square framing, and breathing room. Redraw natively; do not mechanically recolor.
Background requirement: do NOT make a blue cloud. Replace only the pale light wash with a compact subdued smoky charcoal-slate-indigo atmosphere immediately behind and between the foreground, dominated by #1d2138, #232a52 and muted #30354f, with restrained #3e4778 only in broad diffuse variation. On the exact #13162a page it must feel like a subtle atmospheric continuation, not a separate colored object. The outer third becomes darker and increasingly transparent until the boundary is imperceptible. No pale edge pixels, blue bloom, closed scalloped/oval/rectangular plate, or huge empty watercolor geometry. Leave generous transparent page around the compact art.
Foreground: crisp slightly irregular navy ink, clean opaque warm-white faces/screens/paper, muted forest green, restrained indigo. Preserve all hands, limbs, lines and meaningful symbols. No additions or text.
Reject conditions: cobalt/royal/electric blue, bright violet/lavender, luminous rim, feathered halo, glow, hard cutout, closed plate, huge empty wash, copied cloud, page-colored opaque panel, coarse speckles, disconnected dots, jagged noise, malformed anatomy.
Output contract: COMPLETE square illustration on a perfectly uniform opaque saturated #ff00ff chroma backdrop across all four borders. No transparency/checkerboard. The compact dark watercolor thins into the key through diminishing pigment coverage without white underpainting, bright contour, magenta inside the art, or colored fringe. Clean lossless PNG, 1254x1254 if supported.
```

The exact subject substitutions were:

- Data Engineering: `the exact data-engineering pipeline repair scene: one learner repairing the leaking connection, source blocks, pipeline, transformation gears, database, all poses and object positions`
- Journey start: `the exact journey-start scene: one uncertain learner at a laptop beneath exactly three disconnected tiles (database, gear, abstract result), all dotted/broken connections, poses and positions`
- Generic learning: `the exact friendly reading robot scene: one robot looking down at the open blank indigo book, complete antenna, both attached hands and arms, exact crop and scale`
- LLM: `the exact LLM retrieval-and-grounded-answer scene: one learner, one friendly robot, source documents, magnifying/retrieval symbol, answer panel, both robot arms attached to different shoulders and holding the same document`
- Machine Learning: `the exact machine-learning training and prediction scene: one learner, laptop, example-point chart with decision boundary, model block, input tiles and prediction card`
- MLOps: `the exact MLOps deployment and monitoring loop: one learner at laptop, monitoring screen, model cube, server stack and directional arrows`
- Stock Market Analytics: `the exact stock-market analytics scene: one learner at the table, chart with rises and falls, strategy-result sheets, stacks of abstract banknotes and calculator, with every hand and object preserved`
- Tour: `the exact community scene: exactly four diverse peers around one round table, exactly two laptops, one notebook and pen, one mug, two overlapping abstract speech bubbles, every identity, pose, hand, chair and table part preserved`

Their accepted keyed sources and finishing reports are retained in
`.tmp/dark-illustration-audit-20260917/generated/`. The original built-in tool
outputs remain in generator run `01a0b10d-a762-7742-b11c-51aec7d482d5`.

AI Dev Tools required two rejected trials, then this exact targeted correction
of the second keyed source:

```text
Use case: precise-object-edit
Asset type: dark-theme website illustration edge and palette correction.
Input images: Image 1 is the exact keyed candidate to correct. Image 2 is the accepted homepage dark illustration and is the visual authority for its smoky, LOW-SATURATION, low-contrast wash on navy.
Change ONLY Image 1's watercolor wash and its outer transition. Preserve every foreground pixel conceptually: the exact learner, two robots, application window, laptop, UI, poses, anatomy, white fills, green fills, navy ink, positions, scale, square canvas and margins. No new or removed objects.
The current wash is too blue/purple, too bright, too uniform, and reads as a separate plate. Replace it with a much darker charcoal-slate-indigo watercolor dominated by #1d2138, #232a52 and muted #30354f. Reduce blue saturation dramatically. The wash should be only subtly lighter than the actual #13162a page, comparable to Image 2. Keep it compact behind the foreground and break up the perimeter so it is not a closed oval or scalloped cloud. Its outer third must darken toward #13162a while simultaneously thinning away, producing an imperceptible irregular transition with no visible boundary on that page. Remove bright blue edge pixels, violet edge pixels, luminous feathering, speckles, disconnected dots and any halo. Do not make the foreground dull.
Reject: cobalt, royal blue, electric blue, bright violet, pale lavender, a closed flower/oval/rectangle, a huge empty cloud, hard contour, rim, glow, bloom, page-colored opaque panel, checkerboard, malformed characters, new text.
Outside the COMPLETE corrected artwork use one perfectly uniform opaque saturated #ff00ff chroma backdrop to all four borders. The watercolor must fade into that key without magenta inside the art or a colored fringe. No transparency in the delivered source. Return a clean lossless 1254x1254 PNG.
```

The accepted correction kept the complete foreground and changed only the wash
to low-saturation `#1d2138`, `#232a52`, `#30354f`, removing blue/violet edge
pixels, closed plate geometry and luminous feathering.

## Chroma finishing and provenance hashes

All sources used `#ff00ff` and were finished with
`.agents/skills/website-illustrations/scripts/finish_chroma.py`. AI Dev Tools,
DE, ML, MLOps and SMA used `--key-noise-alpha 0.03`, measured from residual
canvas-edge alpha of at most 7/255. Journey start, reading, LLM and tour used the
default zero tolerance. `course-learning-dark` was normalized to its declared
`1024x1024`; the other eight remain `1254x1254`.

| Installed file | Built-in output | Keyed PNG SHA-256 | Finished PNG SHA-256 | Installed WebP SHA-256 |
| --- | --- | --- | --- | --- |
| `course-ai-dev-tools-zoomcamp-dark.webp` | `exec-28f5f9a4-380c-4ca2-97d5-1a2c7bd63867.png` | `1cdf729ec6d645a0f0a4e7e5e4ed31bed561cc04179f750a8ff3fac29a45d946` | `efc4fa8acbeba2250d577887ea6314e5bdf1dfa240158bc784348899fdea3c4d` | `36f39209d7552c077936f2be80f24491c647d12f362a602fe5768dfeb89d97a4` |
| `course-de-zoomcamp-dark.webp` | `exec-9b94e906-50ec-4770-8ce6-e8c54f8281a8.png` | `c208d350898890171038a13ab6c1d86b5380b4e1b5f73034a68c11d49ca7c054` | `da43f376be71ec2a3e0bfe50417d71c21e3084b125e095d92711ecf3a4802a83` | `4b2fbb550ba77e05f65fd5e6ea3c5fee169f4dc7d1996f315057a1588c4ca146` |
| `course-journey-start-dark.webp` | `exec-35efb3ab-78e5-490c-a2ec-b721a3e0f4e4.png` | `403349d5a3638762e9414ca308afad4fa3c8fa7286e34f0fd091c497d1ec3dfd` | `b7b18c2b56166bbfa5ac7efb3c77059aa2dfbf828a47d1ce1249e80a498f6ef0` | `af429875891af6a692c655fef8e3bff9e9ffc8188df3d4ce1364cdf261f05d10` |
| `course-learning-dark.webp` | `exec-4c734d6e-5c74-42c1-967f-b1c920d44325.png` | `9f337f4f6b45db82f9b977972774100c58756cb504649e870a9a8efd89f90232` | `e0c5892d825376749163f22cd66919d45028a8e98494105e4c5b481fb9346f34` | `d865d328252bf505a705f0735c9484d8d4556ea0d933ac445b127e2d32965e42` |
| `course-llm-zoomcamp-dark.webp` | `exec-e0d4e72c-b475-40da-a328-4ec899838293.png` | `1b0b3355c3f450f20549584ac834da643b123e2a02e48be03c6431ead9da5e9a` | `9bde9a817bd4d7023e75a7d8388436cf6616e0ea6d8ad993fc471353ae5f295a` | `b5dea0e02ef0fd54a3e100c54babbe5b476bafc9eb3b593867fc731b1934dc98` |
| `course-ml-zoomcamp-dark.webp` | `exec-2190cc87-5d3a-42b7-971f-cae9ccdf7fef.png` | `5ede18da7a9a31c13b8bc5fdc64063fedd89586294ac0eb2801e30933fcf22d8` | `e93820bdaefc1358678f54359061bac1e1e04eab4f77fd3538f55aaa6b030217` | `a29bb95c81624ea9a1060daf476f58db5086cb932462e69fa6e615dfffe45e02` |
| `course-mlops-zoomcamp-dark.webp` | `exec-da36dfc4-969b-491a-92dc-66b524033ccb.png` | `0c655719f73bdff81ea0cf596b6dbdff5bc44980361286a1880a3c84b3948dab` | `2ca1c9bcad1088774abd004c7d2662c39b16d3f2030d01928bd7473ee7b2577e` | `28e2d1380d307a23870003601fa4594e116c999f7a75fc8b0f78c0ffd6efde89` |
| `course-sma-zoomcamp-dark.webp` | `exec-18a23f87-c43a-46b9-b25e-39cdb60a7bdd.png` | `f6d668929be87e9511f8a9fc64641cac36c7fe8520e80ee3560153891820ca9c` | `3e20260ab8c69494cab85224ac1517a7c77c742ee4d42e3298e51fbc4163e659` | `213402eacc280ba3bcae882ce99b232218cc48f37aa3f18388d0a2f4511aad15` |
| `tour-community-dark.webp` | `exec-6bbf8b7c-eb99-4195-bd59-5adecf73ed5a.png` | `d1ddc579ef2520e8625149dab05b13687c4485731af3770ff1eb42e00541141b` | `b37010f210fcaeac3e608dc39bbe66b857b858f86d5be8e03ced187054826688` | `8048e06f8f6a1955e18f9e75e4f92b4d742e5fb3815e67086457df4d46c3d205` |

## Rejections and verification

- Rejected AI Dev Tools v1: returned direct transparency instead of the required
  keyed source and retained a large pale-edged blue plate.
- Rejected AI Dev Tools v2: keyed correctly, but remained too saturated and had
  noisy blue edge pigment on the dark page.
- Accepted surface review: `.tmp/dark-illustration-audit-20260917/generated/
  batch1-review.png` and `batch2-review.png` on `#13162a`, read at native size.
- `verify_assets.py` confirms decoded PNG/WebP RGBA equality, genuine alpha,
  declared dimensions and fully transparent borders for all nine assets.
- `test_tools.py`: 7 passed.
- Live desktop/mobile evidence and focused Django test results remain under
  `.tmp/dark-illustration-audit-20260917/`.

`core/tests/test_illustration_assets.py` now prevents a future light illustration
from landing without a matching dark filename, while the visual gate above
remains necessary because filename and alpha checks cannot detect a pasted plate.
