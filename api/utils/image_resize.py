"""Shrinks an uploaded image in place before it's saved to storage.

Uploads (especially from phone cameras) routinely come in at 2000-4000px
and several hundred KB-to-multi-MB, but nowhere in this app — product grid
thumbnails, the detail page's gallery, banners — ever needs more than
`max_dimension` px on the long side. Capping dimensions and re-encoding at
a reasonable JPEG/WEBP quality cuts typical file size by 60-90% with no
visible quality loss on a phone screen, which is the single biggest lever
on real-world image load time (bytes over the wire dominate, far more than
client-side decode cost).
"""
from io import BytesIO
from django.core.files.base import ContentFile
from django.core.files.uploadedfile import InMemoryUploadedFile
from PIL import Image, ImageOps


def resize_image_field(image_field, max_dimension: int = 1600, quality: int = 85) -> None:
    """Mutates `image_field` (a Django `ImageField`/`ImageFieldFile`
    attribute, e.g. `instance.image`) in place, replacing its file with a
    resized+recompressed version — call this from a model's `save()`
    override, before `super().save()`, whenever `image_field` holds a
    freshly-uploaded file. No-ops if the file is already within bounds.
    """
    if not image_field or not image_field.name:
        return

    image_field.seek(0)
    img = Image.open(image_field)
    # Respect the camera's EXIF orientation tag before measuring/resizing —
    # otherwise a portrait photo shot sideways (common on phones) gets
    # saved rotated, since Pillow otherwise ignores EXIF orientation.
    img = ImageOps.exif_transpose(img)

    if img.width <= max_dimension and img.height <= max_dimension:
        return

    img.thumbnail((max_dimension, max_dimension), Image.LANCZOS)

    # Flatten transparency onto white — re-encoding a PNG/WEBP with alpha
    # straight to JPEG would otherwise turn transparent areas black.
    if img.mode in ('RGBA', 'LA', 'P'):
        background = Image.new('RGB', img.size, (255, 255, 255))
        background.paste(img, mask=img.convert('RGBA').split()[-1])
        img = background
    elif img.mode != 'RGB':
        img = img.convert('RGB')

    buffer = BytesIO()
    img.save(buffer, format='JPEG', quality=quality, optimize=True)
    buffer.seek(0)

    original_name = image_field.name.rsplit('/', 1)[-1]
    new_name = original_name.rsplit('.', 1)[0] + '.jpg'
    image_field.save(
        new_name,
        InMemoryUploadedFile(buffer, None, new_name, 'image/jpeg', buffer.getbuffer().nbytes, None),
        save=False,
    )
