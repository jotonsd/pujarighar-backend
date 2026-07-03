import os
from collections import deque

from django.conf import settings
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated

from api.permissions import IsAdmin
from api.utils.response import ApiResponse

MAX_LINES = 2000
DEFAULT_LINES = 300


def _log_files():
    return sorted(
        f for f in os.listdir(settings.LOG_DIR)
        if (settings.LOG_DIR / f).is_file()
    )


@api_view(['GET'])
@permission_classes([IsAuthenticated, IsAdmin])
def list_log_files(request):
    files = []
    for name in _log_files():
        stat = (settings.LOG_DIR / name).stat()
        files.append({
            'name': name,
            'size': stat.st_size,
            'modified_at': stat.st_mtime,
        })
    return ApiResponse(message='Log files retrieved', data=files)


@api_view(['GET'])
@permission_classes([IsAuthenticated, IsAdmin])
def get_log_file(request, filename):
    if filename not in _log_files():
        return ApiResponse(message='Log file not found', errors='NOT_FOUND', status_code=404)

    try:
        lines = int(request.query_params.get('lines', DEFAULT_LINES))
    except ValueError:
        lines = DEFAULT_LINES
    lines = max(1, min(lines, MAX_LINES))

    q = request.query_params.get('q', '').strip()

    path = settings.LOG_DIR / filename
    with open(path, encoding='utf-8', errors='replace') as f:
        if q:
            matched = [line for line in f if q.lower() in line.lower()]
            tail = matched[-lines:]
        else:
            tail = deque(f, maxlen=lines)

    return ApiResponse(message='Log content retrieved', data={
        'name': filename,
        'lines': [line.rstrip('\n') for line in tail],
    })
