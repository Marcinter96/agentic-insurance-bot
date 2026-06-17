"""Tests for the RESOLVED vs HUMAN_HANDOFF outcome split."""

from insurance_bot.core import outcomes
from insurance_bot.tools import policy_tools as pt
from insurance_bot.tools import claim_tools as ct
from insurance_bot.tools import offer_tools as ot


class _Ctx:
    def __init__(self, state=None):
        self.state = state if state is not None else {}


def test_policy_close_sets_resolved(monkeypatch):
    monkeypatch.setattr(pt.audit, "log_action", lambda **k: "ts")
    ctx = _Ctx({"active_customer_id": "c"})
    pt.close_conversation(True, ctx)
    assert ctx.state["resolution"] == outcomes.RESOLVED


def test_policy_route_sets_handoff(monkeypatch):
    monkeypatch.setattr(pt.audit, "log_action", lambda **k: "ts")
    ctx = _Ctx({"active_customer_id": "c"})
    pt.route_policy_to_human("x", ctx)
    assert ctx.state["resolution"] == outcomes.HUMAN_HANDOFF


def test_claim_finalize_sets_resolved(monkeypatch):
    monkeypatch.setattr(ct.gcs, "write_to", lambda *a, **k: True)
    monkeypatch.setattr(ct.audit, "log_action", lambda **k: "ts")
    ctx = _Ctx({"active_customer_id": "c"})
    for f in ["incident_type", "incident_date", "location", "description", "policy_number"]:
        ct.record_claim_answer(f, "v", ctx)
    ct.finalize_claim_with_callback("Monday", ctx)
    assert ctx.state["resolution"] == outcomes.RESOLVED


def test_claim_route_sets_handoff(monkeypatch):
    monkeypatch.setattr(ct.gcs, "write_to", lambda *a, **k: True)
    monkeypatch.setattr(ct.audit, "log_action", lambda **k: "ts")
    ctx = _Ctx({"active_customer_id": "c"})
    ct.route_claim_to_human("stuck", ctx)
    assert ctx.state["resolution"] == outcomes.HUMAN_HANDOFF


def test_offer_sale_sets_resolved(monkeypatch):
    monkeypatch.setattr(ot.gcs, "read_from", lambda *a, **k: None)
    monkeypatch.setattr(ot.gcs, "write_to", lambda *a, **k: True)
    monkeypatch.setattr(ot.audit, "log_action", lambda **k: "ts")
    ctx = _Ctx({"active_customer_id": "c"})
    ot.request_human_handoff_to_sign("c", "life", ctx)
    assert ctx.state["resolution"] == outcomes.RESOLVED


def test_outcome_route_decision():
    from insurance_bot.workflow import decide_outcome_route
    assert decide_outcome_route({"resolution": outcomes.HUMAN_HANDOFF}) == outcomes.HUMAN_HANDOFF
    assert decide_outcome_route({"resolution": outcomes.RESOLVED}) == outcomes.RESOLVED
    assert decide_outcome_route({}) == outcomes.RESOLVED  # default = success
