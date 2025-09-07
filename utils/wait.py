"""
wait.py

WaitThread implementation that logs an occasional "⏳ Waiting..." message
when no other logs have been emitted for a configured interval.

This module imports the console handler (ch) and last-log helpers from logging_setup.
"""

import threading
import time
import logging

# Import the logger console handler + last-log helpers
from utils.logging_setup import ch, _get_last_log, _set_last_log

# Note: We intentionally avoid importing the parent logger here (to keep emission simple).
# The thread will emit directly to the console handler 'ch' so the file handler does not
# receive waiting messages (consistent with original behavior).


class WaitThread(threading.Thread):
    """
    Background thread that emits a "⏳ Waiting..." message to the console handler
    if no other log has been emitted for `interval` seconds.

    The thread is daemonized so it won't block process exit.
    """

    def __init__(self, interval=3.0, stop_event: threading.Event = None):
        super().__init__(daemon=True)
        self.interval = float(interval)
        self._stop = stop_event or threading.Event()

    def run(self):
        while not self._stop.is_set():
            now = time.time()
            try:
                last = _get_last_log()
            except Exception:
                last = 0.0

            if now - last >= self.interval:
                # Send only to console handler, not to the file handler.
                msg = "⏳ Waiting..."
                # Prepare a LogRecord-like mapping for emission.
                record = logging.makeLogRecord({
                    "name": "TrafficTester",
                    "levelno": logging.INFO,
                    "levelname": "INFO",
                    "msg": msg,
                    "created": now
                })
                try:
                    ch.emit(record)
                except Exception:
                    # Never let the wait thread raise.
                    pass
                # update last-log time so we don't spam
                try:
                    _set_last_log(now)
                except Exception:
                    pass

            # Sleep a short time but wake often to check stop event
            self._stop.wait(0.25)


__all__ = ["WaitThread"]
