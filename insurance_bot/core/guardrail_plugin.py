"""
GuardrailPlugin — app-wide input & output guardrails for ADK.

Registered on the `App` in insurance_bot/agent.py, so it applies to BOTH the
text Workflow and the voice (live) agent, at the runner level.

  • INPUT  → before_run_callback: screens the incoming user message. On a block
             it returns a Content, which HALTS the run and replies with a fixed,
             safe refusal (Gemini never sees the malicious text).
  • OUTPUT → after_model_callback: screens a SPECIALIST agent's response before
             it is finalized, redacting secrets / payment-card numbers. A
             workflow node can't do this — by the time a node runs, the text has
             already streamed to the UI; after_model_callback rewrites it first.

The brains (classifier/identifier) and the safety brain are NOT screened on
output — only the customer-facing specialist agents are.
"""

from __future__ import annotations

import logging

from google.genai import types
from google.adk.plugins.base_plugin import BasePlugin

from insurance_bot.core import safety
from insurance_bot.core import audit_logger as audit

logger = logging.getLogger(__name__)

# Only these agents' outputs are customer-facing and get screened.
SPECIALIST_AGENTS = {"policy_agent", "claims_agent", "offers_agent", "emergency_agent"}


def _text_of(content: types.Content | None) -> str:
    if not content or not content.parts:
        return ""
    return "".join(p.text for p in content.parts if getattr(p, "text", None))


class GuardrailPlugin(BasePlugin):
    def __init__(self, name: str = "guardrail"):
        super().__init__(name=name)

    # ---- INPUT guardrail -------------------------------------------------
    async def before_run_callback(self, *, invocation_context):
        text = _text_of(getattr(invocation_context, "user_content", None))
        if not text:
            return None

        verdict = safety.screen_input(text)
        if verdict["verdict"] != "block":
            return None

        session_id = getattr(invocation_context, "session", None)
        session_id = getattr(session_id, "id", "unknown")
        audit.log_action(
            session_id=session_id, customer_id=None,
            action="INPUT_GUARDRAIL_BLOCK", intent="unknown", risk_level="HIGH",
            status="BLOCKED", extra={"category": verdict.get("category"),
                                     "reason": verdict.get("reason")},
        )
        logger.warning("INPUT BLOCKED | category=%s", verdict.get("category"))

        # Returning a Content halts the run and replies with this message.
        return types.Content(
            role="model",
            parts=[types.Part(text=safety.refusal_message(verdict.get("category", "injection")))],
        )

    # ---- OUTPUT guardrail ------------------------------------------------
    async def after_model_callback(self, *, callback_context, llm_response):
        if callback_context.agent_name not in SPECIALIST_AGENTS:
            return None  # only screen customer-facing specialists

        text = _text_of(getattr(llm_response, "content", None))
        if not text:
            return None

        verdict = safety.screen_output(text)
        if verdict["verdict"] == "allow":
            return None

        audit.log_action(
            session_id="unknown", customer_id=None,
            action="OUTPUT_GUARDRAIL_" + verdict["verdict"].upper(),
            intent="unknown", risk_level="HIGH", status="REDACTED",
            extra={"category": verdict.get("category"), "agent": callback_context.agent_name},
        )
        logger.warning("OUTPUT %s | agent=%s category=%s",
                       verdict["verdict"], callback_context.agent_name, verdict.get("category"))

        # Replace the model's response with the cleaned/redacted text.
        llm_response.content = types.Content(
            role="model", parts=[types.Part(text=verdict["text"])]
        )
        return llm_response
