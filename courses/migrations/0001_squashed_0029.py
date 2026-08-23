import logging

import courses.validators.criteria_validators
import courses.validators.custom_url_validators
import courses.validators.validating_json_field
import django.core.validators
import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


logger = logging.getLogger("courses.migrations")


def replace_commas_with_linebreaks(apps, schema_editor):
    Question = apps.get_model("courses", "Question")
    for question in Question.objects.all():
        if question.possible_answers:
            question.possible_answers = question.possible_answers.replace(",", "\n")
            question.save()


def replace_answers_with_indexes(possible_answers, answers, question_id=None):
    normalized_answers = [answer.strip().lower() for answer in possible_answers]
    normalized_input = answers.lower().strip()
    correct_indexes = []
    for answer in normalized_input.split(","):
        try:
            correct_indexes.append(str(normalized_answers.index(answer.strip()) + 1))
        except ValueError:
            logger.error(
                "Answer %r not found in possible_answers for question ID %s",
                answer,
                question_id,
            )
    return ",".join(correct_indexes)


def update_correct_answers_to_indexes(apps, schema_editor):
    Question = apps.get_model("courses", "Question")
    for question in Question.objects.all():
        if question.question_type not in ["MC", "CB"]:
            continue
        if question.possible_answers and question.correct_answer:
            question.correct_answer = replace_answers_with_indexes(
                question.possible_answers.split("\n"), question.correct_answer, question.id
            )
            question.save()


def update_answers_with_indexes(apps, schema_editor):
    Answer = apps.get_model("courses", "Answer")
    updated_answers = []
    for answer in Answer.objects.all():
        question = answer.question
        if question.question_type not in ["MC", "CB"]:
            continue
        if question.possible_answers and answer.answer_text:
            answer.answer_text = replace_answers_with_indexes(
                question.possible_answers.split("\n"), answer.answer_text, question.id
            )
            updated_answers.append(answer)
    Answer.objects.bulk_update(updated_answers, ["answer_text"])


def set_first_homework_scored_true_for_existing_records(apps, schema_editor):
    Course = apps.get_model("courses", "Course")
    Course.objects.all().update(first_homework_scored=True)

# 0027 is intentionally not replaced: it is a side branch from 0026, while
# 0028 and 0029 continue the main branch. Migration 0031 merges that branch
# with 0030, so omitting 0027 here keeps its schema operations applied once.
class Migration(migrations.Migration):

    replaces = [('courses', '0001_initial'), ('courses', '0002_alter_enrollment_student'), ('courses', '0003_replace_commas_with_linebreaks_in_possible_answers'), ('courses', '0004_update_correct_answer_indexes'), ('courses', '0005_update_answers_with_indexes'), ('courses', '0006_course_first_homework_scored'), ('courses', '0007_enrollment_position_on_leaderboard'), ('courses', '0008_remove_answer_student'), ('courses', '0009_rename_comments_peerreview_problems_comments_and_more'), ('courses', '0010_remove_reviewcriteria_max_score'), ('courses', '0011_alter_enrollment_position_on_leaderboard'), ('courses', '0012_project_points_for_peer_review_and_more'), ('courses', '0013_remove_homework_is_scored_homework_state_and_more'), ('courses', '0014_alter_projectsubmission_github_link_and_more'), ('courses', '0015_enrollment_certificate_url'), ('courses', '0016_enrollment_about_me_enrollment_github_url_and_more'), ('courses', '0017_alter_projectsubmission_learning_in_public_links_and_more'), ('courses', '0018_course_finished'), ('courses', '0019_remove_homework_problems_comments_field_and_more'), ('courses', '0020_remove_project_points_to_pass_and_more'), ('courses', '0021_course_min_projects_to_pass'), ('courses', '0022_projectstatistics'), ('courses', '0023_course_visible'), ('courses', '0024_alter_question_question_type'), ('courses', '0025_add_wrapped_statistics'), ('courses', '0026_enrollment_disable_learning_in_public_and_more'), ('courses', '0028_leaderboardcomplaint'), ('courses', '0029_enrollment_display_public_profile')]

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='Course',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('slug', models.SlugField(unique=True)),
                ('title', models.CharField(max_length=200)),
                ('description', models.TextField()),
                ('social_media_hashtag', models.CharField(blank=True, help_text='The hashtag associated with the course for social media use.', max_length=100)),
                ('faq_document_url', models.URLField(blank=True, help_text='The URL of the FAQ document for the course.', validators=[django.core.validators.URLValidator()])),
            ],
        ),
        migrations.CreateModel(
            name='Enrollment',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('enrollment_date', models.DateTimeField(auto_now_add=True)),
                ('display_name', models.CharField(blank=True, max_length=255)),
                ('display_on_leaderboard', models.BooleanField(default=True)),
                ('certificate_name', models.CharField(blank=True, max_length=255, null=True)),
                ('total_score', models.IntegerField(default=0)),
                ('course', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to='courses.course')),
                ('student', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'unique_together': {('student', 'course')},
            },
        ),
        migrations.CreateModel(
            name='Homework',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('slug', models.SlugField()),
                ('title', models.CharField(max_length=200)),
                ('description', models.TextField()),
                ('due_date', models.DateTimeField()),
                ('learning_in_public_cap', models.IntegerField(default=7)),
                ('homework_url_field', models.BooleanField(default=True, help_text='Include field for homework URL')),
                ('time_spent_lectures_field', models.BooleanField(default=True, help_text='Include field for time spent on lectures')),
                ('time_spent_homework_field', models.BooleanField(default=True, help_text='Include field for time spent on homework')),
                ('problems_comments_field', models.BooleanField(default=True, help_text='Include field for problems and comments')),
                ('faq_contribution_field', models.BooleanField(default=True, help_text='Include field for FAQ contributions')),
                ('is_scored', models.BooleanField(default=False)),
                ('course', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to='courses.course')),
            ],
            options={
                'unique_together': {('course', 'slug')},
            },
        ),
        migrations.CreateModel(
            name='Project',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('slug', models.SlugField()),
                ('title', models.CharField(max_length=200)),
                ('description', models.TextField()),
                ('submission_due_date', models.DateTimeField()),
                ('learning_in_public_cap_project', models.IntegerField(default=14)),
                ('peer_review_due_date', models.DateTimeField()),
                ('time_spent_project_field', models.BooleanField(default=True)),
                ('problems_comments_field', models.BooleanField(default=True)),
                ('faq_contribution_field', models.BooleanField(default=True, help_text='Include field for FAQ contributions')),
                ('learning_in_public_cap_review', models.IntegerField(default=2)),
                ('number_of_peers_to_evaluate', models.IntegerField(default=3)),
                ('time_spent_evaluation_field', models.BooleanField(default=True)),
                ('points_to_pass', models.IntegerField(default=0)),
                ('state', models.CharField(choices=[('CS', 'COLLECTING_SUBMISSIONS'), ('PR', 'PEER_REVIEWING'), ('CO', 'COMPLETED')], default='CS', max_length=2)),
                ('course', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to='courses.course')),
            ],
            options={
                'unique_together': {('course', 'slug')},
            },
        ),
        migrations.CreateModel(
            name='Submission',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('homework_link', models.URLField(blank=True, null=True, validators=[django.core.validators.URLValidator(schemes=['http', 'https', 'git'])])),
                ('learning_in_public_links', models.JSONField(blank=True, help_text='Links where students talk about the course', null=True)),
                ('time_spent_lectures', models.FloatField(blank=True, help_text='Time spent on lectures and reading (in hours)', null=True)),
                ('time_spent_homework', models.FloatField(blank=True, help_text='Time spent on homework (in hours)', null=True)),
                ('problems_comments', models.TextField(blank=True, help_text='Any problems, comments, or feedback')),
                ('faq_contribution', models.TextField(blank=True, help_text='Contribution to FAQ')),
                ('submitted_at', models.DateTimeField(auto_now=True)),
                ('questions_score', models.IntegerField(default=0)),
                ('faq_score', models.IntegerField(default=0)),
                ('learning_in_public_score', models.IntegerField(default=0)),
                ('total_score', models.IntegerField(default=0)),
                ('enrollment', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to='courses.enrollment')),
                ('homework', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to='courses.homework')),
                ('student', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to=settings.AUTH_USER_MODEL)),
            ],
        ),
        migrations.CreateModel(
            name='ReviewCriteria',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('description', models.CharField(max_length=255)),
                ('options', models.JSONField()),
                ('max_score', models.IntegerField(default=4)),
                ('review_criteria_type', models.CharField(choices=[('RB', 'Radio Buttons'), ('CB', 'Checkboxes')], max_length=2)),
                ('course', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to='courses.course')),
            ],
        ),
        migrations.CreateModel(
            name='Question',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('text', models.TextField()),
                ('question_type', models.CharField(choices=[('MC', 'Multiple Choice'), ('FF', 'Free Form'), ('CB', 'Checkboxes')], max_length=2)),
                ('answer_type', models.CharField(blank=True, choices=[('ANY', 'Any'), ('FLT', 'Float'), ('INT', 'Integer'), ('EXS', 'Exact String'), ('CTS', 'Contains String')], max_length=3, null=True)),
                ('possible_answers', models.TextField(blank=True, null=True)),
                ('correct_answer', models.TextField(blank=True, null=True)),
                ('scores_for_correct_answer', models.IntegerField(default=1)),
                ('homework', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to='courses.homework')),
            ],
        ),
        migrations.CreateModel(
            name='ProjectSubmission',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('github_link', models.URLField(validators=[django.core.validators.URLValidator()])),
                ('commit_id', models.CharField(max_length=40)),
                ('learning_in_public_links', models.JSONField(blank=True, null=True)),
                ('faq_contribution', models.TextField(blank=True)),
                ('time_spent', models.FloatField()),
                ('problems_comments', models.TextField(blank=True)),
                ('submitted_at', models.DateTimeField(auto_now=True)),
                ('enrollment', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to='courses.enrollment')),
                ('project', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to='courses.project')),
                ('student', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to=settings.AUTH_USER_MODEL)),
            ],
        ),
        migrations.CreateModel(
            name='PeerReview',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('note_to_peer', models.TextField()),
                ('learning_in_public_links', models.JSONField(blank=True, null=True)),
                ('time_spent_reviewing', models.FloatField()),
                ('comments', models.TextField(blank=True)),
                ('reviewer', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to=settings.AUTH_USER_MODEL)),
                ('submission_under_evaluation', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to='courses.projectsubmission')),
            ],
        ),
        migrations.CreateModel(
            name='CriteriaResponse',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('score', models.IntegerField()),
                ('criteria', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to='courses.reviewcriteria')),
                ('review', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='criteria_responses', to='courses.peerreview')),
            ],
        ),
        migrations.AddField(
            model_name='course',
            name='students',
            field=models.ManyToManyField(related_name='courses_enrolled', through='courses.Enrollment', to=settings.AUTH_USER_MODEL),
        ),
        migrations.CreateModel(
            name='Answer',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('answer_text', models.TextField(blank=True, null=True)),
                ('is_correct', models.BooleanField(default=False)),
                ('question', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to='courses.question')),
                ('student', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to=settings.AUTH_USER_MODEL)),
                ('submission', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to='courses.submission')),
            ],
        ),
        migrations.AlterField(
            model_name='enrollment',
            name='student',
            field=models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to=settings.AUTH_USER_MODEL),
        ),
        migrations.RunPython(
            code=replace_commas_with_linebreaks,
        ),
        migrations.RunPython(
            code=update_correct_answers_to_indexes,
        ),
        migrations.RunPython(
            code=update_answers_with_indexes,
        ),
        migrations.AddField(
            model_name='course',
            name='first_homework_scored',
            field=models.BooleanField(default=False, help_text='Whether the first homework has been scored. We use that for deciding whether to show the leaderboard.'),
        ),
        migrations.RunPython(
            code=set_first_homework_scored_true_for_existing_records,
        ),
        migrations.AddField(
            model_name='enrollment',
            name='position_on_leaderboard',
            field=models.IntegerField(blank=True, default=0, null=True),
        ),
        migrations.RemoveField(
            model_name='answer',
            name='student',
        ),
        migrations.RenameField(
            model_name='peerreview',
            old_name='comments',
            new_name='problems_comments',
        ),
        migrations.RemoveField(
            model_name='criteriaresponse',
            name='score',
        ),
        migrations.AddField(
            model_name='criteriaresponse',
            name='answer',
            field=models.CharField(blank=True, max_length=255, null=True),
        ),
        migrations.AddField(
            model_name='peerreview',
            name='optional',
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name='peerreview',
            name='state',
            field=models.CharField(choices=[('TR', 'TO_REVIEW'), ('SU', 'SUBMITTED')], default='TR', max_length=2),
        ),
        migrations.AddField(
            model_name='peerreview',
            name='submitted_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AlterField(
            model_name='peerreview',
            name='reviewer',
            field=models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='reviewers', to='courses.projectsubmission'),
        ),
        migrations.AlterField(
            model_name='peerreview',
            name='submission_under_evaluation',
            field=models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='reviews_under_evaluation', to='courses.projectsubmission'),
        ),
        migrations.AlterField(
            model_name='peerreview',
            name='time_spent_reviewing',
            field=models.FloatField(blank=True, null=True),
        ),
        migrations.AlterField(
            model_name='projectsubmission',
            name='time_spent',
            field=models.FloatField(blank=True, null=True),
        ),
        migrations.RemoveField(
            model_name='reviewcriteria',
            name='max_score',
        ),
        migrations.AlterField(
            model_name='enrollment',
            name='position_on_leaderboard',
            field=models.IntegerField(blank=True, default=None, null=True),
        ),
        migrations.AddField(
            model_name='project',
            name='points_for_peer_review',
            field=models.IntegerField(default=3),
        ),
        migrations.AddField(
            model_name='projectsubmission',
            name='passed',
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name='projectsubmission',
            name='peer_review_learning_in_public_score',
            field=models.IntegerField(default=0),
        ),
        migrations.AddField(
            model_name='projectsubmission',
            name='peer_review_score',
            field=models.IntegerField(default=0),
        ),
        migrations.AddField(
            model_name='projectsubmission',
            name='project_faq_score',
            field=models.IntegerField(default=0),
        ),
        migrations.AddField(
            model_name='projectsubmission',
            name='project_learning_in_public_score',
            field=models.IntegerField(default=0),
        ),
        migrations.AddField(
            model_name='projectsubmission',
            name='project_score',
            field=models.IntegerField(default=0),
        ),
        migrations.AddField(
            model_name='projectsubmission',
            name='reviewed_enough_peers',
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name='projectsubmission',
            name='total_score',
            field=models.IntegerField(default=0),
        ),
        migrations.CreateModel(
            name='ProjectEvaluationScore',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('score', models.IntegerField()),
                ('review_criteria', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to='courses.reviewcriteria')),
                ('submission', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to='courses.projectsubmission')),
            ],
        ),
        migrations.RemoveField(
            model_name='homework',
            name='is_scored',
        ),
        migrations.AddField(
            model_name='homework',
            name='state',
            field=models.CharField(choices=[('CL', 'CLOSED'), ('OP', 'OPEN'), ('SC', 'SCORED')], default='OP', max_length=2),
        ),
        migrations.AlterField(
            model_name='project',
            name='state',
            field=models.CharField(choices=[('CL', 'CLOSED'), ('CS', 'COLLECTING_SUBMISSIONS'), ('PR', 'PEER_REVIEWING'), ('CO', 'COMPLETED')], default='CS', max_length=2),
        ),
        migrations.AlterField(
            model_name='projectsubmission',
            name='github_link',
            field=models.URLField(validators=[django.core.validators.URLValidator(), courses.validators.custom_url_validators.validate_url_200]),
        ),
        migrations.AlterField(
            model_name='projectsubmission',
            name='learning_in_public_links',
            field=courses.validators.validating_json_field.ValidatingJSONField(blank=True, null=True),
        ),
        migrations.AlterField(
            model_name='submission',
            name='homework_link',
            field=models.URLField(blank=True, null=True, validators=[django.core.validators.URLValidator(schemes=['http', 'https', 'git']), courses.validators.custom_url_validators.validate_url_200]),
        ),
        migrations.AlterField(
            model_name='submission',
            name='learning_in_public_links',
            field=courses.validators.validating_json_field.ValidatingJSONField(blank=True, help_text='Links where students talk about the course', null=True),
        ),
        migrations.AddField(
            model_name='enrollment',
            name='certificate_url',
            field=models.CharField(blank=True, max_length=255, null=True),
        ),
        migrations.AddField(
            model_name='enrollment',
            name='about_me',
            field=models.TextField(blank=True, help_text='You can put any information about yourself here', null=True, verbose_name='About me'),
        ),
        migrations.AddField(
            model_name='enrollment',
            name='github_url',
            field=models.URLField(blank=True, null=True, validators=[django.core.validators.URLValidator(), courses.validators.custom_url_validators.validate_url_200], verbose_name='GitHub URL'),
        ),
        migrations.AddField(
            model_name='enrollment',
            name='linkedin_url',
            field=models.URLField(blank=True, null=True, validators=[django.core.validators.URLValidator(), courses.validators.custom_url_validators.validate_url_200], verbose_name='LinkedIn URL'),
        ),
        migrations.AddField(
            model_name='enrollment',
            name='personal_website_url',
            field=models.URLField(blank=True, null=True, validators=[django.core.validators.URLValidator(), courses.validators.custom_url_validators.validate_url_200], verbose_name='Personal website URL'),
        ),
        migrations.AlterField(
            model_name='enrollment',
            name='certificate_name',
            field=models.CharField(blank=True, max_length=255, null=True, verbose_name='Name for the certificate'),
        ),
        migrations.AlterField(
            model_name='enrollment',
            name='display_name',
            field=models.CharField(blank=True, max_length=255, verbose_name='Leaderboard name'),
        ),
        migrations.AlterField(
            model_name='enrollment',
            name='about_me',
            field=models.TextField(blank=True, help_text='Any information about you', null=True, verbose_name='About me'),
        ),
        migrations.AlterField(
            model_name='enrollment',
            name='certificate_name',
            field=models.CharField(blank=True, help_text='Your actual name that will appear on your certificate', max_length=255, null=True, verbose_name='Certificate name'),
        ),
        migrations.AlterField(
            model_name='enrollment',
            name='display_name',
            field=models.CharField(blank=True, help_text='Name on the leaderboard', max_length=255, verbose_name='Leaderboard name'),
        ),
        migrations.AlterField(
            model_name='enrollment',
            name='github_url',
            field=models.URLField(blank=True, null=True, validators=[django.core.validators.URLValidator()], verbose_name='GitHub URL'),
        ),
        migrations.AlterField(
            model_name='enrollment',
            name='linkedin_url',
            field=models.URLField(blank=True, null=True, validators=[django.core.validators.URLValidator()], verbose_name='LinkedIn URL'),
        ),
        migrations.AlterField(
            model_name='enrollment',
            name='personal_website_url',
            field=models.URLField(blank=True, null=True, validators=[django.core.validators.URLValidator()], verbose_name='Personal website URL'),
        ),
        migrations.AlterField(
            model_name='projectsubmission',
            name='learning_in_public_links',
            field=models.JSONField(blank=True, null=True),
        ),
        migrations.AlterField(
            model_name='submission',
            name='learning_in_public_links',
            field=models.JSONField(blank=True, help_text='Links where students talk about the course', null=True),
        ),
        migrations.CreateModel(
            name='HomeworkStatistics',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('total_submissions', models.IntegerField(default=0)),
                ('min_questions_score', models.IntegerField(blank=True, null=True)),
                ('max_questions_score', models.IntegerField(blank=True, null=True)),
                ('avg_questions_score', models.FloatField(blank=True, null=True)),
                ('median_questions_score', models.FloatField(blank=True, null=True)),
                ('q1_questions_score', models.FloatField(blank=True, null=True)),
                ('q3_questions_score', models.FloatField(blank=True, null=True)),
                ('min_total_score', models.IntegerField(blank=True, null=True)),
                ('max_total_score', models.IntegerField(blank=True, null=True)),
                ('avg_total_score', models.FloatField(blank=True, null=True)),
                ('median_total_score', models.FloatField(blank=True, null=True)),
                ('q1_total_score', models.FloatField(blank=True, null=True)),
                ('q3_total_score', models.FloatField(blank=True, null=True)),
                ('min_learning_in_public_score', models.IntegerField(blank=True, null=True)),
                ('max_learning_in_public_score', models.IntegerField(blank=True, null=True)),
                ('avg_learning_in_public_score', models.FloatField(blank=True, null=True)),
                ('median_learning_in_public_score', models.FloatField(blank=True, null=True)),
                ('q1_learning_in_public_score', models.FloatField(blank=True, null=True)),
                ('q3_learning_in_public_score', models.FloatField(blank=True, null=True)),
                ('min_time_spent_lectures', models.FloatField(blank=True, null=True)),
                ('max_time_spent_lectures', models.FloatField(blank=True, null=True)),
                ('avg_time_spent_lectures', models.FloatField(blank=True, null=True)),
                ('median_time_spent_lectures', models.FloatField(blank=True, null=True)),
                ('q1_time_spent_lectures', models.FloatField(blank=True, null=True)),
                ('q3_time_spent_lectures', models.FloatField(blank=True, null=True)),
                ('min_time_spent_homework', models.FloatField(blank=True, null=True)),
                ('max_time_spent_homework', models.FloatField(blank=True, null=True)),
                ('avg_time_spent_homework', models.FloatField(blank=True, null=True)),
                ('median_time_spent_homework', models.FloatField(blank=True, null=True)),
                ('q1_time_spent_homework', models.FloatField(blank=True, null=True)),
                ('q3_time_spent_homework', models.FloatField(blank=True, null=True)),
                ('last_calculated', models.DateTimeField(auto_now=True)),
                ('homework', models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name='statistics', to='courses.homework')),
            ],
        ),
        migrations.AddField(
            model_name='course',
            name='finished',
            field=models.BooleanField(default=False, help_text='Whether the course has finished.'),
        ),
        migrations.RemoveField(
            model_name='homework',
            name='problems_comments_field',
        ),
        migrations.AddField(
            model_name='course',
            name='homework_problems_comments_field',
            field=models.BooleanField(default=False, help_text='Include field for problems and comments in homework'),
        ),
        migrations.RemoveField(
            model_name='project',
            name='points_to_pass',
        ),
        migrations.AddField(
            model_name='course',
            name='project_passing_score',
            field=models.IntegerField(default=0, help_text='Minimum score required to pass any project in this course'),
        ),
        migrations.AddField(
            model_name='course',
            name='min_projects_to_pass',
            field=models.IntegerField(default=1, help_text='The minimum number of projects to pass the course.'),
        ),
        migrations.CreateModel(
            name='ProjectStatistics',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('total_submissions', models.IntegerField(default=0)),
                ('min_project_score', models.IntegerField(blank=True, null=True)),
                ('max_project_score', models.IntegerField(blank=True, null=True)),
                ('avg_project_score', models.FloatField(blank=True, null=True)),
                ('median_project_score', models.FloatField(blank=True, null=True)),
                ('q1_project_score', models.FloatField(blank=True, null=True)),
                ('q3_project_score', models.FloatField(blank=True, null=True)),
                ('min_project_learning_in_public_score', models.IntegerField(blank=True, null=True)),
                ('max_project_learning_in_public_score', models.IntegerField(blank=True, null=True)),
                ('avg_project_learning_in_public_score', models.FloatField(blank=True, null=True)),
                ('median_project_learning_in_public_score', models.FloatField(blank=True, null=True)),
                ('q1_project_learning_in_public_score', models.FloatField(blank=True, null=True)),
                ('q3_project_learning_in_public_score', models.FloatField(blank=True, null=True)),
                ('min_peer_review_score', models.IntegerField(blank=True, null=True)),
                ('max_peer_review_score', models.IntegerField(blank=True, null=True)),
                ('avg_peer_review_score', models.FloatField(blank=True, null=True)),
                ('median_peer_review_score', models.FloatField(blank=True, null=True)),
                ('q1_peer_review_score', models.FloatField(blank=True, null=True)),
                ('q3_peer_review_score', models.FloatField(blank=True, null=True)),
                ('min_peer_review_learning_in_public_score', models.IntegerField(blank=True, null=True)),
                ('max_peer_review_learning_in_public_score', models.IntegerField(blank=True, null=True)),
                ('avg_peer_review_learning_in_public_score', models.FloatField(blank=True, null=True)),
                ('median_peer_review_learning_in_public_score', models.FloatField(blank=True, null=True)),
                ('q1_peer_review_learning_in_public_score', models.FloatField(blank=True, null=True)),
                ('q3_peer_review_learning_in_public_score', models.FloatField(blank=True, null=True)),
                ('min_total_score', models.IntegerField(blank=True, null=True)),
                ('max_total_score', models.IntegerField(blank=True, null=True)),
                ('avg_total_score', models.FloatField(blank=True, null=True)),
                ('median_total_score', models.FloatField(blank=True, null=True)),
                ('q1_total_score', models.FloatField(blank=True, null=True)),
                ('q3_total_score', models.FloatField(blank=True, null=True)),
                ('min_time_spent', models.FloatField(blank=True, null=True)),
                ('max_time_spent', models.FloatField(blank=True, null=True)),
                ('avg_time_spent', models.FloatField(blank=True, null=True)),
                ('median_time_spent', models.FloatField(blank=True, null=True)),
                ('q1_time_spent', models.FloatField(blank=True, null=True)),
                ('q3_time_spent', models.FloatField(blank=True, null=True)),
                ('last_calculated', models.DateTimeField(auto_now=True)),
                ('project', models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name='statistics', to='courses.project')),
            ],
        ),
        migrations.AddField(
            model_name='course',
            name='visible',
            field=models.BooleanField(default=True, help_text='Whether the course is visible in the course list. Non-visible courses are still accessible via direct link.'),
        ),
        migrations.AlterField(
            model_name='question',
            name='question_type',
            field=models.CharField(choices=[('MC', 'Multiple Choice'), ('FF', 'Free Form'), ('FL', 'Free Form Long'), ('CB', 'Checkboxes')], max_length=2),
        ),
        migrations.CreateModel(
            name='WrappedStatistics',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('year', models.IntegerField(help_text='The year for which statistics are calculated', unique=True)),
                ('is_visible', models.BooleanField(default=False, help_text='Whether to display this wrapped on the main page')),
                ('total_participants', models.IntegerField(default=0)),
                ('total_enrollments', models.IntegerField(default=0)),
                ('total_hours', models.FloatField(default=0)),
                ('total_certificates', models.IntegerField(default=0)),
                ('total_points', models.IntegerField(default=0)),
                ('course_stats', models.JSONField(default=list, help_text='List of courses with enrollment counts')),
                ('leaderboard', models.JSONField(default=list, help_text='Top 100 users by total score')),
                ('calculated_at', models.DateTimeField(auto_now=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
            ],
            options={
                'verbose_name': 'Wrapped Statistics',
                'verbose_name_plural': 'Wrapped Statistics',
                'ordering': ['-year'],
            },
        ),
        migrations.CreateModel(
            name='UserWrappedStatistics',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('total_points', models.IntegerField(default=0)),
                ('total_hours', models.FloatField(default=0)),
                ('homework_count', models.IntegerField(default=0)),
                ('project_count', models.IntegerField(default=0)),
                ('peer_reviews_given', models.IntegerField(default=0)),
                ('learning_in_public_count', models.IntegerField(default=0)),
                ('faq_contributions_count', models.IntegerField(default=0)),
                ('certificates_earned', models.IntegerField(default=0)),
                ('courses', models.JSONField(default=list, help_text='List of courses with scores')),
                ('rank', models.IntegerField(blank=True, null=True)),
                ('display_name', models.CharField(blank=True, max_length=200)),
                ('calculated_at', models.DateTimeField(auto_now=True)),
                ('user', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='wrapped_statistics', to=settings.AUTH_USER_MODEL)),
                ('wrapped', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='user_statistics', to='courses.wrappedstatistics')),
            ],
            options={
                'verbose_name': 'User Wrapped Statistics',
                'verbose_name_plural': 'User Wrapped Statistics',
                'ordering': ['rank'],
                'unique_together': {('wrapped', 'user')},
            },
        ),
        migrations.AddField(
            model_name='enrollment',
            name='disable_learning_in_public',
            field=models.BooleanField(default=False, help_text='When enabled, all learning in public scores are removed and future submissions are not counted', verbose_name='Disable learning in public'),
        ),
        migrations.AlterField(
            model_name='reviewcriteria',
            name='options',
            field=models.JSONField(validators=[courses.validators.criteria_validators.validate_review_criteria_options]),
        ),
        migrations.CreateModel(
            name='LeaderboardComplaint',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('issue_type', models.CharField(choices=[('learning_in_public', 'Incorrect learning in public links'), ('homework', 'Incorrect homework'), ('project', 'Incorrect project'), ('other', 'Other leaderboard issue')], max_length=32)),
                ('description', models.TextField()),
                ('resolved', models.BooleanField(default=False)),
                ('resolved_at', models.DateTimeField(blank=True, null=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('enrollment', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='complaints', to='courses.enrollment')),
                ('reporter', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='leaderboard_complaints', to=settings.AUTH_USER_MODEL)),
                ('resolved_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='resolved_leaderboard_complaints', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'ordering': ['resolved', '-created_at'],
            },
        ),
        migrations.AddField(
            model_name='enrollment',
            name='display_public_profile',
            field=models.BooleanField(default=False),
        ),
    ]
