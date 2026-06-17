from google.adk.agents import LlmAgent
from insurance_bot.core.config import LLM_MODEL
from insurance_bot.core.gcs_client import gcs
from insurance_bot.core.output_guard import output_guardrail_callback
from insurance_bot.core.conversation import with_history
from insurance_bot.tools.claim_tools import CLAIM_TOOLS


def get_open_claims(customer_id: str) -> list[dict]:
    """List existing claims for the verified customer."""
    return gcs.get_claims(customer_id)


def get_claim_status(claim_id: str, customer_id: str) -> dict:
    """Get the status of a specific existing claim. Enforces customer ownership."""
    for claim in gcs.get_claims(customer_id):
        if claim.get("claim_id") == claim_id:
            return claim
    return {"error": "Claim not found or not owned by this customer."}


_CLAIMS_INSTRUCTION = """You are an insurance CLAIMS specialist. Customers filing claims are often
stressed — be calm, empathetic, and clear.

You can do two things:

A) CHECK an existing claim — use `get_open_claims` / `get_claim_status`.

B) FILE a new claim — this is a guided intake of FIVE questions. Follow this exactly:
   1. Call `list_claim_questions` so you know the five required questions.
   2. Ask the questions ONE AT A TIME (incident type → date → location → description →
      policy number). After EACH answer, call `record_claim_answer(field, value)` to save it.
   3. Use `get_claim_progress` if you need to see what's still missing.
   4. When all five are collected (progress shows complete=true), ask the customer WHEN they'd
      like to be called back. Then call `finalize_claim_with_callback(preferred_callback_time)` —
      a claims agent will call them back at that time. Give them their claim reference.
   5. If you CANNOT collect all five (the customer can't or won't answer, or is stuck after a
      couple of tries), call `route_claim_to_human(reason)` to hand off to a human specialist,
      and give them the reference.

Rules:
- One question per turn. Never bundle the five questions together.
- Save every answer with `record_claim_answer` as you go — don't keep it only in your head.
- Only call `finalize_claim_with_callback` once the intake is complete; if it returns an error
  about missing fields, ask for those instead.
- The customer's ID is already in context — the tools read it for you.
- Be honest about timelines; never promise a specific claim outcome.
"""


claims_agent = LlmAgent(
    name="claims_agent",
    model=LLM_MODEL,
    after_model_callback=output_guardrail_callback,
    instruction=with_history(_CLAIMS_INSTRUCTION),
    tools=[get_open_claims, get_claim_status, *CLAIM_TOOLS],
)
