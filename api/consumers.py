import asyncio
import os
from collections import deque

from channels.generic.websocket import AsyncJsonWebsocketConsumer
from django.conf import settings

POLL_INTERVAL = 1.5
INDEX_POLL_INTERVAL = 5
INIT_LINES = 300


def _valid_log_files():
    return {f for f in os.listdir(settings.LOG_DIR) if (settings.LOG_DIR / f).is_file()}


def _file_listing():
    files = []
    for name in sorted(_valid_log_files()):
        stat = (settings.LOG_DIR / name).stat()
        files.append({'name': name, 'size': stat.st_size, 'modified_at': stat.st_mtime})
    return files


class LogTailConsumer(AsyncJsonWebsocketConsumer):
    """Streams a log file to the client: an initial tail on connect, then any
    lines appended afterward. Replaces the log viewer's HTTP polling.
    """

    async def connect(self):
        user = self.scope.get('user')
        if not user or not user.is_authenticated or getattr(user, 'role', None) != 'ADMIN':
            await self.close(code=4401)
            return

        filename = self.scope['url_route']['kwargs']['filename']
        if filename not in await asyncio.to_thread(_valid_log_files):
            await self.close(code=4404)
            return

        self.path = settings.LOG_DIR / filename
        await self.accept()

        lines, self.offset = await asyncio.to_thread(self._read_tail)
        await self.send_json({'type': 'init', 'lines': lines})

        self._task = asyncio.create_task(self._poll())

    async def disconnect(self, code):
        task = getattr(self, '_task', None)
        if task:
            task.cancel()

    async def receive_json(self, content, **kwargs):
        pass  # client never sends messages; this is a one-way stream

    async def _poll(self):
        try:
            while True:
                await asyncio.sleep(POLL_INTERVAL)
                lines, self.offset, reset = await asyncio.to_thread(self._read_new, self.offset)
                if lines:
                    await self.send_json({'type': 'reset' if reset else 'append', 'lines': lines})
        except asyncio.CancelledError:
            pass

    def _read_tail(self):
        with open(self.path, encoding='utf-8', errors='replace') as f:
            tail = deque(f, maxlen=INIT_LINES)
        return [line.rstrip('\n') for line in tail], os.path.getsize(self.path)

    def _read_new(self, offset):
        size = os.path.getsize(self.path)
        if size < offset:
            # Rotated or truncated since our last read — resync with a fresh tail.
            lines, new_offset = self._read_tail()
            return lines, new_offset, True
        if size == offset:
            return [], offset, False
        with open(self.path, encoding='utf-8', errors='replace') as f:
            f.seek(offset)
            chunk = f.read()
            new_offset = f.tell()
        return [line for line in chunk.split('\n') if line], new_offset, False


class LogFileListConsumer(AsyncJsonWebsocketConsumer):
    """Streams the logs/ directory listing: sent on connect, then again only
    when a file is added/removed or a size/mtime changes (new rotation, growth).
    """

    async def connect(self):
        user = self.scope.get('user')
        if not user or not user.is_authenticated or getattr(user, 'role', None) != 'ADMIN':
            await self.close(code=4401)
            return

        await self.accept()
        self._last = await asyncio.to_thread(_file_listing)
        await self.send_json({'type': 'files', 'files': self._last})
        self._task = asyncio.create_task(self._poll())

    async def disconnect(self, code):
        task = getattr(self, '_task', None)
        if task:
            task.cancel()

    async def receive_json(self, content, **kwargs):
        pass  # one-way stream

    async def _poll(self):
        try:
            while True:
                await asyncio.sleep(INDEX_POLL_INTERVAL)
                current = await asyncio.to_thread(_file_listing)
                if current != self._last:
                    self._last = current
                    await self.send_json({'type': 'files', 'files': current})
        except asyncio.CancelledError:
            pass


class NotificationConsumer(AsyncJsonWebsocketConsumer):
    """Pushes a user's own notifications the instant they're created —
    replaces the notification bell's HTTP polling. Any authenticated user can
    connect; they only ever receive notifications addressed to them (scoped
    by the `notifications_<user_id>` group).
    """

    async def connect(self):
        user = self.scope.get('user')
        if not user or not user.is_authenticated:
            await self.close(code=4401)
            return

        self.group_name = f'notifications_{user.id}'
        await self.channel_layer.group_add(self.group_name, self.channel_name)
        await self.accept()

    async def disconnect(self, code):
        if hasattr(self, 'group_name'):
            await self.channel_layer.group_discard(self.group_name, self.channel_name)

    async def receive_json(self, content, **kwargs):
        pass  # one-way stream

    async def notify(self, event):
        await self.send_json({'type': 'notification', 'notification': event['notification']})
