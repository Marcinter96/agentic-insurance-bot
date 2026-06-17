"""Tests for the SOS (emergency) path: routing bypass, record building, persistence."""

from insurance_bot.core import guardrails
from insurance_bot.core.gcs_client import GCSClient
from insurance_bot.workflow import build_sos_record, SOS_MESSAGE


# --- routing: emergencies always proceed, even unverified -------------------

def test_emergency_route_proceeds_when_unverified():
    assert guardrails.decide_route(
        verification_level="UNVERIFIED", intent="emergency", allowed_actions=[]
    ) == "proceed"


def test_emergency_route_proceeds_with_bypass_level():
    assert guardrails.decide_route(
        verification_level="EMERGENCY_BYPASS", intent="emergency", allowed_actions=[]
    ) == "proceed"


def test_non_emergency_unverified_still_escalates():
    assert guardrails.decide_route(
        verification_level="UNVERIFIED", intent="policy_question", allowed_actions=[]
    ) == "escalate"


# --- record building --------------------------------------------------------

def test_sos_record_uses_sub_intent_as_reason():
    state = {
        "session_id": "sess-1",
        "classification": {"intent": "emergency", "sub_intent": "car accident on the A4"},
        "verification": {"customer_id": "cust_005", "verification_level": "VERIFIED_RETURNING",
                         "customer_data": {"name": "Jane"}},
    }
    rec = build_sos_record(state, "sos_abc123")
    assert rec["sos_id"] == "sos_abc123"
    assert rec["reason"] == "car accident on the A4"
    assert rec["customer"]["customer_id"] == "cust_005"
    assert rec["customer"]["details"] == {"name": "Jane"}
    assert rec["status"] == "ROUTED_TO_HUMAN"
    assert rec["intent"] == "emergency"


def test_sos_record_falls_back_to_first_message():
    state = {
        "session_id": "sess-2",
        "first_message": "I crashed my car help",
        "classification": {"intent": "emergency", "sub_intent": ""},
        "verification": {"customer_id": None, "verification_level": "EMERGENCY_BYPASS",
                         "customer_data": {}},
    }
    rec = build_sos_record(state, "sos_def456")
    assert rec["reason"] == "I crashed my car help"
    assert rec["customer"]["customer_id"] is None


def test_sos_record_defaults_when_nothing_known():
    rec = build_sos_record({}, "sos_zzz")
    assert rec["reason"] == "Unspecified emergency"
    assert rec["intent"] == "emergency"


# --- persistence path & message --------------------------------------------

class _FakeBlob:
    def __init__(self, name, sink):
        self.name = name
        self._sink = sink

    def upload_from_string(self, data):
        self._sink["name"] = self.name
        self._sink["data"] = data


class _FakeBucket:
    def __init__(self, sink):
        self._sink = sink

    def blob(self, name):
        return _FakeBlob(name, self._sink)


def test_log_sos_interaction_writes_to_dedicated_bucket(monkeypatch):
    client = GCSClient(bucket_name="test-bucket")
    captured = {}
    monkeypatch.setattr(client, "get_or_create_bucket",
                        lambda name, *a, **k: captured.update(bucket=name) or _FakeBucket(captured))
    ok = client.log_sos_interaction({"sos_id": "sos_abc123", "reason": "x"}, bucket_name="sos-test")
    assert ok is True
    assert captured["bucket"] == "sos-test"
    assert captured["name"] == "sos_abc123.json"


def test_log_sos_interaction_rejects_unsafe_id(monkeypatch):
    client = GCSClient(bucket_name="test-bucket")
    called = {"hit": False}
    monkeypatch.setattr(client, "get_or_create_bucket",
                        lambda *a, **k: called.update(hit=True) or _FakeBucket({}))
    ok = client.log_sos_interaction({"sos_id": "../escape", "reason": "x"})
    assert ok is False
    assert called["hit"] is False


def test_sos_message_includes_reference_and_112():
    msg = SOS_MESSAGE.format(sos_id="sos_abc123")
    assert "sos_abc123" in msg
    assert "112" in msg
