"""
Insurance Bot — Guardrailed Multi-Agent Workflow (ADK 2.2.0)

Flow:
  START → intent_classifier       (task LlmAgent — conversational, ≤4 questions)
        → identification_node     (task LlmAgent — collects ID, GCS lookup)
        → risk_router             (deterministic, no LLM)
            ├─[escalate]→ escalation_handler  (HITL if needed)
            └─[proceed]→ specialist_router
                           ├─[policy_question]→ policy_agent
                           ├─[claim]         → claims_agent
                           ├─[offer]         → offers_agent
                           └─[emergency]     → emergency_agent
                                      → action_confirmation
"""

import uuid
import logging

from google.adk import Context
from google.adk.workflow import Workflow, node, Edge, START
from google.adk.workflow._function_node import RequestInput

from insurance_bot.agents.policy_agent import policy_agent
from insurance_bot.agents.claims_agent import claims_agent
from insurance_bot.agents.offers_agent import offers_agent
from insurance_bot.agents.emergency_agent import emergency_agent
from insurance_bot.agents.classifier_agent import classifier_agent
from insurance_bot.agents.identifier_agent import identifier_agent
from insurance_bot.core import audit_logger as audit
from insurance_bot.core import guardrails

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# NODE 1 — Intent Classifier (conversational task agent)
# ---------------------------------------------------------------------------

@node(name="intent_classifier", rerun_on_resume=True)
async def intent_classifier(ctx: Context, node_input: str):
    """Run the conversational classifier agent until it calls classify() + finish_task.

    Uses raise_on_wait=True so the framework pauses the workflow whenever the
    task agent produces a question but hasn't finished yet (no finish_task call).
    On resume the parent node reruns, dispatches the agent again — the agent sees
    the full conversation history (including the user's reply) and continues.
    """
    ctx.state.setdefault("session_id", ctx.run_id or str(uuid.uuid4()))
    # NodeInterruptedError (BaseException) propagates to NodeRunner automatically;
    # no try/except needed here.
    await ctx.run_node(classifier_agent, node_input, raise_on_wait=True)

    # Ensure classification is present (defensive fallback)
    if not ctx.state.get("classification"):
        logger.warning("Classifier finished without writing classification — defaulting to unknown")
        ctx.state["classification"] = {
            "intent": "unknown",
            "sub_intent": "",
            "risk_level": "LOW",
            "customer_identifiers": {
                "phone": None, "birthdate": None,
                "policy_number": None, "license_plate": None,
            },
            "confidence": 0.0,
        }

    c = ctx.state["classification"]
    logger.info("CLASSIFICATION | intent=%s risk=%s", c.get("intent"), c.get("risk_level"))


# ---------------------------------------------------------------------------
# NODE 2 — Identity Verification (conversational task agent)
# ---------------------------------------------------------------------------

@node(name="identification_node", rerun_on_resume=True)
async def identification_node(ctx: Context, node_input: str | None = None):
    """Run the conversational identifier agent until it calls identify_customer() + finish_task.

    Passes any identifiers already collected by the classifier so the agent
    does not re-ask for information the caller already provided.
    """
    # Build prefill context from what the classifier already captured
    ids = ctx.state.get("classification", {}).get("customer_identifiers", {})
    if ids and any(ids.values()):
        parts = [f"{k}={v}" for k, v in ids.items() if v]
        prefill = "Already collected: " + ", ".join(parts) + ". Try these first."
    else:
        prefill = ""

    await ctx.run_node(identifier_agent, prefill or "", raise_on_wait=True)

    # Ensure verification is present (defensive fallback)
    if not ctx.state.get("verification"):
        logger.warning("Identifier finished without writing verification — defaulting to UNVERIFIED")
        ctx.state["verification"] = {
            "customer_id": None,
            "verification_level": "UNVERIFIED",
            "allowed_actions": [],
            "failure_reason": "Verification agent did not complete.",
            "customer_data": {},
        }

    v = ctx.state["verification"]
    logger.info("VERIFICATION | level=%s customer=%s", v.get("verification_level"), v.get("customer_id"))

    # Audit log after both agents have completed
    session_id = ctx.state.get("session_id", "unknown")
    classification = ctx.state.get("classification", {})
    audit.log_action(
        session_id=session_id,
        customer_id=v.get("customer_id"),
        action="REQUEST_RECEIVED",
        intent=classification.get("intent", "unknown"),
        risk_level=classification.get("risk_level", "LOW"),
        status="INITIATED",
    )


# ---------------------------------------------------------------------------
# NODE 3 — Risk Router (deterministic, no LLM)
# ---------------------------------------------------------------------------

@node(name="risk_router")
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
            "Could you please provide one of the following?\n"
            "• Your phone number\n"
            "• Your policy number\n"
            "• Your vehicle's license plate"
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
                    "A specialist will contact you shortly. "
                    "Reference: " + session_id
                ),
            )
            return
        message = f"A specialist has reviewed your request. Response: {resume}"
    elif intent == "unknown":
        message = (
            "I'm not sure I understood your request. Could you please clarify?\n"
            "I can help you with:\n"
            "• Policy questions\n"
            "• Filing or checking a claim\n"
            "• Getting a new insurance quote\n"
            "• Emergency / roadside assistance"
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

@node(name="specialist_router")
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
def action_confirmation(ctx: Context, node_input: str | None = None):
    """Risk-gated confirmation before finalising the specialist's response."""
    classification = ctx.state.get("classification", {})
    session_id = ctx.state.get("session_id", "unknown")
    risk_level = classification.get("risk_level", "LOW")
    customer_id = ctx.state.get("active_customer_id")
    intent = classification.get("intent", "unknown")

    if risk_level == "LOW":
        audit.log_action(
            session_id=session_id,
            customer_id=customer_id,
            action="ACTION_AUTO_APPROVED",
            intent=intent,
            risk_level=risk_level,
            status="SUCCESS",
        )
        return

    interrupt_id = f"confirm_{session_id}"
    resume = ctx.resume_inputs.get(interrupt_id)

    if resume is None:
        if risk_level == "MEDIUM":
            yield RequestInput(
                interrupt_id=interrupt_id,
                message="Please confirm you want to proceed with this action. Reply 'yes' to confirm or 'no' to cancel.",
            )
        else:
            yield RequestInput(
                interrupt_id=interrupt_id,
                message=(
                    "⚠️ This action requires human approval (HIGH risk). "
                    "A specialist will review and complete your request. "
                    f"Reference: {session_id}"
                ),
            )
        return

    confirmed = str(resume).lower().strip() in ("yes", "y", "confirm", "ok", "proceed")
    status = "SUCCESS" if confirmed else "REJECTED"

    audit.log_action(
        session_id=session_id,
        customer_id=customer_id,
        action="ACTION_CONFIRMATION",
        intent=intent,
        risk_level=risk_level,
        status=status,
    )

    if not confirmed:
        ctx.output = "Action cancelled. Is there anything else I can help you with?"


# ---------------------------------------------------------------------------
# Build the Workflow
# ---------------------------------------------------------------------------

root_agent = Workflow(
    name="insurance_bot_workflow",
    description=(
        "Guardrailed multi-agent insurance bot. "
        "Sequentially classifies intent (Agent 1), verifies identity (Agent 2), "
        "then routes to the right specialist with full audit logging."
    ),
    edges=[
        # Stage 1: classify intent
        (START, intent_classifier),

        # Stage 2: verify identity (sequential after classification)
        (intent_classifier, identification_node),

        # Stage 3: deterministic risk routing
        (identification_node, risk_router),

        # Stage 4a: escalation path
        Edge(from_node=risk_router, to_node=escalation_handler, route="escalate"),

        # Stage 4b: specialist routing
        Edge(from_node=risk_router, to_node=specialist_router, route="proceed"),
        Edge(from_node=specialist_router, to_node=policy_agent, route="policy_question"),
        Edge(from_node=specialist_router, to_node=claims_agent, route="claim"),
        Edge(from_node=specialist_router, to_node=offers_agent, route="offer"),
        Edge(from_node=specialist_router, to_node=emergency_agent, route="emergency"),

        # Stage 5: all specialist agents → confirmation
        (policy_agent, action_confirmation),
        (claims_agent, action_confirmation),
        (offers_agent, action_confirmation),
        (emergency_agent, action_confirmation),
    ],
)
