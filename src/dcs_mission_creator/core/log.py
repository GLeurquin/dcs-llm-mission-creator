"""Module-wide structlog configuration.

`configure(verbose=False)` is idempotent: callers (the CLI entry point and
tests) can call it without worrying about double-init. Modules then do:

    import structlog
    log = structlog.get_logger(__name__)
    log.info("loaded", count=42)

The ConsoleRenderer produces colored human output; switch to JSONRenderer in
production if/when we ship logs to a collector.
"""

from __future__ import annotations

import logging

import structlog

_CONFIGURED = False


def configure(verbose: bool = False) -> None:
    global _CONFIGURED
    if _CONFIGURED:
        return
    level = logging.DEBUG if verbose else logging.INFO
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="%H:%M:%S", utc=False),
            structlog.dev.ConsoleRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(level),
        cache_logger_on_first_use=True,
    )
    _CONFIGURED = True
