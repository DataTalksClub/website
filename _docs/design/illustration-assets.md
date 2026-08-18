# Adding illustration assets

Homepage and other design-system illustrations are stored in
`core/static/core/illustrations/` as transparent WebP files. The drawing keeps its
own white fills; only the outer background is transparent. The consuming template
must treat decorative artwork as `alt=""`, with `decoding="async"` and
`loading="lazy"` below the fold.

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
