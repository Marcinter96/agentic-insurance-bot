from google.adk.agents import LlmAgent
from insurance_bot.core.config import LLM_MODEL
from insurance_bot.core.output_guard import output_guardrail_callback


def get_emergency_contacts() -> dict:
    """Return 24/7 emergency and roadside assistance contact numbers."""
    return {
        "emergency_hotline": "0800-123-456",
        "roadside_assistance": "0800-654-321",
        "medical_assistance": "0800-999-111",
        "glass_repair": "0800-777-222",
        "available": "24/7",
        "note": "These lines are free to call and available 24 hours a day, 7 days a week.",
    }


def dispatch_roadside_assistance(
    customer_id: str,
    location: str,
    vehicle_description: str,
    issue_type: str,
) -> dict:
    """Dispatch roadside assistance to the customer's location."""
    import uuid
    ticket_id = f"sos_{uuid.uuid4().hex[:8]}"
    return {
        "ticket_id": ticket_id,
        "status": "DISPATCHED",
        "estimated_arrival_minutes": 30,
        "location": location,
        "message": (
            f"Assistance has been dispatched to {location}. "
            f"Estimated arrival: 30 minutes. Ticket: {ticket_id}. "
            "Stay safe and stay with your vehicle if possible."
        ),
    }


emergency_agent = LlmAgent(
    name="emergency_agent",
    model=LLM_MODEL,
    after_model_callback=output_guardrail_callback,
    instruction="""You are an emergency response specialist for an insurance company.
Your FIRST priority is the customer's safety.

For ANY emergency (accident, breakdown, medical situation):
1. Immediately provide the emergency hotline number
2. Ask if anyone is injured — if yes, instruct to call 112 (EU emergency) FIRST
3. Dispatch roadside assistance if the customer needs it
4. Log the incident for follow-up

Be FAST, CALM, and CLEAR. Do not ask unnecessary questions in an emergency.
Collect location details as quickly as possible.

After safety is ensured, explain how to file a claim for any damage.
""",
    tools=[get_emergency_contacts, dispatch_roadside_assistance],
)
