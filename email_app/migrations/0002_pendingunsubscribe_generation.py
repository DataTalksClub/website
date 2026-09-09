import django.db.models.deletion  # noqa: F401

from django.db import migrations, models


class Migration(migrations.Migration):
    """Give every opt-out intent a replay generation (audits BE-10/BE-11).

    The generation is the newer-choice marker both containment fixes hang off:
    the job deduplication key includes it, so a recipient re-submitting against
    a still-pending row always creates fresh runnable work even after the old
    job exhausted, and a worker captures it with its read so an old completion
    can never settle the newer choice.
    """

    dependencies = [
        ("email_app", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="pendingunsubscribe",
            name="generation",
            field=models.PositiveIntegerField(default=1),
        ),
    ]
