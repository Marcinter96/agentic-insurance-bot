FROM python:3.11-slim

WORKDIR /app

# Install Python dependencies first (better layer caching).
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the whole project: the `insurance_bot` package is the agent app, and
# `adk web .` exposes it as the app named "insurance_bot".
COPY . .

# Vertex AI by default; Cloud Run injects PORT (defaults to 8080).
ENV GOOGLE_GENAI_USE_VERTEXAI=true
ENV PORT=8080
EXPOSE 8080

# Agents dir = repo root (contains the insurance_bot/ agent package).
CMD ["sh", "-c", "adk web . --port ${PORT} --host 0.0.0.0"]
