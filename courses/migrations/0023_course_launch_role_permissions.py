from django.db import migrations


def grant_launch_permissions(apps, schema_editor):
    database = schema_editor.connection.alias
    ContentType = apps.get_model("contenttypes", "ContentType")
    Permission = apps.get_model("auth", "Permission")
    Group = apps.get_model("auth", "Group")
    content_type, _ = ContentType.objects.using(database).get_or_create(app_label="courses", model="announcement")
    permissions = {}
    for verb in ("read", "manage", "send"):
        permission, _ = Permission.objects.using(database).get_or_create(content_type=content_type, codename=f"{verb}_course_launch", defaults={"name": f"Can {verb} course launch"})
        permissions[verb] = permission
    grants = {"site_admin": ("read", "manage", "send"), "course_operator": ("read", "manage"), "email_operator": ("read", "send")}
    for group in Group.objects.using(database).filter(name__in=grants):
        for verb in grants[group.name]:
            group.permissions.add(permissions[verb])


def remove_launch_permissions(apps, schema_editor):
    Group = apps.get_model("auth", "Group")
    Permission = apps.get_model("auth", "Permission")
    database = schema_editor.connection.alias
    permissions = Permission.objects.using(database).filter(content_type__app_label="courses", codename__in=("read_course_launch", "manage_course_launch", "send_course_launch"))
    for group in Group.objects.using(database).filter(name__in=("site_admin", "course_operator", "email_operator")):
        group.permissions.remove(*permissions)


class Migration(migrations.Migration):
    dependencies = [
        ("courses", "0022_announcementtemplate_cohort_starts_at_and_more"),
        ("auth", "0012_alter_user_first_name_max_length"),
    ]
    operations = [migrations.RunPython(grant_launch_permissions, remove_launch_permissions)]
