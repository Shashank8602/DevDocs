# 06 — Chat Answer Streaming Sequence

Happy path and no-context path. Source: Plan → Backend Behaviour §6–7; Phases 7–8.

```mermaid
sequenceDiagram
    autonumber
    actor U as User
    participant FE as Frontend
    participant API as FastAPI
    participant DB as PostgreSQL
    participant R as Retrieval pipeline
    participant CB as Context builder
    participant LLM as Groq chat

    U->>FE: Type question (non-empty, ≤ 8,000 chars)
    FE->>API: POST /projects/{pid}/conversations<br/>or /conversations/{id}/messages
    opt first message of a new chat
        API->>DB: Create conversation (heuristic title, no LLM call)
    end
    API->>DB: Save user message (gen_state = pending)
    API-->>FE: SSE: conversation
    FE->>FE: Show rotating loading phrases

    API->>R: retrieve(project_id, question)
    alt NoRelevantContext
        R-->>API: no chunks
        API-->>FE: SSE: no_context
        API->>DB: Save fallback assistant message, gen_state = answered
        Note over API,LLM: LLM is never called
    else context found
        R-->>API: final chunks
        API->>CB: system prompt + summary + recent msgs + chunks + question
        CB-->>API: messages
        API->>LLM: chat.completions (stream=true)
        loop tokens
            LLM-->>API: delta
            API-->>FE: SSE: token
        end
        API->>API: Build citations from chunks
        API-->>FE: SSE: sources
        API-->>FE: SSE: done
        API->>DB: Save assistant message<br/>(content + frozen sources_json)
        API->>DB: gen_state = answered
        API-)API: Maybe trigger summarization (diagram 09)
    end
```

**Rules**
- The user message is persisted before any retrieval or LLM work.
- The assistant message is persisted only after `done`.
- Event order: `conversation` → `token`* → `sources` → `done` | `no_context` | `error`.
