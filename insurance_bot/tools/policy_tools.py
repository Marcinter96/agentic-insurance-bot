"""
Policy sub-agent tools.

Every tool is scoped to ONE verified customer (the `customer_id` the workflow
put in the agent's context). Ownership is enforced against the policy's own
`customer_id` field — the authoritative link — so a customer can never read or
fetch a document for a policy that isn't theirs. Sensitive reads (document
downloads) are audit-logged.
"""

from __future__ import annotations

from datetime import date

from insurance_bot.core.gcs_client import gcs
from insurance_bot.core import audit_logger as audit


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


POLICY_TOOLS = [
    list_customer_policies,
    get_policy_details,
    list_insured_vehicles,
    get_customer_invoices,
    get_unpaid_invoices,
    get_renewal_info,
    download_policy_document,
]
