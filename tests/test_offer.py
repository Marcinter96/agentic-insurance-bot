"""Tests for the Offer sub-agent tools: catalog, availability, age-discount pricing, lead."""

import datetime

import pytest

from insurance_bot.tools import offer_tools
from insurance_bot.data.offer_catalog import OFFER_CATALOG


@pytest.fixture(autouse=True)
def _offline(monkeypatch):
    """Force the embedded catalog (no bucket reads) and stub customer lookups."""
    monkeypatch.setattr(offer_tools.gcs, "read_from", lambda *a, **k: None)
    yield


def test_list_offers_returns_five_products():
    offers = offer_tools.list_offers()
    assert len(offers) == 5
    cats = {o["category"] for o in offers}
    assert cats == {"house", "motor", "life", "pension", "assistance"}


def test_find_product_by_category_id_and_name():
    assert offer_tools._find_product("motor")["product_id"] == "prod_motor"
    assert offer_tools._find_product("prod_life")["category"] == "life"
    assert offer_tools._find_product("AssistEverywhere")["category"] == "assistance"


def test_availability_offered_vs_not():
    yes = offer_tools.check_product_availability("house")
    assert yes["available"] is True and yes["product_id"] == "prod_house"

    no = offer_tools.check_product_availability("pet insurance")
    assert no["available"] is False
    assert len(no["alternatives"]) == 5


def test_compute_age():
    today = datetime.date.today()
    bd = f"{today.year - 40:04d}-01-01"
    assert offer_tools._compute_age(bd) == 40
    assert offer_tools._compute_age("not-a-date") is None


def test_quote_applies_age_discount(monkeypatch):
    # 40-year-old → motor tier 26-45 = 8% off base 600 = 552.
    monkeypatch.setattr(offer_tools.gcs, "get_customer",
                        lambda cid: {"birthdate": f"{datetime.date.today().year - 40}-01-01"})
    q = offer_tools.quote_offer("motor", "cust_x")
    assert q["customer_age"] == 40
    assert q["age_discount_pct"] == 8
    assert q["base_premium_eur"] == 600.0
    assert q["final_premium_eur"] == 552.0


def test_quote_unknown_product():
    assert "error" in offer_tools.quote_offer("spaceship", "cust_x")


def test_handoff_creates_lead(monkeypatch):
    captured = {}
    monkeypatch.setattr(offer_tools.gcs, "write_to",
                        lambda bucket, path, data, **k: captured.update(path=path, data=data) or True)
    monkeypatch.setattr(offer_tools.audit, "log_action", lambda **k: "ts")
    res = offer_tools.request_human_handoff_to_sign("cust_x", "life")
    assert res["status"] == "PENDING_HUMAN_SIGNATURE"
    assert res["lead_id"].startswith("lead_")
    assert captured["path"].startswith("leads/")
    assert captured["data"]["product_id"] == "prod_life"


def test_catalog_discount_tiers_are_well_formed():
    for p in OFFER_CATALOG:
        for tier in p["age_discounts"]:
            assert tier["min_age"] <= tier["max_age"]
            assert 0 <= tier["discount_pct"] <= 100
