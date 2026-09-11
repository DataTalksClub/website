"""Rename the ai-dev-tools-zoomcamp family/cohort/module slugs to their canonical form.

The owner ruled the site's canonical family slug for this course drops the
"-zoomcamp" suffix (matching how courses.datatalks.club already publishes it:
.../ai-dev-tools-2026/) and, for this one family only, drops the module
directory's numeric prefix from the published module slug. This migration
renames the rows a real local/production database already has; the matching
read-time normalization that keeps every future sync converging on the same
values lives in courses/services/curriculum_import.py
(FAMILY_SLUG_OVERRIDES / MODULE_SLUG_PREFIX_STRIP_FAMILIES). The two must
land together: the importer's protected_course_slug_change guard compares
the stored Course.slug against the *overridden* incoming slug on every sync,
so it only stays quiet if both sides already agree.

Idempotent and a no-op on a database that has never imported this course
(fresh/CI databases, most test databases): every step first checks the row
it would touch still has the old slug.
"""

from __future__ import annotations

from django.db import migrations

OLD_FAMILY_SLUG = "ai-dev-tools-zoomcamp"
NEW_FAMILY_SLUG = "ai-dev-tools"

OLD_TO_NEW_MODULE_SLUGS = {
    "01-ai-native-workflow": "ai-native-workflow",
    "02-development": "development",
    "03-deployment": "deployment",
    "04-devops": "devops",
}
NEW_TO_OLD_MODULE_SLUGS = {new: old for old, new in OLD_TO_NEW_MODULE_SLUGS.items()}


def _rename_family(apps, *, old_slug: str, new_slug: str, module_slugs: dict[str, str]) -> None:
    Course = apps.get_model("courses", "Course")
    Cohort = apps.get_model("courses", "Cohort")
    SharedModule = apps.get_model("courses", "SharedModule")

    course = Course.objects.filter(slug=old_slug).first()
    if course is None:
        return
    if Course.objects.filter(slug=new_slug).exclude(pk=course.pk).exists():
        raise RuntimeError(
            f"cannot rename course slug {old_slug!r} to {new_slug!r}: "
            f"a Course row already has slug {new_slug!r}"
        )

    prefix = f"{old_slug}-"
    for cohort in Cohort.objects.filter(course=course, slug__startswith=prefix):
        new_cohort_slug = f"{new_slug}-{cohort.slug.removeprefix(prefix)}"
        if Cohort.objects.filter(slug=new_cohort_slug).exclude(pk=cohort.pk).exists():
            raise RuntimeError(
                f"cannot rename cohort slug {cohort.slug!r} to {new_cohort_slug!r}: taken"
            )
        cohort.slug = new_cohort_slug
        cohort.save(update_fields=["slug"])

    for module in SharedModule.objects.filter(curriculum__course=course):
        new_module_slug = module_slugs.get(module.slug)
        if new_module_slug is None:
            continue
        collision = (
            SharedModule.objects.filter(curriculum=module.curriculum, slug=new_module_slug)
            .exclude(pk=module.pk)
            .exists()
        )
        if collision:
            raise RuntimeError(
                f"cannot rename module slug {module.slug!r} to {new_module_slug!r}: taken"
            )
        module.slug = new_module_slug
        module.save(update_fields=["slug"])

    course.slug = new_slug
    course.save(update_fields=["slug"])


def rename_forward(apps, schema_editor) -> None:
    _rename_family(
        apps,
        old_slug=OLD_FAMILY_SLUG,
        new_slug=NEW_FAMILY_SLUG,
        module_slugs=OLD_TO_NEW_MODULE_SLUGS,
    )


def rename_backward(apps, schema_editor) -> None:
    _rename_family(
        apps,
        old_slug=NEW_FAMILY_SLUG,
        new_slug=OLD_FAMILY_SLUG,
        module_slugs=NEW_TO_OLD_MODULE_SLUGS,
    )


class Migration(migrations.Migration):

    dependencies = [
        ("courses", "0008_remove_cohort_faq_document_url"),
    ]

    operations = [
        migrations.RunPython(rename_forward, rename_backward),
    ]
