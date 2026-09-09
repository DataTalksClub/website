"""The PendingUnsubscribe copy behind the D1.2a data migration.

Forward must reproduce every donor row in the package table with identical
values, survive re-running, and cross the 1000-row batch boundary; backward
must restore the donor for rollback.
"""

from community_base.mail.models import PendingUnsubscribe as PackagePendingUnsubscribe
from django.db import connection
from django.test import TestCase

from email_app import unsubscribe_copy
from email_app.models import PendingUnsubscribe

COPY_FIELDS = (
    "id",
    "unsubscribe_token",
    "token_fingerprint",
    "scope",
    "status",
    "attempt_count",
    "last_outcome",
    "accepted_at",
    "updated_at",
)


class _SchemaEditorStub:
    """The slice of RunPython's schema editor the copy functions use."""

    def __init__(self, connection):
        self.connection = connection


class CopyTests(TestCase):
    def _site_rows(self):
        return {row["id"]: row for row in PendingUnsubscribe.objects.values(*COPY_FIELDS)}

    def _package_rows(self):
        return {row["id"]: row for row in PackagePendingUnsubscribe.objects.values(*COPY_FIELDS)}

    def _copy(self, forward):
        editor = _SchemaEditorStub(connection)
        if forward:
            unsubscribe_copy.copy_forward(None, editor)
        else:
            unsubscribe_copy.copy_backward(None, editor)

    def test_forward_copies_every_row_with_identical_values(self):
        self._make_site_rows(3)

        self._copy(forward=True)

        self.assertEqual(self._package_rows(), self._site_rows())

    def test_forward_is_safe_to_re_run(self):
        self._make_site_rows(2)
        self._copy(forward=True)

        self._copy(forward=True)

        self.assertEqual(len(self._package_rows()), 2)

    def test_forward_crosses_the_batch_boundary(self):
        self._make_site_rows(unsubscribe_copy.BATCH_SIZE + 1)

        self._copy(forward=True)

        self.assertEqual(len(self._package_rows()), unsubscribe_copy.BATCH_SIZE + 1)

    def test_backward_restores_the_donor(self):
        self._make_site_rows(2)
        self._copy(forward=True)
        PendingUnsubscribe.objects.all().delete()

        self._copy(forward=False)

        self.assertEqual(self._site_rows(), self._package_rows())

    def _make_site_rows(self, count):
        PendingUnsubscribe.objects.bulk_create(
            PendingUnsubscribe(
                unsubscribe_token=f"relay-token-{index}",
                token_fingerprint=f"fingerprint-{index}",
                scope="global",
                status=PendingUnsubscribe.Status.PENDING,
            )
            for index in range(count)
        )
