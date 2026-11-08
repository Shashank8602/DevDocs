# DevDocs: gaps and growth ideas

Written for: the project owner, to decide what to build next. Date: 2026-11-05. Based on a read of the whole repo (backend, frontend, docs, reports), not a running session. Everything marked "verified" was seen in the code; everything marked "idea" is a proposal.

**The constraint that shapes every recommendation:** ₹0 infrastructure. Groq free tier is 8,000 tokens/minute per model; Gemini embeddings are about 1,000 calls/day. So the rule used below is: **prefer features that use data DevDocs already stores (Postgres chunks, ES index, file metadata) and need no extra LLM or embedding call.** Anything that does need an LLM call is flagged and should be user-triggered, cached in Postgres, and run once per project, never per page view.

## Start here (status as of 2026-11-05)

**Implementation update (2026-11-05, second session):** the trust fixes (G1 quota pause, G6 generated files, G7 secret scan, G8 privacy notice, G10 LICENSE and CI), phases A to D (identity, access rules and enforcement, Access UI, hardening and audit log) and the permission-aware part of phase E (dashboard, search, symbol index, starter questions, import map) are implemented and covered by tests. **Not done:** R0 (the visual redesign port), F (select-and-ask, thumbs and insights, regenerate, export, command palette) and G (tree-sitter chunking, gate calibration, GitHub import, re-sync, MCP server, CLI), phase E source sync (`acl.json`), F3 clickable identifiers in answers, vetting starter questions against the gate. See `Docs/Reports/Gaps-Implementation.md` for what was verified and the known limits.

Original status line: nothing in this file had been implemented; what existed was this analysis, a clickable design prototype, and a set of owner decisions.

| Item | Where | State |
|---|---|---|
| Gap analysis and growth ideas | sections 1 to 7 | proposals; pick from section 5 |
| **Flagship feature: permission-aware knowledge system** | section 8 | designed, all decisions confirmed, phase A is next |
| **Frontend redesign** | section 9, `Docs/Design/devdocs-redesign.html`, `Docs/Plan/Frontend-Design.md` | existing screens restyled on 2026-11-08 (tokens, account menu, folding sidebar, Dashboard/Map/Files/Chat/Settings polish); the prototype itself was not ported |
| Order of work and a first prompt for the next session | section 10 | ready |

Owner decisions that are settled (do not re-ask): admin reads everything; simple email and password sign-in (no Google/GitHub/OIDC); one shared set of AI keys managed by the admin; permissions on folders and files; access is people-first with no predefined groups; projects home page first, sidebar only inside a project; the dependency map uses no LLM; no left-edge accent bars anywhere in the UI; no hardcoded demo or scenario data in the product.

---

## 1. Why it feels simple (the diagnosis)

The product today is: upload a folder → one Overview tab with six counters and a language bar → Files tree → Chat. Everything clever (hybrid retrieval, gate, rate-limit routing, change detection) is invisible to a first-time visitor, and the visible part is a standard chatbox that looks like every other "chat with your docs" tool.

Verified observations:

| Observation | Where |
|---|---|
| Only 3 tabs: Overview, Files, Chat | `frontend/src/pages/ProjectWorkspace.tsx:12` |
| Overview is counts and a language bar; it says nothing about the *project* | `frontend/src/components/explorer/OverviewTab.tsx` |
| The chat blank state shows 4 hard-coded questions, identical for every project (including "How does authentication work?" for projects with no auth) | `frontend/src/components/chat/ChatParts.tsx:130` |
| The best "wow" tools (Retrieval Playground, ES Inspector, vector peek) are dev-only and hidden behind `DEV_MODE` | `backend/app/api/dev.py` |
| README has zero screenshots or GIFs | `README.md` (0 image references) |
| The only way in is the browser folder picker; no GitHub URL, no zip | `frontend/src/lib/dropFolder.ts`, `backend/app/api/files.py` |
| No way out: answers cannot be exported, shared or consumed by other tools | no export code anywhere in `frontend/src` or `backend/app/api` |

**The core insight:** DevDocs already knows a lot about the project (every file, every function/section name, every chunk, line ranges, languages). It just never *shows* it. Most of the ideas below are "surface what you already have".

---

## 2. Real gaps in what exists

### 2a. Correctness and robustness

| # | Gap | Impact | Cost to fix |
|---|---|---|---|
| G1 | **Daily embedding quota 429 marks remaining files `failed`** (known, in `Optimisation-Analysis.md`). A first-time user with a mid-sized repo can hit this and see a wall of red. | High: ruins the first impression | Low: keep files `pending`, pause the job, show "resumes when quota returns (about HH:MM)" |
| G2 | **Only Python gets structure-aware chunking** (`ast`). JS/TS/Java/Go/Rust/C#… are split by a generic splitter (`parsers/code.py`). Most GitHub visitors have JS/TS projects. | High for retrieval quality on the most common languages | Medium: `tree-sitter` (free, pip-installable) for function/class boundaries; fills the `section` field that BM25 already boosts |
| G3 | **Gate threshold (0.82) was tuned on one project.** Already identified as the main accuracy risk on new projects. | High: on a new project it may refuse good questions or answer bad ones | Medium: per-project calibration (spec'd in the optimisation report) |
| G4 | **No answer-quality evaluation.** Retrieval metrics exist; nothing says whether answers are faithful or citations correct. | Medium | Medium: a small checked-in set of (question, must-mention, must-cite) with a script |
| G5 | **All eval questions were written by the author for one project.** | Medium: numbers are optimistic | Low: log real questions + thumbs up/down locally (see F7) |
| G6 | **Generated and minified files are skipped only by name.** Lock files other than npm/pnpm (`yarn.lock`, `poetry.lock`, `Cargo.lock`), `*.min.js`, source maps and bundles still become chunks and burn embedding quota. | Medium: wastes the scarcest resource | Low: extend `upload_rules.json`, add a content heuristic (very long lines, `sourceMappingURL`) |
| G7 | **No secret scanning before upload.** `.env` is ignored by name, but `*.pem`, `credentials.json`, hard-coded keys in source all get sent to Gemini (embeddings) and Groq (prompts). | High for trust and for any public demo | Low: regex scan (AWS keys, `-----BEGIN PRIVATE KEY`, `ghp_…`, `sk-…`) at upload; warn and skip, show in the upload report |
| G8 | **No privacy disclosure in the UI.** Users are not told file contents leave the machine to two third parties. | Medium: honest and needed before promoting it | Low: a one-line notice on the upload dialog plus a README section |
| G9 | **Phases 7–12 have not had the per-phase proof review**, and the Phase 12 acceptance walkthrough was never run on a fresh clone (per `CLAUDE.md` and the optimisation report). | High before any public launch | Low: run the proofs in `Phases.md` once |
| G10 | **No CI, no LICENSE, no frontend tests.** 8 commits, no `.github/`. Backend has 13 test files; the frontend has none. | Medium: contributors and recruiters check for these | Low: GitHub Actions free tier, MIT license |
| G11 | **Long conversations** re-read all messages every turn and render all of them (known). | Low now | Medium |
| G12 | **JevReranker is a stub.** | Low (reranker is off by default) | Blocked on API details |
| G13 | **The file viewer chunk is 813 KB** because pdf.js and mammoth ship together (known). | Low | Low: split per renderer |
| G14 | **Conversation titles never change** and there is no rename, search or delete-all. | Low | Low |
| G15 | **No cross-conversation search** and no way to find an old answer. | Low–medium | Low (SQL `ILIKE` or Postgres full-text on `messages.content`) |

### 2b. Product gaps (what a user expects and does not get)

| # | Gap |
|---|---|
| P1 | No GitHub URL import, no `.zip` upload. Getting a repo in needs a local clone and a folder picker. |
| P2 | No "re-sync": after the code changes the user must re-upload the folder by hand (change detection exists, the trigger does not). |
| P3 | No scoping of a question to a folder or file (`@file`, "only search `backend/`"). Retrieval always searches the whole project. |
| P4 | No regenerate or edit-and-resend on a past message; Retry exists only for failed turns. |
| P5 | No thumbs up/down, so no feedback signal and no real-question log. |
| P6 | No export or share of a conversation. |
| P7 | No user-facing search. The Files tab filters by name/path only; full-text and semantic search exist only inside the dev Playground. |
| P8 | No keyboard-first UX (no Ctrl+K palette, no shortcuts list). |
| P9 | Single user, no auth, no per-document permissions. Fine locally; a blocker for any shared or hosted use. **Addressed by section 8 (permission-aware enterprise knowledge system).** |
| P10 | Dark mode exists (`ThemeToggle`), but the app was not checked for mobile/small screens in this review. |

---

## 3. Making it intuitive and attractive (all ₹0)

These are ordered by value per effort. "LLM calls" is the budget column.

### Tier 1: surface what you already know (no LLM, no embeddings)

**F1. Project Dashboard replaces the Overview tab.** Built entirely from Postgres rows already stored.
- **Tech stack detector**: read `package.json`, `requirements.txt`, `pyproject.toml`, `pom.xml`, `go.mod`, `Cargo.toml`, `docker-compose.yml` that are already ingested → show badges ("FastAPI, SQLAlchemy, React, Vite, Postgres").
- **Entry points**: files named `main.py`, `app.py`, `index.ts`, `server.js`, `manage.py`, `Program.cs`, plus `if __name__ == "__main__"` hits.
- **Largest / most complex files**, **chunk density map**, **file-type treemap** (clickable → opens in viewer).
- **TODO / FIXME / HACK list** by grepping chunk text. Developers love this and it is a pure SQL query.
- **README preview** pinned at the top if one exists.
- **Config & env vars panel**: every `os.getenv("X")`, `process.env.X`, and `.env.example` key found, with the file and line. Answers the most common onboarding question ("what do I need to set?") with no LLM.
- Cost: 0 calls. Effort: medium (one `/dashboard` endpoint plus a few components).

**F2. Dependency / import graph (the visual hook).** Parse imports per file (`ast` for Python; regex for `import … from`, `require()`, `#include`, `using`, Java `import`), store edges, render an interactive graph (`d3-force` or `reactflow`, both free). Click a node → open file; "Ask about this file" button → pre-filled question. This is the single most screenshot-able feature you can add and is what makes a tool look like a "code intelligence" product instead of a chatbot.
- Cost: 0 calls. Effort: medium. New table `file_edges` plus a migration.
- Bonus (also free): "What imports this file?" panel in the file viewer.
- **How it is built (no LLM, no API calls):**
  1. *Extract at ingest.* The worker already reads each file to chunk it; in the same pass it pulls the import statements. Python: stdlib `ast` (`Import`, `ImportFrom`, relative imports resolved from the file's package path). JS/TS: `tree-sitter` or a regex for `import … from`, `require()` and dynamic `import()`. Java, Go, C#, Rust: import/using lines. New module under `backend/app/ingestion/`, numbered per `Backend-File-Flow.md`.
  2. *Resolve to project files.* Each raw import is matched against the project's own file paths (try extensions and `index` files, read `tsconfig.json` path aliases from the ingested files). Anything that does not resolve (stdlib, pip, npm) is dropped from the graph and can feed stack detection.
  3. *Store.* New table `file_edges(project_id, src_file_id, dst_file_id, raw_import, line)` with cascade delete. Raw imports are kept so that when a file is added or removed, resolution is re-run from stored text without re-parsing. Only files whose SHA-256 changed are re-extracted (existing change detection).
  4. *Serve with permissions.* `GET /projects/{id}/graph` returns nodes only for files the user may read, and an edge only if both ends are readable, so a hidden file's existence never leaks through the graph (see section 8).
  5. *Render in the browser.* Force layout (small custom one or `d3-force`), colour by folder, node size by chunk count, click for imports and importers. Above about 300 nodes, collapse to folder level and expand on click.
  6. *More free analyses on the same table:* "what breaks if I change this" (transitive importers via a recursive SQL query), circular imports (Tarjan), unused files, most-imported files, Mermaid export for the docs.
  - Honest limits: static imports only. Dynamic loading, dependency injection and string-based routing are invisible, and monorepo aliases need a config the user may not have uploaded. Label the map "static imports". Cost: CPU only, parsing thousands of files takes seconds.

**F3. Symbol index and Go-to-definition.** The `section` column already holds function/class names. Expose a symbol list (searchable) → click → file at that line. Make identifiers in chat answers clickable when they match a known symbol. Cost: 0 calls.

**F4. Project-aware starter questions.** Replace the 4 static suggestions with questions *generated from the project's own data by templates*: "What does `parse_pdf` in `parsers/pdf.py` do?" (pick top symbols by size), "How is `DATABASE_URL` used?" (pick env vars found), "Summarise `README.md`". Pre-vetted against the gate so they never get refused. Cost: 0 calls.

**F5. User-facing Search tab (a 4th tab).** Promote the Retrieval Playground idea: instant BM25 search over content/path/section with highlighted snippets, filters (language, type, folder), results as cards with line ranges, and an "Ask about this" button on each. BM25-only needs no embedding call, so it is instant and unmetered. Optional "semantic" toggle uses 1 embedding call per search. People often prefer search to chat for "where is X"; this also makes the product useful when the LLM quota is exhausted.

**F6. Answer UX polish.** All zero-cost frontend work:
- Citations as a side panel with the file preview and highlighted lines opening *beside* the chat instead of a floating viewer (split view).
- "Copy answer as Markdown with file:line links", "Export conversation (.md)".
- Regenerate, edit-and-resend, pin/bookmark an answer.
- Follow-up chips after each answer, taken from the files cited ("Show me `worker.py` in full", "What else touches `Project`?"). Deterministic, no LLM.
- Slash commands in the input: `/files`, `/summarize <path>`, `/todo`, `/env`, `/graph` that call the free endpoints above instead of the LLM.
- `@path` mention to scope retrieval to a file or folder (adds a `path` prefix filter to the ES query).
- Keyboard: Ctrl+K command palette (jump to file, symbol, conversation, setting).
- **Select-and-ask.** Highlight lines in the file viewer or the source panel, or text inside an answer, and a floating prompt appears beside the selection with a text box and quick actions (Explain this, Why is this here?, What calls this?). The question opens in chat with the selection shown as a quoted card. Build: a `selectionchange`/`mouseup` handler on regions marked selectable, line numbers read from the selected rows, no new backend table. The request sends `{question, selection: {file_id, line_start, line_end, text}}`; the server re-checks that the user can read that file, adds the selection as a pinned chunk ahead of normal retrieval (so the answer is about those lines), and keeps the usual gate and citations. "What calls this?" is answered from the dependency graph (F2) with no model call. Zero extra quota beyond a normal question.

**F7. Thumbs up/down + local question log.** One small table. This directly fixes G5 (real questions grow the eval set) and gives you a "Insights" view: most-asked topics, questions that were refused (these are the project's documentation gaps), most-cited files. Cost: 0 calls.

### Tier 2: one cached LLM call per project, user-triggered

These each cost a single call (~2–4k tokens) the user explicitly clicks, result stored in Postgres so it is never regenerated unless files change.

**F8. "Project Tour" / auto onboarding guide.** Feed the LLM the F1 facts (stack, entry points, top symbols, dependency graph summary) rather than raw code. Output: "Start here" reading order, module-by-module overview, "how a request flows". Export as `ONBOARDING.md`. This is the shareable artifact that makes people say "I want this for my repo".

**F9. Auto-generated architecture diagram.** From the F2 import graph, deterministically group by top-level folder and emit **Mermaid** text (no LLM at all); optionally one LLM call to name and describe the groups. Render with `mermaid` (free). The repo already uses Mermaid in docs.

**F10. File explainer.** A button in the file viewer: "Explain this file" → one call with the file's chunks, cached per file hash. Because change detection already hashes files, the cache key is free.

> Budget check: Groq is 8k tokens/minute. A tour built from facts (not raw code) stays under ~3k input tokens, so it fits in one minute even on the primary model. Use the existing fallback routing.

### Tier 3: change how DevDocs is *used* (traffic channels, still ₹0)

**F11. MCP server (highest leverage for visibility).** Expose `search_project`, `get_file`, `list_symbols` over the Model Context Protocol so Claude Code, Cursor and similar tools can query a DevDocs project. The retrieval pipeline and endpoints already exist; this is a thin wrapper that returns chunks and citations *without calling the chat LLM at all* (the client's own model answers). That makes it free for you, differentiated ("grounded retrieval with a relevance gate and honest refusals"), and discoverable in MCP directories.

**F12. CLI: `devdocs ingest .` / `devdocs ask "…"`.** A small Python script over the existing REST API. Lets people try it without the browser and enables a GitHub Action ("keep the index in sync on push").

**F13. GitHub import (paste a URL).** Download `https://github.com/<o>/<r>/archive/HEAD.zip` (public repos need no token), unzip server-side, run through the same ignore rules and the same ingest path. This turns "clone, locate, drag folder" into one paste, and it is the demo hook. Add a size/file cap and show the quota estimate before starting ("about 340 chunks, about 7 embedding calls").

**F14. Re-sync button / watch mode.** Remember the source (GitHub URL + commit SHA, or local path). "Check for updates" re-downloads and runs the existing SHA-256 change detection, so only changed files cost embeddings. This makes DevDocs a living doc instead of a one-shot upload.

**F15. Static "share" export.** Export a project as a self-contained static site (HTML+JSON): file tree, dependency graph, symbol index, dashboard, plus *recorded* Q&A pairs. Host it on GitHub Pages for free. No backend, no keys, no cost per visitor. This solves "how do I let people try it" without a server (see section 4).

**F16. VS Code extension (later).** Sidebar that calls the local API: ask about the selected code, jump to cited lines. Distribution through the VS Code Marketplace is free and is a real traffic source. Only after F11/F12, since it reuses the same API.

---

## 4. The "traffic" question, honestly

You cannot run a public hosted multi-user demo at ₹0 with the current architecture: Elasticsearch wants about 3 GB RAM, and the free LLM/embedding quotas would be exhausted by a handful of visitors. The CLAUDE.md stack decision (Postgres + ES, no Chroma) also rules out a lightweight embedded store. So the traffic strategy should avoid hosting a live backend:

| Channel | Cost | What to do |
|---|---|---|
| **README that sells** | ₹0 | Add a 20-second GIF (upload → dashboard → graph → cited answer), 3–4 screenshots, a "Why not just ChatGPT?" table (grounded, refuses off-topic, exact line citations, runs locally, your keys), and a one-line `docker compose up` quick start. The current README is accurate but text-only. |
| **Recorded demo site (F15)** | ₹0 on GitHub Pages | A static, read-only walkthrough of the bundled `samples/ecommerce-demo` and of DevDocs' own repo ("DevDocs documented by DevDocs"). Link it at the top of the README. |
| **MCP server (F11)** | ₹0 | List in MCP directories. Developers search for these; it positions DevDocs as infrastructure, not another chatbot. |
| **"Honest refusal" angle** | ₹0 | Your real differentiator is the measured gate (AUC 0.99 vs reranker) and held-out numbers. Write one short post: "I measured a RAG relevance gate against a reranker". Honest eval write-ups travel well on Hacker News, r/LocalLLaMA and dev.to. |
| **Docker image on GHCR** | ₹0 | One compose file that pulls prebuilt images, so a visitor goes from README to running app in 3 commands. (Currently Docker is only used for Postgres and ES; the app runs from source.) |
| **GitHub Action** | ₹0 | `devdocs/sync` action (uses F12) to re-index on push; appears in the Marketplace. |
| **Good first issues + LICENSE + CI badge** | ₹0 | Signals a maintained project; needed before anyone contributes. |

---

## 5. Suggested order

Each step is independently shippable and keeps cost at ₹0. **Section 10 supersedes this list as the working order** now that the permission system and the redesign are decided; this list is kept for the reasoning behind each item.

1. **Trust and first impression** (small): G1 quota pause, G6 generated-file skip, G7 secret scan, G8 privacy notice, G9 run the phase proofs, G10 LICENSE + CI.
2. **Make it look smart** (the biggest visible jump): F1 dashboard, F4 project-aware starters, F3 symbol index, F5 Search tab.
3. **The hook feature**: F2 dependency graph (+ F9 Mermaid architecture view).
4. **Chat polish**: F6 (split-view citations, export, regenerate, `@path`, command palette), F7 feedback + Insights.
5. **Quality for the common case**: G2 tree-sitter chunking for JS/TS/Java/Go, G3 per-project gate calibration.
6. **Reach**: F13 GitHub URL import, F14 re-sync, F11 MCP server, F12 CLI.
7. **Share**: F8 Project Tour, F15 static export, README GIF + recorded demo site, then F16.

**Flagship, decided:** the permission-aware enterprise knowledge system (section 8) is the feature to implement. It can run in parallel with step 1 and replaces the "P9 no auth" gap. Steps 2 to 4 (dashboard, graph, chat polish) then need to respect permissions too: anything that lists or counts files must go through the access filter.

If you only do three things: **F1+F2 (dashboard and graph), F13 (GitHub URL import), F11 (MCP server).** Together they change the pitch from "a chatbot over your files" to "a code-intelligence workspace you can point at any repo and plug into your editor", with no new paid service.

---

## 6. Things deliberately not suggested

- A hosted multi-tenant demo, paid vector DB, or any paid API: breaks ₹0.
- Swapping Elasticsearch for Chroma or SQLite-FTS: ruled out in `CLAUDE.md`.
- A second embedding provider or local embedding model: the gate and every threshold are tied to `gemini-embedding-001` (see the optimisation report).
- Always-on LLM features (auto-summary of every file at ingest): would exhaust the Groq/Gemini free quota on the first upload. Anything LLM-backed above is user-triggered and cached.

## 7. Open questions for you

1. Is the goal mainly **a portfolio/interview piece** (then README, GIF, demo site, CI and the dashboard/graph matter most) or **real users** (then GitHub import, re-sync, MCP and multi-language chunking matter most)?
2. Is JS/TS the language you expect visitors to bring? If yes, G2 moves to the top.
3. Do you want the Search tab and dashboard visible by default, or kept behind a setting like the current dev tools? (The redesign prototype assumes visible by default.)

Questions 1 and 2 are still open and do not block the permission feature or the redesign port.

---

## 8. Flagship feature to implement: permission-aware enterprise knowledge system

**Status:** chosen for implementation and all design decisions confirmed (2026-11-05, see 8.6). Not started; phase A is next. This reverses the `CLAUDE.md` stack decision "local single-user app, no auth", so `CLAUDE.md`, the implementation plan and the diagrams must be updated in the same edit as the first phase.

**What it is.** One shared knowledge base where every person gets answers only from the documents they are allowed to read. It is *not* per-user workspaces (multi-tenancy). The unit is a document with an access list, and the question is always "what may *this* user see?". It turns DevDocs from a developer chatbot into something an organisation can adopt, and it is differentiated: most free RAG demos ignore permissions.

**Why DevDocs is a good base.** Retrieval already filters on `project_id` and the Postgres `ready` file IDs, enforced on the server. Permissions become one more term in that same filter. No extra LLM or embedding calls: permissions are plain Postgres lookups, so it respects the ₹0 constraint.

### 8.1 Illustration only (nothing below is seeded, hardcoded or shipped)

**Hard rule: no scenario data in the product.** The folders and rules below exist only to explain the behaviour. The app ships with **no** demo users, rules or sample org. Admins create people in the UI, then set rules on whatever files they uploaded. Tests build their own throwaway users and files inside the test, and nothing in the backend, frontend or migrations may reference these names. The same applies to the redesign prototype's sample data (see section 9): it is mock data for the design only and must not be ported.

Imagine a project containing the folders below. Every rule is just data an admin typed in:

| Folder | Who can read |
|---|---|
| `/handbook` | everyone |
| `/engineering` | a few specific people |
| `/hr/salary-bands.pdf` | two other people, as a file-level override of its folder |
| `/finance/budget.csv` | one named person |

The behaviours that must hold for *any* data (these are the acceptance criteria):

1. **Admin sets access** in a new Access tab: a folder tree with a "who can read" column, inheritance down the tree, file-level overrides, and **Preview as: <user>** to check what that user would see before anyone is exposed.
2. **Files tab differs per user.** Files and folders a user cannot read are not listed and are not counted in Overview totals (the existence of a restricted file must not leak).
3. **Same question, different users.** A user who may read the relevant file gets an answer with a citation to it. A user who may not gets the **identical** standard refusal ("I couldn't find relevant information in this project…"). Never "you don't have permission", which would confirm the document exists.
4. **Changes apply immediately, no re-index.** Add a person to a folder's rule and their next question can use those files. Remove them and their next question is refused; the old citation in their earlier chat shows "Source no longer available"; that chat's memory summary is rebuilt from only what they can still see.
5. **Audit log (admin only):** who asked what, and which files were served for it.
6. **Admin** can read every file in every project and manage all people and rules (decided, see 8.6).

### 8.2 Design

**New data** (migration after the latest; new backend files get the next free numbers in `Backend-File-Flow.md`, with the dependency graph updated):
- `users` (email, password hash, role `admin` or `member`). No `groups` table in V1.
- `access_rules`: target (folder path prefix or file) and a visibility: `everyone`, `people` (a list of user IDs), or `admin` (the default when no rule matches). The most specific rule wins. There are **no predefined groups or roles** beyond admin and member; if bundling people becomes useful, admins can create their own optional teams later, and a team is just a shortcut for a list of people.
- `audit_log`: user, question, served file IDs, timestamp

**Enforcement point.** Where retrieval builds the ready-files list today:

```
allowed_ids = ready files in this project
              ∩ files the current user may read      <- new, computed in Postgres per request
```

Postgres is the source of truth for permissions. The allowed-ID list goes into the ES query as a filter, so top-K comes only from permitted files: a forbidden file can never crowd out a permitted one. The relevance gate then runs on permitted content only, so a question about content the user cannot see scores low and is refused. Permissions are *not* copied into the ES documents, so a permission change needs no re-index. (At much larger scale, an `allowed_principals` field on chunks is the alternative; not needed at this size.)

**Every surface that exposes content needs the same check.** Securing only chat is the classic mistake.

| Surface | Rule |
|---|---|
| Chat retrieval | allowed-ID filter inside the ES query |
| Citation click, file content, chunks endpoints | read check, otherwise 404 |
| Files tree, Overview counts, any search | permitted files only |
| Conversation summary / memory | rebuilt when the user's access changes |
| Frozen citations in old messages (`sources_json`) | re-checked on display |
| Any future answer cache (`Future-Caching.md`) | cache key must include the user's permission set |
| ES Inspector, Retrieval Playground, Context Inspector | admin only; off in shared deployments |
| Refusal message | identical for "no access" and "does not exist" |

**Sign-in (decided: simple).** Email and password accounts stored in Postgres (hashed with argon2 or bcrypt), a signed session cookie, and a first-run screen that creates the initial admin. The admin creates further users in the UI. No Google/GitHub/OIDC/Keycloak: it is a portfolio project, and the permission logic is the part worth showing. Keep the "who is the current user" lookup in one dependency so an OIDC provider could replace it later without touching the permission code.

**Getting permissions from the source (later).** Strongest version: connectors copy the *source system's* permissions at ingest (GitHub-org repo access, Drive/SharePoint sharing) and re-sync on a schedule. V1 is manual rules plus an optional `acl.json` manifest in the uploaded folder.

### 8.3 Phases (each ends in a demo-able proof, per project convention)

| Phase | Scope | Proof |
|---|---|---|
| A. Identity | users, email+password sign-in, first-run admin creation, admin role | create two users; unauthenticated calls get 401 |
| B. Rules and enforcement | `access_rules`, resolver (most specific rule wins, no rule means admin-only), filter in retrieval, 404 on every content endpoint | **leak test suite**, with users and files created inside each test: put the same sentence in a file user A may read and a file user B may not; assert B's retrieval never returns the restricted chunk ID; same for citations, file content, counts, summaries |
| C. Admin UI | people management, Access page (By folder, By person, Matrix, Audit log; see section 9), lock icons, "Preview as" | by hand: create people and rules through the UI on a folder you upload, then check 8.1 behaviours 1 to 3 |
| D. Leak hardening | summary rebuild on access change, frozen-citation re-check, audit log, dev tools locked | by hand: 8.1 behaviours 4 and 5 |
| E. Source sync | `acl.json` manifest import, then GitHub/Drive connectors | upload a folder with a manifest and see the rules applied |

### 8.4 Gaps in the current code this feature depends on

These come from the code survey; the endpoint list is from `backend/app/api/`. Re-verify each when the phase starts.

- No users or sessions exist; `AppSettings` is a single global row (`id=1`), so provider keys are global. Decided: keys stay admin-owned, so this row stays global and only needs to be admin-only (see 8.6).
- Conversations and messages have no owner. They must be owned by a user, and a conversation must never be readable by anyone else.
- The file content, chunks, conversation context and `dev.py` endpoints take IDs with no ownership or permission check today.
- The in-process rate limiters are global per provider. With one org key that is acceptable; with many active users the free Groq limit (8k tokens/minute) becomes the real ceiling.
- The relevance gate (0.82) was calibrated on whole-project corpora. Filtering shrinks the corpus per user; this should only increase refusals for restricted topics, but re-run `python -m eval.run` on a permission-filtered split to confirm (see G3).

### 8.5 Honest limits

- The guarantee is that the model is only *given* permitted text. Anything a user already saw cannot be unsaid.
- Per-request filtering adds one Postgres query. Fine for thousands of files.
- A real enterprise rollout also needs SSO hardening, retention rules and per-source compliance review, outside a free-tier build.
- Free-tier quotas are shared by everyone using the one org key. Realistic ₹0 use is a small trusted team on one machine.

### 8.6 Decisions

| # | Question | Decision |
|---|---|---|
| 1 | Does the admin read everything? | **Yes.** Admin can read all files and manage all people and rules. |
| 2 | Sign-in method | **Simple email + password**, no Google/GitHub/OIDC (see 8.2). |
| 3 | Whose AI keys are used? | **One shared set of keys, managed by the admin in Settings (default, as today).** Users never enter keys. The alternative, every user pasting their own keys, is rejected as unnecessary friction for a portfolio demo. Caveat: all users share one free quota. |
| 4 | Where do permissions attach? | **On folders and files inside a project (default).** Rules are set on paths, inherit downward, and a file can override its folder. The alternative, "a project has a member list, members see everything in it", is only a stepping stone and would not show real document-level permissions. |

| 5 | How is access expressed? | **People first, no predefined groups (owner, 2026-11-05).** The admin picks a folder or file and chooses Everyone, Specific people or Only admins. The Access page offers three views of the same rules: By folder (a share panel), By person (every file with why it is or is not readable) and a people-by-folder Matrix where a click grants or revokes. The redesign prototype implements all three. |

All five decisions are **confirmed by the owner** (2026-11-05); decisions 3 and 4 follow the recommended defaults. Nothing blocks phase A.

---

## 9. Frontend redesign (prototype handoff)

**What it is.** A clickable, single-file prototype of the whole redesigned frontend, built so the design can be judged and then ported. It runs on mock data only and calls no backend.

- **File:** `Docs/Design/devdocs-redesign.html` (open it directly in a browser; only the Google Fonts need internet).
- **Published copy (private to the owner):** https://claude.ai/artifact/DoHdWsoYTuNdi7Wxqs7a1g
- **Verification so far:** the script was syntax-checked and every route, the role switching, chat flows, access editing and select-and-ask were exercised in a simulated browser (jsdom). It has not been reviewed by the author in a real browser at phone width or in both themes; do that first.

### 9.1 Design direction

- **Idea:** a code reader's workspace. Ink-teal neutrals plus one highlighter-yellow accent that always means "this is the evidence": citations, selected lines, the primary action. Semantic colours (ok, warn, bad, info) are separate from the accent.
- **Type:** Bricolage Grotesque (headings), IBM Plex Sans (body), IBM Plex Mono (code, paths, numbers). Self-host or bundle them in the real app rather than relying on the Google CDN.
- **Themes:** full light and dark palettes from the same tokens; dark uses a brighter accent with dark text on it. Tokens are CSS variables at the top of the prototype's `<style>` block.
- **Rules the owner set:** no left-edge accent bars on any component (selected rows, citation chips, callouts, quoted blocks all use fills or full borders instead). Full-width panel dividers are fine. In the existing frontend the only two left-border uses are the blockquote style in `AssistantMarkdown.tsx` and the tree indent guide in `FilesTab.tsx`; remove or restyle them during the port.
- **Accessibility built in:** visible focus rings, `aria-pressed` and `aria-expanded` on toggles, labelled form controls, keyboard command palette (Ctrl+K), keyboard selection triggers select-and-ask, reduced-motion respected.

### 9.2 Information architecture

```
Sign-in (first run: create the admin)
   |
Projects home  (cards, NO sidebar; admin sees every project, a member sees only projects where they can read something)
   |  open a project
Project workspace  (sidebar: All projects link, project name, then:)
   Dashboard · Dependency map · Files · Search · Chat
   Admin only: Access · Settings
```

Non-admins who open `#access` or `#settings` get the same "Page not found" screen as any missing page. Ctrl+K opens a command palette (pages, files you can read).

### 9.3 Screens and what each needs from the backend

| Screen | What it shows | Backed by | Extra AI calls |
|---|---|---|---|
| Sign-in | first-run admin creation | phase A | none |
| Projects home | cards with state: ready, paused for quota (G1), re-index needed, import tile | existing projects API plus G1 and phase B visibility | none |
| New project dialog | GitHub URL, zip, folder tabs; quota estimate; secrets skipped (G7); privacy line (G8) | F13, G6, G7, G8 | none |
| Dashboard | files you can read, chunks, file map, detected stack, entry point, env vars, admin-only insights (F7); to-do notes were built, then removed by the owner on 2026-11-08 | F1, F7, computed from chunks | none |
| Dependency map | interactive import graph, importers and imports, "Ask about this file" | F2 (section 3, `file_edges`) | none |
| Files | tree, viewer, who can read this file (admin), "Explain this file" (F10, cached) | existing plus phase B | one, user-triggered, cached |
| Search | keyword search with filters, optional meaning search | F5 | none, or 1 embedding call if meaning search is on |
| Chat | project-aware starters, trace chips (gate value, passages), citations opening a side panel, slash commands, scope, Stop, regenerate, thumbs, export | F4, F6, F7, existing chat | normal chat calls only |
| Select-and-ask | floating prompt on selected code or answer text | F6 | one normal chat call, or none for "What calls this?" |
| Access: By folder | pick a folder or file, set Everyone, Specific people or Only admins, add or remove people, see who can read it now | phase B and C | none |
| Access: By person | every file for one person with the reason it is or is not readable, "Preview app as" | phase C | none |
| Access: Matrix | people against folders, derived from the project's real paths, open any folder into its children, filled, partial and empty states, click to grant or revoke | phase C | none |
| Access: Audit log | who asked what, which files were served, answered or refused | phase D | none |
| Settings | shared keys with Test, models, relevance gate with "Calibrate for this project" (G3), reranker toggle, free-tier meters | existing settings plus G3 | calibration uses embedding calls, user-triggered |

### 9.4 Behaviours the port must preserve

These are the prototype's acceptance checks. Each should become a test or a manual proof.

1. **Permission awareness everywhere.** A file a person cannot read is not listed, counted, searched, drawn in the graph, offered as a starter question, cited, or reachable by ID. Counts and the file map reflect only readable files.
2. **Identical refusal.** A question that only restricted files could answer gets the same "I couldn't find relevant information in this project" reply as an off-topic question. The trace chip shows the best similarity against the gate and "no model call"; it never mentions access.
3. **Frozen citations are re-checked.** An old citation to a file the person has since lost shows "Source no longer available" and cannot be opened.
4. **Preview as.** The admin can view the whole app as any person; a banner says so and offers "Back to admin". Admin pages disappear in preview.
5. **Changes apply on the next question** with no re-index.
6. **Select-and-ask.** Selecting text inside code viewers or answers shows a floating prompt with the file and line range; the question opens in chat with the selection as a quoted card; the server re-checks read access to that file.
7. **Matrix and folder lists are derived from the project's real file paths.** Top-level folders first, open a folder to see its children, files appear when a folder has 10 or fewer or when a file has its own rule. A folder cell is "partial" when a file inside has its own rule. Nothing about folder or column names is hardcoded.
8. **Projects page is the front door.** No sidebar there; the sidebar appears inside a project with an "All projects" link back.
9. **Free-tier honesty.** Quota meters in the sidebar and Settings; a paused project says when it resumes and that nothing failed; instant answers (`/env`, `/todo`, `/files`, "What calls this?") are labelled "no AI quota used".

### 9.5 What in the prototype is mock and must not be ported

Everything in the script's data blocks: `USERS`, `RULES`, `FILES`, `CODE`, `ANSWERS`, `ENVV`, `TODOS`, `STACK`, `SEARCH_INDEX`, `EDGES_RAW`, the seeded audit rows, the quota numbers, the three sample projects, and every toast saying "Prototype: …". The sample project is a small shop API chosen only to look realistic. In the real app each of these is computed (stack from manifests, env vars from chunk text, edges from `file_edges`, quotas from the rate limiter, answers from the model). "Add person", "New group" and the non-shop project cards are placeholders.

### 9.6 Porting notes (existing frontend is React 18, TypeScript, Vite, Tailwind, TanStack Query)

| Prototype piece | Real frontend target |
|---|---|
| CSS tokens | replace the slate/brand variables in `frontend/src/index.css` and `tailwind.config.js` with the new palette; keep the `rgb(var(--x) / <alpha-value>)` pattern |
| `shell()` | split `Layout.tsx` into a home layout (no sidebar) and a project layout (sidebar, breadcrumb, preview banner) |
| `pProjects` | `ProjectsPage.tsx` (cards, states, visibility by permission) |
| `pDashboard` | replaces `OverviewTab.tsx` |
| `pGraph` | new graph page; use `d3-force` (free) or the small custom layout in the prototype; collapse to folders above about 300 nodes |
| `pFiles` | `FilesTab.tsx` plus `FloatingFileViewer.tsx` (the viewer becomes a side panel in chat) |
| `pSearch` | new search page |
| `pChat` | `ChatTab.tsx`, `ChatParts.tsx`; add a trace row and a quoted-selection card |
| select-and-ask | one reusable hook plus a popover component; mark code and answer regions as selectable and read line numbers from the rows |
| `pAccess`, `accFolder`, `accPerson`, `accMatrix`, `accAudit` | new Access page with four tabs |
| `pSettings` | `SettingsPage.tsx` |
| Ctrl+K palette | new component |
| Routing | the prototype uses `#hash` routes only because it is one file; use `react-router` paths |

The existing dev-only tools (Chunk Inspector, ES Inspector, Retrieval Playground, Context Inspector) stay behind `DEV_MODE` and, with section 8, also behind admin.

---

## 10. Order of work and a first prompt for the next session

Follow the project conventions in `CLAUDE.md`: one phase at a time, stop at each phase boundary with the proof for review; every new backend file takes the next free number in `Docs/Backend-File-Flow.md` and the dependency graph there is updated in the same edit; migrations chain from the latest `down_revision`; diagrams in `Docs/Plan/Diagrams/` stay in sync. `CLAUDE.md` currently says "local single-user app, no auth": update that line (and the plan) when phase A starts.

| Step | Scope | Proof |
|---|---|---|
| R0. Design foundation | new tokens and fonts, split home and project layouts, new Projects home, restyled existing screens. Frontend only, no backend change | the app builds (`npm run build`) and every existing screen works in both themes at phone and desktop width |
| 1. Quick trust fixes | G1 quota pause, G6 generated-file skip, G7 secret scan, G8 privacy notice, LICENSE and CI (G10) | each has a test or a visible result |
| A. Identity | users, email and password sign-in, first-run admin, `LOCAL_MODE` auto-login | two users can sign in; unauthenticated calls get 401 |
| B. Enforcement | `access_rules`, resolver, filter inside the ES query, 404 on every content endpoint | the leak test suite in 8.3 |
| C. Access UI | people management and the four Access tabs, lock icons, Preview as | the 8.1 behaviours by hand |
| D. Hardening | summary rebuild on access change, frozen-citation re-check, audit log, dev tools locked | 8.1 behaviours 4 and 5 |
| E. Show-what-you-know | F1 dashboard, F2 dependency map, F3 symbols, F4 starters, F5 search; all permission-aware | dashboard and graph for two different people show different data |
| F. Chat polish | F6 including select-and-ask, F7 feedback and insights | the 9.4 checks for chat |
| G. Quality and reach | G2 tree-sitter chunking, G3 gate calibration, F13 GitHub import, F14 re-sync, F11 MCP, F12 CLI | per item |

R0 can run in parallel with step 1. Steps A to D must finish before E and F, because those screens list, count and search files.

**Suggested first prompt for the new session:**

> Read `Docs/gaps.md` (start with "Start here", then sections 8, 9 and 10) and `CLAUDE.md`. Do step R0 only: port the design foundation from `Docs/Design/devdocs-redesign.html` into `frontend/` (tokens, fonts, home layout without sidebar, project layout with sidebar, Projects home) using the existing React/Tailwind stack and no backend changes. Use no mock data from the prototype. Show me the result and stop at the phase boundary.

### 10.1 Things to verify before trusting this file

- The endpoint list, the missing ownership checks, and the single global `AppSettings` row were read from the code in a survey, not exercised. Re-check each when its phase starts (8.4).
- The gate value 0.82 and the quota figures (Groq 8,000 tokens per minute, about 1,000 Gemini embedding calls a day) come from the repo's own docs and may have changed.
- Claims about third-party free tiers and what a "free" hosted demo can support are general knowledge, not checked against current pricing pages.