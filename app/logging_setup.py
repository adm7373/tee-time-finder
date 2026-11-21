"""Logging setup for the worker Lambda entry points.

The Python runtime attaches a handler to the root logger but leaves the level at
``WARNING``, so the one-line run summaries the workers log at INFO — what a
sweep added, what a fingerprint batch classified — never reached CloudWatch.
Only the START/END/REPORT lines did, which made a dead run look like a healthy
one. The entry points call :func:`configure_logging` once so those lines appear.
"""

import logging

#: Third-party loggers that say nothing useful at INFO and a lot of noise there.
_NOISY_LOGGERS = ("boto3", "botocore", "urllib3", "s3transfer", "charset_normalizer")


def configure_logging(level: int = logging.INFO) -> None:
    """Raise the root logger to ``level`` and keep the AWS SDK loggers quiet.

    ``basicConfig`` alone is not enough in Lambda: it does nothing once the
    runtime has installed a handler, so the level has to be set on the logger
    itself. A local run has no handler yet, so one is added there.
    """
    root = logging.getLogger()
    if not root.handlers:
        logging.basicConfig(level=level, format="%(levelname)s\t%(name)s\t%(message)s")
    root.setLevel(level)
    for name in _NOISY_LOGGERS:
        logging.getLogger(name).setLevel(logging.WARNING)
