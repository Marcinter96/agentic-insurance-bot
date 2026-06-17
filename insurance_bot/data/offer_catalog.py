"""
The sellable product/offer catalog (source of truth).

Five products — House, Motor, Life, Pension, Assistance — each with coverage
details, base pricing, optional add-ons, and AGE-BASED discount tiers. This
module is the canonical definition; `scripts/generate_offers.py` uploads it to
the `adk-insurance-offer-mi` bucket, and the offer tools read it back from there
(falling back to this module if the bucket isn't reachable).

Discount tiers are inclusive ranges on the customer's age, expressed as a
percentage off the base premium.
"""

from __future__ import annotations

OFFER_CATALOG: list[dict] = [
    {
        "product_id": "prod_house",
        "name": "HomeShield",
        "category": "house",
        "tagline": "Protect the place you call home.",
        "description": "Comprehensive home insurance for owners and renters covering "
                       "fire, water damage, theft and third-party liability.",
        "coverage": {
            "included": ["Fire & smoke", "Water damage", "Theft & burglary",
                         "Storm & natural events", "Personal liability up to €1,000,000"],
            "excluded": ["Wear and tear", "Damage from unfinished construction"],
            "coverage_limit_eur": 500000,
            "deductible_eur": 250,
        },
        "base_premium_eur": 350.0,
        "period": "annual",
        "add_ons": [
            {"name": "Valuables cover", "price_eur": 60.0},
            {"name": "Legal protection", "price_eur": 45.0},
        ],
        "age_discounts": [
            {"min_age": 18, "max_age": 30, "discount_pct": 0},
            {"min_age": 31, "max_age": 55, "discount_pct": 5},
            {"min_age": 56, "max_age": 120, "discount_pct": 10},
        ],
    },
    {
        "product_id": "prod_motor",
        "name": "AutoGuard",
        "category": "motor",
        "tagline": "Drive with total peace of mind.",
        "description": "Full motor insurance: collision, fire, theft and mandatory "
                       "third-party liability, with optional roadside assistance.",
        "coverage": {
            "included": ["Collision damage", "Fire & theft", "Third-party liability",
                         "Windscreen repair", "Courtesy car for 7 days"],
            "excluded": ["Racing/track use", "Driving under the influence"],
            "coverage_limit_eur": 60000,
            "deductible_eur": 400,
        },
        "base_premium_eur": 600.0,
        "period": "annual",
        "add_ons": [
            {"name": "24/7 Roadside assistance", "price_eur": 80.0},
            {"name": "No-claims protection", "price_eur": 70.0},
        ],
        # Younger drivers are higher-risk → no discount; older drivers earn more.
        "age_discounts": [
            {"min_age": 18, "max_age": 25, "discount_pct": 0},
            {"min_age": 26, "max_age": 45, "discount_pct": 8},
            {"min_age": 46, "max_age": 70, "discount_pct": 15},
            {"min_age": 71, "max_age": 120, "discount_pct": 5},
        ],
    },
    {
        "product_id": "prod_life",
        "name": "LifeSecure",
        "category": "life",
        "tagline": "Financial security for the people you love.",
        "description": "Term life insurance paying a lump sum to your beneficiaries, "
                       "with optional critical-illness cover.",
        "coverage": {
            "included": ["Death benefit lump sum", "Terminal illness benefit",
                         "Worldwide cover"],
            "excluded": ["Pre-existing conditions not declared", "High-risk hobbies unless declared"],
            "payout_eur": 200000,
        },
        "base_premium_eur": 480.0,
        "period": "annual",
        "add_ons": [
            {"name": "Critical illness cover", "price_eur": 120.0},
            {"name": "Children's cover", "price_eur": 40.0},
        ],
        # Younger applicants lock in cheaper premiums.
        "age_discounts": [
            {"min_age": 18, "max_age": 35, "discount_pct": 20},
            {"min_age": 36, "max_age": 50, "discount_pct": 10},
            {"min_age": 51, "max_age": 65, "discount_pct": 0},
        ],
    },
    {
        "product_id": "prod_pension",
        "name": "PensionPlus",
        "category": "pension",
        "tagline": "Build the retirement you deserve.",
        "description": "A flexible private pension plan with tax-advantaged "
                       "contributions and a choice of investment profiles.",
        "coverage": {
            "included": ["Tax-advantaged contributions", "Choice of risk profiles",
                         "Flexible retirement age", "Optional capital guarantee"],
            "excluded": ["Withdrawals before retirement age may incur penalties"],
            "min_annual_contribution_eur": 1200,
        },
        "base_premium_eur": 1200.0,
        "period": "annual contribution",
        "add_ons": [
            {"name": "Capital guarantee", "price_eur": 150.0},
            {"name": "Spouse survivor benefit", "price_eur": 200.0},
        ],
        # Starting younger is rewarded with a fee discount.
        "age_discounts": [
            {"min_age": 18, "max_age": 35, "discount_pct": 15},
            {"min_age": 36, "max_age": 50, "discount_pct": 8},
            {"min_age": 51, "max_age": 120, "discount_pct": 0},
        ],
    },
    {
        "product_id": "prod_assistance",
        "name": "AssistEverywhere",
        "category": "assistance",
        "tagline": "Help is always one call away.",
        "description": "24/7 personal and travel assistance: medical help, repatriation, "
                       "trip cancellation and luggage cover worldwide.",
        "coverage": {
            "included": ["24/7 medical assistance", "Emergency repatriation",
                         "Trip cancellation", "Lost luggage", "Legal assistance abroad"],
            "excluded": ["Undeclared chronic conditions", "Travel against medical advice"],
            "coverage_limit_eur": 100000,
        },
        "base_premium_eur": 90.0,
        "period": "annual",
        "add_ons": [
            {"name": "Winter sports cover", "price_eur": 35.0},
            {"name": "Business travel extension", "price_eur": 50.0},
        ],
        "age_discounts": [
            {"min_age": 18, "max_age": 64, "discount_pct": 10},
            {"min_age": 65, "max_age": 120, "discount_pct": 0},
        ],
    },
]

# category → product, for quick interest matching.
CATALOG_BY_CATEGORY = {p["category"]: p for p in OFFER_CATALOG}
CATALOG_BY_ID = {p["product_id"]: p for p in OFFER_CATALOG}
