"""
Shared conversation transcript for the specialist agents.

A Workflow root starts a fresh invocation on every user message, so a specialist
LlmAgent would otherwise see only the latest utterance and keep re-greeting. We
keep a running transcript in `state["history"]` and inject it into each
specialist's instruction via `with_history(...)`, so every agent has the full
context of what was said before.
"""

from __future__ import annotations

_MAX_LINES = 40


def _append(state, line: str) -> None:
    line = (line or "").strip()
    if not line:
        return
    hist = list(state.get("history") or [])
    if hist and hist[-1] == line:  # drop accidental consecutive duplicates
        return
    hist.append(line)
    state["history"] = hist[-_MAX_LINES:]  # reassign so ADK State marks it dirty


def record_user(state, text: str) -> None:
    _append(state, f"Customer: {text}")


def record_assistant(state, text: str) -> None:
    _append(state, f"Assistant: {text}")


def history_text(state) -> str:
    hist = state.get("history") or []
    if not hist:
        return ""
    return ("\n\n--- Conversation so far (most recent last) ---\n"
            + "\n".join(hist)
            + "\n--- end of conversation so far ---\n"
            "Continue this conversation naturally. Do NOT greet or re-introduce "
            "yourself again, and do not repeat questions already answered above.")


def with_history(base_instruction: str):
    """Return an ADK instruction provider that appends the running transcript."""
    def provider(ctx) -> str:
        return base_instruction + history_text(getattr(ctx, "state", {}))
    return provider
