"""Rich, levelled logging setup for the voice_live harness.

Produces readable lines like:

    16:07:07 INFO     voice_live.session  CUSTOMER  Hello, can you hear me?
    16:07:09 INFO     voice_live.session  BOT       Hi! How can I help? (final)
    16:07:09 INFO     voice_live.session  TURN      turn complete — spoke 82.0 KB

Every line carries: time, level, the logger NAME (so you know which module it
came from), and the message. Module loggers should be created with
``get_logger(__name__)`` so the name reflects the source file.
"""

from __future__ import annotations

import logging

_QUIET_LIBS = ("google_genai", "websockets", "httpx", "google_adk")

# Width-padded so messages line up in a column.
_FMT = "%(name)-20s %(message)s"
_FALLBACK_FMT = "%(asctime)s %(levelname)-7s %(name)-20s %(message)s"


def setup_logging(level: str = "INFO") -> logging.Logger:
    """Configure logging so every line shows time, level, source, and message.

    Args:
        level: One of DEBUG | INFO | WARNING | ERROR.

    Returns:
        The root "voice_live" logger.
    """
    handlers: list[logging.Handler]
    try:
        from rich.console import Console
        from rich.logging import RichHandler

        handler = RichHandler(
            console=Console(stderr=True),
            rich_tracebacks=True,
            markup=True,                 # allow [color]..[/color] in messages
            show_level=True,             # show INFO / WARNING / ...
            show_time=True,
            show_path=True,              # show source file:line on EVERY line
            log_time_format="%H:%M:%S",
        )
        fmt = _FMT
        handlers = [handler]
    except Exception:
        # Plain fallback if rich is unavailable.
        handler = logging.StreamHandler()
        fmt = _FALLBACK_FMT
        handlers = [handler]

    logging.basicConfig(level=level, format=fmt, datefmt="%H:%M:%S", handlers=handlers, force=True)

    quiet = logging.WARNING if level != "DEBUG" else logging.INFO
    for name in _QUIET_LIBS:
        logging.getLogger(name).setLevel(quiet)

    return logging.getLogger("voice_live")


def get_logger(name: str) -> logging.Logger:
    """Return a child logger under ``voice_live`` for the given module name.

    Usage in a module:  ``logger = get_logger(__name__)``
    """
    short = name.split(".")[-1] if name else "voice_live"
    return logging.getLogger(f"voice_live.{short}")
