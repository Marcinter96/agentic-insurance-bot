"""
Node 1 brain — intent classifier (ONE-SHOT, structured output).

This is NOT a multi-turn agent. It is a `single_turn` LlmAgent that the
workflow calls once per conversation turn. Given the conversation so far it
returns a structured decision: either "ask the caller ONE more question" or
"I'm done, here is the intent".

The *workflow node* (insurance_bot/workflow.py::intent_classifier) owns the
loop and the pause-for-the-caller. The brain never waits and never loops —
that is exactly what keeps it from getting into the broken task-mode states
(finish_task / `classifier_1` function-response pairing crashes).
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from google.adk.agents import LlmAgent

from insurance_bot.core.config import BRAIN_MODEL, fast_brain_config
from insurance_bot.core.llm import structured_decision

_CLASSIFIER_SYSTEM = """You are the intake brain for Zurich Insurance. You are given the
conversation so far between the Assistant and the Caller. Decide the SINGLE best next step
and return it in the required structured format. You do NOT carry on a conversation yourself —
you return exactly one decision.

Goal: identify which ONE of these the caller needs:
  • policy_question — questions about an existing policy, coverage, documents, invoices
  • offer — wants a quote or a new product
  • claim — file a new claim or check an existing one
  • emergency — accident, breakdown, urgent SOS

Decide in TWO stages:
  STAGE 1 — MAIN INTENT: work out which ONE of the fixed list above the caller needs.
    If you cannot tell yet, return action='ask' with one question to narrow it down.
  STAGE 2 — SUB-INTENT: once the main intent is known, check whether the SPECIFIC need within
    it is clear (e.g. for policy_question: "check an invoice", "coverage details", "update
    address"; for claim: "file new" vs "check status"; for offer: which product).
      • If the specific need is still vague, return action='ask' with ONE question to pin it down.
      • If the specific need is already clear, return action='done' with BOTH `intent` and a
        short `sub_intent` filled in.

Rules:
- Return action='done' only once BOTH the main intent AND a concrete sub_intent are known
  (and include any phone / birthdate / policy_number / license_plate the caller has mentioned).
- Never bundle multiple questions into one — ONE question per turn.
- If it sounds like an emergency (accident, breakdown, danger), immediately return
  action='done' with intent='emergency'. Do not interrogate.
- You may ask at most 4 questions in total; if the intent still cannot be determined by then,
  return action='done' with intent='unknown'.

Tone — sound human, not robotic, and ADAPT to how much the caller has shared:
- If the caller gave a lot of detail, acknowledge it briefly ("Thanks for the detail —")
  and ask only the one thing still missing.
- If the caller said very little (e.g. "hi", "I need help"), be warm and welcoming and
  gently invite them to say more, rather than firing a blunt question.
- If the caller sounds worried or upset, lead with a short reassurance ("I can help with
  that —") before the question.
- Vary your wording; never repeat a question the caller has effectively already answered.
- Keep it to ONE sentence and ONE question mark."""

ALLOWED_INTENTS = {"policy_question", "offer", "claim", "emergency", "unknown"}

# Deterministic guardrail: risk is DERIVED from intent, never guessed by the LLM.
_RISK_BY_INTENT = {
    "offer": "LOW",
    "policy_question": "MEDIUM",
    "claim": "HIGH",
    "emergency": "HIGH",
    "unknown": "LOW",
}


class ClassifierDecision(BaseModel):
    """The brain's structured answer for a single turn."""

    action: Literal["ask", "done"] = Field(
        description="'ask' to ask the caller one more question, 'done' once the intent is clear."
    )
    question: str = Field(
        default="",
        description="The ONE short question to ask next. Required when action='ask'.",
    )
    intent: str = Field(
        default="",
        description="One of policy_question, offer, claim, emergency, unknown. Required when action='done'.",
    )
    sub_intent: str = Field(default="", description="Short free-text detail, e.g. 'check claim status'.")
    phone: str = Field(default="", description="Caller's phone if mentioned, else ''.")
    birthdate: str = Field(default="", description="Caller's birthdate YYYY-MM-DD if mentioned, else ''.")
    policy_number: str = Field(default="", description="Policy number if mentioned, else ''.")
    license_plate: str = Field(default="", description="Licence plate if mentioned, else ''.")


def build_classification(decision: dict) -> dict:
    """Turn a brain decision dict into the canonical ctx.state['classification']."""
    intent = decision.get("intent") or "unknown"
    if intent not in ALLOWED_INTENTS:
        intent = "unknown"
    return {
        "intent": intent,
        "sub_intent": decision.get("sub_intent", ""),
        "risk_level": _RISK_BY_INTENT[intent],
        "customer_identifiers": {
            "phone": decision.get("phone") or None,
            "birthdate": decision.get("birthdate") or None,
            "policy_number": decision.get("policy_number") or None,
            "license_plate": decision.get("license_plate") or None,
        },
        "confidence": 1.0,
    }


classifier_brain = LlmAgent(
    name="classifier_brain",
    model=BRAIN_MODEL,
    mode="single_turn",
    output_schema=ClassifierDecision,
    generate_content_config=fast_brain_config(),
    instruction=_CLASSIFIER_SYSTEM,
)


def decide(transcript: str) -> dict:
    """One-shot classification decision via a direct structured LLM call.

    Returns {"action": "ask"|"done", ...}. Called by the workflow node (wrapped
    in asyncio.to_thread). Not dispatched via ctx.run_node — see core/llm.py.
    """
    return structured_decision(_CLASSIFIER_SYSTEM, transcript, ClassifierDecision)
