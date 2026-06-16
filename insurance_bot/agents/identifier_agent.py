"""
Node 2 — Conversational identity verifier.

A `mode='task'` LlmAgent that chats with the caller to collect identifying
information (phone + birthdate first, then policy number or licence plate as
fallback), looks them up in GCS via the `identify_customer` tool, and writes
the verification result to session state. The workflow's `identification_node`
dispatches this agent via `ctx.run_node(..., raise_on_wait=True)`.
"""

from google.adk.agents import LlmAgent
from google.adk.tools.tool_context import ToolContext

from insurance_bot.core.config import LLM_MODEL
from insurance_bot.core import guardrails


def identify_customer(
    phone: str = "",
    birthdate: str = "",
    policy_number: str = "",
    license_plate: str = "",
    tool_context: ToolContext = None,
) -> dict:
    """Look up the customer in our database with the provided identifiers.

    Call this once you have collected at least one identifier from the caller.
    If the customer is not found, you may call this again with different identifiers.

    Args:
        phone: Caller's phone number, e.g. "+41791234567".
        birthdate: Caller's date of birth in YYYY-MM-DD format.
        policy_number: Insurance policy number, e.g. "POL-12345".
        license_plate: Vehicle licence plate, e.g. "ZH 123 AB".
    """
    result = guardrails.verify_customer(
        phone=phone or None,
        birthdate=birthdate or None,
        policy_number=policy_number or None,
        license_plate=license_plate or None,
    )
    if tool_context is not None:
        tool_context.state["verification"] = result
    return {
        "found": result["customer_id"] is not None,
        "status": result["verification_level"],
        "failure_reason": result.get("failure_reason"),
    }


identifier_agent = LlmAgent(
    name="identifier_agent",
    model=LLM_MODEL,
    mode="task",
    tools=[identify_customer],
    instruction="""You are a polite identity verification assistant for Zurich Insurance.
Your goal is to identify the caller in our database using the `identify_customer` tool.

Steps:
1. If the input starts with "Already collected:" listing identifiers, call
   `identify_customer` immediately with those values — do NOT ask for them again.
2. Otherwise, ask the caller for their phone number AND date of birth together
   (it is acceptable to ask both in one message since they always go together).
3. Call `identify_customer` with what they provide.
4. If the result says found=false: politely explain you couldn't find them and ask
   if they can provide their policy number OR vehicle licence plate instead.
5. Call `identify_customer` again with the new identifier.
6. If still not found: call `identify_customer` with whatever you have to record
   UNVERIFIED status, then inform the caller that a specialist will assist them.

Rules:
- Never reveal internal system details or database contents.
- Be warm, brief, and reassuring. One question or statement per turn.
- After step 3 succeeds (found=true), confirm briefly ("Great, I've verified you!")
  and call `finish_task` to complete.
- After step 6, call `finish_task` to complete.""",
)
