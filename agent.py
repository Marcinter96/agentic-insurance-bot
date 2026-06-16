# agent.py — ADK entry point.
#
# `adk web agentic-insurance-bot` loads `root_agent` from THIS module (agent.py),
# never from workflow.py directly. So we re-export the guardrailed Workflow's
# root_agent here. All the real logic lives in workflow.py.
from workflow import root_agent

__all__ = ["root_agent"]
