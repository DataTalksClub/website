"""Copy PendingUnsubscribe rows into the package mail app (D1.2a).

The column mapping and batching live in ``email_app.unsubscribe_copy`` so
the migration stays reviewable at a glance and the tests exercise the same
functions the deploy runs.
"""

from django.db import migrations

from email_app import unsubscribe_copy


class Migration(migrations.Migration):
    dependencies = [
        ("email_app", "0001_initial"),
        ("cb_mail", "0004_emaildelivery_transport_options"),
    ]

    operations = [
        migrations.RunPython(unsubscribe_copy.copy_forward, unsubscribe_copy.copy_backward),
    ]
