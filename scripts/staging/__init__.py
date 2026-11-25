"""Builders that turn a reviewed source into a staging artifact.

Staging is the middle of the one-way trip described in
``_docs/runbooks/data-ingest.md`` s.1: **source -> staging -> production
database**. A module here reads a source an operator holds, applies the
reviewed policies to it, and writes a file whose only purpose is to be pumped
into the database by a ``scripts/prod/`` importer. The staging artifacts
themselves live outside this repository, at ``~/prod/dtc-data/content-staging/``
(see ``_docs/architecture/database-only-content.md``). Nothing here is on a
request path, and nothing here writes to the database itself.

``event_description_bridge.py``, ``event_speaker_bio_normalization.py`` and
``event_description_link_policy.py`` moved here from the retired
``scripts/projection_build/`` package: they parse and validate event
descriptions, speaker credits and link policy for the frozen legacy corpus
*and* for ``luma_event_descriptions.py``'s ongoing Luma staging path, so they
could not leave with the rest of that package.
"""
