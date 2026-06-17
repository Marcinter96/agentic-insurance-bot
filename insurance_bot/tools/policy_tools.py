"""
Policy sub-agent tools.

Every tool is scoped to ONE verified customer (the `customer_id` the workflow
put in the agent's context). Ownership is enforced against the policy's own
`customer_id` field — the authoritative link — so a customer can never read or
fetch a document for a policy that isn't theirs. Sensitive reads (document
downloads) are audit-logged.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, timezone

from insurance_bot.core.gcs_client import gcs
from insurance_bot.core import audit_logger as audit
from insurance_bot.core import outcomes


def _state(tool_context):
    return getattr(tool_context, "state", {}) if tool_context is not None else {}


def _audit(customer_id: str, action: str, status: str, **extra) -> None:
    audit.log_action(
        session_id=f"tool:{customer_id}", customer_id=customer_id,
        action=action, intent="policy_question", risk_level="MEDIUM",
        status=status, extra=extra or None,
    )


def _owned_policy(policy_id: str, customer_id: str) -> dict | None:
    """Return the policy iff it belongs to `customer_id`, else None."""
    policy = gcs.get_policy(policy_id)
    if not policy or policy.get("customer_id") != customer_id:
        return None
    return policy


def _days_until(iso_date: str) -> int | None:
    try:
        return (date.fromisoformat(iso_date) - date.today()).days
    except (ValueError, TypeError):
        return None


def list_customer_policies(customer_id: str) -> list[dict]:
    """List all policies for the verified customer (summary view)."""
    customer = gcs.get_customer(customer_id)
    if not customer:
        return []
    out = []
    for pid in customer.get("policy_ids", []):
        p = gcs.get_policy(pid)
        if p:
            out.append({"policy_id": p["policy_id"], "type": p.get("type"),
                        "status": p.get("status"), "premium": p.get("premium"),
                        "expiry": p.get("expiry")})
    return out


def get_policy_details(policy_id: str, customer_id: str) -> dict:
    """Retrieve full details of one policy. Enforces customer ownership."""
    policy = _owned_policy(policy_id, customer_id)
    if not policy:
        return {"error": "Policy not found or not owned by this customer."}
    return policy


def list_insured_vehicles(customer_id: str) -> list[dict]:
    """List the vehicles registered to the verified customer."""
    customer = gcs.get_customer(customer_id)
    if not customer:
        return []
    out = []
    for vid in customer.get("vehicle_ids", []):
        v = gcs.get_vehicle(vid)
        if v and v.get("customer_id") == customer_id:
            out.append({"vehicle_id": v["vehicle_id"], "make": v.get("make"),
                        "model": v.get("model"), "year": v.get("year"),
                        "license_plate": v.get("license_plate"), "value": v.get("value")})
    return out


def get_customer_invoices(customer_id: str) -> list[dict]:
    """List all invoices for the verified customer."""
    return gcs.get_invoices(customer_id)


def get_unpaid_invoices(customer_id: str) -> dict:
    """List only DUE/OVERDUE invoices for the customer, with the total owed."""
    unpaid = [inv for inv in gcs.get_invoices(customer_id)
              if inv.get("status") in ("DUE", "OVERDUE")]
    total = round(sum(inv.get("amount", 0) for inv in unpaid), 2)
    return {"unpaid_invoices": unpaid, "count": len(unpaid),
            "total_outstanding": total, "currency": "EUR"}


def get_renewal_info(policy_id: str, customer_id: str) -> dict:
    """When does this policy expire, how many days away, and at what premium?"""
    policy = _owned_policy(policy_id, customer_id)
    if not policy:
        return {"error": "Policy not found or not owned by this customer."}
    days = _days_until(policy.get("expiry"))
    return {"policy_id": policy_id, "status": policy.get("status"),
            "expiry": policy.get("expiry"), "days_until_expiry": days,
            "premium": policy.get("premium"), "currency": "EUR",
            "renewal_due_soon": days is not None and days <= 30}


def download_policy_document(policy_id: str, customer_id: str) -> dict:
    """Issue a (mock) secure download link for the customer's policy document."""
    policy = _owned_policy(policy_id, customer_id)
    if not policy:
        _audit(customer_id, "POLICY_DOC_ACCESS", "DENIED", policy_id=policy_id)
        return {"error": "Policy not found or not owned by this customer."}
    _audit(customer_id, "POLICY_DOC_ACCESS", "SUCCESS", policy_id=policy_id)
    return {"policy_id": policy_id,
            "document_url": f"https://docs.zurich.example/secure/{customer_id}/{policy_id}.pdf",
            "expires_in_minutes": 15,
            "note": "This is a temporary secure link, valid for 15 minutes."}


def route_policy_to_human(reason: str, tool_context=None) -> dict:
    """Hand off to a human when the agent can't answer the customer's policy question.

    Use this when the question is outside what the tools can answer, or the
    customer isn't getting what they need. Audit-logged for follow-up.
    """
    state = _state(tool_context)
    state["resolution"] = outcomes.HUMAN_HANDOFF
    customer_id = state.get("active_customer_id")
    ref_id = f"pol_esc_{uuid.uuid4().hex[:8]}"
    audit.log_action(
        session_id=state.get("session_id", "unknown"), customer_id=customer_id,
        action="POLICY_ESCALATED", intent="policy_question", risk_level="MEDIUM",
        status="PENDING_HUMAN_REVIEW",
        extra={"escalation_id": ref_id, "reason": (reason or "Unspecified").strip(),
               "created_at": datetime.now(timezone.utc).isoformat()},
    )
    return {
        "escalation_id": ref_id, "status": "PENDING_HUMAN_REVIEW",
        "message": (
            "I'm not able to fully answer that one myself, so I've passed it to a human "
            f"specialist who will follow up with you. Reference: {ref_id}."
        ),
    }


def close_conversation(satisfied: bool = True, tool_context=None) -> dict:
    """Close the conversation when the customer's policy questions are answered.

    Call this once the customer says they're satisfied / have no more questions.
    """
    state = _state(tool_context)
    state["resolution"] = outcomes.RESOLVED
    customer_id = state.get("active_customer_id")
    audit.log_action(
        session_id=state.get("session_id", "unknown"), customer_id=customer_id,
        action="POLICY_CONVERSATION_CLOSED", intent="policy_question",
        risk_level="LOW", status="RESOLVED" if satisfied else "ENDED",
        extra={"satisfied": bool(satisfied)},
    )
    return {"status": "closed", "satisfied": bool(satisfied),
            "message": "Glad I could help — take care, and reach out any time!"
                       if satisfied else "Thanks for getting in touch. Have a good day!"}


POLICY_TOOLS = [
    list_customer_policies,
    get_policy_details,
    list_insured_vehicles,
    get_customer_invoices,
    get_unpaid_invoices,
    get_renewal_info,
    download_policy_document,
    route_policy_to_human,
    close_conversation,
]
