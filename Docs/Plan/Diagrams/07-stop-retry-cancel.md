# 07 — Stop, Retry & Cancel

Interruption and failure handling. Source: Plan → Backend Behaviour §6; Phase 10.

## User-message `gen_state` machine

```mermaid
stateDiagram-v2
    [*] --> pending : user message saved
    pending --> answered : done / no_context
    pending --> stopped : client disconnect (Stop)
    pending --> failed : error after retries
    stopped --> pending : Retry (fresh RAG + LLM)
    failed --> pending : Retry
    stopped --> dismissed : Cancel
    failed --> dismissed : Cancel
    stopped --> [*] : new query sent (abandoned)
    answered --> [*]
    dismissed --> [*]
```

## Sequence

```mermaid
sequenceDiagram
    autonumber
    actor U as User
    participant FE as Frontend
    participant API as FastAPI
    participant DB as PostgreSQL
    participant LLM as Groq chat

    Note over U,LLM: Stop
    FE->>API: (streaming in progress)
    U->>FE: Click Stop
    FE->>FE: Abort fetch, keep partial text visible
    API->>API: Detect disconnect → cancel LLM task
    API->>LLM: Cancel stream
    API->>DB: user msg gen_state = stopped<br/>(no assistant message saved)
    FE->>U: Show [Stopped] [Retry] [Cancel]

    Note over U,LLM: Refresh while stopped
    U->>FE: Reload page
    FE->>API: Load conversation
    API-->>FE: Messages (partial answer gone)
    FE->>FE: Rebuild buttons from gen_state = stopped

    Note over U,LLM: Retry
    U->>FE: Click Retry
    FE->>API: POST /messages/{user_msg_id}/retry
    API->>DB: gen_state = pending (no duplicate user message)
    API->>API: Fresh retrieval + LLM, stream as in diagram 06

    Note over U,LLM: Cancel
    U->>FE: Click Cancel
    FE->>API: POST /messages/{id}/dismiss
    API->>DB: gen_state = dismissed (user message stays)

    Note over U,LLM: Failure
    API->>LLM: Call
    LLM--xAPI: Error before first token
    API->>LLM: Limited automatic retries
    LLM--xAPI: Still failing
    API-->>FE: SSE: error (generic)
    API->>DB: gen_state = failed
    FE->>U: "Couldn't generate a response. Please try again." + Retry
```

**Notes**
- Failure after the first token produces `error` directly, with no silent retry.
- An inactivity timeout and a max-output-token ceiling also apply.
- Provider error text never reaches the UI.
