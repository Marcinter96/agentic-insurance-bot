# 02 — Architecture

This document describes how the bot is wired together. It assumes you've read [doc 01](01-adk-concepts.md) for the ADK primitives.

---

## The division of labour

Three kinds of thing, each with one job:

| Building block | Role | Where |
|----------------|------|-------|
| **Workflow** | The director. Owns the order of steps **and** "stop and wait for the caller" (`RequestInput`). Nothing else pauses. | `insurance_bot/workflow.py` |
| **Agent (brain)** | A one-shot calculator. Given the conversation so far, returns one structured decision. Never loops, never waits. | `insurance_bot/agents/*_agent.py` |
| **Function** | Plain deterministic code: the database lookup and the routing rulebook. No AI. | `insurance_bot/core/guardrails.py` |

> **The rule:** Only the Workflow pauses. The agent is a calculator you call once per turn; functions are the database and the rulebook.

---

## The node graph

Defined at the bottom of `workflow.py` as `root_agent = Workflow(edges=[...])`:

```
START
  └─► intent_classifier ──► identification_node ──► risk_router
                                                       │
                          ┌────────────────────────────┴───────────────┐
                       (route="escalate")                          (route="proceed")
                          │                                              │
                   escalation_handler                            specialist_router
                   (HITL via RequestInput)                            │  (route = intent)
                                              ┌──────────┬─────────────┼─────────────┐
                                       policy_agent  claims_agent  offers_agent  emergency_agent
                                              └──────────┴─────────────┴─────────────┘
                                                                 │
                                                          action_confirmation
                                                          (LOW auto · MED confirm · HIGH HITL)
```

It is **fully sequential** — no parallel fan-out, no join nodes. Earlier versions ran verification and audit logging in parallel; we removed that for simplicity (see doc 03).

---

## Shared state

Everything the nodes need flows through `ctx.state`:

| Key | Written by | Shape |
|-----|-----------|-------|
| `session_id` | `intent_classifier` | `str` |
| `classification` | `intent_classifier` | `{intent, sub_intent, risk_level, customer_identifiers, confidence}` |
| `clf_q_{n}` | `intent_classifier` | the question asked on turn *n* (for idempotent replay) |
| `verification` | `identification_node` | `{customer_id, verification_level, allowed_actions, failure_reason, customer_data}` |
| `idf_q_{n}`, `idf_attempts` | `identification_node` | replay/loop bookkeeping |
| `active_customer_id` | `specialist_router` | `str | None` |

`classification.risk_level` is **derived from intent**, never guessed by the LLM:
`offer→LOW`, `policy_question→MEDIUM`, `claim→HIGH`, `emergency→HIGH`, `unknown→LOW`.

---

## Node 1 — `intent_classifier` (conversation loop)

Goal: figure out which one of `policy_question | offer | claim | emergency | unknown` the caller needs, asking at most 4 questions, one at a time.

How it works each time it runs:

1. **Short-circuit:** if `ctx.state["classification"]` already exists, return immediately (no LLM). `adk web` replays the whole workflow on every user message, so this prevents re-classifying on later turns.
2. **Rebuild the transcript** deterministically from the initial message + stored questions (`clf_q_{n}`) + caller replies (`ctx.resume_inputs`), via `replay_transcript()`.
3. **Call the brain once** (`classifier_brain`, a `single_turn` agent with `output_schema=ClassifierDecision`) → `{"action": "ask"|"done", "question", "intent", ...}`.
4. If `action == "done"` (or we hit 4 turns): write `classification` and return.
5. If `action == "ask"`: store the question and `yield RequestInput(...)` to pause for the caller.

The brain (`agents/classifier_agent.py`) is pure judgement; the loop, the turn limit, and the pause are all owned by the workflow.

---

## Node 2 — `identification_node` (conversation + lookup loop)

Goal: identify the caller in GCS. Ask for phone + date of birth first; if not found, ask for policy number or licence plate; if still not found, give up (→ human).

How it works:

1. **Short-circuit** on `ctx.state["verification"]`.
2. **Rebuild the transcript**, seeded with any identifiers the classifier already captured (so it doesn't re-ask).
3. **Inner loop** — call the brain (`identifier_brain`, `single_turn`, `output_schema=IdentifierDecision`) which returns `action ∈ {ask, lookup, give_up}`:
   - **`lookup`** → call `guardrails.verify_customer(...)` (run via `asyncio.to_thread` so the GCS network read doesn't block the event loop). If verified/escalated → write `verification`, done. If not found → add a note and loop again (the brain will then ask for an alternative identifier — **no caller pause needed between a failed lookup and the follow-up question**).
   - **`give_up`** (or 4 turns) → write `UNVERIFIED` and return.
   - **`ask`** → store the question and `yield RequestInput(...)`.

`MAX_LOOKUPS = 2` bounds the lookups (phone+DOB, then policy/plate).

---

## Node 3 — `risk_router` (deterministic)

No LLM. Reads `classification` and `verification`, calls `guardrails.decide_route(...)`, and sets `ctx.route` to `"escalate"` or `"proceed"`. The rulebook is in [doc 04](04-data-and-guardrails.md).

---

## Node 4a — `escalation_handler` (HITL)

Handles the unhappy paths: `UNVERIFIED` (ask the caller to contact support), `ESCALATED` (pause for human review via `RequestInput`), `unknown` intent (ask to clarify), or an unauthorized action (explain politely). Writes the response to `ctx.output`.

## Node 4b — `specialist_router` (deterministic)

No LLM. Sets `active_customer_id` and `ctx.route = intent`, fanning out to the matching specialist.

## Specialist agents

Four `LlmAgent`s (`policy_agent`, `claims_agent`, `offers_agent`, `emergency_agent`). Each has GCS-backed tools and a **hard ownership guardrail**: tools verify the requested resource belongs to `customer_id` before returning anything, so one customer can never see another's data.

## Node 5 — `action_confirmation` (risk gate)

Final gate before completing:
- **LOW** → auto-approve, log `SUCCESS`.
- **MEDIUM** → `RequestInput` "confirm? yes/no".
- **HIGH** → `RequestInput` human-approval pause.

---

## Two channels, one set of guardrails

The deterministic logic in `core/guardrails.py` (`verify_customer`, `decide_route`, `get_allowed_actions`) is imported by **both**:

- the **text** Workflow (`workflow.py`), and
- the **voice/bidi** agent (`live_agent.py`, used when `ADK_BIDI=1`).

So identity verification and authorization behave identically whether the caller types or speaks. See [doc 05](05-running-and-ops.md) for voice mode.
