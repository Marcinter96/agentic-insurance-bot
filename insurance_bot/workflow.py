"""
Insurance Bot — Guardrailed Multi-Agent Workflow (ADK 2.2.0)

Flow:
  START → intent_classifier
        → [verification_node ‖ audit_logger]  (parallel)
        → join_node
        → risk_router
            ├─[escalate]→ escalation_handler  (HITL if needed)
            └─[proceed]→ specialist_router
                           ├─[policy_question]→ policy_agent
                           ├─[claim]         → claims_agent
                           ├─[offer]         → offers_agent
                           └─[emergency]     → emergency_agent
                                      → action_confirmation
"""

import json
import uuid
import logging

from google.adk import Context
from google.adk.workflow import Workflow, node, JoinNode, Edge, START
from google.adk.workflow._function_node import RequestInput

from insurance_bot.agents.policy_agent import policy_agent
from insurance_bot.agents.claims_agent import claims_agent
from insurance_bot.agents.offers_agent import offers_agent
from insurance_bot.agents.emergency_agent import emergency_agent
from insurance_bot.core.config import LLM_MODEL, GCP_PROJECT, GCP_LOCATION, USE_VERTEX_AI
from insurance_bot.core.gcs_client import gcs
from insurance_bot.core import audit_logger as audit
from insurance_bot.core import guardrails

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# LLM client (Vertex AI or Google AI Studio)
# ---------------------------------------------------------------------------

from google import genai

# Lazy singleton: do NOT build the client at import time (that would require
# credentials / an API key just to import the module, which ADK does at startup).
_genai_client = None


def _get_genai_client():
    global _genai_client
    if _genai_client is None:
        _genai_client = genai.Client(
            vertexai=USE_VERTEX_AI,
            project=GCP_PROJECT if USE_VERTEX_AI else None,
            location=GCP_LOCATION if USE_VERTEX_AI else None,
        )
    return _genai_client

_CLASSIFIER_PROMPT = """\
You are a friendly insurance call-centre assistant for Zurich Insurance.
Have a natural conversation to understand what the customer needs, then return a JSON classification.

Conversation so far:
{history}

Based on the conversation, decide ONE of:
A) You need more information — respond with a warm, natural follow-up message (plain text, no JSON).
B) You are confident — respond with ONLY this JSON (no other text):
{{
  "intent": "<policy_question|offer|claim|emergency|unknown>",
  "sub_intent": "<e.g. check claim status>",
  "risk_level": "<LOW|MEDIUM|HIGH>",
  "customer_identifiers": {{
    "phone": "<phone or null>",
    "birthdate": "<YYYY-MM-DD or null>",
    "policy_number": "<policy number or null>",
    "license_plate": "<plate or null>"
  }},
  "confidence": <0.0-1.0>
}}

Rules:
- Ask at most 1 follow-up question per turn. Max 4 questions total (you have asked {questions_asked} so far).
- After 4 questions you MUST return JSON with your best guess.
- If intent is already clear from the conversation, return JSON immediately.
- Risk: LOW=informational, MEDIUM=reading sensitive docs, HIGH=new claim/emergency/modifying data.
"""


# ---------------------------------------------------------------------------
# NODE 1 — Intent Classifier (multi-turn conversation via RequestInput)
# ---------------------------------------------------------------------------

@node(name="intent_classifier", rerun_on_resume=True)
def intent_classifier(ctx: Context, node_input: str):
    """Chat with customer until intent is clear, then classify. Uses RequestInput for multi-turn."""
    session_id = ctx.run_id or str(uuid.uuid4())
    ctx.state["session_id"] = session_id

    user_message = node_input if isinstance(node_input, str) else str(node_input)

    # Restore or init conversation history across resumes
    history: list[dict] = ctx.state.get("_classifier_history", [])
    questions_asked: int = ctx.state.get("_classifier_q_count", 0)

    if not history:
        history.append({"role": "Customer", "content": user_message})

    MAX_QUESTIONS = 4

    while True:
        history_text = "\n".join(f"{t['role']}: {t['content']}" for t in history)
        prompt = _CLASSIFIER_PROMPT.format(history=history_text, questions_asked=questions_asked)

        try:
            response = _get_genai_client().models.generate_content(
                model=LLM_MODEL,
                contents=prompt,
            )
            llm_output = response.text.strip()
        except Exception as e:
            logger.warning("LLM call failed: %s", e)
            llm_output = '{"intent":"unknown","sub_intent":"","risk_level":"LOW","customer_identifiers":{"phone":null,"birthdate":null,"policy_number":null,"license_plate":null},"confidence":0.0}'

        # Try to parse as JSON classification
        classification = None
        stripped = llm_output.strip().lstrip("```json").lstrip("```").rstrip("```").strip()
        if stripped.startswith("{"):
            try:
                classification = json.loads(stripped)
            except Exception:
                pass

        if classification:
            # Done — commit classification
            classification.setdefault("raw_query", user_message)
            ctx.state["classification"] = classification
            ctx.state.pop("_classifier_history", None)
            ctx.state.pop("_classifier_q_count", None)
            logger.info(
                "CLASSIFIED after %d question(s) | intent=%s risk=%s",
                questions_asked, classification.get("intent"), classification.get("risk_level"),
            )
            return

        # It's a follow-up question / conversational reply — send to user
        if questions_asked >= MAX_QUESTIONS:
            # Force classify with unknown rather than asking more
            ctx.state["classification"] = {
                "intent": "unknown", "sub_intent": "", "risk_level": "LOW",
                "customer_identifiers": {"phone": None, "birthdate": None, "policy_number": None, "license_plate": None},
                "confidence": 0.0, "raw_query": user_message,
            }
            ctx.state.pop("_classifier_history", None)
            ctx.state.pop("_classifier_q_count", None)
            return

        questions_asked += 1
        history.append({"role": "Assistant", "content": llm_output})

        # Persist state before yielding (rerun_on_resume will re-enter here)
        ctx.state["_classifier_history"] = history
        ctx.state["_classifier_q_count"] = questions_asked

        interrupt_id = f"classifier_q{questions_asked}_{session_id}"
        customer_reply = yield RequestInput(interrupt_id=interrupt_id, message=llm_output)

        history.append({"role": "Customer", "content": str(customer_reply)})
        ctx.state["_classifier_history"] = history


# ---------------------------------------------------------------------------
# NODE 1b — noop: classification already in state from node 1
# ---------------------------------------------------------------------------

@node(name="parse_classification")
def parse_classification(ctx: Context, node_input: str) -> None:
    """Validate classification is in state (set by intent_classifier)."""
    if not ctx.state.get("classification"):
        logger.warning("No classification in state — defaulting to unknown")
        ctx.state["classification"] = {
            "intent": "unknown", "sub_intent": "", "risk_level": "LOW",
            "customer_identifiers": {"phone": None, "birthdate": None, "policy_number": None, "license_plate": None},
            "confidence": 0.0,
        }
    ctx.state.setdefault("session_id", ctx.run_id or str(uuid.uuid4()))
    c = ctx.state["classification"]
    logger.info("CLASSIFICATION CONFIRMED | intent=%s risk=%s", c.get("intent"), c.get("risk_level"))


# ---------------------------------------------------------------------------
# NODE 2a — Customer Verification
# ---------------------------------------------------------------------------

@node(name="verification_node")
def verification_node(ctx: Context) -> None:
    """Identify and verify the customer using any available identifier."""
    classification = ctx.state.get("classification", {})
    identifiers = classification.get("customer_identifiers", {})

    ctx.state["verification"] = guardrails.verify_customer(
        phone=identifiers.get("phone"),
        policy_number=identifiers.get("policy_number"),
        license_plate=identifiers.get("license_plate"),
        birthdate=identifiers.get("birthdate"),
    )


def _get_allowed_actions(level: str) -> list[str]:
    return guardrails.get_allowed_actions(level)


# ---------------------------------------------------------------------------
# NODE 2b — Audit Logger
# ---------------------------------------------------------------------------

@node(name="audit_logger")
def audit_logger_node(ctx: Context) -> None:
    """Immediately log the incoming request to the audit trail."""
    classification = ctx.state.get("classification", {})
    session_id = ctx.state.get("session_id", ctx.run_id or "unknown")

    log_entry_id = audit.log_action(
        session_id=session_id,
        customer_id=None,
        action="REQUEST_RECEIVED",
        intent=classification.get("intent", "unknown"),
        risk_level=classification.get("risk_level", "LOW"),
        status="INITIATED",
    )
    ctx.state["log_entry_id"] = log_entry_id


# ---------------------------------------------------------------------------
# Join node (waits for both 2a and 2b)
# ---------------------------------------------------------------------------

join_node = JoinNode(name="join_after_parallel")


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
        "Routes customer requests through identity verification, "
        "risk assessment, and specialist agents with audit logging."
    ),
    edges=[
        # Stage 1: START → conversational classifier → parse
        (START, intent_classifier),
        (intent_classifier, parse_classification),

        # Stage 2: Parallel fan-out to verification + audit
        Edge(from_node=parse_classification, to_node=verification_node),
        Edge(from_node=parse_classification, to_node=audit_logger_node),

        # Parallel branches converge at join node
        Edge(from_node=verification_node, to_node=join_node),
        Edge(from_node=audit_logger_node, to_node=join_node),

        # Stage 3: Join → risk router
        (join_node, risk_router),

        # Stage 4a: Escalation path
        Edge(from_node=risk_router, to_node=escalation_handler, route="escalate"),

        # Stage 4b: Specialist routing path
        Edge(from_node=risk_router, to_node=specialist_router, route="proceed"),
        Edge(from_node=specialist_router, to_node=policy_agent, route="policy_question"),
        Edge(from_node=specialist_router, to_node=claims_agent, route="claim"),
        Edge(from_node=specialist_router, to_node=offers_agent, route="offer"),
        Edge(from_node=specialist_router, to_node=emergency_agent, route="emergency"),

        # Stage 5: All specialist agents → confirmation
        (policy_agent, action_confirmation),
        (claims_agent, action_confirmation),
        (offers_agent, action_confirmation),
        (emergency_agent, action_confirmation),
    ],
)
