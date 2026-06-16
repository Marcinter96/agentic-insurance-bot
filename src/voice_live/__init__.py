"""
voice_live — a minimal, well-structured ADK live (voice) test harness.

A standalone microphone <-> speaker loop driven by Google ADK's
``Runner.run_live()`` over the AI Studio (Gemini Developer API) Live API.

    mic     --(16kHz PCM)-->  LiveRequestQueue.send_realtime()  -->  Gemini Live
    speaker <--(24kHz PCM)--  runner.run_live() events          <--  Gemini Live

Run:
    python -m voice_live

Modules:
    config         — settings loaded from .env + audio constants
    logging_setup  — rich, levelled logging
    audio          — microphone capture + speaker playback (sounddevice)
    agent          — builds the ADK LlmAgent, runner, and RunConfig
    session        — the run_live() event loop (LiveVoiceSession)
"""

from voice_live.config import Settings, load_settings

__all__ = ["Settings", "load_settings"]
