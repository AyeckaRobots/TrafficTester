"""
logging_setup.py

Centralized logging setup used by TrafficTester and the wait thread.

Provides:
- logger: configured logger ("TrafficTester")
- ch: console StreamHandler (used by WaitThread to emit waiting messages)
- _get_last_log / _set_last_log: helpers for tracking last log time
- UpdateLastHandler, TestReportFormatter, SkipWaitFilter classes
"""

import logging
import threading
import time
import sys
import re

# track last log time (for wait suppression)
_last_lock = threading.Lock()
_last_log = time.time()


def _get_last_log():
    """Return the timestamp of the last log (thread-safe)."""
    with _last_lock:
        return _last_log


def _set_last_log(ts):
    """Set the timestamp of the last log (thread-safe)."""
    global _last_log
    with _last_lock:
        _last_log = ts


class UpdateLastHandler(logging.Handler):
    """
    A logging handler whose only job is to update the module-level last-log timestamp
    whenever any record is emitted. This lets the WaitThread suppress its "Waiting..."
    message when other logs are recent.
    """

    def emit(self, record):
        try:
            _set_last_log(getattr(record, "created", time.time()))
        except Exception:
            # Never raise from logging handler
            pass


# main logger
logger = logging.getLogger("TrafficTester")
logger.setLevel(logging.DEBUG)


# console handler (keeps emojis + debug info)
ch = logging.StreamHandler(sys.stdout)
ch.setLevel(logging.INFO)
ch.setFormatter(logging.Formatter(
    "%(asctime)s [%(levelname)s] %(message)s",
    "%Y-%m-%d %H:%M:%S"
))


# file handler (serious test report — no emojis, no waits, no internals)
fh = logging.FileHandler("traffictester.log")
fh.setLevel(logging.INFO)


class TestReportFormatter(logging.Formatter):
    """
    Custom formatter for the file handler: strips non-ascii characters (emojis),
    suppresses explicit wait messages from the file output, and formats timestamp.
    """

    def format(self, record):
        msg = record.getMessage()
        # filter wait explicitly (return empty so the record won't be written)
        if "wait" in msg.lower() or "waiting" in msg.lower():
            return ""
        # strip emojis / non-ascii
        clean = re.sub(r"[^\x00-\x7F]+", " ", msg).strip()
        ts = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(record.created))
        return f"{ts} [{record.levelname}] {clean}"


class SkipWaitFilter(logging.Filter):
    """Filter to prevent 'wait' messages from being written to the file handler."""
    def filter(self, record):
        return "wait" not in record.getMessage().lower() and "waiting" not in record.getMessage().lower()


# attach handlers to logger (clear previous handlers first)
logger.handlers = []
logger.addHandler(UpdateLastHandler())

# console handler goes first so it prints to stdout
logger.addHandler(ch)

# setup and attach file handler
fh.setFormatter(TestReportFormatter())
fh.addFilter(SkipWaitFilter())
logger.addHandler(fh)


# exported names
__all__ = [
    "logger",
    "ch",
    "_get_last_log",
    "_set_last_log",
    "UpdateLastHandler",
    "TestReportFormatter",
    "SkipWaitFilter",
]
