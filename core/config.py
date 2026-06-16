import os

GCP_PROJECT = os.getenv("GOOGLE_CLOUD_PROJECT", "project-72fdf994-e492-4b76-83e")
GCP_LOCATION = os.getenv("GOOGLE_CLOUD_LOCATION", "us-central1")
GCS_BUCKET = os.getenv("GCS_BUCKET", "adk-insurance-demo-data-mi")
LLM_MODEL = os.getenv("LLM_MODEL", "gemini-2.5-flash")
USE_VERTEX_AI = os.getenv("GOOGLE_GENAI_USE_VERTEXAI", "true").lower() == "true"
