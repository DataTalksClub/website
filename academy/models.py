from django.db import models


class PublishedResource(models.Model):
    """The retired Astro Academy build, one row per file.  Nothing reads it.

    The Academy pages are synced content now (``content.sync_parsers.academy``)
    and no view serves these rows.  The model and its table stay for exactly one
    release so the previous image keeps working during the rolling deploy and a
    rollback to it is still possible; a later release drops the table
    (``_docs/runbooks/academy.md``).  The rows hold no subscriber data.
    """

    path = models.CharField(max_length=512, unique=True)
    content = models.BinaryField()
    content_type = models.CharField(max_length=100)
    sha256 = models.CharField(max_length=64)
    source_commit = models.CharField(max_length=40)
    published_at = models.DateTimeField(auto_now=True)

    def __str__(self) -> str:
        return f"/academy/{self.path}"
