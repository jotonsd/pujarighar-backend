from django.db import migrations
from django.utils.text import slugify


def backfill_slugs(apps, schema_editor):
    Product = apps.get_model('api', 'Product')
    for product in Product.objects.filter(slug__isnull=True).order_by('created_at'):
        base = slugify(product.name_en) or slugify(product.name_bn, allow_unicode=True) or 'product'
        slug = base
        n = 1
        while Product.objects.filter(slug=slug).exclude(pk=product.pk).exists():
            n += 1
            slug = f'{base}-{n}'
        Product.objects.filter(pk=product.pk).update(slug=slug)


def noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('api', '0042_product_add_slug'),
    ]

    operations = [
        migrations.RunPython(backfill_slugs, noop),
    ]
