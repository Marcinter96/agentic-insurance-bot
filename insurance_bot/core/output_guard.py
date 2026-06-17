"""
Output guardrail — an agent-level `after_model_callback`.

Attached to each specialist LlmAgent. ADK invokes an agent's own
after_model_callback from the LLM flow (base_llm_flow.py), so this fires
reliably even though the root is a Workflow (a plugin's after_model is not
honored on the node path). It redacts secrets / payment-card numbers in the
specialist's response BEFORE the response is finalized.
"""

from __future__ import annotations

import logging

from google.genai import types

from insurance_bot.core import safety
from insurance_bot.core import audit_logger as audit
from insurance_bot.core import conversation

logger = logging.getLogger(__name__)


def _text_of(content) -> str:
    if not content or not getattr(content, "parts", None):
        return ""
    return "".join(p.text for p in content.parts if getattr(p, "text", None))


def output_guardrail_callback(callback_context, llm_response):
    """Screen + redact a specialist's model response. Returns a modified
    LlmResponse to replace it, or None to leave it unchanged."""
    text = _text_of(getattr(llm_response, "content", None))
    if not text:
        return None

    try:
        verdict = safety.screen_output(text)
    except Exception as e:  # a guardrail bug must never break the response
        logger.error("OUTPUT guardrail error (failing open): %s", e)
        return None

    agent = getattr(callback_context, "agent_name", "?")
    logger.info("OUTPUT GUARDRAIL | agent=%s verdict=%s category=%s",
                agent, verdict["verdict"], verdict.get("category"))

    # Record the assistant turn in the shared transcript so later turns (and
    # other specialists) have the context of what was already said.
    final_text = verdict["text"] if verdict["verdict"] != "allow" else text
    try:
        conversation.record_assistant(callback_context.state, final_text)
    except Exception:
        pass

    if verdict["verdict"] == "allow":
        return None

    try:
        audit.log_action(
            session_id="unknown", customer_id=None,
            action="OUTPUT_GUARDRAIL_" + verdict["verdict"].upper(),
            intent="unknown", risk_level="HIGH", status="REDACTED",
            extra={"category": verdict.get("category"), "agent": agent},
        )
    except Exception:
        pass
    logger.warning("OUTPUT %s | agent=%s category=%s",
                   verdict["verdict"], agent, verdict.get("category"))

    llm_response.content = types.Content(
        role="model", parts=[types.Part(text=verdict["text"])]
    )
    return llm_response
