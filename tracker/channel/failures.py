"""Keeps a failure visible and its content out of the logs (R4, T14).

pywa catches every exception from a handler, logs it with a traceback and answers 200,
so a failed write would look like success, and Meta would never retry. Its debug
logging also prints whole messages. So pywa's loggers stop here: below ERROR they're
dropped, and an exception becomes one line holding only its type, and marks this
request as failed so the webhook can answer 500.
"""

import logging
from contextvars import ContextVar
from typing import Final, override

logger: Final = logging.getLogger("tracker.channel")

_NOISY_LIBRARIES: Final = ("httpx", "httpcore", "sqlalchemy.engine", "asyncio")
_has_failed: ContextVar[bool] = ContextVar("has_failed", default=False)


def begin_request() -> None:
    _has_failed.set(False)


def note_failure(error_type: str, stage: str) -> None:
    _has_failed.set(True)
    logger.error("webhook_failed stage=%s error_type=%s", stage, error_type)


def has_failed() -> bool:
    return _has_failed.get()


class _ContentFreeHandler(logging.Handler):
    """Turns a pywa error into a type name. The record's message is never read."""

    @override
    def emit(self, record: logging.LogRecord) -> None:
        if record.exc_info and record.exc_info[0] is not None:
            note_failure(record.exc_info[0].__name__, "pywa")


def contain_library_logs() -> None:
    """Call once at startup, before any message is handled."""
    pywa_logger = logging.getLogger("pywa")  # pywa_async logs through this one too.
    pywa_logger.setLevel(logging.ERROR)
    pywa_logger.propagate = False
    pywa_logger.handlers = [_ContentFreeHandler()]
    for name in _NOISY_LIBRARIES:
        logging.getLogger(name).setLevel(logging.WARNING)
