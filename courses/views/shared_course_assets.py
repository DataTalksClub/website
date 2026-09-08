"""Serving for managed shared-curriculum lesson assets.

The schema-2 importer stores every current relative image and declared code
file in managed storage with a database row and a content-addressed public
path (``/course-assets/lessons/<lesson-id>/<checksum>/<filename>``).  This
view resolves a request through that database row and the managed store only
-- never a GitHub URL and never a request-time fetch.  The checksum segment
makes a path immutable: a source edit imports under a new checksum and the
old path keeps serving the bytes it always served, so the response is
publicly cacheable forever.
"""

from __future__ import annotations

import re

from django.core.files.storage import default_storage
from django.http import FileResponse, HttpRequest, HttpResponseBase
from django.shortcuts import get_object_or_404
from django.views.decorators.http import require_safe

from courses.models import SharedCurriculumAsset

#: Same shape the renderer allowlist admits for ``img[src]``; anything else
#: was never a valid managed reference.
_ASSET_PATH_RE = re.compile(
    r"^/course-assets/lessons/"
    r"[0-9a-f-]{36}/[0-9a-f]{64}/[A-Za-z0-9][A-Za-z0-9._-]*$"
)


@require_safe
def shared_course_asset(
    request: HttpRequest, lesson_id: str, checksum: str, filename: str
) -> HttpResponseBase:
    """Serve one managed shared-curriculum asset by its exact public path."""

    public_path = f"/course-assets/lessons/{lesson_id}/{checksum}/{filename}"
    if _ASSET_PATH_RE.fullmatch(public_path) is None:
        from django.http import Http404

        raise Http404()
    asset = get_object_or_404(
        SharedCurriculumAsset.objects.select_related("lesson"),
        public_path=public_path,
    )
    response = FileResponse(
        default_storage.open(asset.storage_key, "rb"),
        content_type=asset.content_type or "application/octet-stream",
    )
    # Content-addressed path: the bytes at this path can never change.
    response["Cache-Control"] = "public, max-age=31536000, immutable"
    return response
