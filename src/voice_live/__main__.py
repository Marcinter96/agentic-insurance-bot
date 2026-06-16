"""Entry point: ``python -m voice_live``.

Wires together config, logging, the ADK agent/runner, and the live session.
"""

from __future__ import annotations

import asyncio
import sys

from voice_live import config
from voice_live.agent import build_session
from voice_live.audio import check_sounddevice
from voice_live.config import load_settings
from voice_live.logging_setup import setup_logging
from voice_live.session import LiveVoiceSession


async def _run() -> None:
    settings = load_settings()
    check_sounddevice()
    bundle = await build_session(settings)
    await LiveVoiceSession(bundle).run()


def main() -> None:
    logger = setup_logging(config.LOG_LEVEL)
    try:
        asyncio.run(_run())
    except KeyboardInterrupt:
        pass
    except RuntimeError as exc:
        logger.error("[red]%s[/red]", exc)
        sys.exit(1)


if __name__ == "__main__":
    main()
