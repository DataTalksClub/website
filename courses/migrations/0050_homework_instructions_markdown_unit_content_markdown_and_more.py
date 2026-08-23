from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('courses', '0049_question_source_option_ids'),
    ]

    operations = [
        migrations.AddField(
            model_name='homework',
            name='instructions_markdown',
            field=models.TextField(blank=True),
        ),
        migrations.AddField(
            model_name='unit',
            name='content_markdown',
            field=models.TextField(blank=True),
        ),
        migrations.AddField(
            model_name='unit',
            name='rendered_html',
            field=models.TextField(blank=True),
        ),
    ]
