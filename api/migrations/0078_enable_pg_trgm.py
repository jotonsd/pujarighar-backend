from django.db import migrations


def enable_pg_trgm(apps, schema_editor):
    # Only meaningful on Postgres — this app can also run on MySQL (see
    # DB_ENGINE in settings), where this is a no-op rather than an error.
    if schema_editor.connection.vendor != 'postgresql':
        return
    with schema_editor.connection.cursor() as cursor:
        cursor.execute('CREATE EXTENSION IF NOT EXISTS pg_trgm')


def disable_pg_trgm(apps, schema_editor):
    if schema_editor.connection.vendor != 'postgresql':
        return
    with schema_editor.connection.cursor() as cursor:
        cursor.execute('DROP EXTENSION IF EXISTS pg_trgm')


class Migration(migrations.Migration):

    dependencies = [
        ('api', '0077_add_ai_ordering_toggle'),
    ]

    operations = [
        migrations.RunPython(enable_pg_trgm, disable_pg_trgm),
    ]
