# 13 — Frontend Structure

Source: Plan → Frontend and Repository Layout; visual rules in [Frontend-Design.md](../Frontend-Design.md). Updated 2026-11-08.

## Routing and layout

```mermaid
flowchart TD
    App["App (react-router)"] --> Splash["Splash<br/>logo ~0.9 s, once per session"]
    App --> Gate{"AuthContext phase"}
    Gate -- "setup / signed-out" --> LP["LoginPage<br/>first-run admin or sign in"]
    Gate -- "signed-in" --> L["Layout"]

    L --> H["Header<br/>breadcrumbs, ingestion indicator,<br/>server status, theme toggle"]
    H --> AM["Account menu<br/>name + role, Settings (admin), Sign out"]
    AM --> SP["SettingsPage (admin)<br/>section index: General | LLM | Embeddings |<br/>Reranker | Advanced RAG"]
    L --> PP["ProjectsPage<br/>cards with initials, '⋯' menu: edit / delete"]
    L --> PW["ProjectWorkspace<br/>sidebar folds to an icon rail<br/>(folded by default on Chat)"]

    PW --> DB["Dashboard<br/>counts, languages, stack, README,<br/>entry points, largest files, env vars"]
    PW --> MP["Map<br/>import graph + impact panel"]
    PW --> FT["Files"]
    PW --> SR["Search<br/>text search + symbols"]
    PW --> CH["Chat"]
    PW --> AC["Access (admin)<br/>People, By folder, By person,<br/>Matrix, Audit log"]

    FT --> Tree["Tree with file icons + search (2+ chars)<br/>drag-and-drop upload"]
    FT --> Badge["Ready dot / status badges<br/>failed popover: Retry / Replace"]

    CH --> Side["Conversation list: New Chat, switch, delete"]
    CH --> Msgs["Messages<br/>react-markdown + highlight.js"]
    CH --> Comp["Composer<br/>validate, Stop while generating"]
    CH --> Src["Numbered source cards"]

    Src --> FV
    Tree --> FV
    SR --> FV
    MP --> FV
    FV["FloatingFileViewer<br/>breadcrumb header, cited-line chips"] --> Code["CodeRenderer<br/>line numbers + tinted cited lines"]
    Code --> Ask["SelectionAsk / SelectionAnswer<br/>select lines and ask"]
    FV --> Md["MarkdownRenderer"]
    FV --> Pdf["PdfRenderer (pdf.js)"]
    FV --> Docx["DocxRenderer (mammoth)"]
    FV --> Tbl["TableRenderer (papaparse)"]
    FV --> Txt["TextRenderer"]
```

## Data flow

```mermaid
flowchart LR
    Comp["Components"] --> TQ["TanStack Query<br/>(REST cache)"]
    Comp --> SSE["SSE reader<br/>(chat streaming)"]
    TQ --> Client["Typed API client"]
    SSE --> Client
    Client --> BE["FastAPI"]
```

## Chat tab states

```mermaid
stateDiagram-v2
    [*] --> Blank : New Chat (temporary, not persisted)
    Blank --> Generating : first message sent
    Generating --> Idle : done
    Generating --> Stopped : Stop
    Generating --> Errored : error
    Stopped --> Generating : Retry
    Errored --> Generating : Retry
    Stopped --> Idle : Cancel
    Errored --> Idle : Cancel
    Idle --> Generating : new message
    [*] --> Disabled : no ready files / needs_reindex
    Disabled --> Blank : files become ready
```
