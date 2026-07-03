from django.urls import re_path

from api.consumers import LogFileListConsumer, LogTailConsumer

websocket_urlpatterns = [
    re_path(r'^ws/logs-index/$',              LogFileListConsumer.as_asgi()),
    re_path(r'^ws/logs/(?P<filename>[^/]+)/$', LogTailConsumer.as_asgi()),
]
