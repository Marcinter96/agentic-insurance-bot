import os

from google.adk.agents import LlmAgent

# ---------------------------------------------------------------------------
# Model selection
# ---------------------------------------------------------------------------
# For bidirectional (bidi) live streaming with AUDIO output, a Live-capable
# native-audio model is required. Override per-environment via BIDI_AGENT_MODEL.
#   - Vertex AI Live (this project):  gemini-live-2.5-flash-native-audio
#   - Gemini Live API (AI Studio):    gemini-2.5-flash-native-audio-preview-12-2025
# The default text model is kept for the classic `adk web` flow.
TEXT_MODEL = os.getenv("AGENT_MODEL", "gemini-2.5-flash")
BIDI_MODEL = os.getenv("BIDI_AGENT_MODEL", "gemini-live-2.5-flash-native-audio")

# ---------------------------------------------------------------------------
# Skills (dummy tools — replace with real API calls in production)
# ---------------------------------------------------------------------------

def get_invoice(invoice_id: str) -> dict:
    """Fetch details for a specific invoice by its ID."""
    if "123" in invoice_id:
        return {"invoice_id": invoice_id, "amount": "150.00", "status": "paid", "due_date": "2026-05-01"}
    return {"invoice_id": invoice_id, "amount": "200.00", "status": "due", "due_date": "2026-06-30"}

def list_recent_invoices(customer_id: str) -> list:
    """List the most recent invoices for a given customer ID."""
    return [
        {"invoice_id": "inv_12345", "amount": "150.00", "status": "paid"},
        {"invoice_id": "inv_67890", "amount": "200.00", "status": "due"},
    ]

def get_policy_document(policy_number: str) -> dict:
    """Retrieve a policy document for a given policy number."""
    return {
        "policy_number": policy_number,
        "type": "Comprehensive Home Insurance",
        "coverage": "Fire, Theft, Water Damage",
        "expiry": "2027-01-01"
    }

def email_document(policy_number: str, email_address: str) -> dict:
    """Email a policy document to the customer."""
    return {"status": "sent", "policy_number": policy_number, "sent_to": email_address}

def search_knowledge_base(query: str) -> str:
    """Search the FAQ knowledge base for general questions."""
    q = query.lower()
    if "hours" in q:
        return "Our business hours are Monday to Friday, 9 AM to 5 PM."
    if "contact" in q or "phone" in q:
        return "You can reach support at support@example.com or call 1-800-555-0100."
    if "claim" in q:
        return "To file a claim, please call 1-800-555-0200 or visit our website."
    return "I couldn't find a specific answer. Please contact our support team at support@example.com."

# ---------------------------------------------------------------------------
# Specialist sub-agents
# ---------------------------------------------------------------------------

invoice_agent = LlmAgent(
    name="invoice_agent",
    model="gemini-2.5-flash",
    instruction="""
    You are a billing support specialist. Help users with invoice questions.
    Use `get_invoice` when the user provides an invoice ID.
    Use `list_recent_invoices` when the user asks for recent invoices — ask for their customer ID if not provided.
    Always summarize the result clearly.
    """,
    tools=[get_invoice, list_recent_invoices]
)

policy_agent = LlmAgent(
    name="policy_agent",
    model="gemini-2.5-flash",
    instruction="""
    You are a policy document specialist. Help users get copies of their insurance policies.
    You need a policy number — ask for it if not provided.
    Use `get_policy_document` to retrieve the policy, then confirm the details to the user.
    If the user wants it emailed, ask for their email address and use `email_document`.
    """,
    tools=[get_policy_document, email_document]
)

knowledge_agent = LlmAgent(
    name="knowledge_agent",
    model="gemini-2.5-flash",
    instruction="""
    You are a general customer support agent. Answer questions about business hours,
    contact information, how to file claims, and other general FAQs.
    Use `search_knowledge_base` to find answers.
    Do NOT discuss specific customer invoices or policy details.
    """,
    tools=[search_knowledge_base]
)

# ---------------------------------------------------------------------------
# Root orchestrator — this is what ADK loads
# ---------------------------------------------------------------------------

root_agent = LlmAgent(
    name="customer_service_agent",
    model="gemini-2.5-flash",
    instruction="""
    You are a friendly insurance customer service assistant.
    Your job is to understand the customer's request and delegate to the right specialist:

    - For billing, payments, or invoice questions → transfer to `invoice_agent`
    - For policy documents or coverage details → transfer to `policy_agent`
    - For general questions (hours, contact, claims process) → transfer to `knowledge_agent`

    Always greet the customer warmly. If the request is unclear, ask a clarifying question.
    """,
    sub_agents=[invoice_agent, policy_agent, knowledge_agent]
)
