# Gaps implementation report (2026-11-05/06)

Written for: the project owner, to review before pushing.

## What was built

| Area (gaps.md) | State |
|---|---|
| G1 quota pause | Done. A spent embedding quota keeps files `pending`, pauses the job, shows "waiting for quota" and resumes by itself (90 s, or every 30 min for the daily quota). |
| G6 generated files | Done. Lock files, `*.min.js`, bundles, source-map footers, very long lines, "generated" headers are skipped. |
| G7 secret scan | Done. Private keys, AWS/GitHub/Groq/OpenAI/Google/Slack/Stripe keys, credential file names and `.env.*` are skipped; the report names the kind, never the secret. |
| G8 privacy notice | Done (upload dialog + README). |
| G10 LICENSE, CI | MIT licence; GitHub Actions (backend tests with Postgres + ES, frontend build + vitest). Frontend now has unit tests. |
| A identity | Done. First-run admin, email + password (scrypt), signed HttpOnly cookie, login throttle, people CRUD, `LOCAL_MODE`. |
| B enforcement | Done. `access_rules`, resolver, viewer-required retrieval, 404 on every content endpoint, dev tools admin only. |
| C Access UI | Done: By folder, By person, Matrix, Audit log, Preview as, People page. |
| D hardening | Done: summary reset on rule change, history filter, frozen citations re-checked, audit log. |
| E insights | Done, permission-aware: dashboard, keyword search, symbols, starter questions, import map (Python, JS/TS, Java, C). |

## Verified

- Backend: 273 tests pass (199 original + 74 new: auth 14, access/leak suite 28, trust 16, insights 16). One existing test was updated for the new `detail` field.
- Frontend: `npm run build` passes; 9 vitest tests pass.
- End to end (`scripts/e2e_smoke.py`) against a real server, a throwaway database (`devdocs_e2e`), real Elasticsearch, real Gemini embeddings and Groq chat: 46 of 47 checks passed. The one failure was a too-strict assertion in the script (since fixed); the behaviour it checked was correct. The fixed script has not been re-run, because that needs a fresh database.

## Not verified or not done

- No browser testing of the new screens (no browser tooling was available): they compile and use the tested API, but look and feel are unchecked, including narrow widths and dark mode.
- Not implemented: R0 visual redesign port, F (select-and-ask, feedback, regenerate, export, Ctrl+K), G (tree-sitter, gate calibration, GitHub import, re-sync, MCP, CLI), `acl.json` import, starter questions are not vetted against the gate.
- Import graph is computed on demand and cached in memory, not stored in a `file_edges` table as gaps.md proposed.
- Known limits: login throttle and the analysis cache are in-process; a summary being written at the exact moment a rule changes could briefly keep old content; members share one set of free-tier AI quotas.
- The existing dev database now has migration 0007. The first account created on first visit adopts any pre-existing conversations.
