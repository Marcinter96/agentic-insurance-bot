# Agentic Insurance Bot

A production-grade guardrailed multi-agent insurance chatbot built with **Google ADK 2.2.0** and **Gemini 2.5 Flash** on Vertex AI. Uses deterministic workflow routing — not probabilistic LLM delegation — to ensure every customer request is handled safely, auditably, and consistently.

---

## Architecture

```
USER MESSAGE
      │
      ▼
┌─────────────────────────────────┐
│  NODE 1: intent_classifier      │  ← Gemini classifies into 4 categories
│  policy_question / offer /      │    + extracts customer identifiers
│  claim / emergency / unknown    │
└──────────────┬──────────────────┘
               │ parallel fan-out
    ┌──────────┴───────────┐
    ▼                      ▼
NODE 2a                 NODE 2b
verification_node       audit_logger
(GCS lookup by          (Cloud Logging —
phone/policy/plate)     immutable audit trail)
    │                      │
    └──────────┬───────────┘
               ▼
        ┌─────────────┐
        │  JoinNode   │  ← waits for both branches
        └──────┬──────┘
               ▼
┌──────────────────────────────────┐
│  NODE 3: risk_router             │  ← deterministic, no LLM
│  ESCALATE if: UNVERIFIED /       │
│    ESCALATED / unknown intent    │
│  PROCEED if: verified + allowed  │
└──────────┬───────────────────────────┘
     ┌─────┴─────┐
  escalate      proceed
     │              │
     ▼              ▼
NODE 4a         NODE 4b: specialist_router
escalation_     │
handler         ├─ policy_question → policy_agent
(HITL via       ├─ claim          → claims_agent
RequestInput)   ├─ offer          → offers_agent
                └─ emergency      → emergency_agent
                         │
                         ▼
                NODE 5: action_confirmation
                LOW  → auto-approve
                MED  → user confirms
                HIGH → HITL pause
```

### Key Design Decisions

| Decision | Reason |
|----------|--------|
| **Workflow + @node** | Deterministic routing — no probabilistic LLM delegation |
| **Parallel verification + audit** | Speed + instant immutable log before any action |
| **GCS as single source of truth** | All customer/policy/claim data lives in Cloud Storage |
| **Risk-based HITL** | Proportional guardrails — HIGH risk always gets human review |
| **Pydantic I/O schemas** | No hallucinated JSON; strict contracts per node |
| **Cloud Logging audit trail** | Immutable, queryable, compliance-ready |

---

## Project Structure

```
agentic-insurance-bot/
├── workflow.py                   # Main orchestration — root_agent = Workflow
│
├── agents/
│   ├── policy_agent.py           # MEDIUM risk: policy docs, coverage, invoices
│   ├── claims_agent.py           # MEDIUM/HIGH risk: file & check claims
│   ├── offers_agent.py           # LOW risk: quotes for new products
│   └── emergency_agent.py        # HIGH priority: SOS, roadside assistance
│
├── core/
│   ├── config.py                 # GCP project, bucket, model env vars
│   ├── models.py                 # Pydantic schemas
│   ├── gcs_client.py             # All GCS reads/writes
│   └── audit_logger.py           # Cloud Logging integration
│
├── data/
│   └── mock_data_generator.py    # Seeds GCS with 100 customers, 300+ policies, etc.
│
├── evaluation/
│   ├── metrics.py                # 6 evaluation metrics
│   ├── test_scenarios.py         # 30 test cases
│   └── runner.py                 # HTML report generator
│
├── requirements.txt
└── Dockerfile
```

---

## GCS Bucket Structure

All data lives in `gs://adk-insurance-demo-data-mi/`:

```
customers/                    # 100 customer profiles
policies/                     # 300+ policies
invoices/                     # 500+ invoices
claims/                       # ~40 claim records
vehicle_registrations/        # 50 vehicles
indexes/
  ├── phone_to_customer.json  # phone → customer_id lookup
  ├── plate_to_customer.json  # license plate → customer_id lookup
  ├── customer_invoices/      # per-customer invoice index
  └── customer_claims/        # per-customer claims index
audit_logs/                   # append-only audit trail (written at runtime)
```

---

## Customer Verification

The `verification_node` tries identifiers in this order:

1. **Phone number** → `indexes/phone_to_customer.json`
2. **Policy number** → `policies/{policy_id}.json`
3. **License plate** → `indexes/plate_to_customer.json`
4. **Birthdate** (secondary cross-check against stored record)

### Verification Levels & Allowed Actions

| Level | Condition | Allowed |
|-------|-----------|--------|
| `VERIFIED_RETURNING` | Found + birthdate matches + prior activity | All: policy, claim, offer, emergency |
| `VERIFIED_NEW` | Found + birthdate matches, no prior activity | Read-only: policy, offer, emergency |
| `ESCALATED` | Found but verification failed / account suspended | None → human review |
| `UNVERIFIED` | Not found in system | None → ask for identifier |

---

## Specialist Agents

| Agent | Risk | Tools | Ownership check |
|-------|------|-------|----------------|
| `policy_agent` | MEDIUM | `get_policy_details`, `list_customer_policies`, `get_customer_invoices` | policy_id must be in customer's policy_ids |
| `claims_agent` | MEDIUM/HIGH | `get_open_claims`, `get_claim_status`, `file_new_claim` | claims indexed by customer_id |
| `offers_agent` | LOW | `list_available_products`, `get_personalized_quote` | no sensitive data |
| `emergency_agent` | HIGH priority | `get_emergency_contacts`, `dispatch_roadside_assistance` | always responds immediately |

---

## Quick Start

### Prerequisites

- Python 3.11+
- GCP account with Vertex AI enabled
- `gcloud` CLI authenticated

### 1. Clone & install

```bash
git clone https://github.com/Marcinter96/agentic-insurance-bot.git
cd agentic-insurance-bot
python3.11 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 2. Authenticate with GCP

```bash
gcloud auth login
gcloud auth application-default login
gcloud config set project project-72fdf994-e492-4b76-83e
```

### 3. Create GCS bucket & seed data

```bash
# Create the bucket (once)
gsutil mb -l us-central1 gs://adk-insurance-demo-data-mi

# Seed with 100 customers, 300+ policies, 500+ invoices, claims, vehicles
python data/mock_data_generator.py --bucket adk-insurance-demo-data-mi
```

**Verify data in GCS:**
```bash
gsutil ls gs://adk-insurance-demo-data-mi/
gsutil ls gs://adk-insurance-demo-data-mi/customers/ | head -5
```
Or open the [Cloud Console Storage Browser](https://console.cloud.google.com/storage/browser/adk-insurance-demo-data-mi?project=project-72fdf994-e492-4b76-83e).

### 4. Set environment variables

```bash
export GOOGLE_GENAI_USE_VERTEXAI=true
export GOOGLE_CLOUD_PROJECT=project-72fdf994-e492-4b76-83e
export GOOGLE_CLOUD_LOCATION=us-central1
export GCS_BUCKET=adk-insurance-demo-data-mi
```

### 5. Start the dev server

```bash
# Run from the PARENT directory of agentic-insurance-bot/
cd ..
GOOGLE_GENAI_USE_VERTEXAI=true \
GOOGLE_CLOUD_PROJECT=project-72fdf994-e492-4b76-83e \
GOOGLE_CLOUD_LOCATION=us-central1 \
GCS_BUCKET=adk-insurance-demo-data-mi \
adk web agentic-insurance-bot --port 8001
```

Open **http://127.0.0.1:8001**

---

## Demo Scenarios

Try these messages in the ADK web UI:

| Scenario | Example message | Expected path |
|----------|----------------|---------------|
| Policy question | `"What does my car insurance cover? My policy is pol_0001"` | → `policy_agent` |
| New quote | `"I'd like a quote for home insurance"` | → `offers_agent` |
| File a claim | `"I had an accident on the E40 yesterday, policy pol_0001"` | → `claims_agent` → confirm |
| Emergency | `"I'm broken down on the highway near Brussels!"` | → `emergency_agent` |
| No identifier | `"I have a question about my policy"` | → escalation asks for ID |
| Wrong birthdate | Phone found but birthdate mismatch | → `ESCALATED` → HITL |

---

## Evaluation

```bash
python -m evaluation.runner --output-report evaluation/reports/report.html
```

### Targets

| Metric | Target |
|--------|--------|
| Routing accuracy | ≥ 95% |
| Authorization block rate | 100% |
| Hallucination rate | ≤ 5% |
| Latency p95 | ≤ 2 000 ms |
| Audit completeness | 100% |
| HITL escalation rate | 100% |

---

## Deployment to Cloud Run

```bash
docker build -t agentic-insurance-bot:latest .
docker tag agentic-insurance-bot:latest \
  us-central1-docker.pkg.dev/project-72fdf994-e492-4b76-83e/insurance-repo/agentic-insurance-bot:latest
docker push us-central1-docker.pkg.dev/project-72fdf994-e492-4b76-83e/insurance-repo/agentic-insurance-bot:latest

gcloud run deploy agentic-insurance-bot \
  --image us-central1-docker.pkg.dev/project-72fdf994-e492-4b76-83e/insurance-repo/agentic-insurance-bot:latest \
  --platform managed \
  --region us-central1 \
  --project project-72fdf994-e492-4b76-83e \
  --set-env-vars GOOGLE_GENAI_USE_VERTEXAI=true,GOOGLE_CLOUD_PROJECT=project-72fdf994-e492-4b76-83e,GOOGLE_CLOUD_LOCATION=us-central1,GCS_BUCKET=adk-insurance-demo-data-mi \
  --allow-unauthenticated
```

---

## Troubleshooting

**Vertex AI permission denied:**
```bash
gcloud projects add-iam-policy-binding project-72fdf994-e492-4b76-83e \
  --member="user:marcourfali@gmail.com" \
  --role="roles/aiplatform.user"
```

**GCS bucket not found / data missing:**
```bash
gsutil mb -l us-central1 gs://adk-insurance-demo-data-mi
python data/mock_data_generator.py --bucket adk-insurance-demo-data-mi
```

**Port 8001 in use:**
```bash
lsof -i :8001 | grep LISTEN | awk '{print $2}' | xargs kill -9
```

**Environment variables not loading:**
Set them inline on the command — do not rely on `.env` files:
```bash
GOOGLE_GENAI_USE_VERTEXAI=true GOOGLE_CLOUD_PROJECT=... adk web agentic-insurance-bot --port 8001
```

---

## Tech Stack

| Component | Technology |
|-----------|-----------|
| Agent framework | Google ADK 2.2.0 |
| LLM | Gemini 2.5 Flash (Vertex AI) |
| Data store | Google Cloud Storage |
| Audit logging | Google Cloud Logging |
| Deployment | Google Cloud Run |
| Language | Python 3.11+ |
| Schemas | Pydantic v2 |
