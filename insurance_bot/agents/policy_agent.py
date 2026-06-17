from google.adk.agents import LlmAgent
from insurance_bot.core.config import LLM_MODEL
from insurance_bot.core.gcs_client import gcs
from insurance_bot.core.output_guard import output_guardrail_callback


def get_policy_details(policy_id: str, customer_id: str) -> dict:
    """Retrieve policy details. Enforces customer ownership."""
    customer = gcs.get_customer(customer_id)
    if not customer or policy_id not in customer.get("policy_ids", []):
        return {"error": "Policy not found or not owned by this customer."}
    return gcs.get_policy(policy_id) or {"error": "Policy document unavailable."}


def list_customer_policies(customer_id: str) -> list[dict]:
    """List all policies for the verified customer."""
    customer = gcs.get_customer(customer_id)
    if not customer:
        return []
    return [gcs.get_policy(pid) for pid in customer.get("policy_ids", []) if gcs.get_policy(pid)]


def get_customer_invoices(customer_id: str) -> list[dict]:
    """List all invoices for the verified customer."""
    return gcs.get_invoices(customer_id)


policy_agent = LlmAgent(
    name="policy_agent",
    model=LLM_MODEL,
    after_model_callback=output_guardrail_callback,
    instruction="""You are an insurance policy specialist. Help the verified customer with:
- Understanding their coverage (what is and isn't covered)
- Getting a copy of their policy document
- Checking their renewal date and premium
- Understanding invoice details and payment history

The customer's ID is available in your context. ALWAYS use it when calling tools.
NEVER reveal or discuss policies belonging to other customers.
Be clear, empathetic, and precise. If you can't answer, say so honestly.

GUARDRAIL: You may only access data for the customer whose ID is in your context (customer_id).
""",
    tools=[get_policy_details, list_customer_policies, get_customer_invoices],
)
