"""Structured logging. One JSON object per line in json mode, so runs can be grepped
and loaded later. Bind ``run_id`` (and ``stage``) at the start of every job."""

from __future__ import annotations

import logging
import sys
from typing import Any, TextIO

import structlog


def configure_logging(level: str = "INFO", fmt: str = "json", stream: TextIO | None = None) -> None:
    out = stream if stream is not None else sys.stderr
    renderer: Any = (
        structlog.processors.JSONRenderer()
        if fmt == "json"
        else structlog.dev.ConsoleRenderer(colors=False)
    )
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            renderer,
        ],
        wrapper_class=structlog.make_filtering_bound_logger(logging.getLevelName(level)),
        logger_factory=structlog.PrintLoggerFactory(file=out),
        cache_logger_on_first_use=False,
    )


def get_logger(name: str | None = None, **initial: Any) -> Any:
    log = structlog.get_logger(name)
    return log.bind(**initial) if initial else log


def bind_run(run_id: str, stage: str) -> None:
    """Attach run context to every log line emitted in this context."""
    structlog.contextvars.bind_contextvars(run_id=run_id, stage=stage)


def clear_run() -> None:
    structlog.contextvars.clear_contextvars()
