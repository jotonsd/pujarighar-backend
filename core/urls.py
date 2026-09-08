from django.urls import path, include
from django.conf import settings
from django.conf.urls.static import static

from api.views.short_link_views import resolve_short_link

urlpatterns = [
    path('api/', include('api.urls')),
    path('t/<str:code>/', resolve_short_link, name='short-link'),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
