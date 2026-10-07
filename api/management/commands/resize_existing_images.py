"""Retroactively shrinks already-uploaded images that predate (or somehow
bypassed) the `resize_image_field` pass in each model's `save()` — new
uploads are already handled automatically, this is a one-off catch-up for
old files sitting on disk at full camera/export resolution (some found at
2000-2400px and 300-700KB), which is what was actually slowing image loads
on the site.

Usage:
    python manage.py resize_existing_images            # dry run, reports only
    python manage.py resize_existing_images --apply     # actually resize + save
"""
from django.core.management.base import BaseCommand
from django.db import transaction
from PIL import Image as PILImage

from api.models import Banner, HeroSlide, ProductImage
from api.utils.image_resize import resize_image_field

# (model, image attr, label fn, target_bytes) — target_bytes mirrors each
# model's own save() override, since a full-width hero banner needs a
# bigger budget than a product thumbnail before quality loss shows.
_MODELS = [
    (ProductImage, 'image', lambda o: f'{o.product.name_en} #{o.id}', 80 * 1024, {'select_related': 'product'}),
    (HeroSlide, 'image', lambda o: f'{o.title_en or o.title_bn or "Slide"} #{o.id}', 150 * 1024, {}),
    (Banner, 'image', lambda o: f'{o.title_en} #{o.id}', 80 * 1024, {}),
]


class Command(BaseCommand):
    help = 'Resize existing uploaded images that are larger than their standard cap (dry-run by default).'

    def add_arguments(self, parser):
        parser.add_argument('--apply', action='store_true', help='Actually resize and save (default is dry-run).')
        parser.add_argument('--max-dimension', type=int, default=1600)

    def handle(self, *args, **options):
        apply = options['apply']
        max_dim = options['max_dimension']
        total_before = 0
        total_after = 0
        touched = 0

        for model, attr, label_fn, target_bytes, query_opts in _MODELS:
            qs = model.objects.all()
            if query_opts.get('select_related'):
                qs = qs.select_related(query_opts['select_related'])

            for obj in qs:
                field = getattr(obj, attr)
                if not field or not field.name:
                    continue
                try:
                    field.open()
                    size_before = field.size
                except (FileNotFoundError, ValueError):
                    self.stdout.write(self.style.WARNING(f'  missing file: {field.name}'))
                    continue

                try:
                    with PILImage.open(field) as pil_img:
                        w, h = pil_img.size
                except Exception as e:
                    self.stdout.write(self.style.WARNING(f'  unreadable: {field.name} ({e})'))
                    continue
                finally:
                    field.seek(0)

                if max(w, h) <= max_dim and size_before <= target_bytes:
                    continue

                touched += 1
                total_before += size_before
                label = f'{model.__name__} {label_fn(obj)}'
                if apply:
                    with transaction.atomic():
                        resize_image_field(field, max_dimension=max_dim, target_bytes=target_bytes)
                        obj.save(update_fields=None)
                    size_after = field.size
                    total_after += size_after
                    self.stdout.write(
                        f'  resized {label}: {size_before/1024:.0f}KB -> {size_after/1024:.0f}KB'
                    )
                else:
                    self.stdout.write(f'  would resize {label}: {size_before/1024:.0f}KB -> ~{target_bytes//1024}KB')

        if not touched:
            self.stdout.write(self.style.SUCCESS('No oversized images found.'))
            return

        if apply:
            self.stdout.write(self.style.SUCCESS(
                f'\nResized {touched} images: {total_before/1024:.0f}KB -> {total_after/1024:.0f}KB total'
            ))
        else:
            self.stdout.write(self.style.WARNING(
                f'\n{touched} images would be resized ({total_before/1024:.0f}KB total). Re-run with --apply to do it.'
            ))
