# 09 — Conversation Summarization & Context Assembly

Keeps prompts small as chats grow. Source: Plan → Backend Behaviour §6; Phase 9.

## Summarization trigger

```mermaid
sequenceDiagram
    autonumber
    participant API as FastAPI
    participant DB as PostgreSQL
    participant S as Summarizer (background task)
    participant LLM as Groq chat

    API->>DB: Save assistant answer
    API->>DB: Update recent_token_estimate (tiktoken)
    alt estimate > internal threshold
        API-)S: Start background task
        S->>DB: Load summary + messages after checkpoint
        S->>S: Select older messages to fold in
        S->>LLM: Summarize (previous summary + older messages)
        LLM-->>S: New summary
        S->>DB: Save summary,<br/>move summary_checkpoint_message_id forward,<br/>recompute recent_token_estimate
    else below threshold
        Note over API: Nothing to do
    end
```

## What the LLM receives

```mermaid
flowchart LR
    SP["System prompt<br/>scope guardrail, strict grounding,<br/>adaptive Markdown, label simplified code"] --> P
    SM["Conversation summary<br/>(if any)"] --> P
    RM["Recent messages<br/>after the checkpoint"] --> P
    RC["Retrieved chunks<br/>labelled with path + lines"] --> P
    Q["Current question"] --> P
    P["Final prompt<br/>(visible in dev Context Inspector)"] --> LLM["Groq chat"]
```

**Notes**
- Summarization never blocks the answer stream.
- Deleting a conversation removes its messages only; project files and chunks are untouched.
