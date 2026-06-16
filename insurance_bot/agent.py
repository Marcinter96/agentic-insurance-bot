# agent.py — ADK entry point.
#
# Run `adk web` from the project root and select the `insurance_bot` agent.
# ADK loads `root_agent` from this package.
#
# Two modes, selected by the ADK_BIDI environment variable:
#
#   ADK_BIDI unset / false  -> text mode: the guardrailed deterministic
#                              Workflow (workflow.py). This is the default and
#                              preserves the existing `adk web` text behavior.
#
#   ADK_BIDI=1              -> live/voice mode: a Live-capable LlmAgent
#                              (live_agent.py) that `adk web` can drive via
#                              run_live() with AUDIO. It keeps the same
#                              verification / risk / audit guardrails, exposed
#                              as tools.
#
# Example:
#   ADK_BIDI=1 adk web insurance_bot --port 8001
# Guardrails are enforced INSIDE the graph (input_guardrail node) and via
# agent-level after_model_callback on the specialists — NOT via an App plugin.
# For a Workflow (BaseNode) root, ADK's node runtime does not honor a plugin's
# before_run early-exit, so a plugin cannot block input; a node can.
from insurance_bot.core.config import ADK_BIDI

if ADK_BIDI:
    from .live_agent import root_agent
else:
    from .workflow import root_agent

__all__ = ["root_agent"]
