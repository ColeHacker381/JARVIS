"""Payload-free JSON logging and the future audit sink boundary."""

import json
import logging
from typing import Protocol, TextIO

from jarvis.core.contracts import AuditRecord
from jarvis.core.time import utc_now


class AuditSink(Protocol):
    def append(self, record: AuditRecord) -> None: ...


class AuditWriteError(RuntimeError):
    """Sanitized failure: callers must not perform unaudited privileged work."""


def write_audit(sink: AuditSink, record: AuditRecord) -> None:
    try:
        sink.append(record)
    except Exception as exc:
        logging.getLogger("jarvis").error(
            "audit.unavailable",
            extra={"correlation_id": record.correlation_id, "reason_code": "audit_unavailable"},
        )
        raise AuditWriteError("audit_unavailable") from exc


class InMemoryAuditSink:
    """Local append-only-by-API records; not durable or tamper-resistant storage."""

    def __init__(self) -> None:
        self._records: list[AuditRecord] = []

    def append(self, record: AuditRecord) -> None:
        self._records.append(AuditRecord.model_validate(record.model_dump()))

    def records(self) -> tuple[AuditRecord, ...]:
        return tuple(record.model_copy(deep=True) for record in self._records)


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
