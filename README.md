# Agentic Insurance Bot

A guardrailed multi-agent insurance chatbot built with **Google ADK 2.2.0** and **Gemini 2.5 Flash** on Vertex AI.

Every routing decision is **deterministic** (plain Python rules), never probabilistic LLM delegation. The LLM is used only where judgement is genuinely needed — understanding what a caller wants and extracting identifiers — and even there it runs as a stateless "brain" that the workflow drives one turn at a time.

> **New here?** Read the [`docs/`](docs/) folder. It explains the ADK building blocks from first principles and walks through every design choice (including the bugs that shaped them).

---

## What it does (the happy path)

```
1. Caller says something vague        "Hi, I need some help."
2. Bot finds out WHAT they want       → classifier asks ≤4 questions → intent = claim
3. Bot finds out WHO they are         → identifier asks phone+DOB → GCS lookup → verified
4. Bot checks the rulebook            → verified + allowed → proceed (else escalate to human)
5. Bot does the work                  → routes to the claims specialist
6. Risk gate                          → low risk auto-answers; higher risk asks to confirm / HITL
```

## Architecture

Fully **sequential**. The Workflow is a pure orchestrator; the two conversational steps are loops that the workflow itself drives, pausing for the caller between questions.

```
USER MESSAGE
     │
     ▼
┌──────────────────────────────────────────────┐
│ NODE 1  intent_classifier   (loop)            │  ask ⇄ wait, ≤4 turns
│   workflow owns the loop + the pause          │  brain = one-shot decision: ask / done
│   writes ctx.state["classification"]          │
└───────────────────┬──────────────────────────┘
                    ▼
┌──────────────────────────────────────────────┐
│ NODE 2  identification_node (loop)            │  ask ⇄ wait ⇄ GCS lookup
│   brain decides: ask / lookup / give_up       │  lookup = plain function (guardrails)
│   writes ctx.state["verification"]            │
└───────────────────┬──────────────────────────┘
                    ▼
┌──────────────────────────────────────────────┐
│ NODE 3  risk_router   (deterministic, no LLM) │  guardrails.decide_route(...)
└───────┬───────────────────────────────┬──────┘
   escalate                         proceed
        │                                │
        ▼                                ▼
NODE 4a escalation_handler     NODE 4b specialist_router (deterministic)
(HITL via RequestInput)         ├─ policy_question → policy_agent
                                ├─ claim          → claims_agent
                                ├─ offer          → offers_agent
                                └─ emergency      → emergency_agent
                                         │
                                         ▼
                                NODE 5 action_confirmation
                                LOW → auto · MED → confirm · HIGH → HITL
```

### The one rule that makes it robust

> **Only the Workflow pauses. The agents never pause** — each is a one-shot brain that, given the conversation so far, returns a single structured decision.

Earlier versions tried to let a multi-turn `mode='task'` agent run the conversation itself. That repeatedly broke on pause/resume (the `No function call event found for function response ids` crash). Moving the loop into the workflow and demoting the agents to one-shot brains removed an entire class of bugs. The full story is in [`docs/03-design-decisions.md`](docs/03-design-decisions.md).

### Key design decisions (summary)

| Decision | Reason |
|----------|--------|
| **Workflow + `@node`** | Deterministic routing — no probabilistic LLM delegation |
| **Workflow owns the conversation loop; agents are one-shot brains** | `RequestInput` is the only mechanism that truly pauses a Workflow; keeps agents un-breakable |
| **`single_turn` brains with `output_schema`** | Strict structured decisions (ask / done / lookup), no `finish_task` bookkeeping |
| **Deterministic lookup + rulebook in `guardrails.py`** | Identity and routing are plain Python, shared by text and voice channels |
| **GCS as single source of truth** | All customer/policy/claim data lives in Cloud Storage |
| **Risk-based HITL** | Proportional guardrails — HIGH risk always gets human review |
| **Thinking disabled + `to_thread` GCS** | Brains skip Gemini's reasoning pass; blocking I/O kept off the event loop |

---

## Documentation

| Doc | Contents |
|-----|----------|
| [docs/01-adk-concepts.md](docs/01-adk-concepts.md) | ADK from first principles: Workflow, FunctionNode, LlmAgent modes, `RequestInput`, `run_node`, replay/resume |
| [docs/02-architecture.md](docs/02-architecture.md) | How this bot is wired: the node graph, state, the conversation loop pattern |
| [docs/03-design-decisions.md](docs/03-design-decisions.md) | Every choice and trade-off, including the task-mode bug and the perf tuning |
| [docs/04-data-and-guardrails.md](docs/04-data-and-guardrails.md) | GCS data model, verification levels, the routing rulebook, audit trail |
| [docs/05-running-and-ops.md](docs/05-running-and-ops.md) | Setup, env vars, performance knobs, voice mode, troubleshooting |
| [docs/06-guardrails.md](docs/06-guardrails.md) | Input/output guardrails: the app-wide GuardrailPlugin, the hybrid rules+brain logic, and per-node/agent/function protections |

---

## Project structure

```
agentic-insurance-bot/
├── insurance_bot/                # the ADK app (run: `adk web insurance_bot`)
│   ├── agent.py                  # ADK entry point → re-exports root_agent
│   ├── workflow.py               # the Workflow: all nodes + the conversation loops
│   ├── live_agent.py             # voice/bidi mode (ADK_BIDI=1)
│   ├── agents/
│   │   ├── classifier_agent.py   # Node 1 brain (single_turn, ask/done)
│   │   ├── identifier_agent.py   # Node 2 brain (single_turn, ask/lookup/give_up)
│   │   ├── policy_agent.py       # specialist: policy docs, coverage, invoices
│   │   ├── claims_agent.py       # specialist: file & check claims
│   │   ├── offers_agent.py       # specialist: quotes for new products
│   │   └── emergency_agent.py    # specialist: SOS, roadside assistance
│   └── core/
│       ├── config.py             # env vars, model + thinking config
│       ├── models.py             # Pydantic schemas
│       ├── gcs_client.py         # all GCS reads/writes (lazy client)
│       ├── guardrails.py         # verify_customer() + decide_route() (shared, no LLM)
│       └── audit_logger.py       # audit trail
├── data/mock_data_generator.py   # seeds the GCS bucket
├── evaluation/                   # metrics, scenarios, HTML report
├── bidi_local.py                 # local mic/speaker voice runner
├── requirements.txt
└── Dockerfile
```

---

## Quick start

```bash
# 1. install
python3.11 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# 2. authenticate with GCP (Vertex AI + GCS)
gcloud auth application-default login
gcloud config set project project-72fdf994-e492-4b76-83e

# 3. seed data (once)
python data/mock_data_generator.py --bucket adk-insurance-demo-data-mi

# 4. run — from the repo root
export GOOGLE_GENAI_USE_VERTEXAI=true
export GOOGLE_CLOUD_PROJECT=project-72fdf994-e492-4b76-83e
export GOOGLE_CLOUD_LOCATION=us-central1
export GCS_BUCKET=adk-insurance-demo-data-mi
export BRAIN_MODEL=gemini-2.5-flash-lite   # fast classification/extraction
adk web insurance_bot
```

Open **http://127.0.0.1:8000**, pick `insurance_bot`, and try:

| Try saying | Expected |
|------------|----------|
| `"Hi, I need help"` | classifier asks one question at a time |
| `"I want to check on a claim"` then phone + DOB | → verified → claims specialist |
| `"I'm broken down on the highway!"` | → emergency specialist immediately |
| an unknown phone/DOB | → asks for policy / plate → escalates if still not found |

Full setup, env vars, performance tuning, voice mode and troubleshooting: [docs/05-running-and-ops.md](docs/05-running-and-ops.md).

---

## Tech stack

| Component | Technology |
|-----------|-----------|
| Agent framework | Google ADK 2.2.0 |
| LLM | Gemini 2.5 Flash / Flash-Lite (Vertex AI) |
| Data store | Google Cloud Storage |
| Deployment | Google Cloud Run |
| Language | Python 3.11+ |
| Schemas | Pydantic v2 |
