"""
Claims sub-agent tools — guided, stateful claim intake.

The agent collects a claim through FIVE required questions, one at a time. Every
answer is saved into the session state under `claim_intake` so progress survives
across turns and can be inspected ("see if all is connected"). Once all five are
collected we ask when the customer wants to be called back and file the claim
with that callback; if we can't collect everything, we route to a human.

State shape (tool_context.state["claim_intake"]):
    {
      "incident_type": "...",   # only the answered fields are present
      "incident_date": "...",
      ...
    }
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone

from insurance_bot.core.config import CLAIMS_BUCKET
from insurance_bot.core.gcs_client import gcs
from insurance_bot.core import audit_logger as audit
from insurance_bot.core import outcomes

logger = logging.getLogger(__name__)


# The five questions that must be answered to file a claim, in asking order.
CLAIM_QUESTIONS: list[dict] = [
    {"field": "incident_type",
     "question": "What type of incident are you claiming for — e.g. car accident, theft, fire, or water damage?"},
    {"field": "incident_date",
     "question": "When did it happen? Please give me the date (and rough time if you have it)."},
    {"field": "location",
     "question": "Where did it happen? A street, city, or address is fine."},
    {"field": "description",
     "question": "Can you briefly describe what happened?"},
    {"field": "policy_number",
     "question": "Which policy does this relate to? Please share your policy number."},
]

_FIELDS = [q["field"] for q in CLAIM_QUESTIONS]
_QUESTION_BY_FIELD = {q["field"]: q["question"] for q in CLAIM_QUESTIONS}


# ---------------------------------------------------------------------------
# Pure helpers (testable without an ADK ToolContext)
# ---------------------------------------------------------------------------

def missing_fields(intake: dict) -> list[str]:
    """Required fields not yet answered, in asking order."""
    return [f for f in _FIELDS if not (intake or {}).get(f)]


def next_question(intake: dict) -> dict | None:
    """The next unanswered question, or None when the intake is complete."""
    for f in _FIELDS:
        if not (intake or {}).get(f):
            return {"field": f, "question": _QUESTION_BY_FIELD[f]}
    return None


def is_complete(intake: dict) -> bool:
    return not missing_fields(intake)


def progress(intake: dict) -> dict:
    intake = intake or {}
    collected = [f for f in _FIELDS if intake.get(f)]
    return {
        "answers": {f: intake[f] for f in collected},
        "collected": collected,
        "missing": missing_fields(intake),
        "collected_count": len(collected),
        "total": len(_FIELDS),
        "complete": is_complete(intake),
        "next_question": next_question(intake),
    }


def _state(tool_context):
    return getattr(tool_context, "state", {}) if tool_context is not None else {}


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------

def list_claim_questions() -> dict:
    """The full list of questions needed to file a claim (ask them one at a time)."""
    return {"questions": CLAIM_QUESTIONS, "total": len(CLAIM_QUESTIONS)}


def record_claim_answer(field: str, value: str, tool_context=None) -> dict:
    """Save the answer to ONE claim question into state and report progress.

    `field` must be one of: incident_type, incident_date, location, description,
    policy_number. Returns which fields are still missing and the next question.
    """
    if field not in _FIELDS:
        return {"error": f"Unknown field '{field}'. Valid fields: {_FIELDS}"}
    if not (value or "").strip():
        return {"error": "Empty answer — please provide a value."}

    state = _state(tool_context)
    intake = dict(state.get("claim_intake") or {})
    intake[field] = value.strip()
    state["claim_intake"] = intake  # reassign so ADK State marks it dirty

    p = progress(intake)
    logger.info("CLAIM INTAKE | recorded %s (%d/%d) missing=%s",
                field, p["collected_count"], p["total"], p["missing"])
    return {"recorded": field, **p}


def get_claim_progress(tool_context=None) -> dict:
    """Show what's collected so far, what's missing, and whether the intake is complete."""
    return progress(_state(tool_context).get("claim_intake") or {})


def finalize_claim_with_callback(preferred_callback_time: str, tool_context=None) -> dict:
    """File the claim once ALL five answers are collected, with a callback time.

    Requires a complete intake. Persists the claim (status SUBMITTED) including the
    customer's preferred callback time; an agent will call them back then.
    """
    state = _state(tool_context)
    intake = dict(state.get("claim_intake") or {})
    if not is_complete(intake):
        return {"error": "Claim is not complete yet.", **progress(intake)}
    if not (preferred_callback_time or "").strip():
        return {"error": "Please provide a preferred callback time."}

    state["resolution"] = outcomes.RESOLVED
    customer_id = state.get("active_customer_id")
    claim_id = f"clm_{uuid.uuid4().hex[:8]}"
    record = {
        "claim_id": claim_id,
        "customer_id": customer_id,
        "status": "SUBMITTED",
        "date_filed": datetime.now(timezone.utc).isoformat(),
        "preferred_callback_time": preferred_callback_time.strip(),
        "callback_status": "SCHEDULED",
        **intake,
    }
    written = gcs.write_to(CLAIMS_BUCKET, f"claims/{claim_id}.json", record)
    audit.log_action(
        session_id=state.get("session_id", "unknown"), customer_id=customer_id,
        action="CLAIM_FILED", intent="claim", risk_level="HIGH", status="SUBMITTED",
        extra={"claim_id": claim_id, "callback_time": preferred_callback_time.strip(),
               "persisted": written},
    )
    logger.info("CLAIM | filed %s customer=%s callback=%s persisted=%s",
                claim_id, customer_id, preferred_callback_time.strip(), written)
    return {
        "claim_id": claim_id, "status": "SUBMITTED", "callback_status": "SCHEDULED",
        "preferred_callback_time": preferred_callback_time.strip(),
        "message": (
            f"Your claim {claim_id} has been registered. One of our claims agents will "
            f"call you back at your preferred time ({preferred_callback_time.strip()})."
        ),
    }


def route_claim_to_human(reason: str, tool_context=None) -> dict:
    """Hand off to a human when the full claim details can't be collected.

    Records what was gathered so a human can continue. Use when the customer can't
    or won't provide all required answers.
    """
    state = _state(tool_context)
    state["resolution"] = outcomes.HUMAN_HANDOFF
    intake = dict(state.get("claim_intake") or {})
    customer_id = state.get("active_customer_id")
    ref_id = f"clm_esc_{uuid.uuid4().hex[:8]}"
    record = {
        "escalation_id": ref_id,
        "customer_id": customer_id,
        "status": "PENDING_HUMAN_REVIEW",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "reason": (reason or "Incomplete claim intake").strip(),
        "collected_so_far": intake,
        "missing": missing_fields(intake),
    }
    written = gcs.write_to(CLAIMS_BUCKET, f"escalations/{ref_id}.json", record)
    audit.log_action(
        session_id=state.get("session_id", "unknown"), customer_id=customer_id,
        action="CLAIM_ESCALATED", intent="claim", risk_level="HIGH",
        status="PENDING_HUMAN_REVIEW",
        extra={"escalation_id": ref_id, "reason": record["reason"],
               "missing": record["missing"], "persisted": written},
    )
    logger.info("CLAIM | escalated %s customer=%s missing=%s persisted=%s",
                ref_id, customer_id, record["missing"], written)
    return {
        "escalation_id": ref_id, "status": "PENDING_HUMAN_REVIEW",
        "message": (
            "I wasn't able to collect everything needed to file this automatically, so I've "
            f"passed your claim to a human specialist who will follow up. Reference: {ref_id}."
        ),
    }


CLAIM_TOOLS = [
    list_claim_questions,
    record_claim_answer,
    get_claim_progress,
    finalize_claim_with_callback,
    route_claim_to_human,
]
