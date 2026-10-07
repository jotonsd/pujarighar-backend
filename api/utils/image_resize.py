"""Shrinks an uploaded image in place before it's saved to storage.

Uploads (especially from phone cameras) routinely come in at 2000-4000px
and several hundred KB-to-multi-MB, but nowhere in this app — product grid
thumbnails, the detail page's gallery, banners — ever needs more than
`max_dimension` px on the long side. Targeting a byte budget (`target_bytes`)
by stepping quality down first, and only shrinking the resolution further as
a last resort, cuts typical file size by 60-90%+ with no visible quality
loss on a phone screen, which is the single biggest lever on real-world
image load time (bytes over the wire dominate, far more than client-side
decode cost).
"""
from io import BytesIO
from django.core.files.uploadedfile import InMemoryUploadedFile
from PIL import Image, ImageOps

# Quality steps tried (in order) at the target dimension before resolution
# itself is reduced — stopping at 40 keeps JPEG/WEBP artifacts from becoming
# visible; below that, a smaller image looks better than a blockier one.
_QUALITY_STEPS = (85, 75, 65, 55, 45, 40)
# If quality alone can't hit the byte budget, shrink the longest side by
# this factor and retry at the top quality step again, down to a floor so a
# tiny product photo never gets reduced to a postage stamp chasing the
# target.
_DIMENSION_SHRINK_FACTOR = 0.85
_MIN_DIMENSION = 700
_MAX_DIMENSION_ATTEMPTS = 6


def _encode(img: Image.Image, output_format: str, quality: int) -> bytes:
    buffer = BytesIO()
    if output_format == 'WEBP':
        img.save(buffer, format='WEBP', quality=quality, method=6)
    else:
        img.save(buffer, format='JPEG', quality=quality, optimize=True)
    return buffer.getvalue()


def resize_image_field(
    image_field,
    max_dimension: int = 1600,
    quality: int = 85,
    target_bytes: int = 80 * 1024,
) -> None:
    """Mutates `image_field` (a Django `ImageField`/`ImageFieldFile`
    attribute, e.g. `instance.image`) in place, replacing its file with a
    resized+recompressed version targeting `target_bytes` — call this from
    a model's `save()` override, before `super().save()`, whenever
    `image_field` holds a freshly-uploaded file. No-ops if the file is
    already within bounds on both dimensions AND size.

    Strategy (resolution-preserving by design): cap the long side at
    `max_dimension` first, then step JPEG/WEBP quality down through
    `_QUALITY_STEPS` until the byte budget is hit. Only if the lowest
    acceptable quality still isn't small enough does it start shrinking the
    resolution itself, in small steps, re-trying quality from the top each
    time — so a crisp 80KB result at slightly smaller dimensions is always
    preferred over a blocky one at full size.
    """
    if not image_field or not image_field.name:
        return

    image_field.seek(0)
    original_size = image_field.size
    img = Image.open(image_field)
    # Respect the camera's EXIF orientation tag before measuring/resizing —
    # otherwise a portrait photo shot sideways (common on phones) gets
    # saved rotated, since Pillow otherwise ignores EXIF orientation.
    img = ImageOps.exif_transpose(img)

    needs_resize = img.width > max_dimension or img.height > max_dimension
    if not needs_resize and original_size <= target_bytes:
        return

    # Re-encode as WEBP when the source already is one — it's typically
    # 25-35% smaller than JPEG at equivalent visual quality, so forcing
    # every upload through JPEG (as this used to) could make an
    # already-efficient WEBP *larger* after "shrinking" it. JPEG stays the
    # output for everything else (including PNG, which JPEG beats for
    # photos) since there's no transparency to preserve on product photos.
    is_webp_source = (getattr(img, 'format', None) or '').upper() == 'WEBP'
    output_format = 'WEBP' if is_webp_source else 'JPEG'

    if output_format == 'JPEG':
        # Flatten transparency onto white — re-encoding a PNG/WEBP with
        # alpha straight to JPEG would otherwise turn transparent areas
        # black.
        if img.mode in ('RGBA', 'LA', 'P'):
            background = Image.new('RGB', img.size, (255, 255, 255))
            background.paste(img, mask=img.convert('RGBA').split()[-1])
            img = background
        elif img.mode != 'RGB':
            img = img.convert('RGB')

    dimension = max_dimension
    base = img
    if needs_resize or max(img.width, img.height) > max_dimension:
        base = img.copy()
        base.thumbnail((dimension, dimension), Image.LANCZOS)

    best_data = None
    for _ in range(_MAX_DIMENSION_ATTEMPTS):
        for q in _QUALITY_STEPS:
            data = _encode(base, output_format, q)
            if best_data is None or len(data) < len(best_data):
                best_data = data
            if len(data) <= target_bytes:
                best_data = data
                break
        else:
            # Every quality step tried at this resolution, still over
            # budget — shrink the resolution and try the quality ladder
            # again, unless we've hit the floor.
            if dimension <= _MIN_DIMENSION:
                break
            dimension = max(int(dimension * _DIMENSION_SHRINK_FACTOR), _MIN_DIMENSION)
            base = img.copy()
            base.thumbnail((dimension, dimension), Image.LANCZOS)
            continue
        break

    # Re-encoding can occasionally come out larger than the original (a
    # heavily-optimized source re-saved at similar quality has little left
    # to gain) — keep the original in that case rather than regressing it.
    if best_data is None or (len(best_data) >= original_size and not needs_resize):
        return

    original_name = image_field.name.rsplit('/', 1)[-1]
    ext = 'webp' if output_format == 'WEBP' else 'jpg'
    new_name = original_name.rsplit('.', 1)[0] + '.' + ext
    content_type = 'image/webp' if output_format == 'WEBP' else 'image/jpeg'
    image_field.save(
        new_name,
        InMemoryUploadedFile(BytesIO(best_data), None, new_name, content_type, len(best_data), None),
        save=False,
    )
