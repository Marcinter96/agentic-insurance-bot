"""
Direct structured LLM calls for the deterministic "brains".

The classifier and identifier brains used to run via ``ctx.run_node`` (as
single_turn LlmAgents dispatched as dynamic child nodes). That made the loop
nodes depend on ADK's dynamic-node scheduler, whose state did not restore
cleanly when resuming the FIRST RequestInput interrupt — the node silently
failed to re-run (the "freeze after answering").

Calling the model directly removes the dynamic child entirely: the loop nodes
become plain ``rerun_on_resume`` generators that yield ``RequestInput`` — the
simplest, most-supported pause pattern. (This is the same approach the safety
brain already uses.)
"""

from __future__ import annotations

import json
import logging

from insurance_bot.core.config import BRAIN_MODEL

logger = logging.getLogger(__name__)

_client = None


def _genai_client():
    global _client
    if _client is None:
        from google import genai
        from insurance_bot.core.config import USE_VERTEX_AI, GCP_PROJECT, GCP_LOCATION
        if USE_VERTEX_AI:
            _client = genai.Client(vertexai=True, project=GCP_PROJECT, location=GCP_LOCATION)
        else:
            _client = genai.Client()
    return _client


def structured_decision(system_instruction: str, user_text: str, schema) -> dict:
    """One structured LLM call → dict. Thinking disabled for low latency.

    Returns {} on any failure so callers can apply a safe default.
    Synchronous (the GenAI SDK is sync); call via ``asyncio.to_thread`` from
    async workflow nodes so the event loop isn't blocked.
    """
    from google.genai import types

    try:
        resp = _genai_client().models.generate_content(
            model=BRAIN_MODEL,
            contents=user_text or " ",
            config=types.GenerateContentConfig(
                system_instruction=system_instruction,
                response_mime_type="application/json",
                response_schema=schema,
                thinking_config=types.ThinkingConfig(thinking_budget=0),
            ),
        )
        return json.loads(resp.text)
    except Exception as e:  # never let a brain call crash the workflow
        logger.error("structured_decision failed: %s", e)
        return {}
