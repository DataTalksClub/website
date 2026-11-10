"""Safe structured logging for application and Django request errors."""

from __future__ import annotations

import json
import logging
import os
from datetime import UTC, datetime
from typing import Any

from community_base.kernel.context import current_context
from community_base.kernel.redaction import REDACTED, redact

_CONTEXT_FIELDS = ("request_id", "correlation_id", "job_id")
_FORMATTER_FIELDS = frozenset({"timestamp", "level", "logger", "message"})
_LOG_RECORD_FIELDS = set(
    logging.LogRecord(
        name="",
        level=logging.INFO,
        pathname=__file__,
        lineno=0,
        msg="",
        args=(),
        exc_info=None,
    ).__dict__
) | {"message", "asctime", "exc_text"}
_MAX_TRACEBACK_FRAMES = 64


class RequestContextFilter(logging.Filter):
    """Attach the validated request or job context to each log record."""

    def filter(self, record: logging.LogRecord) -> bool:
        context = current_context()
        for field in _CONTEXT_FIELDS:
            value = getattr(context, field)
            setattr(record, field, redact(value) if value else None)
        return True


def safe_traceback(exc_info: tuple[Any, BaseException, Any] | None) -> list[dict[str, Any]]:
    """Return bounded frame locations without source lines, locals, or values."""

    if not exc_info:
        return []

    traceback = exc_info[2]
    frames: list[dict[str, Any]] = []
    while traceback is not None and len(frames) < _MAX_TRACEBACK_FRAMES:
        code = traceback.tb_frame.f_code
        frames.append(
            {
                "file": redact(os.path.basename(code.co_filename)),
                "line": traceback.tb_lineno,
                "function": redact(code.co_name),
            }
        )
        traceback = traceback.tb_next

    if traceback is not None:
        frames.append({"truncated": True})
    return frames


def _safe_message(record: logging.LogRecord) -> str:
    template = redact(record.msg)
    if not isinstance(template, str):
        return str(REDACTED)

    args = redact(record.args)
    if args:
        try:
            template = template % args
        except (TypeError, ValueError, KeyError):
            pass
    safe = redact(template)
    return safe if isinstance(safe, str) else str(REDACTED)


class SafeJsonFormatter(logging.Formatter):
    """Emit JSON records after redacting messages, context, and extra fields."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, tz=UTC).isoformat(
                timespec="milliseconds"
            ),
            "level": record.levelname,
            "logger": redact(record.name),
            "message": _safe_message(record),
        }

        for field in _CONTEXT_FIELDS:
            value = getattr(record, field, None)
            if value:
                payload[field] = redact(value)

        extra = {
            key: value
            for key, value in record.__dict__.items()
            if key not in _LOG_RECORD_FIELDS and key not in _CONTEXT_FIELDS
        }
        safe_extra = redact(extra)
        if isinstance(safe_extra, dict):
            for key, value in safe_extra.items():
                # Event properties are caller-controlled. Preserve the
                # formatter's canonical log fields and expose collisions with
                # an application prefix instead of letting them spoof level,
                # timestamp, logger, or message.
                payload[f"app_{key}" if key in _FORMATTER_FIELDS else key] = value

        if record.exc_info:
            exception_type = record.exc_info[0]
            payload["exception_type"] = redact(getattr(exception_type, "__name__", "Exception"))
            payload["traceback"] = safe_traceback(record.exc_info)

        return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
