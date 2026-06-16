"""
Live / bidirectional (voice) root agent for `adk web`.

`adk web` drives voice through `runner.run_live()`, which requires the
`root_agent` to be a Live-capable `LlmAgent` running a native-audio model.
The deterministic `Workflow` in workflow.py cannot serve `run_live`, so this
module provides an alternative `root_agent` that keeps the SAME guardrails by
exposing them as tools the model is instructed to call in order:

    1. verify_customer_identity(...)   -> identity check (GCS)
    2. authorize_action(intent)        -> allowed-actions + risk gate
    3. (delegate to specialist sub_agent)
    4. log_audit_event(...)            -> audit trail

Verification state is persisted in the ADK session via `tool_context.state`
so it survives across conversational turns within a live session.

Enable by running:  ADK_BIDI=1 adk web insurance_bot
"""

from __future__ import annotations

import logging

from google.adk.agents import LlmAgent
from google.adk.models import BaseLlm, Gemini
from google.adk.tools import ToolContext

from insurance_bot.agents.policy_agent import policy_agent
from insurance_bot.agents.claims_agent import claims_agent
from insurance_bot.agents.offers_agent import offers_agent
from insurance_bot.agents.emergency_agent import emergency_agent
from insurance_bot.core.config import BIDI_MODEL, BIDI_TEXT_MODEL
from insurance_bot.core import guardrails
from insurance_bot.core import audit_logger as audit

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Guardrail tools (state-backed)
# ---------------------------------------------------------------------------

def verify_customer_identity(
    tool_context: ToolContext,
    phone: str = "",
    policy_number: str = "",
    license_plate: str = "",
    birthdate: str = "",
) -> dict:
    """Verify the caller's identity from any identifier they provide.

    Call this BEFORE doing anything customer-specific. Provide whatever the
    caller gave you (a phone number, policy number, or license plate; birthdate
    is an optional secondary check). Returns the verification level and which
    actions are now permitted.

    Args:
        phone: The caller's phone number, if given.
        policy_number: The caller's policy number, if given.
        license_plate: The caller's vehicle license plate, if given.
        birthdate: The caller's date of birth (YYYY-MM-DD), optional.
    """
    result = guardrails.verify_customer(
        phone=phone or None,
        policy_number=policy_number or None,
        license_plate=license_plate or None,
        birthdate=birthdate or None,
    )

    # Persist for later tool calls / turns in this live session.
    tool_context.state["verification"] = result

    audit.log_action(
        session_id=str(tool_context.state.get("session_id", "live")),
        customer_id=result.get("customer_id"),
        action="IDENTITY_VERIFICATION",
        intent="verification",
        risk_level="MEDIUM",
        status=result.get("verification_level", "UNVERIFIED"),
        verification_level=result.get("verification_level"),
        extra={"failure_reason": result.get("failure_reason")},
    )

    return {
        "verification_level": result.get("verification_level"),
        "allowed_actions": result.get("allowed_actions", []),
        "customer_name": result.get("customer_data", {}).get("name"),
        "failure_reason": result.get("failure_reason"),
    }


def authorize_action(tool_context: ToolContext, intent: str, risk_level: str = "LOW") -> dict:
    """Check whether the verified caller is allowed to perform an action.

    Call this AFTER verifying identity and BEFORE delegating to a specialist or
    performing any sensitive operation. It applies the same risk gate as the
    text workflow.

    Args:
        intent: One of "policy_question", "claim", "offer", "emergency".
        risk_level: "LOW", "MEDIUM", or "HIGH" for the specific operation.

    Returns a decision:
        {"decision": "proceed"|"escalate",
         "requires_confirmation": bool,
         "requires_human": bool,
         "reason": str}
    """
    verification = tool_context.state.get("verification", {})
    verification_level = verification.get("verification_level", "UNVERIFIED")
    allowed_actions = verification.get("allowed_actions", [])

    route = guardrails.decide_route(
        verification_level=verification_level,
        intent=intent,
        allowed_actions=allowed_actions,
    )

    risk = (risk_level or "LOW").upper()
    decision = {
        "decision": route,
        "requires_confirmation": route == "proceed" and risk == "MEDIUM",
        "requires_human": route == "escalate" or risk == "HIGH",
        "verification_level": verification_level,
        "reason": (
            verification.get("failure_reason")
            or ("Not permitted at current verification level."
                if route == "escalate" else "Authorized.")
        ),
    }

    audit.log_action(
        session_id=str(tool_context.state.get("session_id", "live")),
        customer_id=verification.get("customer_id"),
        action="ACTION_AUTHORIZATION",
        intent=intent,
        risk_level=risk,
        status=route.upper(),
        verification_level=verification_level,
    )
    return decision


def log_audit_event(
    tool_context: ToolContext,
    action: str,
    intent: str,
    status: str,
    risk_level: str = "LOW",
) -> dict:
    """Record an audit-trail entry for an action taken during the call.

    Call this after completing or rejecting a sensitive action so the
    interaction is fully auditable, mirroring the text workflow.
    """
    verification = tool_context.state.get("verification", {})
    audit.log_action(
        session_id=str(tool_context.state.get("session_id", "live")),
        customer_id=verification.get("customer_id"),
        action=action,
        intent=intent,
        risk_level=(risk_level or "LOW").upper(),
        status=status,
        verification_level=verification.get("verification_level"),
    )
    return {"logged": True}


# ---------------------------------------------------------------------------
# Live root agent
# ---------------------------------------------------------------------------

_LIVE_INSTRUCTION = """\
You are the voice assistant for an insurance company. You speak with customers
over a live audio call. Be warm, concise, and natural — you are talking, not
writing. Keep responses short and conversational.

You MUST follow this guardrailed procedure on every call:

1. GREET the caller and ask how you can help.

2. VERIFY IDENTITY before doing anything customer-specific. Ask for a phone
   number, policy number, or license plate, then call `verify_customer_identity`.
   - If the result is UNVERIFIED, politely ask for another identifier.
   - If it is ESCALATED, tell the caller their account needs human review and a
     specialist will follow up. Do NOT proceed with the request.

3. AUTHORIZE before acting. Once you know the customer's intent, classify it as
   one of: policy_question, claim, offer, emergency. Estimate the risk:
   - LOW: general questions, viewing offers, checking a claim status
   - MEDIUM: reading sensitive documents (policy copy, invoice details)
   - HIGH: filing a new claim, modifying data, dispatching emergency help
   Then call `authorize_action(intent, risk_level)`.
   - If decision is "escalate" or requires_human is true, explain you can't
     proceed and a specialist will help. Do not delegate.
   - If requires_confirmation is true (MEDIUM risk), VERBALLY confirm with the
     caller ("Shall I go ahead?") and wait for a clear "yes" before proceeding.

4. DELEGATE to the right specialist sub-agent once authorized:
   - policy_question -> policy_agent
   - claim           -> claims_agent
   - offer           -> offers_agent
   - emergency       -> emergency_agent
   For an EMERGENCY, prioritize safety immediately; if anyone is injured tell
   them to call 112 first. You may transfer to emergency_agent right away.

5. After any sensitive action, call `log_audit_event` to record what happened.

NEVER reveal or act on data for a customer who has not been verified. Never
discuss other customers. If unsure, ask a clarifying question.
"""

# ---------------------------------------------------------------------------
# Dual-model agent
# ---------------------------------------------------------------------------
# `adk web` drives ONE root_agent two ways:
#   - text box -> run_async -> generateContent  (uses canonical_model)
#   - mic/voice -> run_live  -> Live WebSocket   (uses canonical_live_model)
#
# Native-audio Live models (BIDI_MODEL) reject generateContent (HTTP 400), and
# plain text models reject the Live API. ADK 2.x resolves the two paths through
# two separate properties, so we override each to return the right model:
#   canonical_model      -> BIDI_TEXT_MODEL (e.g. gemini-2.5-flash)  for text
#   canonical_live_model -> BIDI_MODEL (native-audio)               for voice
#
# The `model` field is left empty so these overrides fully control resolution.

class DualModelLiveAgent(LlmAgent):
    """LlmAgent that uses a text model for run_async and a Live model for run_live."""

    text_model: str = BIDI_TEXT_MODEL
    live_model: str = BIDI_MODEL

    @property
    def canonical_model(self) -> BaseLlm:  # consumed by the text/run_async path
        return Gemini(model=self.text_model)

    @property
    def canonical_live_model(self) -> BaseLlm:  # consumed by the live/run_live path
        return Gemini(model=self.live_model)


# The shared specialist agents hardcode model=LLM_MODEL (a text model), whose
# Live resolution would reject the Live API. For the live tree we clone them
# with an EMPTY model so each inherits the root's dual resolution: text ->
# BIDI_TEXT_MODEL, live -> BIDI_MODEL (ADK walks ancestors when model is "").
def _as_live_specialist(agent: LlmAgent) -> LlmAgent:
    return agent.model_copy(update={"model": "", "parent_agent": None})


_live_specialists = [
    _as_live_specialist(policy_agent),
    _as_live_specialist(claims_agent),
    _as_live_specialist(offers_agent),
    _as_live_specialist(emergency_agent),
]


root_agent = DualModelLiveAgent(
    name="insurance_bot_live",
    description=(
        "Live (voice) + text insurance assistant with the same "
        "identity-verification, risk-authorization, and audit guardrails as "
        "the text workflow. Uses a native-audio Live model for voice and a "
        "standard Gemini model for the text box."
    ),
    instruction=_LIVE_INSTRUCTION,
    tools=[verify_customer_identity, authorize_action, log_audit_event],
    sub_agents=_live_specialists,
)
