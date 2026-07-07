import os
from logging.handlers import TimedRotatingFileHandler


class DailyRotatingFileHandler(TimedRotatingFileHandler):
    """Rotated files are named '<stem>_<date>.<ext>' (e.g. app_2026_07_02.log)
    instead of the library default '<stem>.<ext>.<date>' (app.log.2026-07-02),
    so every log file keeps its .log extension and sorts naturally by name.
    """

    def rotation_filename(self, default_name):
        directory = os.path.dirname(default_name)
        stem, ext = os.path.splitext(os.path.basename(self.baseFilename))
        date_part = default_name[len(self.baseFilename) + 1:].replace('-', '_')
        return os.path.join(directory, f'{stem}_{date_part}{ext}')
