"""Site parsers registered with the community_base.content_sync engine.

D2.2a ships articles and people; D2.2b adds podcast and books.  Importing
this package performs the parser registration, so the content app's ``ready``
imports it once.
"""

from . import articles, people  # noqa: F401
