"""Converge databases that hold two family rows for one course.

The curriculum importer keyed the family row on the repository's own course slug,
so the ``ai-dev-tools-zoomcamp`` repository minted a second family beside the
published ``ai-dev-tools`` one and orphaned the 2026 cohort there.  The importer
now normalizes to the published slug, but a succeeded import run replays instead
of re-applying, so the existing rows only converge here.
"""

from django.db import migrations

import courses.migration_family_identity


def merge_families(apps, schema_editor):
    courses.migration_family_identity.merge_duplicate_course_families(
        apps.get_model("courses", "Course"),
        apps.get_model("courses", "Cohort"),
    )


class Migration(migrations.Migration):
    dependencies = [
        ("courses", "0051_unitreadstate"),
    ]

    operations = [
        # Irreversible by design: the duplicate family row carried no information
        # the surviving row does not keep, and re-splitting one course across two
        # catalogue entries is never a wanted state.
        migrations.RunPython(merge_families, migrations.RunPython.noop),
    ]
