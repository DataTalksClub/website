"""Builders that turn a reviewed source into a ``temporary/content/`` artifact.

Staging is the middle of the one-way trip described in
``_docs/runbooks/data-ingest.md`` s.1: **source -> staging -> production
database**. A module here reads a source an operator holds, applies the
reviewed policies to it, and writes a file whose only purpose is to be pumped
into the database by a ``scripts/prod/`` importer. Nothing here is on a request
path, and nothing here writes to the database itself.
"""
