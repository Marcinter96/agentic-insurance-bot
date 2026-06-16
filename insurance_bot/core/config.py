import os

GCP_PROJECT = os.getenv("GOOGLE_CLOUD_PROJECT", "project-72fdf994-e492-4b76-83e")
GCP_LOCATION = os.getenv("GOOGLE_CLOUD_LOCATION", "us-central1")
GCS_BUCKET = os.getenv("GCS_BUCKET", "adk-insurance-demo-data-mi")
LLM_MODEL = os.getenv("LLM_MODEL", "gemini-2.5-flash")
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
