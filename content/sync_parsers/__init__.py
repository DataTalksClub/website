"""Site parsers registered with the community_base.content_sync engine.

D2.2a ships articles and people; D2.2b adds podcast and books; D2.2c adds
docs, faq, podwiki, media and the site pages (podcast platforms, /slack).
Importing this package performs the parser registration, so the content app's
``ready`` imports it once.
"""

from . import (  # noqa: F401
    articles,
    books,
    docs,
    faq,
    media,
    people,
    podcasts,
    podwiki,
    platforms,
    slack,
)
