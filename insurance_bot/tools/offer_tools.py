"""
Offer sub-agent tools.

The agent's job is to sell. These tools let it: see the catalog, check whether
a product the customer asks about is one we offer, build an age-discounted quote
for the verified customer, and — if the customer wants to sign — hand off to a
human by recording a lead.

The catalog lives in the `adk-insurance-offer-mi` bucket; if it can't be reached
we fall back to the in-code `OFFER_CATALOG` so the agent still works offline.
"""

from __future__ import annotations

import logging
import uuid
from datetime import date, datetime, timezone

from insurance_bot.core.config import OFFER_BUCKET
from insurance_bot.core.gcs_client import gcs
from insurance_bot.core import audit_logger as audit
from insurance_bot.data.offer_catalog import OFFER_CATALOG, CATALOG_BY_CATEGORY, CATALOG_BY_ID

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Catalog access (bucket first, embedded fallback)
# ---------------------------------------------------------------------------

def _all_products() -> list[dict]:
    index = gcs.read_from(OFFER_BUCKET, "products/index.json")
    if not index or not index.get("products"):
        return OFFER_CATALOG
    products = []
    for entry in index["products"]:
        p = gcs.read_from(OFFER_BUCKET, f"products/{entry['product_id']}.json")
        products.append(p or CATALOG_BY_ID.get(entry["product_id"]))
    return [p for p in products if p]


def _find_product(key: str) -> dict | None:
    """Resolve a product by category name, product_id, or product name (case-insensitive)."""
    if not key:
        return None
    k = key.strip().lower()
    # Try the live catalog first so bucket edits are honoured.
    for p in _all_products():
        if k in (p.get("category", "").lower(), p.get("product_id", "").lower(),
                 p.get("name", "").lower()):
            return p
    # Fallback to embedded maps.
    return CATALOG_BY_CATEGORY.get(k) or CATALOG_BY_ID.get(k)


def _compute_age(birthdate: str) -> int | None:
    try:
        b = date.fromisoformat(birthdate)
    except (ValueError, TypeError):
        return None
    today = date.today()
    return today.year - b.year - ((today.month, today.day) < (b.month, b.day))


def _discount_pct(product: dict, age: int | None) -> int:
    if age is None:
        return 0
    for tier in product.get("age_discounts", []):
        if tier["min_age"] <= age <= tier["max_age"]:
            return tier["discount_pct"]
    return 0


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------

def list_offers() -> list[dict]:
    """List the products we currently sell (name, category, starting price, tagline)."""
    return [
        {"product_id": p["product_id"], "name": p["name"], "category": p["category"],
         "starting_from_eur": p["base_premium_eur"], "period": p["period"],
         "tagline": p.get("tagline", "")}
        for p in _all_products()
    ]


def get_offer_details(product: str) -> dict:
    """Full details (coverage, pricing, add-ons) for one product by category/id/name."""
    p = _find_product(product)
    if not p:
        return {"error": f"We don't currently offer '{product}'.",
                "available_categories": [x["category"] for x in _all_products()]}
    return p


def check_product_availability(product: str) -> dict:
    """Check whether the customer's product of interest is one we offer.

    If yes → available with a short summary. If no → what we CAN offer instead."""
    p = _find_product(product)
    if p:
        return {"available": True, "product_id": p["product_id"], "name": p["name"],
                "category": p["category"], "starting_from_eur": p["base_premium_eur"]}
    return {"available": False, "requested": product,
            "message": "We don't offer that, but here's what we can offer:",
            "alternatives": [
                {"name": x["name"], "category": x["category"],
                 "starting_from_eur": x["base_premium_eur"]}
                for x in _all_products()
            ]}


def quote_offer(product: str, customer_id: str) -> dict:
    """Build an age-discounted indicative quote for the verified customer.

    Age is derived from the customer's stored birthdate; the matching age tier's
    discount is applied to the base premium."""
    p = _find_product(product)
    if not p:
        return {"error": f"We don't currently offer '{product}'."}
    customer = gcs.get_customer(customer_id)
    age = _compute_age(customer.get("birthdate")) if customer else None
    pct = _discount_pct(p, age)
    base = p["base_premium_eur"]
    final = round(base * (1 - pct / 100), 2)
    return {
        "product_id": p["product_id"], "name": p["name"], "category": p["category"],
        "customer_age": age, "base_premium_eur": base,
        "age_discount_pct": pct, "discount_eur": round(base - final, 2),
        "final_premium_eur": final, "period": p["period"], "currency": "EUR",
        "add_ons": p.get("add_ons", []),
        "note": "Indicative quote. A human advisor finalises the contract.",
    }


def request_human_handoff_to_sign(customer_id: str, product: str) -> dict:
    """Customer wants to sign: record a sales lead and hand off to a human advisor."""
    p = _find_product(product)
    if not p:
        return {"error": f"We don't currently offer '{product}'."}
    lead_id = f"lead_{uuid.uuid4().hex[:10]}"
    record = {
        "lead_id": lead_id,
        "customer_id": customer_id,
        "product_id": p["product_id"],
        "product_name": p["name"],
        "created_at": datetime.now(timezone.utc).isoformat(),
        "status": "PENDING_HUMAN_SIGNATURE",
    }
    written = gcs.write_to(OFFER_BUCKET, f"leads/{lead_id}.json", record)
    audit.log_action(
        session_id=f"tool:{customer_id}", customer_id=customer_id,
        action="OFFER_LEAD_CREATED", intent="offer", risk_level="LOW",
        status="PENDING_HUMAN_SIGNATURE",
        extra={"lead_id": lead_id, "product_id": p["product_id"], "persisted": written},
    )
    logger.info("OFFER | lead %s customer=%s product=%s persisted=%s",
                lead_id, customer_id, p["product_id"], written)
    return {
        "lead_id": lead_id, "product": p["name"], "status": "PENDING_HUMAN_SIGNATURE",
        "message": (
            "Great choice! I've passed your details to one of our human advisors, who "
            f"will call you shortly to complete the paperwork for {p['name']}. "
            f"Your reference is {lead_id}."
        ),
    }


OFFER_TOOLS = [
    list_offers,
    get_offer_details,
    check_product_availability,
    quote_offer,
    request_human_handoff_to_sign,
]
