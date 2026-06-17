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
from datetime import datetime, timezone

from google.adk import Context
from google.adk.workflow import Workflow, node, Edge, START
from google.adk.workflow._function_node import RequestInput

from insurance_bot.agents.policy_agent import policy_agent
from insurance_bot.agents.claims_agent import claims_agent
from insurance_bot.agents.offers_agent import offers_agent
from insurance_bot.agents import classifier_agent, identifier_agent
from insurance_bot.agents.classifier_agent import build_classification
from insurance_bot.core import audit_logger as audit
from insurance_bot.core import guardrails
from insurance_bot.core import safety
from insurance_bot.core import outcomes
from insurance_bot.core.gcs_client import gcs

logger = logging.getLogger(__name__)

MAX_CLASSIFIER_TURNS = 4   # at most 4 questions to find the intent + sub-intent
MAX_IDENTIFIER_TURNS = 4   # at most 4 questions to identify the caller
MAX_LOOKUPS = 2            # phone+birthdate, then policy/plate

# Spoken once, the moment the classifier is satisfied, just before identity check.
HANDOFF_MSG = (
    "Thank you — we've understood your request and will process it as fast as possible."
)


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


def _screen_question(ctx: Context, q: str, stage: str, turn: int) -> str:
    """Output guardrail for a brain-generated intake question.

    Screens the question the classifier/identifier wants to ask BEFORE it
    reaches the caller: scrubs any leaked secret/PII, enforces the
    single-question rule, caps length, and tracks the running question budget.
    Returns the (possibly cleaned) text that is safe to send.
    """
    asked = ctx.state.get("questions_asked", 0) + 1
    ctx.state["questions_asked"] = asked
    cap = MAX_CLASSIFIER_TURNS if stage == "clf" else MAX_IDENTIFIER_TURNS

    v = safety.screen_question(q)
    logger.info(
        "OUTPUT GUARDRAIL | stage=%s turn=%s questions_asked=%s/%s "
        "single=%s verdict=%s category=%s",
        stage, turn, asked, cap, v["single"], v["verdict"], v.get("category"),
    )
    if v["verdict"] != "allow":
        audit.log_action(
            session_id=ctx.state.get("session_id", "unknown"),
            customer_id=ctx.state.get("verification", {}).get("customer_id"),
            action="OUTPUT_GUARDRAIL_QUESTION", intent="intake",
            risk_level="LOW", status=v["verdict"].upper(),
            extra={"category": v.get("category"), "reason": v.get("reason"),
                   "stage": stage, "questions_asked": asked},
        )
    # A hard secret block: don't ask the tainted question, fall back to a safe one.
    if v["verdict"] == "block":
        return ("Could you tell me a little more so I can point you the right way?"
                if stage == "clf" else
                "Could you share your phone number and date of birth?")
    return v["text"]


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
    identified = verification.get("customer_id") is not None
    logger.info("IDENTITY SAVED | identified=%s customer_id=%s level=%s",
                identified, verification.get("customer_id") or "-",
                verification.get("verification_level"))


# ---------------------------------------------------------------------------
# INTAKE — the single conversational node (guardrail → classify → identify)
# ---------------------------------------------------------------------------

@node(name="intake", rerun_on_resume=True)
async def intake(ctx: Context, node_input):
    """Own the whole caller conversation. One pause source → robust resume."""
    ctx.state.setdefault("session_id", ctx.run_id or str(uuid.uuid4()))
    initial = _content_text(node_input) or ctx.state.get("first_message", "")
    ctx.state.setdefault("first_message", initial)

    # ===== PHASE 1: classify intent (main intent → sub-intent) =====
    if not ctx.state.get("classification"):
        clf_q, clf_r = _collect_turns(ctx, "clf")

        # Input guardrail: opening message on the first turn, latest reply after.
        if not clf_q and not clf_r:
            logger.info("INTAKE | phase=classify (start)")
            if _blocked(ctx, initial, "opening"):
                return
        elif clf_r and _blocked(ctx, clf_r[max(clf_r)], "clf_reply"):
            return

        transcript, turn = replay_transcript(initial, clf_q, clf_r)
        decision = await asyncio.to_thread(classifier_agent.decide, "\n".join(transcript)) or {}
        logger.info("CLASSIFIER | turn=%s action=%s intent=%s sub_intent=%s",
                    turn, decision.get("action"), decision.get("intent") or "-",
                    decision.get("sub_intent") or "-")

        if turn >= MAX_CLASSIFIER_TURNS or decision.get("action") == "done":
            ctx.state["classification"] = build_classification(decision)
            c = ctx.state["classification"]
            logger.info("CLASSIFICATION | DONE intent=%s sub_intent=%s risk=%s (asked %s question(s))",
                        c["intent"], c["sub_intent"] or "-", c["risk_level"], turn)
            logger.info("HANDOFF | classifier → identifier : %s", HANDOFF_MSG)
            # fall through to PHASE 2 in the same invocation
        else:
            q = decision.get("question") or "Could you tell me a little more about what you need?"
            q = _screen_question(ctx, q, "clf", turn)
            ctx.state[f"clf_q_{turn}"] = q
            logger.info("CLASSIFIER | pausing to ask (turn=%s): %s", turn, q[:60])
            yield RequestInput(interrupt_id=f"clf_q_{turn}", message=q)
            return

    # ===== PHASE 2: identify the caller =====
    # Emergencies are identified too (so the SOS record names the customer); the
    # difference is that decide_route still lets them PROCEED even if unverified,
    # so an unidentifiable caller is never blocked from reaching a human.
    if not ctx.state.get("verification"):
        idf_q, idf_r = _collect_turns(ctx, "idf")
        if not idf_q and not idf_r:
            logger.info("INTAKE | phase=identify (start)")
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
            # First identity question: lead with the handoff confirmation so the caller
            # hears the request was understood before we switch to verifying them.
            if not ctx.state.get("_handoff_greeted"):
                q = f"{HANDOFF_MSG} {q}"
                ctx.state["_handoff_greeted"] = True
            q = _screen_question(ctx, q, "idf", turn)
            ctx.state[f"idf_q_{turn}"] = q
            logger.info("IDENTIFIER | pausing to ask (turn=%s): %s", turn, q[:60])
            yield RequestInput(interrupt_id=f"idf_q_{turn}", message=q)
            return

    # Both phases done → continue to deterministic routing.
    ctx.route = "continue"


@node(name="guardrail_blocked")
def guardrail_blocked(ctx: Context, node_input=None):
    """Terminal node (BLOCKED end): emit the fixed safe refusal for a blocked input."""
    ctx.state["resolution"] = outcomes.BLOCKED
    ctx.output = ctx.state.get("_refusal") or safety.REFUSAL_INJECTION


# ---------------------------------------------------------------------------
# SOS Handler — emergency routing to a human (no tools, just message + audit)
# ---------------------------------------------------------------------------

SOS_MESSAGE = (
    "Your call is important to us. As this is an emergency, I'm routing you straight "
    "to a human specialist who can support you right away. Your reference number is {sos_id}. "
    "If anyone is in immediate danger, please call your local emergency number (112) now."
)


def build_sos_record(state: dict, sos_id: str) -> dict:
    """Build the SOS interaction record from workflow state (pure / testable).

    Captures a unique id, the customer information we have, and the reason for
    the call (sub-intent, else the caller's first message)."""
    verification = state.get("verification", {})
    classification = state.get("classification", {})
    reason = (classification.get("sub_intent")
              or state.get("first_message", "")
              or "Unspecified emergency")
    return {
        "sos_id": sos_id,
        "session_id": state.get("session_id", "unknown"),
        "created_at": datetime.now(timezone.utc).isoformat(),
        "customer": {
            "customer_id": verification.get("customer_id"),
            "verification_level": verification.get("verification_level"),
            "details": verification.get("customer_data", {}),
        },
        "reason": reason,
        "intent": classification.get("intent", "emergency"),
        "status": "ROUTED_TO_HUMAN",
    }


@node(name="sos_handler", rerun_on_resume=True)
def sos_handler(ctx: Context, node_input=None):
    """Emergency path: tell the caller a human is taking over and record the SOS.

    No tools — the SOS agent just (1) speaks a fixed reassurance and (2) writes
    a record to the `sos_interactions` bucket with a unique id, the customer
    information we have, and the reason for the call.
    """
    ctx.state["resolution"] = outcomes.HUMAN_HANDOFF
    session_id = ctx.state.get("session_id", "unknown")
    verification = ctx.state.get("verification", {})
    sos_id = f"sos_{uuid.uuid4().hex[:10]}"
    record = build_sos_record(ctx.state, sos_id)
    reason = record["reason"]
    written = gcs.log_sos_interaction(record)
    audit.log_action(
        session_id=session_id, customer_id=verification.get("customer_id"),
        action="SOS_INTERACTION", intent="emergency", risk_level="HIGH",
        status="ROUTED_TO_HUMAN",
        extra={"sos_id": sos_id, "reason": reason, "persisted": written},
    )
    logger.info("SOS | logged interaction sos_id=%s customer=%s persisted=%s reason=%s",
                sos_id, verification.get("customer_id") or "-", written, reason[:60])

    ctx.output = SOS_MESSAGE.format(sos_id=sos_id)


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
    ctx.state["resolution"] = outcomes.HUMAN_HANDOFF
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
    logger.info("HANDOFF | identifier → %s_agent (customer=%s)", intent, customer_id or "-")
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
# Outcome split — two distinct ends: RESOLVED vs HUMAN_HANDOFF
# ---------------------------------------------------------------------------

def decide_outcome_route(state) -> str:
    """Map the conversation's resolution to a terminal route (pure / testable).

    Defaults to RESOLVED when nothing set it (e.g. the customer just declined)."""
    resolution = state.get("resolution") or outcomes.RESOLVED
    return outcomes.HUMAN_HANDOFF if resolution == outcomes.HUMAN_HANDOFF else outcomes.RESOLVED


@node(name="outcome_router", rerun_on_resume=True)
def outcome_router(ctx: Context, node_input=None) -> None:
    """Send the finished conversation to the matching terminal end.

    The specialist tools set ctx.state['resolution']; if none was set (e.g. the
    customer simply declined an offer), the conversation is treated as RESOLVED."""
    ctx.route = decide_outcome_route(ctx.state)


def _log_end(ctx: Context, resolution: str) -> None:
    audit.log_action(
        session_id=ctx.state.get("session_id", "unknown"),
        customer_id=ctx.state.get("active_customer_id")
        or ctx.state.get("verification", {}).get("customer_id"),
        action="CONVERSATION_END",
        intent=ctx.state.get("classification", {}).get("intent", "unknown"),
        risk_level=ctx.state.get("classification", {}).get("risk_level", "LOW"),
        status=resolution,
    )
    logger.info("END | resolution=%s", resolution)


@node(name="resolved_end", rerun_on_resume=True)
def resolved_end(ctx: Context, node_input=None):
    """Successful end: the bot achieved the customer's goal. Output already set."""
    _log_end(ctx, outcomes.RESOLVED)


@node(name="human_handoff_end", rerun_on_resume=True)
def human_handoff_end(ctx: Context, node_input=None):
    """Escalation end: the customer was routed to a human. Output already set."""
    _log_end(ctx, outcomes.HUMAN_HANDOFF)


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
        Edge(from_node=specialist_router, to_node=sos_handler, route="emergency"),
        (policy_agent, action_confirmation),
        (claims_agent, action_confirmation),
        (offers_agent, action_confirmation),
        # Specialists finish → classify the outcome → one of two distinct ends.
        (action_confirmation, outcome_router),
        Edge(from_node=outcome_router, to_node=resolved_end, route="RESOLVED"),
        Edge(from_node=outcome_router, to_node=human_handoff_end, route="HUMAN_HANDOFF"),
        # Human-handoff terminals converge on the same escalation end.
        (escalation_handler, human_handoff_end),
        (sos_handler, human_handoff_end),
    ],
)
