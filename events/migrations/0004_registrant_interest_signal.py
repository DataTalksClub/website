import uuid

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('events', '0003_remove_historical_event_mapping'),
    ]

    operations = [
        migrations.CreateModel(
            name='EventRegistrantInterestSignal',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('category', models.CharField(choices=[('general', 'General'), ('conference', 'Conference'), ('podcast', 'Podcast'), ('production', 'Production'), ('analytics', 'Analytics'), ('data', 'Data'), ('soft_skills', 'Soft skills'), ('data_science', 'Data science')], max_length=32)),
                ('source', models.CharField(choices=[('mailchimp_tag', 'Mailchimp tag')], default='mailchimp_tag', max_length=16)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('identity', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='interest_signals', to='events.eventregistrantidentity')),
            ],
            options={
                'ordering': ('identity_id', 'category', 'source'),
            },
        ),
        migrations.AddConstraint(
            model_name='eventregistrantinterestsignal',
            constraint=models.UniqueConstraint(fields=('identity', 'category', 'source'), name='events_registrant_interest_signal_unique'),
        ),
    ]
