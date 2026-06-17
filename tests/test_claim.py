"""Tests for the Claims sub-agent: 5-question stateful intake, callback, human routing."""

import pytest

from insurance_bot.tools import claim_tools as ct


class _Ctx:
    """Minimal stand-in for an ADK ToolContext (just a .state dict)."""
    def __init__(self, state=None):
        self.state = state if state is not None else {}


@pytest.fixture(autouse=True)
def _no_gcs(monkeypatch):
    monkeypatch.setattr(ct.gcs, "write_to", lambda bucket, path, data, **k: True)
    monkeypatch.setattr(ct.audit, "log_action", lambda **k: "ts")
    yield


def _answer_all(ctx):
    answers = {
        "incident_type": "car accident",
        "incident_date": "2026-06-10",
        "location": "Brussels ring road",
        "description": "Rear-ended at a red light",
        "policy_number": "pol_123",
    }
    for field, value in answers.items():
        ct.record_claim_answer(field, value, ctx)


def test_five_questions_defined():
    out = ct.list_claim_questions()
    assert out["total"] == 5
    assert [q["field"] for q in out["questions"]] == [
        "incident_type", "incident_date", "location", "description", "policy_number"]


def test_record_answer_tracks_state_and_next_question():
    ctx = _Ctx()
    res = ct.record_claim_answer("incident_type", "theft", ctx)
    assert res["recorded"] == "incident_type"
    assert res["collected_count"] == 1
    assert ctx.state["claim_intake"] == {"incident_type": "theft"}
    # next question should be the date
    assert res["next_question"]["field"] == "incident_date"
    assert res["complete"] is False


def test_record_answer_rejects_unknown_field_and_empty():
    ctx = _Ctx()
    assert "error" in ct.record_claim_answer("color", "blue", ctx)
    assert "error" in ct.record_claim_answer("incident_type", "   ", ctx)
    assert ctx.state.get("claim_intake") in (None, {})


def test_progress_becomes_complete_after_five():
    ctx = _Ctx()
    _answer_all(ctx)
    p = ct.get_claim_progress(ctx)
    assert p["complete"] is True
    assert p["collected_count"] == 5
    assert p["missing"] == []
    assert p["next_question"] is None


def test_finalize_requires_complete_intake():
    ctx = _Ctx({"active_customer_id": "cust_005"})
    ct.record_claim_answer("incident_type", "fire", ctx)
    res = ct.finalize_claim_with_callback("tomorrow 10am", ctx)
    assert "error" in res
    assert "incident_date" in res["missing"]


def test_finalize_schedules_callback_when_complete():
    ctx = _Ctx({"active_customer_id": "cust_005", "session_id": "s1"})
    _answer_all(ctx)
    res = ct.finalize_claim_with_callback("Friday afternoon", ctx)
    assert res["status"] == "SUBMITTED"
    assert res["callback_status"] == "SCHEDULED"
    assert res["preferred_callback_time"] == "Friday afternoon"
    assert res["claim_id"].startswith("clm_")


def test_finalize_requires_callback_time():
    ctx = _Ctx({"active_customer_id": "cust_005"})
    _answer_all(ctx)
    assert "error" in ct.finalize_claim_with_callback("  ", ctx)


def test_records_go_to_dedicated_claims_bucket(monkeypatch):
    from insurance_bot.core.config import CLAIMS_BUCKET
    captured = []
    monkeypatch.setattr(ct.gcs, "write_to",
                        lambda bucket, path, data, **k: captured.append((bucket, path)) or True)
    ctx = _Ctx({"active_customer_id": "cust_005"})
    _answer_all(ctx)
    ct.finalize_claim_with_callback("Monday", ctx)
    ct.route_claim_to_human("stuck", ctx)
    assert all(bucket == CLAIMS_BUCKET for bucket, _ in captured)
    assert any(path.startswith("claims/") for _, path in captured)
    assert any(path.startswith("escalations/") for _, path in captured)


def test_route_to_human_records_partial():
    ctx = _Ctx({"active_customer_id": "cust_005"})
    ct.record_claim_answer("incident_type", "water damage", ctx)
    res = ct.route_claim_to_human("customer doesn't know policy number", ctx)
    assert res["status"] == "PENDING_HUMAN_REVIEW"
    assert res["escalation_id"].startswith("clm_esc_")
