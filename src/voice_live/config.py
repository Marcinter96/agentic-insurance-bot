"""Configuration for the voice_live harness.

Loads runtime settings from environment / .env and exposes audio constants.
The AI Studio (Gemini Developer API) backend is forced here, and the corporate
TLS CA bundle is propagated to the SSL env vars if present.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv

# ---------------------------------------------------------------------------
# Audio format constants (fixed by the Gemini Live API).
# ---------------------------------------------------------------------------

INPUT_SAMPLE_RATE = 16_000   # mic -> model: 16 kHz, 16-bit, mono PCM
OUTPUT_SAMPLE_RATE = 24_000  # model -> speaker: 24 kHz, 16-bit, mono PCM
CHANNELS = 1
DTYPE = "int16"
BLOCKSIZE = 1_600            # ~100 ms at 16 kHz

# Logging level (NOT taken from env — hardcoded for now).
LOG_LEVEL = "INFO"  # DEBUG | INFO | WARNING | ERROR

# Live model (hardcoded — AI Studio).
MODEL = "gemini-3.1-flash-live-preview"


@dataclass(frozen=True)
class Settings:
    """Resolved runtime settings."""

    api_key: str
    model: str
    app_name: str = "voice_live"
    user_id: str = "local_user"
    session_id: str = "local_session"


def load_settings() -> Settings:
    """Load .env, force the AI Studio backend, wire SSL, and build Settings.

    Returns:
        Settings with the resolved API key and model.

    Raises:
        RuntimeError: if GOOGLE_API_KEY is not set.
    """
    load_dotenv()

    # Force the AI Studio (Gemini Developer API) backend, not Vertex.
    os.environ["GOOGLE_GENAI_USE_VERTEXAI"] = "false"

    # If behind a TLS-inspecting proxy, make sure both vars point at the CA
    # bundle (some HTTP stacks read SSL_CERT_FILE, others REQUESTS_CA_BUNDLE).
    ca = os.getenv("SSL_CERT_FILE") or os.getenv("REQUESTS_CA_BUNDLE")
    if ca:
        os.environ.setdefault("SSL_CERT_FILE", ca)
        os.environ.setdefault("REQUESTS_CA_BUNDLE", ca)

    api_key = os.getenv("GOOGLE_API_KEY", "")
    if not api_key:
        raise RuntimeError("GOOGLE_API_KEY is not set. Add it to .env first.")

    return Settings(
        api_key=api_key,
        model=MODEL,
    )
