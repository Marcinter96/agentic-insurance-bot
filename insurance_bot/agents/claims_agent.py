from google.adk.agents import LlmAgent
from insurance_bot.core.config import LLM_MODEL
from insurance_bot.core.gcs_client import gcs
from insurance_bot.core.output_guard import output_guardrail_callback
import uuid
from datetime import datetime


def get_open_claims(customer_id: str) -> list[dict]:
    """List all claims for the verified customer."""
    return gcs.get_claims(customer_id)


def get_claim_status(claim_id: str, customer_id: str) -> dict:
    """Get the status of a specific claim. Enforces customer ownership."""
    claims = gcs.get_claims(customer_id)
    for claim in claims:
        if claim.get("claim_id") == claim_id:
            return claim
    return {"error": "Claim not found or not owned by this customer."}


def file_new_claim(
    customer_id: str,
    policy_id: str,
    description: str,
    incident_date: str,
) -> dict:
    """File a new insurance claim for the verified customer."""
    customer = gcs.get_customer(customer_id)
    if not customer or policy_id not in customer.get("policy_ids", []):
        return {"error": "Policy not found or not owned by this customer."}

    claim_id = f"clm_{uuid.uuid4().hex[:8]}"
    claim = {
        "claim_id": claim_id,
        "policy_id": policy_id,
        "customer_id": customer_id,
        "status": "SUBMITTED",
        "date_filed": datetime.now().strftime("%Y-%m-%d"),
        "incident_date": incident_date,
        "description": description,
        "amount": None,
    }
    gcs._write(f"claims/{claim_id}.json", claim)
    return {"success": True, "claim_id": claim_id, "status": "SUBMITTED",
            "message": "Your claim has been submitted. A handler will contact you within 48 hours."}


claims_agent = LlmAgent(
    name="claims_agent",
    model=LLM_MODEL,
    after_model_callback=output_guardrail_callback,
    instruction="""You are an insurance claims specialist. Help the verified customer with:
- Checking the status of an existing claim
- Filing a new claim (accident, theft, damage, etc.)
- Understanding the claims process and next steps
- Providing the required documentation list

When filing a new claim, always collect:
1. The policy number / policy ID
2. Date of incident
3. Description of what happened

Be empathetic — customers filing claims are often stressed. Be clear about timelines.

GUARDRAIL: You may only access claims belonging to the customer_id in your context.
Never expose other customers' claim details.
""",
    tools=[get_open_claims, get_claim_status, file_new_claim],
)
