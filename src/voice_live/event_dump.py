"""Raw event recorder for the live session.

Writes the full ``repr()`` of every ADK ``run_live()`` event to
``logs/session-<timestamp>.txt`` so we can inspect *all* available fields
offline and decide what is worth surfacing in the live logs.

This is a diagnostics sink, separate from the human-facing rich logging.
"""

from __future__ import annotations

import time
from datetime import datetime
from pathlib import Path

from voice_live.logging_setup import get_logger

logger = get_logger(__name__)

# Repo-root/logs (config.py lives at src/voice_live/, so go up 3 levels).
LOGS_DIR = Path(__file__).resolve().parents[2] / "logs"


class EventRecorder:
    """Append the full repr of each event to a per-session text file."""

    def __init__(self) -> None:
        self._fh = None
        self._count = 0
        self._started = time.monotonic()
        self.path: Path | None = None

    def open(self) -> None:
        LOGS_DIR.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        self.path = LOGS_DIR / f"session-{stamp}.txt"
        self._fh = self.path.open("w", encoding="utf-8")
        self._fh.write(f"# voice_live raw event dump — started {datetime.now().isoformat()}\n")
        self._fh.write("# one block per run_live() event; full repr for field discovery\n\n")
        self._fh.flush()
        logger.info("[dim]RECORD    raw events -> %s[/dim]", self.path)

    def record(self, event) -> None:
        if self._fh is None:
            return
        self._count += 1
        elapsed = time.monotonic() - self._started
        self._fh.write(f"===== event #{self._count}  t+{elapsed:7.3f}s =====\n")
        try:
            self._fh.write(repr(event))
        except Exception as exc:  # never let logging break the session
            self._fh.write(f"<repr failed: {exc!r}>")
        self._fh.write("\n\n")
        self._fh.flush()

    def close(self) -> None:
        if self._fh is not None:
            self._fh.write(f"# end — {self._count} events\n")
            self._fh.close()
            self._fh = None
            if self.path:
                logger.info("[dim]RECORD    saved %d events -> %s[/dim]", self._count, self.path)
