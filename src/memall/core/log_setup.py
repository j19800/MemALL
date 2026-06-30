"""Shared logging configuration — JSON-structured output for all entry points."""

import json
import logging
import logging.config
import os
import sys
from datetime import datetime, timezone


class JsonFormatter(logging.Formatter):
    """Output each log record as a single compact JSON line."""

    def format(self, record: logging.LogRecord) -> str:
        ts = datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat()
        obj = {
            "ts": ts,
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
            "module": record.module,
            "func": record.funcName,
            "line": record.lineno,
        }
        if record.exc_info and record.exc_info[0]:
            obj["exc"] = self.formatException(record.exc_info)
        if hasattr(record, "extra_fields"):
            obj.update(record.extra_fields)
        return json.dumps(obj, ensure_ascii=False)


class ExtraLogger(logging.Logger):
    """Logger subclass that accepts ``extra=`` fields merged into the JSON payload."""

    def _log(self, level, msg, args, exc_info=None, extra=None, exc_info_on_logger=False, **kwargs):
        if extra and "extra_fields" not in extra:
            extra["extra_fields"] = {}
        super()._log(level, msg, args, exc_info=exc_info, extra=extra)


def configure(level: str | None = None, json_output: bool | None = None):
    """Set up root logger with JSON formatting.

    Args:
        level: Log level override (default: INFO, or DEBUG if MEMALL_DEBUG is set).
        json_output: Force JSON (default: true, unless MEMALL_PLAIN_LOG is set).
    """
    log_level = level or os.environ.get("LOG_LEVEL") or ("DEBUG" if os.environ.get("MEMALL_DEBUG") else "INFO")
    use_json = json_output if json_output is not None else (not os.environ.get("MEMALL_PLAIN_LOG"))

    if use_json:
        fmt = JsonFormatter()
    else:
        fmt = logging.Formatter("%(asctime)s %(name)s %(levelname)s %(message)s")

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(fmt)

    logging.setLoggerClass(ExtraLogger)
    root = logging.getLogger()
    root.__class__ = ExtraLogger
    root.setLevel(log_level)
    # Remove any pre-existing handlers
    for h in root.handlers[:]:
        root.removeHandler(h)
    root.addHandler(handler)
