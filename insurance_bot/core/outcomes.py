"""
Conversation outcome taxonomy.

Every conversation ends in exactly one resolution, stored in
`ctx.state["resolution"]` and surfaced in the graph as a distinct terminal node:

  • RESOLVED       — the bot achieved the customer's goal on its own (policy
                     question answered, sale closed, claim fully captured).
  • HUMAN_HANDOFF  — the bot routed the customer to a person (couldn't finish,
                     emergency, or verification failed).
  • BLOCKED        — a safety guardrail refused the request.

These strings double as workflow routes, so they must match the Edge routes in
workflow.py.
"""

RESOLVED = "RESOLVED"
HUMAN_HANDOFF = "HUMAN_HANDOFF"
BLOCKED = "BLOCKED"
