# Insurance Bot — Multi-Agent Chatbot

A production-ready multi-agent insurance chatbot built with Google ADK 2.0 and Vertex AI.

## Architecture

- **CustomerServiceAgent** (root): Routes customer inquiries to specialized sub-agents
- **InvoiceAgent**: Handles invoice queries, retrieval, and payment status
- **PolicyAgent**: Manages policy documents, coverage details, and modifications
- **KnowledgeAgent**: Provides general insurance knowledge and FAQs

## Local Development

### Prerequisites
- Python 3.11+
- `uv` package manager
- GCP project with:
  - Vertex AI API enabled
  - `roles/aiplatform.user` IAM role on your account

### Setup

1. **Install dependencies:**
   ```bash
   cd insurance_bot
   pip install -r requirements.txt
   ```

2. **Set environment variables:**
   ```bash
   export GOOGLE_GENAI_USE_VERTEXAI=true
   export GOOGLE_CLOUD_PROJECT=your-project-id
   export GOOGLE_CLOUD_LOCATION=us-central1
   ```

3. **Run the dev server:**
   ```bash
   cd ..  # Go to parent directory containing insurance_bot/
   adk web insurance_bot --port 8001
   ```

4. **Access at:** http://127.0.0.1:8001

## Production Deployment (Cloud Run)

### Build & Deploy

```bash
cd insurance_bot
gcloud run deploy insurance-bot \
  --source . \
  --platform managed \
  --region us-central1 \
  --project PROJECT_ID \
  --set-env-vars \
    GOOGLE_GENAI_USE_VERTEXAI=true,\
    GOOGLE_CLOUD_PROJECT=PROJECT_ID,\
    GOOGLE_CLOUD_LOCATION=us-central1
```

### Manual Docker Build

```bash
# Build image
docker build -t insurance-bot:latest .

# Test locally
docker run -p 8080:8080 \
  -e GOOGLE_GENAI_USE_VERTEXAI=true \
  -e GOOGLE_CLOUD_PROJECT=your-project-id \
  -e GOOGLE_CLOUD_LOCATION=us-central1 \
  insurance-bot:latest

# Push to Artifact Registry
docker tag insurance-bot:latest us-central1-docker.pkg.dev/PROJECT_ID/insurance-repo/insurance-bot:latest
docker push us-central1-docker.pkg.dev/PROJECT_ID/insurance-repo/insurance-bot:latest
```

## API

The ADK dev UI is available at the root path. Use it to:
- Select the `insurance_bot` app
- Create new sessions
- Test agent routing and responses

Example queries:
- "What are your business hours?"
- "I need a copy of my policy"
- "Show me invoice inv_12345"

## Implementation Notes

- **agent.py**: Contains all agent definitions and skill (tool) implementations
- Skills are currently dummy implementations — replace with real API calls (GCS, Firestore, etc.)
- `.env` is excluded from git (add to `.gitignore`) — set via environment in production

## Extending the Bot

### Add New Skill

In `agent.py`:

```python
def my_skill(param: str) -> dict:
    """Skill description."""
    return {"result": "value"}

# Add to agent's tools list
policy_agent = LlmAgent(
    ...
    tools=[
        my_skill,
        ...
    ]
)
```

### Add New Sub-Agent

```python
new_agent = LlmAgent(
    name="new_agent_name",
    model="gemini-2.5-flash",
    tools=[skill1, skill2],
    instruction="Instructions for this agent..."
)

# Add to root agent
root_agent = LlmAgent(
    ...
    sub_agents=[invoice_agent, policy_agent, knowledge_agent, new_agent]
)
```

## Troubleshooting

### Port Already in Use
```bash
lsof -i :8080
kill -9 <PID>
```

### Permission Denied on Vertex AI
Ensure your GCP user has `roles/aiplatform.user`:
```bash
gcloud projects add-iam-policy-binding PROJECT_ID \
  --member=user:YOUR_EMAIL \
  --role=roles/aiplatform.user
```

### .env Not Loading
Set variables explicitly:
```bash
export GOOGLE_CLOUD_LOCATION=us-central1
adk web insurance_bot --port 8001
```

## License

Internal use — AI Factory Project
