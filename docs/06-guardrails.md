# 06 — Guardrails

Guardrails are layered: **common** (cross-cutting, app-wide) and **specific** (attached to a node, agent, or function). The philosophy is the same as the rest of the app — enforce in code, fail safe, deterministic-first with an LLM only for the gray area.

---

## Common guardrails — the `GuardrailPlugin`

A single `BasePlugin` (`core/guardrail_plugin.py`) registered on the `App` in `agent.py`, so it applies to **every** user message and model response — text Workflow *and* voice. ADK loads `app` (with plugins) before `root_agent`.

| Direction | Hook | What it does |
|-----------|------|--------------|
| **Input** | `before_run_callback` | Screens the incoming message. On a block it returns a `Content`, which **halts the run** and replies with a fixed safe refusal — Gemini never sees the malicious text. |
| **Output** | `after_model_callback` | Screens a **specialist** agent's response *before it is finalized* and redacts secrets / payment-card numbers. (A workflow node can't do this — by the time it runs, the text has already streamed to the UI.) |

Only the four customer-facing specialists are screened on output; the classifier/identifier brains and the safety brain are not.

### Hybrid decision logic (`core/safety.py`)

```
message ──▶ deterministic rules ──▶ allow ─▶ proceed
                                └──▶ block ─▶ refuse + audit
                                └──▶ unsure ─▶ safety brain ─▶ allow / block
```

- **Deterministic first** (`screen_input_rules`, `screen_output_rules`): regex injection signatures, a small abuse list, secret patterns, and a Luhn check for card numbers. Fast, free, fully unit-tested.
- **Safety brain** only for the **gray area** (soft signals present, no hard match): a direct GenAI call with `response_schema=SafetyVerdict` and thinking disabled. If it can't run, input **fails open with an audit** (don't block legit users); output always falls back to the deterministically-scrubbed text.
- Blocks are **audited** (`INPUT_GUARDRAIL_BLOCK`, `OUTPUT_GUARDRAIL_*`) and the user gets a fixed, safe message — never an echo of the offending content.

---

## Specific guardrails

| Component | Guardrail | Kind |
|-----------|-----------|------|
| `intent_classifier` | intent ∈ 5 allowed values; ≤4 questions; **risk derived from intent, not the LLM** | output-validation + bound |
| `identification_node` | ≤2 lookups; returns only a yes/no + level, never another customer's record; birthdate cross-check | bound + privacy |
| `risk_router` | the authorization matrix (`decide_route`) — unverified / escalated / unknown / not-allowed → human | **core authZ gate**, deterministic |
| `action_confirmation` | LOW auto · MEDIUM confirm · HIGH HITL — **except emergency, which bypasses HITL and responds immediately** (still audited) | risk gate |
| specialist **tools** | every tool re-checks the resource belongs to `customer_id` | data-layer authZ (the real defense against cross-customer leakage) |
| `gcs_client` | **id sanitization** — ids are validated against `^[A-Za-z0-9_-]{1,64}$` before being interpolated into a blob path; anything else fails safe to "not found" | path-injection prevention |

### Why emergency bypasses the gate
Emergency / SOS is HIGH risk but time-critical. Blocking a roadside-assistance request behind a human-approval pause is the wrong trade-off, so `action_confirmation` auto-proceeds for `intent == "emergency"` and records `emergency_bypass` in the audit entry.

### Why ownership checks live in the tools
Cross-customer data leakage is best prevented at the **data-access layer**, not in a prompt or a post-hoc text scan. Each specialist tool verifies ownership before returning anything, so even a routing or model error cannot surface another customer's policy, invoice, or claim.

---

## What we deliberately did **not** add (yet)

- **LLM grounding judge** — instead, grounding is structural: a specialist's only data source is its ownership-checked tools. An optional judge can be added behind a flag later.
- **PII masking in audit logs** — noted as a follow-up; audit entries currently store `customer_id` (an opaque id), not raw PII, but masking phone/card values in `extra` payloads would harden it further.

---

## Testing

The deterministic guardrails are pure functions and unit-tested offline (injection detection, Luhn, output scrub, id sanitization, emergency bypass). The safety brain and the live `before_run`/`after_model` halt/rewrite behavior require a credentialed `adk web` run to exercise end-to-end.
