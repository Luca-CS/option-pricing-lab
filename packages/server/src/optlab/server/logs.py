"""Log files of the server's processes (handoff §4.12): JSON lines in `<data>/logs/<role>.log`, a new file every
midnight, `Settings.log_keep_days` of them kept (the rotation is the handler's; the `housekeeping` schedule removes
what a stopped process left behind). The console keeps the plain format of `optlab.core.log.configure`."""

from __future__ import annotations

import json
import logging
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path

from optlab.server.settings import Settings


class JsonLines(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        out = {
            "at": self.formatTime(record, "%Y-%m-%dT%H:%M:%S") + f".{int(record.msecs):03d}",
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "thread": record.threadName,
        }
        if record.exc_info:
            out["trace"] = self.formatException(record.exc_info)
        return json.dumps(out, ensure_ascii=False)


_HANDLER: TimedRotatingFileHandler | None = None
SERVER_LOGGERS = ("uvicorn.error", "uvicorn.access")  # the requests and the server's own messages, in the api's file


def to_files(settings: Settings, role: str, level: str = "INFO") -> Path:
    """Add the rotating JSON file of this process (`api` or `worker`) to the optlab logger; returns its path."""
    global _HANDLER
    folder = Path(settings.logs)
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{role}.log"
    root = logging.getLogger("optlab")
    for h in list(root.handlers):
        if isinstance(h, TimedRotatingFileHandler):
            root.removeHandler(h)
            h.close()
    handler = TimedRotatingFileHandler(path, when="midnight", backupCount=settings.log_keep_days, encoding="utf-8")
    handler.setFormatter(JsonLines())
    handler.setLevel(level.upper())
    root.addHandler(handler)
    _HANDLER = handler
    return path


def attach_server_logs() -> None:
    """Uvicorn's loggers into the same file, once uvicorn has configured them (the api's startup calls it)."""
    if _HANDLER is None:
        return
    for name in SERVER_LOGGERS:
        logger = logging.getLogger(name)
        if _HANDLER not in logger.handlers:
            logger.addHandler(_HANDLER)
