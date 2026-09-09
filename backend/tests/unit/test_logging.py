"""Structured logging: shape, levels, and what must never reach a log."""

from __future__ import annotations

import json
import logging

import pytest

from app.logging_config import JsonFormatter, configure_logging


def render(record_factory) -> dict:
    return json.loads(JsonFormatter().format(record_factory()))


def make_record(level=logging.INFO, msg="hello", **extra):
    record = logging.LogRecord(
        name="app.test",
        level=level,
        pathname=__file__,
        lineno=1,
        msg=msg,
        args=(),
        exc_info=None,
    )
    for key, value in extra.items():
        setattr(record, key, value)
    return record


def test_output_is_one_json_object_per_line():
    line = JsonFormatter().format(make_record())
    assert "\n" not in line
    parsed = json.loads(line)
    assert parsed["message"] == "hello"
    assert parsed["level"] == "INFO"
    assert parsed["logger"] == "app.test"
    assert parsed["timestamp"].startswith("20")


def test_extra_fields_are_hoisted_to_top_level_for_querying():
    parsed = json.loads(
        JsonFormatter().format(
            make_record(
                event="event.ingest.received", event_id="abc", actor_id="finance-agent"
            )
        )
    )
    # Top level, not nested in the message — otherwise queries need regex.
    assert parsed["event"] == "event.ingest.received"
    assert parsed["event_id"] == "abc"
    assert parsed["actor_id"] == "finance-agent"


def test_standard_record_noise_is_not_emitted():
    parsed = json.loads(JsonFormatter().format(make_record()))
    for noisy in ("msg", "args", "pathname", "relativeCreated", "processName"):
        assert noisy not in parsed


def test_non_serialisable_values_do_not_break_the_line():
    class Opaque:
        def __repr__(self):
            return "<opaque>"

    parsed = json.loads(JsonFormatter().format(make_record(thing=Opaque())))
    assert parsed["thing"] == "<opaque>"


def test_exceptions_are_captured_as_a_field():
    try:
        raise ValueError("boom")
    except ValueError:
        import sys

        record = make_record(level=logging.ERROR)
        record.exc_info = sys.exc_info()
        parsed = json.loads(JsonFormatter().format(record))
    assert "ValueError: boom" in parsed["exception"]


@pytest.mark.parametrize("fmt", ["json", "text"])
def test_configure_logging_installs_exactly_one_handler(fmt):
    configure_logging(level="INFO", fmt=fmt)
    root = logging.getLogger()
    assert len(root.handlers) == 1
    is_json = isinstance(root.handlers[0].formatter, JsonFormatter)
    assert is_json is (fmt == "json")
    configure_logging(level="INFO", fmt="json")


def test_noisy_driver_logging_is_turned_down():
    """The neo4j driver narrates every connection at INFO and buries the signal."""
    configure_logging(level="INFO", fmt="json")
    assert logging.getLogger("neo4j").level == logging.WARNING
