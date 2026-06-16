# Documentation

These docs explain both **how Google ADK works** and **why this bot is built the way it is**. They are meant to be read in order, but each stands on its own.

| # | Doc | Read this if you want to understand… |
|---|-----|--------------------------------------|
| 01 | [ADK concepts](01-adk-concepts.md) | The framework primitives: `Workflow`, `FunctionNode`, `LlmAgent` modes, `RequestInput`, `ctx.run_node`, and how pause/resume actually works |
| 02 | [Architecture](02-architecture.md) | How this specific bot is wired — the node graph, shared state, and the "workflow owns the loop" pattern |
| 03 | [Design decisions](03-design-decisions.md) | Every significant choice and trade-off, including the task-mode bug that reshaped the design and the latency tuning |
| 04 | [Data & guardrails](04-data-and-guardrails.md) | The GCS data model, verification levels, the deterministic routing rulebook, and the audit trail |
| 05 | [Running & ops](05-running-and-ops.md) | Setup, environment variables, performance knobs, voice mode, deployment, and troubleshooting |

## TL;DR of the philosophy

1. **Determinism where it matters.** Routing, authorization, and risk gating are plain Python — auditable and testable, never left to an LLM's discretion.
2. **The LLM is a calculator you call once per turn.** It decides "what to ask next" or "here's the structured answer." It does not run the conversation or hold the loop.
3. **Only the Workflow pauses.** Waiting for a human is done with exactly one mechanism (`RequestInput`), which is what the Workflow engine is built to resume.
