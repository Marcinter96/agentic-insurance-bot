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
#
# ADK loads `app` first (App with app-wide plugins), then `root_agent`.
# We expose an `App` so the GuardrailPlugin (input + output guardrails) applies
# to whichever root agent is active — text Workflow or voice agent.
from google.adk.apps import App

from insurance_bot.core.config import ADK_BIDI
from insurance_bot.core.guardrail_plugin import GuardrailPlugin

if ADK_BIDI:
    from .live_agent import root_agent
else:
    from .workflow import root_agent

app = App(
    name="insurance_bot",
    root_agent=root_agent,
    plugins=[GuardrailPlugin()],
)

__all__ = ["app", "root_agent"]
