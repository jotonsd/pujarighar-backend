#!/usr/bin/env python
import os
import sys

from decouple import config

SETTINGS_MODULE = 'core.settings.prod' if config('ENVIRONMENT', default='development') == 'production' else 'core.settings.dev'


def main():
    os.environ.setdefault('DJANGO_SETTINGS_MODULE', SETTINGS_MODULE)
    try:
        from django.core.management import execute_from_command_line
    except ImportError as exc:
        raise ImportError("Couldn't import Django.") from exc
    execute_from_command_line(sys.argv)


if __name__ == '__main__':
    main()
