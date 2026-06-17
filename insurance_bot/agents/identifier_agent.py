"""
Node 2 brain — identity verifier (ONE-SHOT, structured output).

Like the classifier brain, this is a `single_turn` LlmAgent the workflow calls
once per turn. Given the conversation so far (and the outcome of any lookups it
has already tried) it returns a structured decision:

  • action='ask'    → ask the caller ONE question (e.g. phone + birthdate)
  • action='lookup' → it has identifiers; the workflow should query the database
  • action='give_up'→ identifiers exhausted, hand off to a human

The deterministic database lookup itself is a plain function
(insurance_bot.core.guardrails.verify_customer) called by the workflow node —
not by the LLM. The brain only decides WHAT to ask and WHEN to look up.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from google.adk.agents import LlmAgent

from insurance_bot.core.config import BRAIN_MODEL, fast_brain_config
from insurance_bot.core.llm import structured_decision

_IDENTIFIER_SYSTEM = """You are the identity-verification brain for Zurich Insurance. You are given
the conversation so far, plus notes about any database lookups already attempted. Decide the
SINGLE best next step and return it in the required structured format. You do NOT carry on a
conversation yourself — you return exactly one decision.

Goal: collect enough to identify the caller in our database.

Step-by-step flow (you may ask at most 3-4 questions in total):
1. FIRST, if you have NO identifiers yet, return action='ask' for the caller's phone number
   AND date of birth together.
2. If the caller gave only ONE of phone / birthdate, thank them and return action='ask' for
   the OTHER one only. Do not re-ask for the one you already have.
3. If the caller gave NEITHER phone nor birthdate, look at what they said:
     • If they indicate they do NOT want to be identified / refuse → return action='give_up'.
     • If they instead offered a policy number or licence plate → return action='lookup' with it.
     • Otherwise → return action='ask' once more for phone + birthdate.
4. As soon as you have phone+birthdate (or a policy number / licence plate) that has NOT yet
   been looked up, return action='lookup' with those values.
5. If a lookup on phone+birthdate FAILED, return action='ask' offering to try with a policy
   number OR vehicle licence plate instead.
6. If they then provide a policy/plate → action='lookup'. If they decline, can't, or that
   also fails → return action='give_up'.
- Be warm and brief. One question per turn. Never reveal internal database details.

Tone — sound human, not robotic, and ADAPT to the situation:
- First ask: be welcoming and explain briefly WHY you need it ("Just so I can pull up your
  details securely, could you share…").
- If the caller already volunteered some identifiers, acknowledge them and ask only for
  what's still missing — never re-ask for something they've already given.
- If a lookup just FAILED, reassure first so they don't feel accused ("No problem — that one
  didn't match, it happens. Could you try…"). Do not blame the caller.
- If they sound frustrated, be extra patient and offer the alternative identifier proactively.
- Keep it to ONE sentence and ONE question mark."""


class IdentifierDecision(BaseModel):
    """The brain's structured answer for a single turn."""

    action: Literal["ask", "lookup", "give_up"] = Field(
        description=(
            "'ask' to ask the caller for identifiers, 'lookup' once you have identifiers "
            "to check, 'give_up' when no identifiers can be obtained."
        )
    )
    question: str = Field(
        default="",
        description="The question to ask the caller. Required when action='ask'.",
    )
    phone: str = Field(default="", description="Phone number to look up, else ''.")
    birthdate: str = Field(default="", description="Birthdate YYYY-MM-DD to look up, else ''.")
    policy_number: str = Field(default="", description="Policy number to look up, else ''.")
    license_plate: str = Field(default="", description="Licence plate to look up, else ''.")


identifier_brain = LlmAgent(
    name="identifier_brain",
    model=BRAIN_MODEL,
    mode="single_turn",
    output_schema=IdentifierDecision,
    generate_content_config=fast_brain_config(),
    instruction=_IDENTIFIER_SYSTEM,
)


def decide(transcript: str) -> dict:
    """One-shot verification decision via a direct structured LLM call.

    Returns {"action": "ask"|"lookup"|"give_up", ...}. Called by the workflow
    node (wrapped in asyncio.to_thread), not via ctx.run_node — see core/llm.py.
    """
    return structured_decision(_IDENTIFIER_SYSTEM, transcript, IdentifierDecision)
