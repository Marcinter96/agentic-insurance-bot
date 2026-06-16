"""
Insurance Bot — Guardrailed Multi-Agent Workflow (ADK 2.2.0)

The conversational front-end (input guardrail + intent classification + identity
verification) is ONE node: `intake`. This is deliberate.

ADK's workflow resume reliably re-runs a SINGLE interrupting node, but it does
NOT cope when one node completes and a *different*, later node is the first to
pause in the same invocation (the "freeze after answering the identifier").
Keeping all the conversational turns inside one rerun_on_resume node means there
is exactly one pause source, so every resume just re-runs `intake` and rebuilds
its state — robust.

Flow:
  START → intake   (input guardrail → classify → identify; pauses for the caller)
        → risk_router          (deterministic, no LLM)
            ├─[escalate]→ escalation_handler
            └─[proceed]→ specialist_router → {policy|claims|offers|emergency}_agent
                                           → action_confirmation
  intake ─[blocked]→ guardrail_blocked (safe refusal)
"""

from __future__ import annotations

import asyncio
import uuid
import logging

from google.adk import Context
from google.adk.workflow import Workflow, node, Edge, START
from google.adk.workflow._function_node import RequestInput

from insurance_bot.agents.policy_agent import policy_agent
from insurance_bot.agents.claims_agent import claims_agent
from insurance_bot.agents.offers_agent import offers_agent
from insurance_bot.agents.emergency_agent import emergency_agent
from insurance_bot.agents import classifier_agent, identifier_agent
from insurance_bot.agents.classifier_agent import build_classification
from insurance_bot.core import audit_logger as audit
from insurance_bot.core import guardrails
from insurance_bot.core import safety

logger = logging.getLogger(__name__)

MAX_CLASSIFIER_TURNS = 4   # at most 4 questions to find the intent
MAX_IDENTIFIER_TURNS = 4   # at most 4 questions to identify the caller
MAX_LOOKUPS = 2            # phone+birthdate, then policy/plate


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------

def _content_text(value) -> str:
    """Best-effort extraction of plain text from a node_input."""
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    parts = getattr(getattr(value, "content", value), "parts", None)
    if parts:
        return "".join(p.text for p in parts if getattr(p, "text", None))
    return str(value)


def replay_transcript(initial: str, questions: dict[int, str], replies: dict[int, str]):
    """Rebuild the conversation transcript deterministically.

    A turn is "closed" only when both its question and reply are present.
    Returns (transcript_lines, open_turn).
    """
    transcript = [f"Caller: {initial}"] if initial else []
    turn = 0
    while turn in questions and turn in replies:
        transcript.append(f"Assistant: {questions[turn]}")
        transcript.append(f"Caller: {replies[turn]}")
        turn += 1
    return transcript, turn


def _collect_turns(ctx: Context, prefix: str):
    """Rebuild stored questions + caller replies for a conversation phase.

    ``ctx.resume_inputs`` only carries the MOST RECENTLY answered interrupt (ADK
    replaces it each resume), so each reply is persisted into ``ctx.state`` the
    moment it appears and the transcript is rebuilt from state.
    """
    questions: dict[int, str] = {}
    replies: dict[int, str] = {}
    t = 0
    while True:
        q = ctx.state.get(f"{prefix}_q_{t}")
        r = ctx.state.get(f"{prefix}_r_{t}")
        if r is None:
            r = ctx.resume_inputs.get(f"{prefix}_q_{t}")
            if r is not None:
                ctx.state[f"{prefix}_r_{t}"] = r
        if q is None and r is None:
            break
        if q is not None:
            questions[t] = q
        if r is not None:
            replies[t] = r
        t += 1
    return questions, replies


def _blocked(ctx: Context, text: str, stage: str) -> bool:
    """Input guardrail: screen `text`. On a block, stash the refusal, set
    ctx.route='blocked', and return True."""
    verdict = safety.screen_input(text)
    logger.info("INPUT GUARDRAIL | stage=%s verdict=%s category=%s",
                stage, verdict["verdict"], verdict.get("category"))
    if verdict["verdict"] != "block":
        return False
    audit.log_action(
        session_id=ctx.state.get("session_id", "unknown"), customer_id=None,
        action="INPUT_GUARDRAIL_BLOCK", intent="unknown", risk_level="HIGH",
        status="BLOCKED", extra={"category": verdict.get("category"),
                                 "reason": verdict.get("reason"), "stage": stage},
    )
    logger.warning("INPUT BLOCKED | stage=%s category=%s", stage, verdict.get("category"))
    ctx.state["_refusal"] = safety.refusal_message(verdict.get("category", "injection"))
    ctx.route = "blocked"
    return True


def _seed_for_identifier(ctx: Context) -> str:
    """Seed line: identifiers the classifier already captured (so we don't re-ask)."""
    ids = ctx.state.get("classification", {}).get("customer_identifiers", {})
    have = {k: v for k, v in ids.items() if v}
    if have:
        return "Caller already provided: " + ", ".join(f"{k}={v}" for k, v in have.items())
    return "Caller needs to be identified."


def _finalize_verification(ctx: Context, verification: dict) -> None:
    """Write verification to state and audit-log the resolved request."""
    ctx.state["verification"] = verification
    classification = ctx.state.get("classification", {})
    audit.log_action(
        session_id=ctx.state.get("session_id", "unknown"),
        customer_id=verification.get("customer_id"),
        action="REQUEST_RECEIVED",
        intent=classification.get("intent", "unknown"),
        risk_level=classification.get("risk_level", "LOW"),
        status="INITIATED",
    )
    logger.info("VERIFICATION | level=%s customer=%s",
                verification.get("verification_level"), verification.get("customer_id"))


# ---------------------------------------------------------------------------
# INTAKE — the single conversational node (guardrail → classify → identify)
# ---------------------------------------------------------------------------

@node(name="intake", rerun_on_resume=True)
async def intake(ctx: Context, node_input):
    """Own the whole caller conversation. One pause source → robust resume."""
    ctx.state.setdefault("session_id", ctx.run_id or str(uuid.uuid4()))
    initial = _content_text(node_input) or ctx.state.get("first_message", "")
    ctx.state.setdefault("first_message", initial)

    # ===== PHASE 1: classify intent =====
    if not ctx.state.get("classification"):
        clf_q, clf_r = _collect_turns(ctx, "clf")

        # Input guardrail: opening message on the first turn, latest reply after.
        if not clf_q and not clf_r:
            if _blocked(ctx, initial, "opening"):
                return
        elif clf_r and _blocked(ctx, clf_r[max(clf_r)], "clf_reply"):
            return

        transcript, turn = replay_transcript(initial, clf_q, clf_r)
        decision = await asyncio.to_thread(classifier_agent.decide, "\n".join(transcript)) or {}

        if turn >= MAX_CLASSIFIER_TURNS or decision.get("action") == "done":
            ctx.state["classification"] = build_classification(decision)
            c = ctx.state["classification"]
            logger.info("CLASSIFICATION | intent=%s risk=%s", c["intent"], c["risk_level"])
            # fall through to PHASE 2 in the same invocation
        else:
            q = decision.get("question") or "Could you tell me a little more about what you need?"
            ctx.state[f"clf_q_{turn}"] = q
            logger.info("CLASSIFIER | pausing to ask (turn=%s): %s", turn, q[:60])
            yield RequestInput(interrupt_id=f"clf_q_{turn}", message=q)
            return

    # ===== PHASE 2: identify the caller =====
    if not ctx.state.get("verification"):
        idf_q, idf_r = _collect_turns(ctx, "idf")
        logger.info("IDENTIFIER | collected q=%s r=%s resume=%s",
                    sorted(idf_q), sorted(idf_r), sorted(ctx.resume_inputs.keys()))

        if idf_r and _blocked(ctx, idf_r[max(idf_r)], "idf_reply"):
            return

        transcript, turn = replay_transcript(_seed_for_identifier(ctx), idf_q, idf_r)
        attempts = ctx.state.get("idf_attempts", 0)
        notes: list[str] = []

        while True:
            decision = await asyncio.to_thread(
                identifier_agent.decide, "\n".join(transcript + notes)
            ) or {}
            action = decision.get("action")
            logger.info(
                "IDENTIFIER | turn=%s attempts=%s action=%s phone=%s dob=%s policy=%s plate=%s",
                turn, attempts, action,
                bool(decision.get("phone")), bool(decision.get("birthdate")),
                bool(decision.get("policy_number")), bool(decision.get("license_plate")),
            )

            if action == "lookup" and attempts < MAX_LOOKUPS:
                result = await asyncio.to_thread(
                    guardrails.verify_customer,
                    phone=decision.get("phone") or None,
                    birthdate=decision.get("birthdate") or None,
                    policy_number=decision.get("policy_number") or None,
                    license_plate=decision.get("license_plate") or None,
                )
                attempts += 1
                ctx.state["idf_attempts"] = attempts
                level = result.get("verification_level")
                if level in ("VERIFIED_RETURNING", "VERIFIED_NEW", "ESCALATED"):
                    _finalize_verification(ctx, result)
                    break
                notes.append("System: No record matched those details.")
                if attempts >= MAX_LOOKUPS:
                    _finalize_verification(ctx, result)
                    break
                continue

            if action == "give_up" or turn >= MAX_IDENTIFIER_TURNS:
                _finalize_verification(ctx, {
                    "customer_id": None, "verification_level": "UNVERIFIED",
                    "allowed_actions": [], "failure_reason": "Could not verify the caller.",
                    "customer_data": {},
                })
                break

            q = decision.get("question") or "Could you share your phone number and date of birth?"
            ctx.state[f"idf_q_{turn}"] = q
            logger.info("IDENTIFIER | pausing to ask (turn=%s): %s", turn, q[:60])
            yield RequestInput(interrupt_id=f"idf_q_{turn}", message=q)
            return

    # Both phases done → continue to deterministic routing.
    ctx.route = "continue"


@node(name="guardrail_blocked")
def guardrail_blocked(ctx: Context, node_input=None):
    """Terminal node: emit the fixed safe refusal for a blocked input."""
    ctx.output = ctx.state.get("_refusal") or safety.REFUSAL_INJECTION


# ---------------------------------------------------------------------------
# Risk Router (deterministic, no LLM)
# ---------------------------------------------------------------------------

@node(name="risk_router", rerun_on_resume=True)
def risk_router(ctx: Context) -> None:
    """Decide: escalate to human or proceed to specialist."""
    verification = ctx.state.get("verification", {})
    classification = ctx.state.get("classification", {})
    ctx.route = guardrails.decide_route(
        verification_level=verification.get("verification_level", "UNVERIFIED"),
        intent=classification.get("intent", "unknown"),
        allowed_actions=verification.get("allowed_actions", []),
    )


# ---------------------------------------------------------------------------
# Escalation Handler (HITL)
# ---------------------------------------------------------------------------

@node(name="escalation_handler", rerun_on_resume=True)
def escalation_handler(ctx: Context):
    """Handle unverified, escalated, or unknown requests — HITL when needed."""
    verification = ctx.state.get("verification", {})
    classification = ctx.state.get("classification", {})
    session_id = ctx.state.get("session_id", "unknown")

    verification_level = verification.get("verification_level", "UNVERIFIED")
    intent = classification.get("intent", "unknown")
    failure_reason = verification.get("failure_reason")

    if verification_level == "UNVERIFIED":
        message = (
            "I wasn't able to identify you in our system. "
            "Please contact our support team so a specialist can assist you."
        )
    elif verification_level == "ESCALATED":
        interrupt_id = f"hitl_escalation_{session_id}"
        resume = ctx.resume_inputs.get(interrupt_id)
        if resume is None:
            audit.log_action(
                session_id=session_id, customer_id=verification.get("customer_id"),
                action="HITL_ESCALATION", intent=intent,
                risk_level=classification.get("risk_level", "HIGH"),
                status="PENDING_HUMAN_REVIEW", extra={"failure_reason": failure_reason},
            )
            yield RequestInput(
                interrupt_id=interrupt_id,
                message=(
                    "⚠️ This account requires human review before proceeding. "
                    "A specialist will contact you shortly. Reference: " + session_id
                ),
            )
            return
        message = f"A specialist has reviewed your request. Response: {resume}"
    elif intent == "unknown":
        message = (
            "I'm not sure I understood your request. Could you please clarify?\n"
            "I can help with policy questions, claims, quotes, or emergencies."
        )
    else:
        message = (
            "I'm sorry, but you don't have permission to perform this action "
            "with your current account level. Please contact our support team."
        )

    audit.log_action(
        session_id=session_id, customer_id=verification.get("customer_id"),
        action="ESCALATION_HANDLED", intent=intent,
        risk_level=classification.get("risk_level", "LOW"), status="ESCALATED",
    )
    ctx.output = message


# ---------------------------------------------------------------------------
# Specialist Router (deterministic, no LLM)
# ---------------------------------------------------------------------------

@node(name="specialist_router", rerun_on_resume=True)
def specialist_router(ctx: Context) -> None:
    """Route to the correct specialist agent based on classified intent."""
    intent = ctx.state.get("classification", {}).get("intent", "unknown")
    customer_id = ctx.state.get("verification", {}).get("customer_id")
    ctx.state["active_customer_id"] = customer_id
    ctx.route = intent
    logger.info("SPECIALIST_ROUTE | %s → %s", customer_id, intent)


# ---------------------------------------------------------------------------
# Action Confirmation (risk gate before executing)
# ---------------------------------------------------------------------------

@node(name="action_confirmation", rerun_on_resume=True)
def action_confirmation(ctx: Context, node_input=None):
    """Risk-gated confirmation before finalising the specialist's response."""
    classification = ctx.state.get("classification", {})
    session_id = ctx.state.get("session_id", "unknown")
    risk_level = classification.get("risk_level", "LOW")
    customer_id = ctx.state.get("active_customer_id")
    intent = classification.get("intent", "unknown")

    # Emergency is time-critical: never block an SOS behind a human-approval pause.
    if risk_level == "LOW" or intent == "emergency":
        audit.log_action(
            session_id=session_id, customer_id=customer_id,
            action="ACTION_AUTO_APPROVED", intent=intent, risk_level=risk_level,
            status="SUCCESS",
            extra={"emergency_bypass": True} if intent == "emergency" else None,
        )
        return

    interrupt_id = f"confirm_{session_id}"
    resume = ctx.resume_inputs.get(interrupt_id)
    if resume is None:
        if risk_level == "MEDIUM":
            yield RequestInput(
                interrupt_id=interrupt_id,
                message="Please confirm you want to proceed. Reply 'yes' to confirm or 'no' to cancel.",
            )
        else:
            yield RequestInput(
                interrupt_id=interrupt_id,
                message=(
                    "⚠️ This action requires human approval (HIGH risk). "
                    f"A specialist will review and complete your request. Reference: {session_id}"
                ),
            )
        return

    confirmed = str(resume).lower().strip() in ("yes", "y", "confirm", "ok", "proceed")
    status = "SUCCESS" if confirmed else "REJECTED"
    audit.log_action(
        session_id=session_id, customer_id=customer_id,
        action="ACTION_CONFIRMATION", intent=intent, risk_level=risk_level, status=status,
    )
    if not confirmed:
        ctx.output = "Action cancelled. Is there anything else I can help you with?"


# ---------------------------------------------------------------------------
# Build the Workflow
# ---------------------------------------------------------------------------

root_agent = Workflow(
    name="insurance_bot_workflow",
    description=(
        "Guardrailed multi-agent insurance bot. A single intake node screens input, "
        "classifies intent, and verifies identity; then deterministic routing sends the "
        "request to the right specialist with audit logging."
    ),
    edges=[
        (START, intake),
        Edge(from_node=intake, to_node=guardrail_blocked, route="blocked"),
        Edge(from_node=intake, to_node=risk_router, route="continue"),
        Edge(from_node=risk_router, to_node=escalation_handler, route="escalate"),
        Edge(from_node=risk_router, to_node=specialist_router, route="proceed"),
        Edge(from_node=specialist_router, to_node=policy_agent, route="policy_question"),
        Edge(from_node=specialist_router, to_node=claims_agent, route="claim"),
        Edge(from_node=specialist_router, to_node=offers_agent, route="offer"),
        Edge(from_node=specialist_router, to_node=emergency_agent, route="emergency"),
        (policy_agent, action_confirmation),
        (claims_agent, action_confirmation),
        (offers_agent, action_confirmation),
        (emergency_agent, action_confirmation),
    ],
)
