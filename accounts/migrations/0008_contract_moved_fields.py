"""Contract: remove the twelve moved fields and the moved constraint.

Plan issue D3.1d (playbook P7, DTC step 1, contract half). The course-platform
columns moved to ``courses.LearnerProfile`` and the identity reconciliation
columns to ``accounts_ext.IdentityState``; both extension data-copy migrations
ran in the expand phase and every reader and writer has since been switched, so
every value is already where it is read from.

The conditional unique constraint ``accounts_active_normalized_email_unique``
is removed here and recreated, under the same name, over
``accounts_ext.IdentityState`` by ``accounts_ext.0004`` -- it moves, it is not
renamed. It could not be created there while this index still held the name, so
that migration depends on this one.

Reversible as migration operations: the reverse recreates the dropped columns
with their declared defaults and recreates the constraint. The pre-contract
values live on in the extension rows, and reversing the extension copy
migrations restores them onto these columns; reversing this migration alone
gives every account the column defaults. Reversing after newer profile writes
therefore reopens the window where user columns and extension rows disagree --
it is a rollback tool, not a data round trip.

DTC-only additions stay untouched: ``username``, ``newsletter_subscribed``,
``home_dismissals`` and ``newsletter_preference_changed_at`` are reconciled by
the shared-model adoption (D3.2/C3.7), not here.
"""

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0007_move_identity_models_state"),
        ("accounts_ext", "0003_identity_state_refresh"),
        ("courses", "0018_learnerprofile_refresh"),
    ]

    operations = [
        migrations.RemoveConstraint(
            model_name="customuser",
            name="accounts_active_normalized_email_unique",
        ),
        migrations.RemoveField(model_name="customuser", name="role"),
        migrations.RemoveField(model_name="customuser", name="certificate_name"),
        migrations.RemoveField(model_name="customuser", name="country"),
        migrations.RemoveField(model_name="customuser", name="region"),
        migrations.RemoveField(model_name="customuser", name="registration_role"),
        migrations.RemoveField(model_name="customuser", name="github_url"),
        migrations.RemoveField(model_name="customuser", name="linkedin_url"),
        migrations.RemoveField(model_name="customuser", name="personal_website_url"),
        migrations.RemoveField(model_name="customuser", name="about_me"),
        migrations.RemoveField(model_name="customuser", name="dark_mode"),
        migrations.RemoveField(model_name="customuser", name="normalized_email"),
        migrations.RemoveField(model_name="customuser", name="identity_state"),
    ]
