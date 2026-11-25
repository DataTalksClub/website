"""The event picture: every event has one from the moment it exists.

The owner's rule: it must not be possible to have an event without a picture.
The picture is the 1000x1000 banner stored in
``event_registrants.EventOperatorConfig.banner_image``.  It is enforced where
events are written:

* :func:`events.identity.create_event_identity` -- the only creation path --
  takes an :class:`EventPicture` and writes it in the same transaction as the
  event row, or refuses (``event_picture_required``);
* ``EventOperatorConfig.save()`` refuses an empty picture and
  ``EventOperatorConfig.delete()`` refuses to drop one, so a picture can be
  replaced but never cleared;
* a ``pre_save`` receiver on ``Event`` refuses to save an existing event that
  has no picture (``event_registrants.signals``) -- the model-level rule that
  holds on SQLite too;
* on PostgreSQL the deferred constraint triggers from ``event_registrants``
  migration 0005, enabled by 0006, refuse the commit.

This module is pure Python (no model imports) so the model layer can use it.
"""

from __future__ import annotations

from dataclasses import dataclass

#: Accepted picture content types and the file suffix each is stored as.
ALLOWED_PICTURE_TYPES: dict[str, str] = {
    "image/png": "png",
    "image/jpeg": "jpg",
    "image/webp": "webp",
}
MAX_PICTURE_BYTES = 5 * 1024 * 1024

PICTURE_REQUIRED = "event_picture_required"
PICTURE_INVALID = "event_picture_invalid"


class EventPictureRequired(ValueError):
    """A write would leave an event without a picture."""

    code = PICTURE_REQUIRED

    def __init__(self, message: str = PICTURE_REQUIRED) -> None:
        super().__init__(message)


class EventPictureInvalid(ValueError):
    """The bytes given as an event picture are not an accepted image."""

    code = PICTURE_INVALID

    def __init__(self, message: str = PICTURE_INVALID) -> None:
        super().__init__(message)


def sniff_picture_type(data: bytes) -> str | None:
    """The content type the image bytes declare by their signature, if accepted."""

    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return None


@dataclass(frozen=True, slots=True)
class EventPicture:
    """Validated picture bytes and their content type."""

    data: bytes
    content_type: str

    @classmethod
    def from_bytes(
        cls, data: bytes | bytearray | memoryview | None, content_type: str = ""
    ) -> EventPicture:
        """Validate ``data``; the signature decides the type when none is given.

        Empty bytes raise :class:`EventPictureRequired`; oversized, unknown or
        mislabelled bytes raise :class:`EventPictureInvalid`.
        """

        raw = bytes(data or b"")
        if not raw:
            raise EventPictureRequired()
        if len(raw) > MAX_PICTURE_BYTES:
            raise EventPictureInvalid(f"event picture must be at most {MAX_PICTURE_BYTES} bytes")
        sniffed = sniff_picture_type(raw)
        if sniffed is None:
            raise EventPictureInvalid("event picture must be a PNG, JPEG or WebP image")
        declared = (content_type or "").strip().lower()
        if declared and declared != sniffed:
            raise EventPictureInvalid(
                f"event picture is a {sniffed} image but was labelled {declared}"
            )
        return cls(data=raw, content_type=sniffed)


def has_picture_bytes(value: bytes | bytearray | memoryview | None) -> bool:
    """Whether a stored ``banner_image`` value holds any bytes."""

    return value is not None and len(value) > 0
