# Export `app` (App with the GuardrailPlugin) AND `root_agent`. The ADK loader
# checks the package for `app` first; if only `root_agent` is exported here it
# returns that and never discovers the App, so the plugins are silently dropped.
from .agent import app, root_agent

__all__ = ["app", "root_agent"]
