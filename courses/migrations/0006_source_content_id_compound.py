"""Source content IDs become compound strings instead of UUIDs.

The type change has to stay vendor-free: Postgres refuses a plain
uuid-to-varchar ALTER COLUMN without a USING clause, and raw vendor-specific
SQL is out of bounds, so the column is rebuilt portably instead:

1. the uuid column is renamed (constraints follow the rename everywhere),
2. a fresh varchar column takes the real name,
3. a RunPython copy converts the stored UUID values to their canonical
   string form,
4. the constraints that referenced the uuid column are dropped and the
   renamed column is removed,
5. the same constraints are re-created against the varchar column.

The migration is deliberately not reversible: compound IDs have no UUID
representation, so once applied there is no mechanical way back to the uuid
column, and Django refuses unapplication up front instead of failing part
way through the schema dance.
"""

import django.core.validators
from django.db import migrations, models
from django.db.models import Q

COMPOUND_ID_VALIDATOR = django.core.validators.RegexValidator(
    "^[a-z0-9]+(?:-[a-z0-9]+)*(?:/[a-z0-9]+(?:-[a-z0-9]+)*)*$",
    "Enter a lowercase compound content ID: slug segments separated by slashes, "
    "namespaced by the course slug.",
)

LEGACY_COLUMN = "source_content_id_legacy"

# Every provenance model carrying the column.
PROVENANCE_MODELS = (
    "course",
    "cohort",
    "module",
    "unit",
    "homework",
    "question",
    "sharedcurriculum",
    "sharedmodule",
    "sharedlesson",
    "sharedcurriculumasset",
)


def _provenance_check(name: str, *identity_fields: str) -> models.CheckConstraint:
    """Mirror ``courses.models.curriculum_import.source_provenance_constraint``."""

    fields = (
        *(identity_fields or ("source_content_id",)),
        "source_path",
        "source_commit_sha",
        "source_checksum",
    )
    absent = Q(**{f"{field}__isnull": True for field in fields})
    present = Q(**{f"{field}__isnull": False for field in fields})
    present &= Q(source_commit_sha__regex=r"^[0-9a-f]{40}$")
    present &= Q(source_checksum__regex=r"^[0-9a-f]{64}$")
    return models.CheckConstraint(condition=absent | present, name=name)


def _unique(*fields: str, name: str) -> models.UniqueConstraint:
    return models.UniqueConstraint(
        fields=fields,
        condition=Q(source_content_id__isnull=False),
        name=name,
    )


# Unique constraints referencing the column, exactly as their models declare.
_UNIQUE_CONSTRAINTS_BY_MODEL: dict[str, tuple[models.UniqueConstraint, ...]] = {
    "course": (_unique("source_content_id", name="courses_course_source_content_uq"),),
    "cohort": (_unique("course", "source_content_id", name="courses_cohort_source_content_uq"),),
    "module": (_unique("cohort", "source_content_id", name="courses_module_source_content_uq"),),
    "unit": (_unique("module", "source_content_id", name="courses_unit_source_content_uq"),),
    "homework": (
        _unique("course", "source_content_id", name="courses_homework_source_content_uq"),
    ),
    "question": (
        _unique(
            "homework",
            "source_content_id",
            name="courses_question_source_content_uq",
        ),
    ),
    "sharedmodule": (
        _unique(
            "curriculum",
            "source_content_id",
            name="courses_shared_module_source_content_uq",
        ),
    ),
    "sharedlesson": (
        _unique("module", "source_content_id", name="courses_shared_lesson_source_content_uq"),
    ),
}

# Check constraints referencing the column, exactly as their models declare.
_CHECK_CONSTRAINTS_BY_MODEL: dict[str, tuple[models.CheckConstraint, ...]] = {
    "course": (
        _provenance_check(
            "courses_course_source_complete", "source_content_id", "source_stable_id"
        ),
    ),
    "cohort": (_provenance_check("courses_cohort_source_complete"),),
    "module": (_provenance_check("courses_module_source_complete"),),
    "unit": (_provenance_check("courses_unit_source_complete"),),
    "homework": (_provenance_check("courses_homework_source_complete"),),
    "question": (
        _provenance_check(
            "courses_question_source_complete", "source_content_id", "source_question_id"
        ),
    ),
    "sharedcurriculum": (_provenance_check("courses_shared_curriculum_source_complete"),),
    "sharedmodule": (_provenance_check("courses_shared_module_source_complete"),),
    "sharedlesson": (_provenance_check("courses_shared_lesson_source_complete"),),
}


def _compound_field() -> models.CharField:
    return models.CharField(
        blank=True,
        max_length=255,
        null=True,
        validators=[COMPOUND_ID_VALIDATOR],
    )


def copy_uuids_to_compound_ids(apps, schema_editor) -> None:
    for model_name in PROVENANCE_MODELS:
        model = apps.get_model("courses", model_name)
        for row in model.objects.exclude(**{LEGACY_COLUMN: None}).iterator():
            row.source_content_id = str(getattr(row, LEGACY_COLUMN))
            row.save(update_fields=("source_content_id",))


def _operations():
    operations = []
    for model_name in PROVENANCE_MODELS:
        operations.append(
            migrations.RenameField(
                model_name=model_name,
                old_name="source_content_id",
                new_name=LEGACY_COLUMN,
            )
        )
    for model_name in PROVENANCE_MODELS:
        operations.append(
            migrations.AddField(
                model_name=model_name,
                name="source_content_id",
                field=_compound_field(),
            )
        )
    # No reverse code: the migration is deliberately irreversible, so Django
    # refuses unapplication up front instead of failing mid-rewrite.
    operations.append(migrations.RunPython(copy_uuids_to_compound_ids))
    for constraints_by_model in (
        _UNIQUE_CONSTRAINTS_BY_MODEL,
        _CHECK_CONSTRAINTS_BY_MODEL,
    ):
        for model_name, constraints in constraints_by_model.items():
            for constraint in constraints:
                operations.append(
                    migrations.RemoveConstraint(model_name=model_name, name=constraint.name)
                )
    for model_name in PROVENANCE_MODELS:
        operations.append(migrations.RemoveField(model_name=model_name, name=LEGACY_COLUMN))
    for constraints_by_model in (
        _UNIQUE_CONSTRAINTS_BY_MODEL,
        _CHECK_CONSTRAINTS_BY_MODEL,
    ):
        for model_name, constraints in constraints_by_model.items():
            for constraint in constraints:
                operations.append(
                    migrations.AddConstraint(model_name=model_name, constraint=constraint)
                )
    return operations


class Migration(migrations.Migration):
    dependencies = [
        ("courses", "0005_shared_current_curriculum"),
    ]

    operations = _operations()
