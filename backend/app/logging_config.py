"""Structured JSON logging.

Logs from a security tool are read by machines first: they get shipped, indexed
and queried ("show me every alert this agent raised", "find the events that
failed to project"). Free text does not survive that. Every record here is one
JSON object per line, with the interesting fields hoisted to the top level so
they are queryable without regex.

What deliberately does NOT go into logs
---------------------------------------
Event ``metadata`` and alert ``details`` stay out. They carry the substance of
what an agent did — transaction amounts, query text, counterparties — and that
belongs in Postgres, behind the access controls, not duplicated into a log
pipeline that is usually readable by a much wider audience. Logs carry
identifiers, types, statuses, rule names, severities and counts: enough to find
and correlate an event, not enough to leak what it contained.
"""

from __future__ import annotations

import datetime as _datetime
import json
import logging
import sys
from typing import Any

# Attributes present on every LogRecord. Anything else a caller attaches via
# ``extra=`` is application context and gets promoted to a top-level field.
_RESERVED = frozenset(
    {
        "args",
        "asctime",
        "created",
        "exc_info",
        "exc_text",
        "filename",
        "funcName",
        "levelname",
        "levelno",
        "lineno",
        "module",
        "msecs",
        "message",
        "msg",
        "name",
        "pathname",
        "process",
        "processName",
        "relativeCreated",
        "stack_info",
        "thread",
        "threadName",
        "taskName",
    }
)


class JsonFormatter(logging.Formatter):
    """Render a LogRecord as a single-line JSON object."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": _datetime.datetime.fromtimestamp(
                record.created, tz=_datetime.UTC
            ).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }

        for key, value in record.__dict__.items():
            if key in _RESERVED or key.startswith("_"):
                continue
            payload[key] = value if _is_jsonable(value) else repr(value)

        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        if record.stack_info:
            payload["stack"] = self.formatStack(record.stack_info)

        return json.dumps(payload, default=str, separators=(",", ":"))


def _is_jsonable(value: Any) -> bool:
    return isinstance(
        value, str | int | float | bool | type(None) | list | dict | tuple
    )


def configure_logging(level: str = "INFO", fmt: str = "json") -> None:
    """Install a single stdout handler on the root logger.

    ``fmt="text"`` swaps in the human-readable formatter for local work; the
    default is JSON so what runs in production is what is tested.
    """
    handler = logging.StreamHandler(sys.stdout)
    if fmt == "text":
        handler.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)-8s %(name)s: %(message)s")
        )
    else:
        handler.setFormatter(JsonFormatter())

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level.upper())

    # uvicorn installs its own handlers; strip them so every line in the
    # process is emitted in one format by one handler.
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        uvicorn_logger = logging.getLogger(name)
        uvicorn_logger.handlers.clear()
        uvicorn_logger.propagate = True

    # The neo4j driver narrates every connection at INFO; it drowns the signal.
    logging.getLogger("neo4j").setLevel(logging.WARNING)
    logging.getLogger("neo4j.notifications").setLevel(logging.WARNING)
