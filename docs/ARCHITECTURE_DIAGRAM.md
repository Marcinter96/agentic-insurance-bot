# 🏛️ Full Architecture

> Renders on GitHub automatically. To export an image for slides:
> paste into <https://mermaid.live> → **Export PNG/SVG**, or run
> `mmdc -i docs/ARCHITECTURE_DIAGRAM.md -o docs/architecture.png` (`npm i -g @mermaid-js/mermaid-cli`).

## System overview

```mermaid
flowchart TB
    user([👤 Customer · adk web / voice]):::user

    subgraph INTAKE["🟦 INTAKE  (single node · owns pause & state)"]
        direction TB
        ig["🛡️ Input Guardrail<br/>rules → safety brain"]:::guard
        clf["🧠 Classifier brain<br/>intent + sub-intent"]:::brain
        idf["🧠 Identifier brain<br/>collect identifiers"]:::brain
        ig --> clf --> idf
    end

    subgraph ROUTING["⚙️ Deterministic routing  (no LLM)"]
        direction TB
        rr{{risk_router}}:::route
        sr{{specialist_router}}:::route
        rr -->|proceed| sr
    end

    subgraph SPECIALISTS["🤝 Specialist agents  (Gemini 2.5 Flash · tools + output guardrail)"]
        direction TB
        policy["📄 policy_agent<br/>7 read tools + route/close"]:::agent
        claims["📝 claims_agent<br/>5-question stateful intake"]:::agent
        offers["💰 offers_agent<br/>catalog · quote · sale"]:::agent
        sos["🚨 sos_handler<br/>deterministic · no tools"]:::sos
    end

    subgraph ENDS["🏁 Outcome split"]
        direction TB
        oroute{{outcome_router}}:::route
        resolved(["✅ resolved_end"]):::okend
        human(["🤝 human_handoff_end"]):::humanend
        blocked(["⛔ guardrail_blocked"]):::blockend
    end

    subgraph DATA["🗄️ Google Cloud Storage"]
        direction TB
        gcs[("customer data<br/>customers · policies<br/>vehicles · invoices · claims")]:::data
        sosb[("adk-insurance-sos-mi")]:::data
        offb[("adk-insurance-offer-mi")]:::data
        clmb[("adk-insurance-claims-mi")]:::data
    end

    audit[["📊 Cloud Logging · Audit trail"]]:::audit

    %% main flow
    user --> INTAKE
    INTAKE -->|blocked| blocked
    INTAKE -->|continue| rr
    rr -->|escalate| human
    sr -->|policy_question| policy
    sr -->|claim| claims
    sr -->|offer| offers
    sr -->|emergency| sos

    policy --> oroute
    claims --> oroute
    offers --> oroute
    oroute -->|RESOLVED| resolved
    oroute -->|HUMAN_HANDOFF| human
    sos --> human

    %% data + verification
    idf -. "verify_customer (lookup + DOB)" .-> gcs
    policy -. "ownership-checked reads" .-> gcs
    claims -. "file claim" .-> clmb
    offers -. "catalog + leads" .-> offb
    sos -. "SOS record" .-> sosb

    %% everything is audited
    INTAKE -.-> audit
    policy -.-> audit
    claims -.-> audit
    offers -.-> audit
    sos -.-> audit
    resolved -.-> audit
    human -.-> audit
    blocked -.-> audit

    classDef user fill:#1f2937,stroke:#7dcfff,color:#e6e9ef;
    classDef guard fill:#3b1f2b,stroke:#f7768e,color:#fde;
    classDef brain fill:#2a1f3d,stroke:#bb9af7,color:#eee;
    classDef route fill:#1a2b3d,stroke:#7aa2f7,color:#cde;
    classDef agent fill:#13283a,stroke:#7dcfff,color:#dff;
    classDef sos fill:#3b1f1f,stroke:#ff9e64,color:#fed;
    classDef okend fill:#16301f,stroke:#9ece6a,color:#dfe;
    classDef humanend fill:#2d2a16,stroke:#e0af68,color:#fed;
    classDef blockend fill:#301616,stroke:#f7768e,color:#fde;
    classDef data fill:#101a2b,stroke:#7aa2f7,color:#cde;
    classDef audit fill:#102018,stroke:#9ece6a,color:#dfe;
```

## The one idea behind it all

```mermaid
flowchart LR
    wf["⚙️ Workflow<br/><b>owns</b> routing · pausing · state · guardrails"]:::w
    llm["🧠 LLM<br/>one-shot decision function<br/>(per turn, never the router)"]:::l
    wf -->|"calls"| llm
    llm -->|"returns a structured decision"| wf
    classDef w fill:#1a2b3d,stroke:#7aa2f7,color:#cde,font-size:18px;
    classDef l fill:#2a1f3d,stroke:#bb9af7,color:#eee,font-size:18px;
```

**The Workflow is the boss; the LLM is a calculator it calls.**

## Defense-in-depth (guardrail layers)

| # | Layer | Mechanism |
|---|---|---|
| 1 | Input guardrail | rules (regex) → LLM safety brain only for gray area — **blocks** before any routing |
| 2 | Identity | `verify_customer` GCS lookup + DOB cross-check → verification level |
| 3 | Authorization | verification-level → allowed-actions matrix (deterministic) |
| 4 | Per-tool ownership | every tool checks the record's own `customer_id` |
| 5 | Output guardrail | `after_model_callback` scrubs secrets / card numbers |
| 6 | Audit | every node + tool → Cloud Logging |
