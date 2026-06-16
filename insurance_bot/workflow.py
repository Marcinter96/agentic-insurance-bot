"""
Insurance Bot — Guardrailed Multi-Agent Workflow (ADK 2.2.0)

Division of labour (the model we agreed on):
  • Workflow  = the director. Owns the order of steps AND "stop and wait for
                the caller" (via yield RequestInput). Nothing else pauses.
  • Agent     = a one-shot brain. Given the conversation so far it returns ONE
                structured decision (ask / done / lookup). It never loops or waits.
  • Function  = plain deterministic code: the database lookup (guardrails.
                verify_customer) and the routing rulebook (guardrails.decide_route).

Flow:
  START → intent_classifier   (loop: ask ↔ wait, until intent is known)
        → identification_node  (loop: ask ↔ wait ↔ db lookup, until verified or give up)
        → risk_router          (deterministic, no LLM)
            ├─[escalate]→ escalation_handler
            └─[proceed]→ specialist_router → {policy|claims|offers|emergency}_agent
                                           → action_confirmation
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
from insurance_bot.agents.classifier_agent import classifier_brain, build_classification
from insurance_bot.agents.identifier_agent import identifier_brain
from insurance_bot.core import audit_logger as audit
from insurance_bot.core import guardrails
from insurance_bot.core import safety

logger = logging.getLogger(__name__)

MAX_CLASSIFIER_TURNS = 4   # at most 4 questions to find the intent
MAX_IDENTIFIER_TURNS = 4   # at most 4 questions to identify the caller
MAX_LOOKUPS = 2            # phone+birthdate, then policy/plate


# ---------------------------------------------------------------------------
# Pure helpers (no ADK, no network — unit-testable)
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

    `questions[t]` is the bot's question on turn t (stored in state before each
    pause); `replies[t]` is the caller's answer (from ctx.resume_inputs). A turn
    is "closed" only when both are present. Returns (transcript_lines, open_turn).
    """
    transcript = [f"Caller: {initial}"] if initial else []
    turn = 0
    while turn in questions and turn in replies:
        transcript.append(f"Assistant: {questions[turn]}")
        transcript.append(f"Caller: {replies[turn]}")
        turn += 1
    return transcript, turn


def _collect_turns(ctx: Context, prefix: str):
    """Rebuild stored questions + caller replies for a loop node.

    IMPORTANT: ``ctx.resume_inputs`` only carries the MOST RECENTLY answered
    interrupt — ADK replaces it on each resume (it does not accumulate). So a
    reply must be persisted into ``ctx.state`` the moment it appears, otherwise
    a 2nd+ turn loses the earlier replies and the conversation restarts.
    """
    questions: dict[int, str] = {}
    replies: dict[int, str] = {}
    t = 0
    while True:
        q = ctx.state.get(f"{prefix}_q_{t}")
        # Prefer the persisted reply; otherwise capture the just-arrived one and persist it.
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


def _reply_blocked(ctx: Context, replies: dict[int, str]) -> bool:
    """Screen the most recent caller reply (mid-conversation input guardrail).

    On a block, stashes the refusal and sets ctx.route='blocked'. Returns True
    if blocked. Earlier replies were already screened on their own turn, so only
    the latest one needs checking each invocation.
    """
    if not replies:
        return False
    latest = replies[max(replies)]
    verdict = safety.screen_input(latest)
    if verdict["verdict"] != "block":
        return False
    logger.warning("INPUT BLOCKED (reply) | category=%s", verdict.get("category"))
    audit.log_action(
        session_id=ctx.state.get("session_id", "unknown"), customer_id=None,
        action="INPUT_GUARDRAIL_BLOCK", intent="unknown", risk_level="HIGH",
        status="BLOCKED", extra={"category": verdict.get("category"),
                                 "reason": verdict.get("reason"), "stage": "reply"},
    )
    ctx.state["_refusal"] = safety.refusal_message(verdict.get("category", "injection"))
    ctx.route = "blocked"
    return True


# ---------------------------------------------------------------------------
# NODE 0 — Input guardrail (deterministic-first; brain only for the gray area)
# ---------------------------------------------------------------------------
#
# This is a NODE, not a plugin: for a Workflow (BaseNode) root, ADK does not
# honor a plugin's before_run early-exit, so the only reliable way to BLOCK an
# incoming message is to route to a refusal inside the graph.

@node(name="input_guardrail", rerun_on_resume=True)
def input_guardrail(ctx: Context, node_input):
    """Screen the opening message. Block → refusal; allow → continue.

    rerun_on_resume=True: this is a router (sets ctx.route) with conditional
    outgoing edges. On resume the workflow replays from START to reach the
    paused node, so this must re-emit its route or the conditional edge won't
    fire and the resume stalls here.
    """
    ctx.state.setdefault("session_id", ctx.run_id or str(uuid.uuid4()))
    text = _content_text(node_input)
    # Remember the opening message so the classifier still sees it (this node
    # returns nothing, so it isn't echoed back to the caller).
    ctx.state.setdefault("first_message", text)

    verdict = safety.screen_input(text)
    logger.info("INPUT GUARDRAIL | verdict=%s category=%s", verdict["verdict"], verdict.get("category"))

    if verdict["verdict"] == "block":
        audit.log_action(
            session_id=ctx.state.get("session_id", "unknown"), customer_id=None,
            action="INPUT_GUARDRAIL_BLOCK", intent="unknown", risk_level="HIGH",
            status="BLOCKED", extra={"category": verdict.get("category"),
                                     "reason": verdict.get("reason")},
        )
        logger.warning("INPUT BLOCKED | category=%s", verdict.get("category"))
        ctx.state["_refusal"] = safety.refusal_message(verdict.get("category", "injection"))
        ctx.route = "blocked"
        return

    ctx.route = "ok"


@node(name="guardrail_blocked")
def guardrail_blocked(ctx: Context, node_input=None):
    """Terminal node: emit the fixed safe refusal for a blocked input."""
    ctx.output = ctx.state.get("_refusal") or safety.REFUSAL_INJECTION


# ---------------------------------------------------------------------------
# NODE 1 — Intent Classifier (workflow owns the loop; brain answers one turn)
# ---------------------------------------------------------------------------

@node(name="intent_classifier", rerun_on_resume=True)
async def intent_classifier(ctx: Context, node_input):
    """Ask the caller (one question at a time, ≤4) until the intent is clear."""
    ctx.state.setdefault("session_id", ctx.run_id or str(uuid.uuid4()))

    # Each user message replays the workflow from START. Once the intent is
    # settled, skip the brain entirely — no redundant LLM call on later turns.
    if ctx.state.get("classification"):
        ctx.route = "continue"
        return

    # The opening message arrives via input_guardrail (which returns nothing),
    # so fall back to the stashed first_message.
    initial = _content_text(node_input) or ctx.state.get("first_message", "")
    questions, replies = _collect_turns(ctx, "clf")

    # Input guardrail for mid-conversation replies (the opening message was
    # already screened by the input_guardrail node).
    if _reply_blocked(ctx, replies):
        return

    transcript, turn = replay_transcript(initial, questions, replies)

    # One-shot brain call for THIS turn (single_turn agent → returns a dict, never waits).
    decision = await ctx.run_node(
        classifier_brain, "\n".join(transcript), run_id=f"clf_brain_t{turn}"
    ) or {}

    if turn >= MAX_CLASSIFIER_TURNS or decision.get("action") == "done":
        ctx.state["classification"] = build_classification(decision)
        c = ctx.state["classification"]
        logger.info("CLASSIFICATION | intent=%s risk=%s", c["intent"], c["risk_level"])
        ctx.route = "continue"
        return

    # action == 'ask': store the question, then PAUSE for the caller's reply.
    question = decision.get("question") or "Could you tell me a little more about what you need?"
    ctx.state[f"clf_q_{turn}"] = question
    yield RequestInput(interrupt_id=f"clf_q_{turn}", message=question)


# ---------------------------------------------------------------------------
# NODE 2 — Identity Verification (loop: ask ↔ wait ↔ db lookup)
# ---------------------------------------------------------------------------

def _seed_for_identifier(ctx: Context) -> str:
    """Seed line: tell the brain which identifiers the classifier already captured."""
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
    logger.info(
        "VERIFICATION | level=%s customer=%s",
        verification.get("verification_level"),
        verification.get("customer_id"),
    )
    ctx.route = "continue"


@node(name="identification_node", rerun_on_resume=True)
async def identification_node(ctx: Context, node_input=None):
    """Identify the caller: ask for identifiers, look them up, retry, or escalate."""
    # Already resolved on a previous turn — skip the brain (replay short-circuit).
    if ctx.state.get("verification"):
        ctx.route = "continue"
        return

    questions, replies = _collect_turns(ctx, "idf")
    logger.info("IDENTIFIER | collected questions=%s replies=%s resume_keys=%s",
                sorted(questions), sorted(replies), sorted(ctx.resume_inputs.keys()))

    # Input guardrail for mid-conversation replies.
    if _reply_blocked(ctx, replies):
        return

    transcript, turn = replay_transcript(_seed_for_identifier(ctx), questions, replies)

    attempts = ctx.state.get("idf_attempts", 0)
    notes: list[str] = []

    while True:
        brain_input = "\n".join(transcript + notes)
        decision = await ctx.run_node(
            identifier_brain, brain_input, run_id=f"idf_brain_t{turn}_a{attempts}"
        ) or {}
        action = decision.get("action")
        logger.info(
            "IDENTIFIER | turn=%s attempts=%s action=%s phone=%s dob=%s policy=%s plate=%s",
            turn, attempts, action,
            bool(decision.get("phone")), bool(decision.get("birthdate")),
            bool(decision.get("policy_number")), bool(decision.get("license_plate")),
        )

        if action == "lookup" and attempts < MAX_LOOKUPS:
            # verify_customer does synchronous GCS reads; run it off the event
            # loop so the blocking network I/O doesn't stall the workflow.
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
                return

            # UNVERIFIED: lookup failed. Note it and let the brain decide again
            # (ask for an alternative identifier, or give up) — no caller pause yet.
            notes.append("System: No record matched those details.")
            if attempts >= MAX_LOOKUPS:
                _finalize_verification(ctx, result)
                return
            continue

        if action == "give_up" or turn >= MAX_IDENTIFIER_TURNS:
            _finalize_verification(ctx, {
                "customer_id": None,
                "verification_level": "UNVERIFIED",
                "allowed_actions": [],
                "failure_reason": "Could not verify the caller.",
                "customer_data": {},
            })
            return

        # action == 'ask': store the question, then PAUSE for the caller's reply.
        question = decision.get("question") or "Could you share your phone number and date of birth?"
        ctx.state[f"idf_q_{turn}"] = question
        logger.info("IDENTIFIER | pausing to ask (turn=%s): %s", turn, question[:60])
        yield RequestInput(interrupt_id=f"idf_q_{turn}", message=question)
        return


# ---------------------------------------------------------------------------
# NODE 3 — Risk Router (deterministic, no LLM)
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
# NODE 4a — Escalation Handler (HITL)
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
                session_id=session_id,
                customer_id=verification.get("customer_id"),
                action="HITL_ESCALATION",
                intent=intent,
                risk_level=classification.get("risk_level", "HIGH"),
                status="PENDING_HUMAN_REVIEW",
                extra={"failure_reason": failure_reason},
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
        session_id=session_id,
        customer_id=verification.get("customer_id"),
        action="ESCALATION_HANDLED",
        intent=intent,
        risk_level=classification.get("risk_level", "LOW"),
        status="ESCALATED",
    )
    ctx.output = message


# ---------------------------------------------------------------------------
# NODE 4b — Specialist Router (deterministic, no LLM)
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
# NODE 5 — Action Confirmation (risk gate before executing)
# ---------------------------------------------------------------------------

@node(name="action_confirmation", rerun_on_resume=True)
def action_confirmation(ctx: Context, node_input=None):
    """Risk-gated confirmation before finalising the specialist's response."""
    classification = ctx.state.get("classification", {})
    session_id = ctx.state.get("session_id", "unknown")
    risk_level = classification.get("risk_level", "LOW")
    customer_id = ctx.state.get("active_customer_id")
    intent = classification.get("intent", "unknown")

    # Emergency is time-critical: never block an SOS behind a human-approval
    # pause. Auto-proceed (still audited). LOW risk also auto-proceeds.
    if risk_level == "LOW" or intent == "emergency":
        audit.log_action(
            session_id=session_id, customer_id=customer_id,
            action="ACTION_AUTO_APPROVED", intent=intent,
            risk_level=risk_level,
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
        action="ACTION_CONFIRMATION", intent=intent,
        risk_level=risk_level, status=status,
    )
    if not confirmed:
        ctx.output = "Action cancelled. Is there anything else I can help you with?"


# ---------------------------------------------------------------------------
# Build the Workflow
# ---------------------------------------------------------------------------

root_agent = Workflow(
    name="insurance_bot_workflow",
    description=(
        "Guardrailed multi-agent insurance bot. Classifies intent (Agent 1), "
        "verifies identity (Agent 2), then routes to the right specialist with audit logging."
    ),
    edges=[
        # Stage 0: input guardrail — block or continue
        (START, input_guardrail),
        Edge(from_node=input_guardrail, to_node=guardrail_blocked, route="blocked"),
        Edge(from_node=input_guardrail, to_node=intent_classifier, route="ok"),

        # Mid-conversation replies are screened inside the loop nodes too.
        Edge(from_node=intent_classifier, to_node=guardrail_blocked, route="blocked"),
        Edge(from_node=intent_classifier, to_node=identification_node, route="continue"),
        Edge(from_node=identification_node, to_node=guardrail_blocked, route="blocked"),
        Edge(from_node=identification_node, to_node=risk_router, route="continue"),
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
