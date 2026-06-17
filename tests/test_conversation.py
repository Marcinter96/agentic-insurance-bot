"""Tests for the shared conversation transcript injected into specialists."""

from insurance_bot.core import conversation as conv


def test_records_and_dedups_consecutive():
    st = {}
    conv.record_user(st, "I need a car offer")
    conv.record_assistant(st, "We have AutoGuard!")
    conv.record_user(st, "yes please")
    conv.record_user(st, "yes please")  # consecutive dup ignored
    assert st["history"] == [
        "Customer: I need a car offer",
        "Assistant: We have AutoGuard!",
        "Customer: yes please",
    ]


def test_blank_lines_ignored():
    st = {}
    conv.record_user(st, "   ")
    assert st.get("history") in (None, [])


def test_history_text_has_transcript_and_no_regreet_hint():
    st = {}
    conv.record_user(st, "hi")
    txt = conv.history_text(st)
    assert "Customer: hi" in txt
    assert "Do NOT greet" in txt


def test_with_history_provider_appends():
    st = {}
    conv.record_user(st, "hello")
    provider = conv.with_history("BASE INSTRUCTION")

    class _Ctx:
        state = st

    out = provider(_Ctx())
    assert out.startswith("BASE INSTRUCTION")
    assert "Customer: hello" in out


def test_empty_history_leaves_instruction_unchanged():
    provider = conv.with_history("BASE")

    class _Ctx:
        state = {}

    assert provider(_Ctx()) == "BASE"
