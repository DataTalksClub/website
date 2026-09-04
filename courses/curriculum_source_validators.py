"""Stable validators shared by curriculum models and their migrations.

Django serializes a field's validators by import path, so a migration that
validates a column keeps importing the module the callable lives in forever.
Keeping these callables out of the model modules -- the same reason
``content.migration_validators`` and ``courses.migration_family_identity``
exist -- means a later model refactor cannot break a historical migration.
"""

from __future__ import annotations

from pathlib import PurePosixPath

from django.core.exceptions import ValidationError

MAX_CODE_SOURCE_LABEL_CHARS = 200
MAX_SOURCE_PATH_CHARS = 1024


def validate_source_path(value: str) -> None:
    """Require a normalized repository-relative POSIX path."""

    if not value or value.startswith("/") or "\\" in value:
        raise ValidationError("Enter a repository-relative POSIX path.")
    parts = value.split("/")
    if any(part in {"", ".", ".."} for part in parts):
        raise ValidationError("Enter a normalized repository-relative POSIX path.")
    if PurePosixPath(value).as_posix() != value:
        raise ValidationError("Enter a normalized repository-relative POSIX path.")


def validate_unit_code_sources(value: object) -> None:
    """Require the bounded ``[{label, source_path}]`` shape the importer writes.

    A course repository declares a lesson's companion code files in its
    frontmatter.  Only the label and the repository-relative path are kept, so
    the stored value stays a projection of reviewed source rather than free-form
    JSON a later reader would have to trust.
    """

    if not isinstance(value, list):
        raise ValidationError("Enter a list of lesson code sources.")
    for entry in value:
        if not isinstance(entry, dict) or set(entry) != {"label", "source_path"}:
            raise ValidationError("Each lesson code source needs a label and a source path.")
        label = entry["label"]
        source_path = entry["source_path"]
        if not isinstance(label, str) or not label.strip():
            raise ValidationError("Enter a lesson code source label.")
        if len(label) > MAX_CODE_SOURCE_LABEL_CHARS:
            raise ValidationError("Enter a shorter lesson code source label.")
        if not isinstance(source_path, str) or len(source_path) > MAX_SOURCE_PATH_CHARS:
            raise ValidationError("Enter a repository-relative POSIX path.")
        validate_source_path(source_path)


__all__ = [
    "MAX_CODE_SOURCE_LABEL_CHARS",
    "MAX_SOURCE_PATH_CHARS",
    "validate_source_path",
    "validate_unit_code_sources",
]
