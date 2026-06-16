# 05 — Running & operations

Setup, configuration, performance tuning, voice mode, deployment, and troubleshooting.

---

## Prerequisites

- Python 3.11+
- A GCP project with **Vertex AI** enabled
- `gcloud` CLI authenticated (`gcloud auth application-default login`)
- Access to the GCS data bucket (or create + seed your own)

## Install

```bash
python3.11 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

## Seed the data (once)

```bash
python data/mock_data_generator.py --bucket adk-insurance-demo-data-mi
# verify
gsutil ls gs://adk-insurance-demo-data-mi/
```

## Run (text mode)

Run from the **repo root** (the directory that contains the `insurance_bot/` package):

```bash
export GOOGLE_GENAI_USE_VERTEXAI=true
export GOOGLE_CLOUD_PROJECT=project-72fdf994-e492-4b76-83e
export GOOGLE_CLOUD_LOCATION=us-central1
export GCS_BUCKET=adk-insurance-demo-data-mi
export BRAIN_MODEL=gemini-2.5-flash-lite
adk web insurance_bot
```

Open **http://127.0.0.1:8000** and select `insurance_bot`.

> `adk web insurance_bot` loads `insurance_bot/agent.py` → `root_agent`. It does **not** load `workflow.py` by filename — `agent.py` re-exports it.

---

## Environment variables

| Variable | Default | Purpose |
|----------|---------|---------|
| `GOOGLE_GENAI_USE_VERTEXAI` | `true` | Use Vertex AI backend |
| `GOOGLE_CLOUD_PROJECT` | `project-72fdf994-e492-4b76-83e` | GCP project |
| `GOOGLE_CLOUD_LOCATION` | `us-central1` | Vertex region |
| `GCS_BUCKET` | `adk-insurance-demo-data-mi` | Data bucket |
| `LLM_MODEL` | `gemini-2.5-flash` | Model for the specialist agents |
| `BRAIN_MODEL` | `= LLM_MODEL` | Model for the classifier/identifier brains. Set to `gemini-2.5-flash-lite` for speed |
| `BRAIN_THINKING_BUDGET` | `0` | Gemini "thinking" budget for the brains (0 = off) |
| `ADK_BIDI` | `false` | `1` → voice/bidi mode instead of the text Workflow |
| `BIDI_MODEL` | `gemini-live-2.5-flash-native-audio` | Native-audio model for voice |
| `BIDI_TEXT_MODEL` | `= LLM_MODEL` | Text path for the live agent |

All defaults live in `insurance_bot/core/config.py`.

---

## Performance tuning

The bot makes a few sequential model calls per conversation (one per classifier turn, one per identifier turn/lookup). To keep it snappy:

1. **Keep thinking off for the brains** — `BRAIN_THINKING_BUDGET=0` (default). This is the biggest lever; Gemini 2.5's reasoning pass is wasted on classification/extraction.
2. **Use a small brain model** — `BRAIN_MODEL=gemini-2.5-flash-lite`.
3. The brains already **short-circuit** once their result is in state (no re-classifying on later turns), and the GCS lookup runs off the event loop (`asyncio.to_thread`).

You may see a **"System Instruction Performance Analysis"** warning in the ADK UI about system instructions changing between turns. This is expected for a multi-agent app (each agent has its own system prompt) and is a minor cache-miss cost — not a bug.

A `WARNING ... cancelling N leftover tasks` line at the end of a run is the workflow tearing down dynamic child nodes; it's benign.

---

## Voice / bidi mode

`adk web` drives voice through `run_live()`, which needs the `root_agent` to be a Live-capable `LlmAgent` on a native-audio model. The deterministic `Workflow` can't serve `run_live`, so `live_agent.py` provides an alternative root agent that **keeps the same guardrails** by exposing them as tools the model must call in order (verify → authorize → specialist → audit), persisting verification state in `tool_context.state`.

```bash
ADK_BIDI=1 adk web insurance_bot
```

There's also a local mic/speaker runner:

```bash
python bidi_local.py
```

---

## Deployment (Cloud Run)

```bash
docker build -t agentic-insurance-bot:latest .
docker tag agentic-insurance-bot:latest \
  us-central1-docker.pkg.dev/$PROJECT/insurance-repo/agentic-insurance-bot:latest
docker push us-central1-docker.pkg.dev/$PROJECT/insurance-repo/agentic-insurance-bot:latest

gcloud run deploy agentic-insurance-bot \
  --image us-central1-docker.pkg.dev/$PROJECT/insurance-repo/agentic-insurance-bot:latest \
  --region us-central1 --project $PROJECT --allow-unauthenticated \
  --set-env-vars GOOGLE_GENAI_USE_VERTEXAI=true,GOOGLE_CLOUD_PROJECT=$PROJECT,GOOGLE_CLOUD_LOCATION=us-central1,GCS_BUCKET=adk-insurance-demo-data-mi
```

The Cloud Run service account needs `roles/aiplatform.user` and read access to the GCS bucket.

---

## Evaluation

```bash
python -m evaluation.runner --output-report evaluation/reports/report.html
```

Targets (see `evaluation/metrics.py`): routing accuracy ≥ 95%, authorization block rate 100%, hallucination ≤ 5%, latency p95 ≤ 2000 ms, audit completeness 100%, HITL escalation rate 100%.

---

## Troubleshooting

**`'State' object has no attribute 'pop'`** — `ctx.state` has no `.pop()`/`del`. Set the key to `None` instead.

**`No function call event found for function response ids: {…}`** — the task-mode pause/resume bug. The fix is the current design (workflow owns the loop, brains are `single_turn`); see [doc 03 §2](03-design-decisions.md). If you reintroduce a `mode='task'` agent as a paused node, this returns.

**Vertex AI permission denied:**
```bash
gcloud projects add-iam-policy-binding $PROJECT \
  --member="user:you@example.com" --role="roles/aiplatform.user"
```

**GCS bucket not found / data missing:**
```bash
gsutil mb -l us-central1 gs://adk-insurance-demo-data-mi
python data/mock_data_generator.py --bucket adk-insurance-demo-data-mi
```

**Credentials error at `adk web` startup** — something constructed a credentialed client at import time. Keep all client construction lazy (see `gcs_client.py`).

**Model still `gemini-2.5-flash` despite `BRAIN_MODEL=...flash-lite`** — your running tree predates the `BRAIN_MODEL` change, or a `.env` overrides it. Confirm `BRAIN_MODEL` exists in `core/config.py` and that the log line `Sending out request, model: …` shows flash-lite.

**Port in use:**
```bash
lsof -i :8000 | grep LISTEN | awk '{print $2}' | xargs kill -9
```
