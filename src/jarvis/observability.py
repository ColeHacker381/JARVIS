"""Payload-free JSON logging and the future audit sink boundary."""

import json
import logging
from typing import Protocol, TextIO

from jarvis.core.contracts import AuditRecord
from jarvis.core.time import utc_now


class AuditSink(Protocol):
    def append(self, record: AuditRecord) -> None: ...


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        # Only stable codes/IDs should be logged; no proposal/memory payloads.
        data = {
            "timestamp": utc_now().isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "event": record.getMessage(),
        }
        for key in ("correlation_id", "proposal_id", "task_id", "reason_code"):
            if hasattr(record, key):
                data[key] = str(getattr(record, key))
        if record.exc_info and record.exc_info[0] is not None:
            data["exception_type"] = record.exc_info[0].__name__
        return json.dumps(data)


def configure_logging(level: str = "INFO", stream: TextIO | None = None) -> logging.Logger:
    logger = logging.getLogger("jarvis")
    logger.setLevel(level)
    logger.propagate = False
    for old_handler in logger.handlers[:]:
        if getattr(old_handler, "_jarvis_owned", False):
            logger.removeHandler(old_handler)
            old_handler.close()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(JsonFormatter())
    handler._jarvis_owned = True  # type: ignore[attr-defined]
    logger.addHandler(handler)
    return logger
