# 03 — Design decisions & trade-offs

This is the "why" document. Several of these choices were reached by hitting real bugs; the history is included because it explains the design better than the conclusion alone.

---

## 1. Deterministic Workflow, not probabilistic delegation

**Choice:** Use `Workflow + @node` with explicit edges. Intent classification feeds a plain-Python rulebook (`decide_route`) that chooses the path.

**Why:** This is a regulated domain. Every routing and authorization decision must be predictable, testable, and auditable. A probabilistic `LlmAgent` that "decides" whether to escalate could be prompt-injected or simply wrong, and its decisions are hard to unit-test. Determinism is the whole point.

**Trade-off:** More wiring than a single chat agent. Worth it for safety.

---

## 2. The Workflow owns the conversation loop; agents are one-shot brains

This is the central decision, and it was forced by a bug.

### What we tried first
Make the classifier (and identifier) a **`mode='task'` LlmAgent** that chats with the caller across turns and calls a `classify` tool when done, dispatched from a node via `ctx.run_node(agent, ..., raise_on_wait=True)`.

### How it failed
Two distinct symptoms:

1. **It raced ahead.** Without `raise_on_wait=True`, `ctx.run_node` returned the moment the agent produced a question, so the workflow continued to verification *before the caller answered* — landing on `UNVERIFIED` and escalating immediately.
2. **It crashed on resume.** With the task agent dispatched directly as a workflow node and spanning a pause/resume, ADK threw:
   ```
   No function call event found for function response ids: {classifier_1}
   ```
   Root cause (from `flows/llm_flows/contents.py`): task mode pairs a synthesized *function-response* with an originating *function-call*. A task agent dispatched **directly as a top-level node** has no originating call (its first turn comes from `user_content`), so across a HITL pause the resumed run finds an orphan response and blows up. Task mode is built to be invoked **by a coordinator that emits the anchoring call** — not as a standalone paused node.

We were advised to "switch to `mode='chat'`." Chat mode would dodge *that* crash, but it has **no terminal signal** — as a node it would pause on every text turn and never cleanly "complete and advance," so it wasn't a real fix either.

### What we did instead
Invert the responsibility:

- The **Workflow node owns the loop** (ask → `RequestInput` → resume → repeat) — using the one mechanism the engine is actually built to pause on.
- The **agent becomes a `single_turn` "brain"** with an `output_schema`: given the transcript, it returns exactly one decision (`ask` / `done`, or `ask` / `lookup` / `give_up`). It never loops, never waits, and never touches `finish_task` — so the entire fragile task-mode bookkeeping disappears.

**Result:** the `classifier_1` class of crash is gone, the conversation pauses reliably, and the brains are too simple to get into a broken state.

> Lesson: don't use a multi-turn *agent abstraction* as a *paused workflow node*. Use `RequestInput` for the pause and a one-shot brain for the judgement.

---

## 3. Idempotent loops via transcript replay

**Choice:** `rerun_on_resume=True` nodes never mutate counters in place. On every (re-)entry they **rebuild** the conversation from `ctx.state` (stored questions) + `ctx.resume_inputs` (caller replies) using `replay_transcript()`, and use per-turn `run_id`s (`clf_brain_t{n}`, `idf_brain_t{n}_a{a}`).

**Why:** A node that yields `RequestInput` is re-run from the top on resume. Any "append to history" or "increment count" done imperatively would double-count. Reconstructing from the source of truth makes re-runs safe and the brain gets called exactly once per real turn.

---

## 4. Replay short-circuit guards

**Choice:** At the top of `intent_classifier` and `identification_node`, return immediately if the result (`classification` / `verification`) is already in state.

**Why:** `adk web` replays the **entire workflow from START on every user message**. Without the guard, the classifier re-called its LLM on every later turn even after the intent was settled — roughly doubling the LLM round-trips and latency. The guard makes settled nodes free.

---

## 5. `single_turn` + `output_schema` for the brains

**Choice:** Brains are `single_turn` agents with a Pydantic `output_schema`.

**Why:** We need a *structured* decision (`{action, question, intent, ...}`), not free text, and we need it without `finish_task`. `output_schema` makes ADK validate the model's JSON and hand back a dict — a reliable "LLM as a typed function." `single_turn` guarantees one call, no waiting.

**Trade-off:** `single_turn` sets `include_contents='none'`, so the brain doesn't see history automatically — we pass the full transcript as the input each turn. That's a few hundred tokens; cheap and explicit.

---

## 6. Risk derived from intent, not from the model

**Choice:** `risk_level` is a fixed map from intent (`offer→LOW`, `policy_question→MEDIUM`, `claim→HIGH`, `emergency→HIGH`), computed in code.

**Why:** Risk gating must be deterministic. Letting the LLM "rate the risk" would make the guardrail probabilistic. Easy to change the policy in one place.

---

## 7. Performance: disable thinking, smaller model, non-blocking I/O

Three latency fixes, in order of impact:

1. **Disable Gemini 2.5 "thinking"** on the brains (`thinking_budget=0` via `generate_content_config`). For "pick one of five intents" or "extract a phone number," the reasoning pass is wasted time. This was the single biggest win (per-call time dropped from ~2–3s).
2. **Smaller brain model.** `BRAIN_MODEL` (default `LLM_MODEL`) can be set to `gemini-2.5-flash-lite` — the right tool for classification/extraction.
3. **Non-blocking GCS lookup.** `guardrails.verify_customer` does synchronous GCS reads; calling it directly in an async node froze the event loop. It now runs via `asyncio.to_thread`.

A secondary, unavoidable cost: switching agents between turns changes the system instruction, which breaks Gemini's implicit context cache (the "System Instruction Performance Analysis" warning in the ADK UI). That's inherent to any multi-agent design and is minor next to the thinking cost.

---

## 8. Lazy GCS / GenAI clients

**Choice:** Never construct `storage.Client()` (or any credentialed client) at module import time.

**Why:** `adk web` imports the package at startup, before a GCP auth context is guaranteed. Constructing a client at import crashed the server with a credentials error. `gcs_client.py` builds the client lazily on first read via a `bucket` property.

---

## 9. Specialist ownership guardrails

**Choice:** Every specialist tool re-checks that the requested resource belongs to the verified `customer_id` before returning data.

**Why:** Defence in depth. Even if routing or the model misbehaved, a customer can never retrieve another customer's policy, invoice, or claim — the check is in the data-access function, not the prompt.

---

## 10. Fully sequential (removed the parallel fan-out)

**Choice:** Verification runs after classification, sequentially. An earlier design fanned out verification and audit logging in parallel with a `JoinNode`.

**Why:** The parallelism saved nothing meaningful (the steps are fast) and added real complexity to the graph and to reasoning about state. A straight line — classify → identify → route — is far easier to read, debug, and explain. Audit logging is now a single inline call once both results exist.
