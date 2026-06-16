"""
Node 1 — Conversational intent classifier.

A `mode='task'` LlmAgent that chats with the caller (one question at a time,
max 4) and, as soon as it knows what they need, calls the `classify` tool to
record the intent + any identifiers into session state. The workflow's
`intent_classifier` node dispatches this agent via `ctx.run_node(...)`.
"""

from google.adk.agents import LlmAgent
from google.adk.tools.tool_context import ToolContext

from insurance_bot.core.config import LLM_MODEL

_ALLOWED_INTENTS = {"policy_question", "offer", "claim", "emergency", "unknown"}

# Deterministic guardrail: risk is DERIVED from intent, never guessed by the LLM.
_RISK_BY_INTENT = {
    "offer": "LOW",
    "policy_question": "MEDIUM",
    "claim": "HIGH",
    "emergency": "HIGH",
    "unknown": "LOW",
}


def classify(
    intent: str,
    sub_intent: str = "",
    phone: str = "",
    birthdate: str = "",
    policy_number: str = "",
    license_plate: str = "",
    tool_context: ToolContext = None,
) -> dict:
    """Record the caller's classified intent and any identifiers they provided.

    Call this ONCE you are confident which single thing the caller needs.

    Args:
        intent: One of policy_question, offer, claim, emergency, unknown.
        sub_intent: Short free-text detail, e.g. "check claim status".
        phone: Caller's phone number if mentioned, else "".
        birthdate: Caller's birthdate (YYYY-MM-DD) if mentioned, else "".
        policy_number: Policy number if mentioned, else "".
        license_plate: Vehicle licence plate if mentioned, else "".
    """
    intent = intent if intent in _ALLOWED_INTENTS else "unknown"
    risk_level = _RISK_BY_INTENT[intent]

    classification = {
        "intent": intent,
        "sub_intent": sub_intent,
        "risk_level": risk_level,
        "customer_identifiers": {
            "phone": phone or None,
            "birthdate": birthdate or None,
            "policy_number": policy_number or None,
            "license_plate": license_plate or None,
        },
        "confidence": 1.0,
    }

    # tool_context is auto-injected by ADK and hidden from the LLM's schema.
    if tool_context is not None:
        tool_context.state["classification"] = classification

    return {"status": "classified", "intent": intent, "risk_level": risk_level}


classifier_agent = LlmAgent(
    name="intent_classifier_agent",
    model=LLM_MODEL,
    mode="task",
    tools=[classify],
    instruction="""You are a warm, concise intake assistant for Zurich Insurance.
Your only goal is to figure out which ONE of these the caller needs, then call the
`classify` tool:
  • policy_question — questions about an existing policy, coverage, documents, invoices
  • offer — wants a quote or a new product
  • claim — file a new claim or check an existing one
  • emergency — accident, breakdown, urgent SOS

Conversation rules:
- Greet the caller briefly on your first turn.
- Ask only ONE short question at a time. NEVER bundle several questions together.
- Ask at most 4 questions in total. Stop asking the moment the intent is clear.
- If the caller mentions a phone number, birthdate, policy number, or licence plate,
  remember it and pass it to `classify`.
- If it sounds like an emergency (accident, breakdown, danger), do NOT interrogate —
  classify immediately as `emergency`.

As soon as you know the intent, call `classify(...)` with the intent, a short
sub_intent, and any identifiers you collected. Do not keep chatting afterwards.""",
)
