from google.adk.agents import LlmAgent
from insurance_bot.core.config import LLM_MODEL
from insurance_bot.core.output_guard import output_guardrail_callback


def list_available_products() -> list[dict]:
    """List all insurance products available for new quotes."""
    return [
        {"product_id": "prod_auto_comp", "name": "Comprehensive Auto Insurance",
         "description": "Full coverage: fire, theft, collision, third-party liability",
         "starting_from": 600.0, "currency": "EUR", "period": "annual"},
        {"product_id": "prod_auto_tpl", "name": "Third-Party Liability Auto",
         "description": "Mandatory minimum coverage for vehicle owners",
         "starting_from": 280.0, "currency": "EUR", "period": "annual"},
        {"product_id": "prod_home_std", "name": "Home Insurance Standard",
         "description": "Fire, water damage, theft for primary residence",
         "starting_from": 350.0, "currency": "EUR", "period": "annual"},
        {"product_id": "prod_home_prem", "name": "Home Insurance Premium",
         "description": "All risks coverage including natural disasters",
         "starting_from": 550.0, "currency": "EUR", "period": "annual"},
        {"product_id": "prod_travel", "name": "Travel Insurance",
         "description": "Medical, cancellation, luggage coverage worldwide",
         "starting_from": 45.0, "currency": "EUR", "period": "per trip"},
    ]


def get_personalized_quote(
    product_id: str,
    vehicle_value: float | None = None,
    property_value: float | None = None,
    num_drivers: int = 1,
) -> dict:
    """Generate an indicative quote for the requested product."""
    base_prices = {
        "prod_auto_comp": 600.0, "prod_auto_tpl": 280.0,
        "prod_home_std": 350.0, "prod_home_prem": 550.0, "prod_travel": 45.0,
    }
    base = base_prices.get(product_id)
    if not base:
        return {"error": f"Unknown product: {product_id}"}
    premium = base
    if vehicle_value and vehicle_value > 30000:
        premium *= 1.2
    if num_drivers > 1:
        premium *= 1 + (num_drivers - 1) * 0.15
    if property_value and property_value > 500000:
        premium *= 1.3
    return {"product_id": product_id, "indicative_premium": round(premium, 2),
            "currency": "EUR", "valid_for_days": 30,
            "note": "Final premium depends on full underwriting. Call us or visit a branch to finalize."}


offers_agent = LlmAgent(
    name="offers_agent",
    model=LLM_MODEL,
    after_model_callback=output_guardrail_callback,
    instruction="""You are an insurance sales and offers specialist. Help customers with:
- Getting a quote for a new insurance product (auto, home, travel, etc.)
- Understanding what different coverage options include
- Comparing products to find the best fit for their needs
- Explaining pricing and how premiums are calculated

Always ask clarifying questions to personalise the quote (e.g. vehicle value, property value).
Be helpful and transparent — explain what affects the price.
This is a LOW-RISK operation: no sensitive data is modified.

Note: Quotes are indicative. Always remind the customer to call or visit a branch to finalise.
""",
    tools=[list_available_products, get_personalized_quote],
)
