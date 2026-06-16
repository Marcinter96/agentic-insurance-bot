"""
Shared guardrail logic for the insurance bot.

This module holds the pure, reusable business rules that protect customer
data and gate risky actions:

  - customer verification (identity lookup via GCS)
  - the verification-level → allowed-actions matrix
  - the deterministic risk-routing decision (escalate vs. proceed)

Both the deterministic text `Workflow` (workflow.py) and the Live/voice
`LlmAgent` (live_agent.py) import from here so the guardrails behave
identically regardless of the channel (text or voice).

Nothing here performs any LLM call, so it is cheap and side-effect free
(aside from the GCS reads inside `verify_customer`).
"""

from __future__ import annotations

import logging
import re

from insurance_bot.core.gcs_client import gcs

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Verification-level → allowed-actions matrix
# ---------------------------------------------------------------------------

_ALLOWED_ACTIONS = {
    "VERIFIED_RETURNING": ["policy_question", "claim", "offer", "emergency"],
    "VERIFIED_NEW": ["policy_question", "offer", "emergency"],
    "ESCALATED": [],
    "UNVERIFIED": [],
}


def get_allowed_actions(level: str) -> list[str]:
    """Return the list of intents permitted for a given verification level."""
    return _ALLOWED_ACTIONS.get(level, [])


# ---------------------------------------------------------------------------
# Customer verification
# ---------------------------------------------------------------------------

def verify_customer(
    *,
    phone: str | None = None,
    policy_number: str | None = None,
    license_plate: str | None = None,
    birthdate: str | None = None,
) -> dict:
    """Identify and verify a customer from any available identifier.

    Returns a dict shaped like the workflow's ``ctx.state["verification"]``:
        {
          "customer_id": str | None,
          "verification_level": "UNVERIFIED" | "ESCALATED"
                                | "VERIFIED_NEW" | "VERIFIED_RETURNING",
          "allowed_actions": list[str],
          "failure_reason": str | None,
          "customer_data": dict,
        }
    """
    customer = None
    matched_by = None

    if phone:
        customer = gcs.find_customer_by_phone(phone)
        if customer:
            matched_by = "phone"
    if not customer and policy_number:
        customer = gcs.find_customer_by_policy(policy_number)
        if customer:
            matched_by = "policy_number"
    if not customer and license_plate:
        customer = gcs.find_customer_by_plate(license_plate)
        if customer:
            matched_by = "license_plate"

    logger.info(
        "SEARCH | tried phone=%s policy=%s plate=%s -> %s",
        bool(phone), bool(policy_number), bool(license_plate),
        f"matched cust={customer['id']} by {matched_by}" if customer else "no match",
    )

    if not customer:
        return {
            "customer_id": None,
            "verification_level": "UNVERIFIED",
            "allowed_actions": [],
            "failure_reason": "No matching customer found for the provided identifiers.",
            "customer_data": {},
        }

    # Secondary check: birthdate cross-validation (when supplied).
    # Compare digits-only so "1978-03-12" and "1978/03/12" both match.
    stored_birthdate = customer.get("birthdate")
    _bd = lambda s: re.sub(r"\D", "", s or "")
    birthdate_match = (birthdate is None) or (_bd(stored_birthdate) == _bd(birthdate))

    if not birthdate_match:
        return {
            "customer_id": customer["id"],
            "verification_level": "ESCALATED",
            "allowed_actions": [],
            "failure_reason": "Birthdate does not match our records.",
            "customer_data": {},
        }

    account_status = customer.get("account_status", "ACTIVE")
    verification_level = customer.get("verification_level", "VERIFIED_NEW")

    if account_status != "ACTIVE":
        verification_level = "ESCALATED"

    result = {
        "customer_id": customer["id"],
        "verification_level": verification_level,
        "allowed_actions": get_allowed_actions(verification_level),
        "failure_reason": None,
        "customer_data": {
            "name": customer.get("name"),
            "policy_ids": customer.get("policy_ids", []),
            "vehicle_ids": customer.get("vehicle_ids", []),
        },
    }
    logger.info("VERIFIED | customer=%s level=%s", customer["id"], verification_level)
    return result


# ---------------------------------------------------------------------------
# Risk routing (deterministic, no LLM)
# ---------------------------------------------------------------------------

def decide_route(*, verification_level: str, intent: str, allowed_actions: list[str]) -> str:
    """Decide whether a request must escalate to a human or may proceed.

    Returns "escalate" or "proceed".
    """
    if (
        verification_level in ("UNVERIFIED", "ESCALATED")
        or intent == "unknown"
        or (intent != "unknown" and intent not in allowed_actions)
    ):
        route = "escalate"
    else:
        route = "proceed"

    logger.info("ROUTING | %s → %s", verification_level, route)
    return route
