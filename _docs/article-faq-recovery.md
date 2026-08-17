# Article FAQ recovery

Ten blog articles ended with a frequently-asked-questions section on the legacy site. The pairs were
never part of the article Markdown: the body carried only
`{% include faq-accordion.html faqs=site.data.faqs.<key> %}`, and the questions and answers lived in
`_data/faqs/<key>.yml` in `DataTalksClub/datatalksclub.github.io`. `DataTalksClub/content` — the sole
editorial source for articles — therefore has no copy of them, and the projected article records
inherit the section's heading with nothing beneath it.

`content/article_faq.json` is that missing half, recovered verbatim from the pinned legacy revision
`ee43d3fa0929faf691178d79f19528e6f15a83e5`: 10 articles, 159 question/answer pairs. It is a build
artifact, not a synchronization path. Public requests, Django startup, ordinary tests, and container
builds read only the committed file; nothing reaches a checkout or the network.

| Article | Legacy data file | Pairs |
| --- | --- | --- |
| `ai-dev-tools-zoomcamp` | `_data/faqs/ai-dev-tools-zoomcamp.yml` | 24 |
| `data-engineering-zoomcamp` | `_data/faqs/data-engineering-zoomcamp.yml` | 17 |
| `free-machine-learning-courses` | `_data/faqs/free-ml-courses.yml` | 8 |
| `guide-to-free-online-courses-at-datatalks-club` | `_data/faqs/free-datatalksclub-courses-zoomcamps.yml` | 9 |
| `llm-zoomcamp` | `_data/faqs/llm-zoomcamp.yml` | 26 |
| `machine-learning-zoomcamp` | `_data/faqs/machine-learning-zoomcamp.yml` | 20 |
| `mlops-zoomcamp` | `_data/faqs/mlops-zoomcamp.yml` | 23 |
| `open-source-free-ai-agent-evaluation-tools` | `_data/faqs/open-source-free-ai-agent-evaluation-tools.yml` | 8 |
| `slack-communities` | `_data/faqs/data-science-slack-communities.yml` | 15 |
| `sponsor-datatalks-club` | `_data/faqs/sponsor-datatalks-club.yml` | 9 |

The data-file key is not always the article slug, which is why the mapping is read from each
article's own include rather than guessed from the filename. Every other article renders no FAQ
section at all.

## Rebuilding

Run it from a clean pair of pinned checkouts:

```console
uv run python scripts/build_article_faq.py \
  --content-root /path/to/DataTalksClub-content \
  --legacy-main-root /path/to/datatalksclub.github.io
```

Both checkouts must be at the revisions `scripts/build_public_projection.py` pins and must be clean;
the builder refuses anything else. It copies each question and answer verbatim, derives a stable
`faq-<question>` anchor for each pair, and recomputes the position of the section with the
projection's own block builder, so the section renders exactly where the legacy page put it. Five of
these articles put a sentence between the heading and the accordion, and six put a call to action or
a closing note after it; a position recorded as a block index keeps all of them in their written
order.

## Binding and failure

Each recovered section records the digest of the projected article body it positions itself inside.
If that body changes, the position and the heading it answers to are no longer known, so the load
fails loudly rather than rendering a section in the wrong place. Rebuild the capture in the same
change that moves an article body. Digest, schema, count, anchor, and shape failures are all
refusals; nothing degrades to a partial or invented section.

## Retiring the recovery

The recovery exists only because the pairs live outside the editorial source. It goes away when
`DataTalksClub/content` carries them itself — the natural shape being a `faq:` front-matter list of
`question`/`answer` mappings on each of the ten article files, replacing the `faq-accordion` include.
That is an upstream change in a separate repository and requires a new accepted content revision, a
projection rebuild that carries `faq` onto the article records, and a website change that reads the
record instead of this file. Until then the capture is the only honest source, and it is never
edited by hand: every word in it must be diffable against the pinned legacy commit.
