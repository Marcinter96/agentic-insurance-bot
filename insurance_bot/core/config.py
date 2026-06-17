import os

GCP_PROJECT = os.getenv("GOOGLE_CLOUD_PROJECT", "project-72fdf994-e492-4b76-83e")
GCP_LOCATION = os.getenv("GOOGLE_CLOUD_LOCATION", "us-central1")
GCS_BUCKET = os.getenv("GCS_BUCKET", "adk-insurance-demo-data-mi")
# Dedicated bucket for emergency (SOS) interaction records, kept separate from
# the demo data so it can have its own retention / access policy.
SOS_BUCKET = os.getenv("SOS_BUCKET", "adk-insurance-sos-mi")
LLM_MODEL = os.getenv("LLM_MODEL", "gemini-2.5-flash")

# Model used by the one-shot "brains" (classifier / identifier). These do a
# simple, structured classification/extraction job, so a smaller/faster model
# is plenty. Default to LLM_MODEL; set BRAIN_MODEL=gemini-2.5-flash-lite for an
# extra speed bump.
BRAIN_MODEL = os.getenv("BRAIN_MODEL", LLM_MODEL)

# Gemini 2.5 models "think" before answering, which adds significant latency.
# For the brains (pick-one-intent / extract-an-identifier) that reasoning pass
# is wasted, so we disable it by default (thinking_budget=0). Override via env.
BRAIN_THINKING_BUDGET = int(os.getenv("BRAIN_THINKING_BUDGET", "0"))


def fast_brain_config():
    """GenerateContentConfig for the brains: thinking disabled for low latency."""
    from google.genai import types

    return types.GenerateContentConfig(
        thinking_config=types.ThinkingConfig(thinking_budget=BRAIN_THINKING_BUDGET),
    )
USE_VERTEX_AI = os.getenv("GOOGLE_GENAI_USE_VERTEXAI", "true").lower() == "true"

# Live / bidirectional (voice) model. Used when ADK_BIDI is enabled so that
# `adk web` can drive the agent via run_live() with AUDIO modality.
#   - Vertex AI Live:  gemini-live-2.5-flash-native-audio
#   - AI Studio Live:  gemini-2.5-flash-native-audio-preview-12-2025
BIDI_MODEL = os.getenv("BIDI_MODEL", "gemini-live-2.5-flash-native-audio")

# Text model used by the live agent's NON-live path (the adk web text box,
# which calls generateContent). Native-audio Live models reject generateContent,
# so the dual-model live agent uses this for text and BIDI_MODEL for voice.
BIDI_TEXT_MODEL = os.getenv("BIDI_TEXT_MODEL", LLM_MODEL)

# When truthy, the ADK entry point (agent.py) exposes a Live-capable LlmAgent
# (run_live / voice) instead of the deterministic text Workflow.
ADK_BIDI = os.getenv("ADK_BIDI", "false").lower() in ("1", "true", "yes")
