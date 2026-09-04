from django.http import HttpResponseRedirect
from django.shortcuts import get_object_or_404
from django.views.decorators.http import require_GET

from api.models import ShortLink


@require_GET
def resolve_short_link(request, code):
    link = get_object_or_404(ShortLink, code=code)
    ShortLink.objects.filter(pk=link.pk).update(hits=link.hits + 1)
    return HttpResponseRedirect(link.target_url)
