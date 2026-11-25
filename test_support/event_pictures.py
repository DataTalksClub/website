"""A tiny valid picture for test events: every event is created with one."""

from __future__ import annotations

import base64

from events.pictures import EventPicture

#: A 1x1 PNG.
TEST_PICTURE_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)
TEST_EVENT_PICTURE = EventPicture.from_bytes(TEST_PICTURE_PNG, "image/png")
TEST_EVENT_DESCRIPTION = "A test event."
#: The same picture as the admin create payload carries it.
TEST_PICTURE_PAYLOAD = {
    "image_base64": base64.b64encode(TEST_PICTURE_PNG).decode("ascii"),
    "content_type": "image/png",
}
