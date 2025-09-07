"""
helpers.py

General-purpose helpers used by traffictester.
"""

from utils.logging_setup import logger


def safe_call(obj, method_name, *args, fallback=None, **kwargs):
    """
    Call obj.method_name(*args, **kwargs) and catch/log any exception.
    Returns fallback on error.

    Example:
        safe_call(dut, "get_esno", fallback=None)
    """
    try:
        method = getattr(obj, method_name)
        return method(*args, **kwargs)
    except Exception as e:
        # Keep consistent logging style with the rest of the project
        logger.exception("Error calling %s.%s: %s", obj.__class__.__name__, method_name, e)
        return fallback


__all__ = ["safe_call"]
