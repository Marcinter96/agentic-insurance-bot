FROM python:3.11-slim

# Set working directory
WORKDIR /app

# Copy requirements and install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy agent code
COPY agent.py .

# Set environment variables (can be overridden by Cloud Run)
ENV GOOGLE_GENAI_USE_VERTEXAI=true
ENV PORT=8080

# Expose port
EXPOSE 8080

# Run the ADK web server
CMD ["adk", "web", ".", "--port", "8080", "--host", "0.0.0.0"]
