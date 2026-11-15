from django.db import models


class PublishedResource(models.Model):
    """An Academy page or asset published from its reviewed source repository.

    These rows own public reads. They contain no subscriber or registration data.
    """

    path = models.CharField(max_length=512, unique=True)
    content = models.BinaryField()
    content_type = models.CharField(max_length=100)
    sha256 = models.CharField(max_length=64)
    source_commit = models.CharField(max_length=40)
    published_at = models.DateTimeField(auto_now=True)

    def __str__(self) -> str:
        return f"/academy/{self.path}"
