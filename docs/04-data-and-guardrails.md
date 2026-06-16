# 04 — Data model & guardrails

This document covers where data lives, how a caller is verified, the routing rulebook, and the audit trail. The logic here is deliberately **pure Python with no LLM** — it lives in `insurance_bot/core/` and is shared by both the text and voice channels.

---

## GCS as the single source of truth

All customer, policy, invoice, claim, and vehicle data lives in a Cloud Storage bucket (default `gs://adk-insurance-demo-data-mi/`). It is seeded by `data/mock_data_generator.py`.

```
gs://adk-insurance-demo-data-mi/
├── customers/                     # customer profiles  (id, name, birthdate, account_status, …)
├── policies/                      # policy documents
├── invoices/                      # invoices
├── claims/                        # claim records
├── vehicle_registrations/         # vehicles
├── indexes/
│   ├── phone_to_customer.json     # phone        → customer_id
│   ├── plate_to_customer.json     # licence plate → customer_id
│   ├── customer_invoices/         # per-customer invoice index
│   └── customer_claims/           # per-customer claims index
└── audit_logs/                    # append-only audit trail (written at runtime)
```

### Lazy client

`core/gcs_client.py` exposes a singleton `gcs` with a **lazy** `storage.Client()` — created on first read, never at import. This is required because `adk web` imports the package before GCP credentials are guaranteed (see [doc 01 §7](01-adk-concepts.md)).

---

## Customer verification — `guardrails.verify_customer(...)`

Called by the `identification_node` (and by the voice agent's `verify_customer_identity` tool). It tries identifiers in priority order and cross-checks the birthdate:

1. **phone** → `indexes/phone_to_customer.json`
2. **policy_number** → `policies/{id}.json`
3. **license_plate** → `indexes/plate_to_customer.json`
4. **birthdate** → secondary cross-check against the found record

It returns a dict shaped exactly like `ctx.state["verification"]`:

```python
{
  "customer_id": str | None,
  "verification_level": "VERIFIED_RETURNING" | "VERIFIED_NEW" | "ESCALATED" | "UNVERIFIED",
  "allowed_actions": list[str],
  "failure_reason": str | None,
  "customer_data": { "name", "policy_ids", "vehicle_ids" },
}
```

### Verification levels & allowed actions

| Level | Condition | Allowed intents |
|-------|-----------|-----------------|
| `VERIFIED_RETURNING` | found + birthdate matches + active, returning account | `policy_question`, `claim`, `offer`, `emergency` |
| `VERIFIED_NEW` | found + birthdate matches, new account | `policy_question`, `offer`, `emergency` (no `claim`) |
| `ESCALATED` | found but birthdate mismatch **or** account not `ACTIVE` | none → human review |
| `UNVERIFIED` | no matching customer | none → ask for another identifier / support |

The level → allowed-actions map is `guardrails.get_allowed_actions(level)`.

---

## The routing rulebook — `guardrails.decide_route(...)`

The deterministic gate at the heart of Node 3. No LLM:

```python
def decide_route(*, verification_level, intent, allowed_actions) -> str:
    if (verification_level in ("UNVERIFIED", "ESCALATED")
            or intent == "unknown"
            or intent not in allowed_actions):
        return "escalate"
    return "proceed"
```

In words — **escalate to a human** if the caller isn't verified, the account is flagged, we couldn't understand the intent, or the intent isn't permitted at their verification level. Otherwise **proceed** to the specialist.

---

## Risk levels

Derived from intent in code (never from the model), and used by `action_confirmation` as the final gate:

| Intent | Risk | Final gate |
|--------|------|-----------|
| `offer` | LOW | auto-approve |
| `policy_question` | MEDIUM | ask the caller to confirm |
| `claim` | HIGH | human-in-the-loop approval |
| `emergency` | HIGH | human-in-the-loop approval |
| `unknown` | LOW | (routed to escalation anyway) |

---

## Specialist ownership enforcement

Every specialist tool re-verifies ownership before returning anything. Example from `policy_agent.py`:

```python
def get_policy_details(policy_id, customer_id):
    customer = gcs.get_customer(customer_id)
    if not customer or policy_id not in customer.get("policy_ids", []):
        return {"error": "Policy not found or not owned by this customer."}
    return gcs.get_policy(policy_id)
```

This is defence in depth: the check lives in the data-access code, so even a routing or prompt error cannot leak another customer's data.

---

## Audit trail — `core/audit_logger.py`

`log_action(...)` writes a structured entry for each significant step (`REQUEST_RECEIVED`, `HITL_ESCALATION`, `ESCALATION_HANDLED`, `ACTION_AUTO_APPROVED`, `ACTION_CONFIRMATION`, …). Each entry carries:

```
timestamp · session_id · customer_id · action · intent · risk_level · status · [extra]
```

It writes to **Google Cloud Logging** when available (logger name `insurance-bot`) and **always** falls back to the standard Python logger, so audit never depends on cloud connectivity at import time. The workflow logs once per request after identity is resolved, and again at each gate/escalation.
