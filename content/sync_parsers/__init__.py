"""Site parsers registered with the community_base.content_sync engine.

D2.2a ships articles and people; D2.2b adds podcast and books; D2.2c adds
docs, faq and podwiki.  Importing this package performs the parser
registration, so the content app's ``ready`` imports it once.
"""

from . import articles, books, docs, faq, people, podcasts, podwiki  # noqa: F401
