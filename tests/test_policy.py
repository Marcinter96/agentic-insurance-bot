"""Tests for the Policy sub-agent tools: ownership enforcement, reads, routing, close."""

import datetime

import pytest

from insurance_bot.tools import policy_tools as pt


class _Ctx:
    def __init__(self, state=None):
        self.state = state if state is not None else {}


# --- fake GCS data ----------------------------------------------------------

_CUSTOMERS = {
    "cust_a": {"id": "cust_a", "birthdate": "1980-01-01",
               "policy_ids": ["pol_a1"], "vehicle_ids": ["veh_a1"]},
}
_POLICIES = {
    "pol_a1": {"policy_id": "pol_a1", "customer_id": "cust_a", "type": "motor",
               "status": "ACTIVE", "premium": 600.0, "expiry": "2030-01-01"},
    "pol_b1": {"policy_id": "pol_b1", "customer_id": "cust_b", "type": "house",
               "status": "ACTIVE", "premium": 350.0, "expiry": "2030-01-01"},
}
_VEHICLES = {
    "veh_a1": {"vehicle_id": "veh_a1", "customer_id": "cust_a", "make": "Toyota",
               "model": "Corolla", "year": 2020, "license_plate": "1-ABC-123", "value": 18000},
}
_INVOICES = {
    "cust_a": [
        {"invoice_id": "inv1", "amount": 150.0, "status": "PAID"},
        {"invoice_id": "inv2", "amount": 150.0, "status": "DUE"},
        {"invoice_id": "inv3", "amount": 150.0, "status": "OVERDUE"},
    ],
}


@pytest.fixture(autouse=True)
def _stub_gcs(monkeypatch):
    monkeypatch.setattr(pt.gcs, "get_customer", lambda cid: _CUSTOMERS.get(cid))
    monkeypatch.setattr(pt.gcs, "get_policy", lambda pid: _POLICIES.get(pid))
    monkeypatch.setattr(pt.gcs, "get_vehicle", lambda vid: _VEHICLES.get(vid))
    monkeypatch.setattr(pt.gcs, "get_invoices", lambda cid: _INVOICES.get(cid, []))
    monkeypatch.setattr(pt.audit, "log_action", lambda **k: "ts")
    yield


# --- ownership enforcement (the core guardrail) -----------------------------

def test_get_policy_details_allows_owned():
    assert pt.get_policy_details("pol_a1", "cust_a")["type"] == "motor"


def test_get_policy_details_blocks_other_customers_policy():
    res = pt.get_policy_details("pol_b1", "cust_a")  # pol_b1 belongs to cust_b
    assert "error" in res


def test_download_document_blocked_for_unowned_policy():
    assert "error" in pt.download_policy_document("pol_b1", "cust_a")


def test_download_document_ok_for_owned():
    res = pt.download_policy_document("pol_a1", "cust_a")
    assert res["document_url"].endswith("cust_a/pol_a1.pdf")


# --- reads ------------------------------------------------------------------

def test_list_policies_and_vehicles():
    assert [p["policy_id"] for p in pt.list_customer_policies("cust_a")] == ["pol_a1"]
    assert [v["vehicle_id"] for v in pt.list_insured_vehicles("cust_a")] == ["veh_a1"]


def test_unpaid_invoices_filters_and_totals():
    res = pt.get_unpaid_invoices("cust_a")
    assert res["count"] == 2
    assert res["total_outstanding"] == 300.0


def test_renewal_info_flags_due_soon(monkeypatch):
    soon = (datetime.date.today() + datetime.timedelta(days=10)).isoformat()
    monkeypatch.setitem(_POLICIES, "pol_a1", {**_POLICIES["pol_a1"], "expiry": soon})
    res = pt.get_renewal_info("pol_a1", "cust_a")
    assert res["renewal_due_soon"] is True
    assert 0 <= res["days_until_expiry"] <= 10


# --- routing & close --------------------------------------------------------

def test_route_to_human():
    ctx = _Ctx({"active_customer_id": "cust_a"})
    res = pt.route_policy_to_human("needs account change", ctx)
    assert res["status"] == "PENDING_HUMAN_REVIEW"
    assert res["escalation_id"].startswith("pol_esc_")


def test_close_conversation():
    res = pt.close_conversation(True, _Ctx({"active_customer_id": "cust_a"}))
    assert res["status"] == "closed" and res["satisfied"] is True
