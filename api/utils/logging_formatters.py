import logging

from django.conf import settings


class RelativePathFormatter(logging.Formatter):
    """Strips the project's absolute BASE_DIR prefix from log messages
    (e.g. Django's autoreload "file changed" messages embed a full path),
    so logs show paths relative to the project root instead.
    """

    def format(self, record):
        message = super().format(record)
        base = str(settings.BASE_DIR)
        return message.replace(base + '/', '').replace(base, '.')
