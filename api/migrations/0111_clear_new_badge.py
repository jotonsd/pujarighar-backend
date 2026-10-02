from django.db import migrations


def clear_new_badge(apps, schema_editor):
    # One-time production reset, by request: every product currently
    # carrying the 'new' badge (regardless of how long ago it was tagged)
    # gets it actually removed, not just hidden by the 2-week expiry window
    # added in the previous migration. From here on, 'new' only reappears
    # when an admin explicitly (re-)checks it on a product, which starts a
    # fresh 2-week window (see Product.save/effective_badges).
    Product = apps.get_model('api', 'Product')
    for product in Product.objects.filter(badges__contains=['new']):
        product.badges = [b for b in product.badges if b != 'new']
        product.new_badge_set_at = None
        product.save(update_fields=['badges', 'new_badge_set_at'])


class Migration(migrations.Migration):

    dependencies = [
        ('api', '0110_product_new_badge_set_at'),
    ]

    operations = [
        migrations.RunPython(clear_new_badge, migrations.RunPython.noop),
    ]
