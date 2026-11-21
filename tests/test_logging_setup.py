"""The worker entry points must actually emit their INFO run summaries.

Lambda leaves the root logger at WARNING with a handler already attached, which
is why ``basicConfig`` is not enough and why a run that did nothing produced no
log line at all.
"""

import logging

from app.logging_setup import configure_logging


class _Capture(logging.Handler):
    def __init__(self):
        super().__init__()
        self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)


def test_info_is_emitted_with_a_handler_already_attached():
    """The Lambda case: a root handler exists and the level is WARNING."""
    root = logging.getLogger()
    handler = _Capture()
    original_level, original_handlers = root.level, list(root.handlers)
    root.handlers = [handler]
    root.setLevel(logging.WARNING)
    try:
        configure_logging()
        logging.getLogger("app.discovery").info("discovery run summary")
    finally:
        root.handlers = original_handlers
        root.setLevel(original_level)

    assert [r.getMessage() for r in handler.records] == ["discovery run summary"]


def test_noisy_sdk_loggers_are_held_at_warning():
    configure_logging()
    assert logging.getLogger("botocore").level == logging.WARNING
