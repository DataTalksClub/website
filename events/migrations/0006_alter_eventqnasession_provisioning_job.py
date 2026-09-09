"""Point EventQnaSession.provisioning_job at the package JobIntent (D1.1).

The copy migration in the data app preserves durable-job ids, so existing
references stay valid across the swap.
"""

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("events", "0005_eventcontent_eventlink_eventspeaker_and_more"),
        ("data", "0003_copy_durable_jobs_to_job_intents"),
    ]

    operations = [
        migrations.AlterField(
            model_name="eventqnasession",
            name="provisioning_job",
            field=models.OneToOneField(
                blank=True,
                null=True,
                on_delete=models.SET_NULL,
                related_name="qna_provisioning_session",
                to="cb_jobs.jobintent",
            ),
        ),
    ]
