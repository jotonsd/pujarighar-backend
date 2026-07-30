from django.db import migrations

# 'delete' is no longer a grantable permission dimension — resources are
# managed via view/create/edit only. Endpoints previously gated by
# has_permission(module, 'delete') keep working unchanged: with no matching
# Permission row left to grant, only the ADMIN superuser bypass can ever
# satisfy that check, so those actions become ADMIN-only automatically —
# no view-file changes needed.

def remove_delete_permissions(apps, schema_editor):
    Permission = apps.get_model('api', 'Permission')
    Permission.objects.filter(action='delete').delete()


def restore_delete_permissions(apps, schema_editor):
    # Irreversible by design (the seed data for these rows lived in migration
    # 0049's one-off RunPython, not reconstructable generically here).
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('api', '0051_add_missing_permissions'),
    ]

    operations = [
        migrations.RunPython(remove_delete_permissions, restore_delete_permissions),
    ]
