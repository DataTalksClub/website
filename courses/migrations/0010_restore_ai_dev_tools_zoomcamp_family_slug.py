"""Restore the "-zoomcamp" suffix on the AI Dev Tools family/cohort slugs.

Migration 0009 dropped the "-zoomcamp" suffix from this course's family and
cohort slugs (and separately stripped the numeric prefix from its module
slugs -- a different, still-valid fix that this migration does not touch).
The owner has now reversed the family-slug half of that decision, verbatim:
"let's not drop zoomcamp - i think it could be important signal for google
and our zoomcamp is kind of a trademark, so let's also restore it for ai dev
tools." This migration renames the family and cohort rows a real
local/production database already has back to their "-zoomcamp" form; the
matching read-time behavior lives in courses/services/curriculum_import.py,
where FAMILY_SLUG_OVERRIDES is now empty (the raw incoming course.yaml slug,
"ai-dev-tools-zoomcamp", already matches the site's canonical family slug, so
no override is needed) while MODULE_SLUG_PREFIX_STRIP_FAMILIES stays its own
independent frozenset -- decoupled from FAMILY_SLUG_OVERRIDES specifically so
this reversal cannot silently take the module-prefix-stripping fix with it.

Module (``SharedModule``) slugs are deliberately left untouched: the
prefix-stripped form (``ai-native-workflow``, not ``01-ai-native-workflow``)
stays exactly as migration 0009 left it.

Idempotent and a no-op on a database that has never imported this course
(fresh/CI databases, most test databases): every step first checks the row
it would touch still has the old slug.
"""

from __future__ import annotations

from django.db import migrations

OLD_FAMILY_SLUG = "ai-dev-tools"
NEW_FAMILY_SLUG = "ai-dev-tools-zoomcamp"

# Module slugs are not part of this reversal -- see the module docstring.
NO_MODULE_SLUG_CHANGES: dict[str, str] = {}


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
        module_slugs=NO_MODULE_SLUG_CHANGES,
    )


def rename_backward(apps, schema_editor) -> None:
    _rename_family(
        apps,
        old_slug=NEW_FAMILY_SLUG,
        new_slug=OLD_FAMILY_SLUG,
        module_slugs=NO_MODULE_SLUG_CHANGES,
    )


class Migration(migrations.Migration):

    dependencies = [
        ("courses", "0009_rename_ai_dev_tools_zoomcamp_slug"),
    ]

    operations = [
        migrations.RunPython(rename_forward, rename_backward),
    ]
