from django.db import migrations

TIME_BOXED_BADGES = ['new', 'trendy', 'flash_sale']


def clear_time_boxed_badges(apps, schema_editor):
    # One-time production reset, by request: every product currently
    # carrying 'new', 'trendy', or 'flash_sale' gets those actually removed
    # from `badges` (not just hidden by the 2-week expiry window the
    # previous migration's fields enable) — any badge outside
    # TIME_BOXED_BADGES is left untouched. From here on, each of these
    # badges only reappears when an admin explicitly (re-)checks it on a
    # product, which starts a fresh 2-week window (see
    # Product.save/effective_badges).
    Product = apps.get_model('api', 'Product')
    for product in Product.objects.all():
        badges = product.badges or []
        if any(b in TIME_BOXED_BADGES for b in badges):
            product.badges = [b for b in badges if b not in TIME_BOXED_BADGES]
            product.new_badge_set_at = None
            product.trendy_badge_set_at = None
            product.flash_sale_badge_set_at = None
            product.save(update_fields=[
                'badges', 'new_badge_set_at', 'trendy_badge_set_at', 'flash_sale_badge_set_at',
            ])


class Migration(migrations.Migration):

    dependencies = [
        ('api', '0110_product_flash_sale_badge_set_at_and_more'),
    ]

    operations = [
        migrations.RunPython(clear_time_boxed_badges, migrations.RunPython.noop),
    ]
