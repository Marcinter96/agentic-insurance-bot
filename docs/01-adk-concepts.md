# 01 — ADK concepts from first principles

This document explains the Google ADK (Agent Development Kit) 2.2.0 primitives this project relies on. You don't need to know ADK already; by the end you'll understand exactly what each building block does and, crucially, **how a workflow pauses and resumes for a human** — the single most important behaviour in this app.

---

## 1. The two ways to build with ADK

ADK gives you two broad styles:

| Style | What it is | When to use |
|-------|-----------|-------------|
| **`LlmAgent`** (probabilistic) | An LLM that decides what to do, which tools to call, and which sub-agents to hand off to. Routing emerges from the model. | Open-ended assistants where flexible, model-driven behaviour is desired. |
| **`Workflow` + `@node`** (deterministic) | A graph you define. Each node is a step; edges define the routing. *You* decide the control flow; the LLM is used only inside nodes where you ask for it. | Regulated / auditable flows where every decision must be predictable and testable. |

This project uses the **`Workflow` + `@node`** style. We never let the model decide routing — intent classification feeds a deterministic rulebook.

---

## 2. `Workflow` and `@node`

A `Workflow` is a directed graph of **nodes** connected by **edges**.

```python
from google.adk.workflow import Workflow, node, Edge, START

@node(name="step_one")
def step_one(ctx):
    ...

root_agent = Workflow(
    name="my_flow",
    edges=[
        (START, step_one),
        Edge(from_node=step_one, to_node=step_two, route="proceed"),
    ],
)
```

- **`START`** is the implicit entry node; the first edge wires it to your first real node.
- **Plain tuple edges** `(a, b)` mean "after `a`, always go to `b`."
- **`Edge(from_node=a, to_node=b, route="x")`** is a *conditional* edge: it's taken only when node `a` sets `ctx.route = "x"`. This is how a node fans out to different successors.
- The variable named **`root_agent`** is what ADK loads (see §7).

### Node functions

A node is a Python function wrapped by `@node`. It can be:

- **sync** (`def`) or **async** (`async def`),
- a **plain function** (returns a value or `None`), or
- a **generator** (`yield`s items) — required when the node needs to pause (see §5).

The node receives a **`Context`** (`ctx`) and optionally a `node_input`:

```python
@node(name="risk_router")
def risk_router(ctx):
    ctx.route = "escalate" if unsafe(ctx.state) else "proceed"
```

---

## 3. `ctx` — the Context and shared state

Every node gets a `Context`. The pieces this project uses:

| Member | Purpose |
|--------|---------|
| `ctx.state` | A dict-like store shared across all nodes for the whole run. **Persisted across pauses.** |
| `ctx.route` | Set this to pick a conditional outgoing edge. |
| `ctx.output` | Set this to emit a message to the user as the node's output. |
| `ctx.resume_inputs` | A dict of `{interrupt_id: user_reply}` — how a paused node receives the human's answer on resume. |
| `ctx.run_id` | Identifier for the current run. |
| `await ctx.run_node(agent, input, ...)` | Dynamically run another node/agent from inside this node (see §6). |

### `ctx.state` has a deliberately small API

`ctx.state` supports `get`, `setdefault`, `update`, `[]`, `in`, and `to_dict`. It does **not** support `.pop()` or `del` — attempting them raises `'State' object has no attribute 'pop'`. To "clear" a key, set it to `None`. (We learned this the hard way; see doc 03.)

State writes are captured as a *delta* and flushed onto the events the node emits, which is how they survive a pause/resume.

---

## 4. `LlmAgent` and its three modes

An `LlmAgent` wraps a model call. When an `LlmAgent` runs **as a node** (or is dispatched via `ctx.run_node`), its `mode` decides its behaviour:

| Mode | Behaviour | Pauses? | Terminal signal |
|------|-----------|---------|-----------------|
| **`single_turn`** | One model call in, one response out. Doesn't see prior history by itself (`include_contents='none'`). | Never | Completes immediately |
| **`task`** | A goal-directed agent that *chats over multiple turns* until done. ADK auto-adds a `finish_task` tool; the agent calls it to return a structured value. | Can (via `wait_for_output`) | `finish_task` |
| **`chat`** | An open-ended coordinator that holds a full conversation and delegates sub-tasks to other agents. | Can | None (open-ended) |

### `output_schema` — forcing structured answers

A `single_turn` agent with `output_schema=SomePydanticModel` returns a **dict** matching that schema (ADK validates the model's JSON for you). This turns the LLM into a reliable structured-decision function:

```python
class Decision(BaseModel):
    action: Literal["ask", "done"]
    question: str = ""
    intent: str = ""

brain = LlmAgent(name="brain", model=MODEL, mode="single_turn", output_schema=Decision)
result = await ctx.run_node(brain, transcript)   # -> {"action": "ask", "question": "..."}
```

This is exactly how our classifier and identifier brains work. See doc 03 for *why* we use `single_turn` here instead of `task`.

---

## 5. `RequestInput` — the only true pause

To wait for a human, a **generator node** `yield`s a `RequestInput`:

```python
from google.adk.workflow._function_node import RequestInput

@node(name="confirm", rerun_on_resume=True)
def confirm(ctx):
    reply = ctx.resume_inputs.get("confirm_1")
    if reply is None:
        yield RequestInput(interrupt_id="confirm_1", message="Proceed? (yes/no)")
        return                                  # pause here
    if reply.lower() == "yes":
        ...                                     # resumed with the answer
```

What happens mechanically:

1. The node yields a `RequestInput` and returns. The Workflow **suspends**; the `message` is surfaced to the user.
2. The user replies. ADK records it under the matching `interrupt_id`.
3. The node **runs again from the top** (because `rerun_on_resume=True`), and this time `ctx.resume_inputs["confirm_1"]` holds the reply.

**Two rules that matter:**

- A node that yields `RequestInput` **must** be declared `@node(..., rerun_on_resume=True)`, otherwise it won't be re-entered with the answer.
- Because the node re-runs from the top, its logic must be **idempotent** — re-running with the same inputs must not double-count. (We achieve this by reconstructing all conversation state from `ctx.state` + `ctx.resume_inputs` rather than mutating counters in place.)

`RequestInput` is the **only** mechanism that reliably pauses a Workflow. Everything else (task-mode agents "waiting", etc.) ultimately depends on the framework translating an interrupt into this same suspend/resume cycle.

---

## 6. `ctx.run_node` — running an agent from inside a node

A node can dynamically run another agent and await its result:

```python
decision = await ctx.run_node(brain, transcript, run_id="brain_t0")
```

Key facts (from the ADK source, `agents/context.py`):

- The calling node **must** be `rerun_on_resume=True` (ADK raises a `ValueError` otherwise) — because the child might interrupt, and on resume the parent is re-run to collect the child's result.
- For a `single_turn` child, `run_node` returns the child's output (the `output_schema` dict for us) and never pauses.
- For a `task`/`chat` child, ADK sets `wait_for_output=True`. If you pass `raise_on_wait=True` and the child produces no output yet (it's waiting on the user), ADK raises an internal `NodeInterruptedError` so the parent is recorded as *waiting* rather than falsely *completed*.
- Pass an explicit `run_id` (containing non-numeric characters) to keep each call distinct and idempotent across resumes.

We use `ctx.run_node` to call our `single_turn` brains once per conversation turn.

---

## 7. How `adk web` loads the app

`adk web insurance_bot` looks for a module named **`agent.py`** in the package and loads the variable **`root_agent`** from it. It does **not** import `workflow.py` by filename. That's why `insurance_bot/agent.py` simply re-exports:

```python
# insurance_bot/agent.py
from insurance_bot.core.config import ADK_BIDI
if ADK_BIDI:
    from .live_agent import root_agent      # voice mode
else:
    from .workflow import root_agent         # text mode (the Workflow)
__all__ = ["root_agent"]
```

A consequence: **anything imported at module load must not require credentials.** ADK imports the package at server startup before any GCP auth context is guaranteed. That's why `gcs_client.py` creates `storage.Client()` lazily (on first read), not at import time.

---

## 8. Putting it together: the pause/resume loop

The pattern this whole app is built around — a node that runs a multi-turn conversation while only the *workflow* pauses:

```python
@node(name="ask_until_known", rerun_on_resume=True)
async def ask_until_known(ctx, node_input):
    # 1. Rebuild the transcript deterministically from saved Qs + replies.
    transcript, turn = rebuild(ctx)

    # 2. Ask the one-shot brain for THIS turn's decision (never pauses).
    decision = await ctx.run_node(brain, transcript, run_id=f"brain_t{turn}")

    # 3. If the brain is done, write the result to state and return.
    if decision["action"] == "done":
        ctx.state["result"] = decision
        return

    # 4. Otherwise store the question and PAUSE for the human.
    ctx.state[f"q_{turn}"] = decision["question"]
    yield RequestInput(interrupt_id=f"q_{turn}", message=decision["question"])
```

Read doc 02 to see how the classifier and identifier nodes implement exactly this.
