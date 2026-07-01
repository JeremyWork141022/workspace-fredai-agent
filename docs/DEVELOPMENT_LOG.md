# Development Log

This log records implementation decisions, known concerns, and follow-up work for
the CRT Analytics Agent / FredAI workspace agent.

## 2026-07-01 - Switch Formula Rendering Back To KaTeX

### Request

Use KaTeX instead of MathJax and make formula rendering work with the minimum
setup burden on the work computer.

### Change

- Removed the active MathJax loader path from `web/app.js`.
- Restored KaTeX as the only active professional formula renderer.
- The UI now loads:

```text
/static/vendor/katex/katex.min.css
/static/vendor/katex/katex.min.js
```

- `renderMathFormula()` now calls:

```text
window.katex.render(...)
```

- If KaTeX is not installed locally, formulas remain visible as TeX-style text.
- Removed the MathJax vendor README.
- Added `web/vendor/katex/README.md`.
- Updated the CRT Cost development plan to reference KaTeX instead of MathJax.

### Minimum Work-Computer Setup

The minimum setup is to copy the KaTeX browser distribution into:

```text
web/vendor/katex/
```

Required files/folders:

```text
web/vendor/katex/katex.min.js
web/vendor/katex/katex.min.css
web/vendor/katex/fonts/
```

`fonts/` should be copied because `katex.min.css` references KaTeX web fonts.
No Python package is needed and `requirements.txt` should not change.

## 2026-06-30 - CRT Cost Agent Branch Preparation

### Request

Prepare a new CRT Cost branch as the first real single-process showcase agent.
The prototype should focus on a deal-level CRT Cost database, where each row is
a deal and fields include CRT Cost, UPB, payoff date, settle year, deal type,
and other features needed for aggregation, derived columns, dashboarding, and
partial-year normalization formulas.

The user also asked why the UI used KaTeX instead of MathJax and clarified that
using a large, popular formula-rendering package is acceptable for this
specific formula-rendering task.

### Branch

Created:

```text
codex/crt-cost-agent
```

### Implemented Changes

- Switched branch-specific UI text from `CRT Analytics Agent` to
  `CRT Cost Agent`.
- Switched the browser title, empty-thread prompt, composer placeholder, share
  message text, and disclaimer to CRT Cost wording.
- Switched the backend FastAPI title/description to `CRT Cost Agent`.
- Switched the default knowledge-base namespace from `CRT Analytics` to
  `CRT Cost`.
- Replaced the active curated memory with a CRT Cost-specific
  `.runtime/memories/MEMORY.md`.
- Updated the default memory seed used on a fresh runtime when `MEMORY.md` does
  not already exist.
- Added `docs/CRT_COST_AGENT_DEVELOPMENT_PLAN.md`.
- Added `web/vendor/mathjax/README.md` documenting the local MathJax asset
  layout.
- Changed formula rendering to prefer local MathJax:

```text
web/vendor/mathjax/tex-mml-chtml.js
```

The UI loads that file from:

```text
/static/vendor/mathjax/tex-mml-chtml.js
```

If the MathJax bundle is not present, formulas stay visible as TeX-style text.
There is no CDN dependency and no npm install requirement.

### Why MathJax Here

KaTeX is fast and smaller, but MathJax supports a broader TeX/MathML surface and
is a better default for business documentation that may contain diverse Word,
PDF, and methodology formulas. Since this project can vendor approved static
browser assets, MathJax is acceptable for the rendering layer. Formula
extraction and formula correctness are still backend/document-intake concerns;
MathJax only renders formula strings that the agent already has.

### Development Starting Point

Start with data and formula definitions before dashboard visuals:

1. Create a small non-sensitive sample CRT Cost CSV/XLSX.
2. Draft a data dictionary with column meanings and examples.
3. Draft a formula catalog for CRT Cost, UPB weighting, aggregation, and
   partial-year normalization.
4. Upload the dictionary/catalog to the `CRT Cost` knowledge base.
5. Add deterministic backend tools for profiling, aggregation, and approved
   formula execution.
6. Add the dashboard drawer only after tool output is stable.

## 2026-06-30 - Runtime Hook Infrastructure For Drawer Events And Self-Inspection

### User Questions

- The previous drawer trigger was too narrow because it depended on the user
  asking knowledge/source/wiki/correction inventory questions.
- The requested behavior is: when any knowledge-source-related tool runs, open
  the drawer automatically.
- Delete the old query-only drawer trigger.
- Establish the overall hook infrastructure for the agent.
- Research Claude Code, Codex, and OpenClaw hook/self-knowledge architecture
  patterns.
- Make the agent better at answering questions about its own code and
  documentation by inspecting the project instead of guessing.
- Use professional math rendering if allowed on the work computer, and remove
  the prior bad custom formula display overlays.

### Research Notes

- Claude Code documents lifecycle hooks around events such as tool use. The
  useful architectural idea is event plus matcher plus handler, especially a
  post-tool-use style hook for side effects after a tool has actually run.
- Codex documents lifecycle hooks loaded from `hooks.json` or inline config
  tables, with trust boundaries for project-local hooks. Codex also treats
  durable repo guidance (`AGENTS.md`), skills, MCP tools, and hooks as separate
  surfaces. The relevant lesson for this project is to keep operating rules,
  tools, and runtime hooks separate instead of hiding UI behavior inside model
  prompt text.
- OpenClaw's local source and docs show a broader version of the same pattern:
  plugin hook registries, composed live hook registries, before/after tool-call
  hooks, message hooks, and lifecycle hooks. The relevant lesson is that hook
  dispatch should be ordered, explicit, and event-based.

Local source references inspected:

- Claude Code Hooks: `https://docs.claude.com/en/docs/claude-code/hooks`
- Codex manual: `C:\Users\Jeremy\AppData\Local\Temp\openai-docs-cache\codex-manual.md`
- OpenClaw: `openclaw-research/docs/concepts/agent-loop.md`
- OpenClaw: `openclaw-research/docs/plugins/hooks.md`
- OpenClaw: `openclaw-research/src/plugins/hook-registry.types.ts`
- OpenClaw: `openclaw-research/src/plugins/hook-runner-global-state.ts`

### Implemented Hook Infrastructure

- Added `app/runtime_hooks.py`.
- Added:
  - `RuntimeHook`,
  - `RuntimeHookContext`,
  - `RuntimeHookRegistry`,
  - `build_default_hook_registry()`.
- The orchestrator now owns `self.hook_registry`.
- At the end of every `/agent/respond` turn, the orchestrator fires:

```text
event = "turn_completed"
```

- The hook receives:
  - `workspace_id`,
  - `user_id`,
  - `session_id`,
  - `request_id`,
  - `status`,
  - original `query_text`,
  - executed `tool_names`,
  - attachments.
- The default hook is:

```text
knowledge_drawer_on_tool_use
```

- It opens the Knowledge drawer only when one of these tools actually ran:
  - `knowledge_ingest`,
  - `knowledge_search`,
  - `knowledge_grep`,
  - `knowledge_read`,
  - `wiki_search`,
  - `wiki_read`,
  - `wiki_write`,
  - `wiki_issue`.

### Drawer Section Routing

The hook maps tools to drawer sections:

- `knowledge_ingest`, `knowledge_search`, `knowledge_grep`, `knowledge_read`
  -> `documents`
- `wiki_search`, `wiki_read`, `wiki_write`
  -> `wiki_pages`
- `wiki_issue`
  -> `pending_corrections`

If multiple knowledge tools run, the section priority is:

1. `pending_corrections`
2. `wiki_pages`
3. `documents`

This means a correction issue opens the pending-corrections section even if the
agent also searched source documents during the same turn.

### Removed / Superseded Logic

- Removed the backend query-text router from `app/ui_events.py`.
- Deleted `app/ui_events.py`.
- Removed the frontend mock keyword matcher.
- Mock mode now mirrors the hook principle by deriving UI events from mock
  `toolNames`, not from user wording.

### Self-Inspection Behavior

- Added a runtime instruction to the system prompt:
  for questions about this agent's own implementation, code, API, UI, tools,
  memory, hooks, configuration, or documentation, the agent must inspect the
  local project using workspace file tools before answering.
- Added the same durable rule to `.runtime/memories/MEMORY.md`.
- The intended tool path is:
  - `workspace_find_files` to locate likely files,
  - `workspace_list_files` to inspect folders,
  - `workspace_read_file` to read implementation or documentation,
  - answer with file citations.

### Math Rendering

- The previous UI formula renderer attempted to draw fractions, roots, and
  superscripts with custom DOM/CSS. That was removed from the active rendering
  path because it produced poor visual output.
- The UI now tries to load local KaTeX assets:
  - `/static/vendor/katex/katex.min.js`
  - `/static/vendor/katex/katex.min.css`
- If KaTeX is available, formulas are rendered by KaTeX.
- If KaTeX is not available, formulas remain visible as plain formula text.
- This avoids npm install, CDN dependency, or runtime failure on the work
  computer.

How to check whether KaTeX is allowed on the work computer:

1. Ask IT whether internally vendored JavaScript/CSS libraries are allowed in
   the project folder.
2. If allowed, download/copy `katex.min.js` and `katex.min.css` from the
   approved KaTeX distribution into:

```text
web/vendor/katex/
```

3. Start the app and open the browser developer console.
4. Run:

```javascript
Boolean(window.katex && window.katex.render)
```

5. `true` means KaTeX loaded. `false` means the UI is using plain formula text
   fallback.

## 2026-06-30 - Backend UI Events For Knowledge Drawer Auto-Open

Superseded by `2026-06-30 - Runtime Hook Infrastructure For Drawer Events And
Self-Inspection`. The active behavior is now hook-based on executed tool names,
not query-text inventory matching.

### User Questions

- Why does the drawer not automatically pop up when the user asks
  "what is my knowledge source"?
- What is the current drawer UI logic?
- What is the current trigger logic for opening the drawer?
- How should this be fixed while preserving the reusable drawer
  infrastructure for future different views?
- Can an LLM API call be used later without dramatically increasing runtime?

### Current-State Diagnosis Before This Change

- The drawer infrastructure existed in the UI as a reusable shell:
  - `DRAWER_VIEWS` in `web/app.js`,
  - `state.drawer`,
  - `openDrawer(view)`,
  - `closeDrawer()`,
  - `renderDrawer()`.
- The implemented view was `knowledge`.
- The drawer opened reliably from the left-sidebar `Knowledge Base` button.
- The current active code did not contain an automatic trigger for ordinary
  chat questions.
- An older development-log note referenced a frontend-only
  `isKnowledgeIntent` matcher. That implementation was no longer present in
  the active `web/app.js`, so asking "what is my knowledge source" did not
  open the drawer automatically.

### Implemented Fix

- Added a backend-to-frontend UI event contract to `/agent/respond`.
- Extended `AgentResponse` and `AgentRespondResponse` with:

```json
{
  "ui_events": [
    {
      "type": "open_drawer",
      "view": "knowledge",
      "section": "documents",
      "reason": "User asked for knowledge-base/source inventory.",
      "source": "backend_ui_event_router"
    }
  ]
}
```

- Added deterministic backend routing in `app/ui_events.py`, called by
  `WorkspaceAgentOrchestrator` before the response is returned.
- The router emits an `open_drawer` event for knowledge inventory/source
  questions such as:
  - "what is my knowledge source",
  - "show knowledge base",
  - "list source documents",
  - "what documentation is indexed",
  - "show wiki pages",
  - "show pending wiki issues".
- Added section routing:
  - source/document questions open the `documents` section,
  - wiki/glossary questions open the `wiki_pages` section,
  - pending correction/wiki issue questions open the
    `pending_corrections` section.
- Added frontend `applyUiEvents(events)`.
- The frontend now consumes `data.ui_events` after `/agent/respond`.
- `open_drawer` events still use the existing reusable drawer shell, so future
  views can reuse the same right-side container.
- Added drawer section IDs and a short section highlight when an event opens a
  specific section.
- Added mock-mode UI event routing so `?mock=1` can test the behavior without
  FredAI.
- Added tests for:
  - `"what is my knowledge source"` opening the Knowledge Base documents
    section,
  - pending wiki issue questions opening the pending corrections section,
  - unrelated questions not opening a drawer.

### Current Trigger Logic After This Change

The trigger is backend-owned and cheap:

1. User sends a message through `/agent/respond`.
2. The normal agent turn runs.
3. Before returning the response, the backend checks the user text with a
   deterministic inventory-intent router.
4. If the user asked for knowledge/source/wiki/correction inventory, the
   backend returns `ui_events`.
5. The browser applies the events and opens the right drawer.

This does not add another FredAI call, embedding call, OCR pass, or retrieval
pass. Runtime impact is negligible.

### Future Low-Runtime LLM Router Plan

If deterministic routing becomes too limited, add a small optional UI-router
model call only when the cheap router is uncertain and the user text contains
UI/navigation verbs such as "show", "open", "display", "browse", "where",
"what is my", or "list".

The model call should:

- use a tiny prompt,
- return strict JSON,
- choose only from registered drawer views and sections,
- run after or parallel to the main response only for ambiguous UI-intent
  cases,
- never perform document retrieval itself,
- never replace tool calls or knowledge search.

The eventual router output should match the same `ui_events` contract, for
example:

```json
{
  "type": "open_drawer",
  "view": "files",
  "section": "generated_outputs",
  "reason": "The user asked to inspect files created by the EVA run."
}
```

This keeps the current drawer architecture stable while allowing future views
such as file explorer, process dashboard, schedule/planner, and tool activity.

## 2026-06-30 - Formula Extraction And Formula Rendering

### Request

CRT Analytics documentation will contain many math formulas. The agent should
handle formulas when documents are uploaded and should present formulas
readably in chat responses. `MEMORY.md` should also tell the agent not to
invent formulas.

### Implemented Changes

- Added a formula policy to `.runtime/memories/MEMORY.md`.
- The formula policy tells the agent:
  - do not invent formulas,
  - preserve formula text from source evidence,
  - cite source evidence when formulas are used,
  - use explicit math delimiters such as `\( ... \)` or `$$ ... $$`,
  - say when a needed formula is not available in the indexed source material.
- Added best-effort DOCX Office Math extraction in
  `app/attachment_extractors.py`.
- DOCX formulas are now extracted as readable text markers such as
  `[Formula: (A)/(B)]` so FredAI can retrieve and reason over formula content
  from uploaded Word documents.
- Added frontend formula rendering in `web/app.js` without external packages.
- Supported chat formula delimiters:
  - inline `$...$` when the content looks math-like,
  - inline `\(...\)`,
  - display `$$...$$`,
  - display `\[...\]`.
- Added lightweight rendering for common formula structures:
  - superscript,
  - subscript,
  - `\frac{...}{...}`,
  - `\sqrt{...}`,
  - common symbols such as `\alpha`, `\Delta`, `\sum`, `\leq`, `\geq`.
- Added CSS classes for inline math, display math, fractions, and roots.
- Added a unit test for DOCX Office Math formula extraction.

### Limitations

- This intentionally does not add KaTeX, MathJax, Pandoc, OCR, or other
  package-heavy formula tooling because the work computer may block packages.
- DOCX formula extraction is best-effort over Office Open XML math nodes. It
  preserves formulas in a readable form but is not a full symbolic math parser.
- PDF/image formula extraction still depends on existing PDF text extraction,
  FredAI vision support, or future OCR work.

## 2026-06-30 - Chat Follow-Latest Scrolling And Emoji Feedback

### Request

Make the thumbs-up feedback control a real thumbs-up icon, and make the chat
automatically stay at the latest content while the agent is generating. If the
user scrolls upward during generation, stop automatic follow so the user can
read earlier messages.

### Implemented Changes

- Changed the per-message positive feedback action from the text label
  `Thumbs Up` to the `👍` icon with an accessible label and tooltip.
- Strengthened the chat follow-latest logic in `web/app.js`.
- Added a post-render follow step that scrolls after layout has updated, then
  checks one frame later again. This handles long responses and progressive
  thinking/progress content more reliably than a single pre-render bottom
  check.
- Added scroll-direction tracking through `state.lastMessagesScrollTop`.
- Kept automatic scrolling active while the user remains near the bottom.
- Disabled automatic scrolling when the user scrolls upward away from the
  bottom, including while the agent is still thinking.
- Clicking the scroll-to-bottom button re-enables follow-latest behavior.

### Behavior Rule

The UI should act like a normal chat product:

- New agent progress should stay visible by default.
- The browser should not force the user back down after the user intentionally
  scrolls up to inspect prior messages.
- Returning to the bottom, or pressing the bottom arrow, opts back into
  automatic follow.

## 2026-06-30 - Extensible Right Drawer Framework And Positive Feedback

### Request

Design the full drawer UI logic, implement the Knowledge Base drawer in that
framework, keep it extensible for other tool-related views, and add a
thumbs-up option beside the red-flag feedback action for each chat message.

### Implemented Changes

- Created a reusable right-side drawer shell in `web/index.html`.
- Moved Knowledge Base content into the drawer as the first registered drawer
  view instead of a one-off fixed panel.
- Updated `web/app.js` with a `DRAWER_VIEWS` registry and `state.drawer`.
- Added generic drawer functions:
  - `openDrawer(view)`
  - `closeDrawer()`
  - `renderDrawer()`
- Kept `openKnowledgePanel()` as the Knowledge Base convenience entry point,
  now backed by the generic drawer.
- Changed desktop layout so opening the drawer creates a third right column:
  left sidebar, chat, drawer.
- Kept tablet/mobile behavior as an overlay so the chat does not become too
  narrow.
- Added a per-message `Thumbs Up` action.
- Stored thumbs-up feedback through the existing durable message feedback
  endpoint with label `thumbs_up`.
- Updated feedback rendering so positive and red-flag feedback share one
  review trail.
- Fixed the scroll-to-latest button rendering through CSS so Windows text
  encoding does not corrupt the arrow glyph.

### Full Drawer Logic

The drawer should be treated as a single UI container with multiple possible
views, not as separate panels scattered through the app.

1. The shell layout owns the position:
   - left sidebar for threads,
   - center chat,
   - right drawer.
2. The drawer owns only chrome:
   - drawer kind,
   - title,
   - description,
   - close control,
   - body host.
3. Each drawer view owns its own content:
   - `knowledge`: documentation folder, wiki pages, pending corrections,
     upload/replace/download controls,
   - future `files`: file explorer and generated file outputs,
   - future `schedule`: scheduled jobs, planner, run queue,
   - future `tool_run`: live tool timeline and artifacts,
   - future `process`: EVA/Macs step dashboard.
4. The chat should remain usable while the drawer is open. On wide screens the
   chat shrinks into the center column. On smaller screens the drawer overlays
   because a three-column layout would be unusable.
5. Drawer opening should ultimately be event-driven from runtime/tool behavior,
   not guessed from user text.

### Future Tool-Triggered Drawer Plan

Add a backend/UI event contract to `/agent/respond`.

Suggested response addition:

```json
{
  "ui_events": [
    {
      "type": "open_drawer",
      "view": "knowledge",
      "section": "pending_corrections",
      "reason": "wiki_issue was created"
    }
  ]
}
```

Suggested mapping:

- `knowledge_ingest` -> open `knowledge`, focus `documentation`.
- `knowledge_search` / `knowledge_read` -> open `knowledge`, focus source
  documents and cited chunks.
- `wiki_search` / `wiki_read` -> open `knowledge`, focus wiki pages.
- `wiki_issue` -> open `knowledge`, focus pending corrections.
- future file tools -> open `files`.
- future scheduler tools -> open `schedule`.
- future EVA execution tools -> open `process` or `tool_run`.

Implementation steps for the future event contract:

1. Add `ui_events: List[Dict[str, Any]]` to `AgentRespondResponse`.
2. In `WorkspaceAgentOrchestrator`, convert tool trace events into high-level
   UI events.
3. Return those events from `/agent/respond`.
4. In `web/app.js`, apply events after the response:
   - `open_drawer` calls `openDrawer(event.view)`,
   - optional `section` scrolls/focuses within that drawer view,
   - optional payload selects a document, issue, wiki page, or generated file.
5. Keep frontend keyword guessing disabled.

### Design Notes

- The Knowledge Base drawer is implemented now. Other drawer views intentionally
  use the same shell but are not implemented yet.
- This keeps the UI extensible without committing to the exact file explorer,
  schedule, or EVA execution dashboard layout before those tools exist.
- Red Flag and Thumbs Up are both durable feedback records. Red Flag is for
  commented review; Thumbs Up is quick positive feedback.

## 2026-06-30 - Scroll Follow, Runtime Indicator, And Issue-Only Correction Policy

### Request

Improve the chat experience during long responses, remove brittle keyword-based
knowledge drawer auto-open behavior, and clarify that missing/incorrect
knowledge should be logged for review instead of automatically converted into
wiki corrections.

### Changes

- Removed the frontend keyword trigger that opened the Knowledge Base drawer
  based on guessed user wording.
- Added bottom-follow behavior while the assistant is responding.
- Stopped bottom-follow automatically when the user scrolls upward during a
  response.
- Changed the floating latest-message button into a compact down-arrow button
  placed directly above the composer.
- Added a small runtime status indicator near the chat box:
  - animated dots while the agent is thinking,
  - green dot when the agent is ready.
- Updated `MEMORY.md`:
  - undefined source terms should create/log concise `wiki_issue` items,
  - `wiki_write` should not create corrections/glossary pages unless the user
    explicitly asks after review,
  - answers should be concise unless detail is requested.
- Updated runtime system instructions in `app/orchestrator.py` to match the
  issue-only correction policy.

### Design Notes

- The previous drawer auto-open was frontend-only text matching. It was brittle
  because the browser guessed intent before FredAI actually chose tools.
- Better future behavior is tool-event driven: the backend should return
  structured UI events such as `open_panel=knowledge` when tools like
  `knowledge_search`, `wiki_search`, `wiki_issue`, or `knowledge_ingest` run.
  The UI can then open the relevant drawer because the runtime actually used
  that subsystem, not because a regex matched the user's wording.
- `wiki_issue` should be understood as the issue log / review queue.
  `wiki_write` should be understood as the tool that creates or updates the
  curated wiki layer after review.

## 2026-06-30 - Readable Markdown Chat Rendering

### Request

Make agent responses readable in the browser instead of displaying raw Markdown
syntax such as `**bold**`, inline backticks, and pipe tables.

### Changes

- Extended the browser Markdown renderer in `web/app.js` to support:
  - bold text with `**text**` and `__text__`,
  - inline code with backticks,
  - safe links,
  - horizontal rules,
  - Markdown pipe tables.
- Updated `web/styles.css` so rendered tables, inline code, links, and dividers
  are readable inside assistant messages.

### Design Notes

- The renderer still uses DOM nodes and `textContent` for parsed text, not raw
  HTML injection.
- This keeps the project package-light for the work-computer environment while
  covering the Markdown patterns the agent commonly returns.

## 2026-06-29 - Message Red Flag Review Trail

### Request

Add a durable way to flag a specific chat message and attach a reviewer
comment, good or bad, so daily review can find problematic answers and decide
whether they should become wiki corrections or glossary entries.

### Changes

- Added `message_feedback` SQLite storage in `app/session_store.py`.
- Added backend review APIs:
  - `POST /agent/messages/{message_id}/feedback`
  - `GET /agent/feedback`
- Updated `GET /agent/sessions/{session_id}` so each visible user/assistant
  message includes its saved feedback comments.
- Added a `Red Flag` message action in the chat UI.
- Added an inline comment editor for a selected message.
- Render saved review comments under the exact message they belong to.
- Preserved mock-mode comments in browser `localStorage` for UI-only testing.
- Added regression coverage for message feedback persistence.

### Design Notes

- Source-document uploads shown in the knowledge sidebar are stored on the
  backend machine running FastAPI, not on each user's browser. In the current
  prototype, original uploaded source-file bytes are retained as base64 text in
  the `knowledge_files.content_base64` column in `.runtime/state.sqlite3`.
- Red-flag comments are session-review annotations, not automatic knowledge
  corrections. This keeps the original answer intact while making mistakes easy
  to audit and promote into wiki pages, wiki issues, or curated memory later.
- In shared-session mode, everyone viewing the same thread can see the same
  review comments. A future private-user mode should decide whether review
  comments are shared, reviewer-only, or manager-only.

## 2026-06-29 - Single Curated Memory And Knowledge Browser

### Request

Move curated memory to a single `MEMORY.md` file, remove the per-message chat
edit control, and add a UI/API path for users to inspect and manage source
documents in the knowledge base.

### Changes

- Removed `.runtime/memories/USER.md` from the tracked runtime files.
- Updated `app/memory_manager.py` so curated memory is loaded only from
  `.runtime/memories/MEMORY.md`.
- Updated the `memory` tool schema so `target` can only be `memory`.
- Updated `routine_rule` curated-memory side effects to default to `memory`,
  not the former user-profile target.
- Removed `WORKSPACE_AGENT_USER_MEMORY_CHAR_LIMIT` from runtime configuration.
- Updated default agent identity to `CRT Analytics Agent`.
- Updated runtime instructions to treat `MEMORY.md` as stable operating
  guidance only.
- Removed the per-message `Edit` button and dead edit flow from the chat UI.
- Added a left-sidebar `Knowledge Base` button that opens a browser drawer.
- Added knowledge-browser UI for:
  - source-document listing,
  - source-document upload,
  - source-document replacement,
  - original-file download when available,
  - text export for older indexed documents without retained raw bytes,
  - wiki page listing,
  - pending correction/issue listing.
- Added `knowledge_files` SQLite storage to retain raw uploaded files for new
  knowledge documents.
- Added FastAPI endpoints:
  - `GET /agent/knowledge/documents`
  - `POST /agent/knowledge/documents`
  - `PUT /agent/knowledge/documents/{document_id}`
  - `GET /agent/knowledge/documents/{document_id}/download`
- Rewrote `docs/CURATED_MEMORY_GUIDE.md` for the single-file memory model.

### Design Notes

- Raw source documents are the governed source-of-truth layer.
- Wiki pages and wiki issues are the correction, supplement, and interpretation
  layer above raw documents.
- If the LLM misunderstands an EVA, Dynamic CRT Cost, Spot CRT Cost, workflow,
  or PRM detail, the source document should not be silently rewritten. The user
  should add a wiki correction or issue, preferably with `chunk_refs` pointing
  back to source evidence.
- Existing documents ingested before this change may not have original file
  bytes in `knowledge_files`. Those remain searchable through chunks and can be
  downloaded as reconstructed text exports.
- Newly uploaded/replaced source documents retain original bytes in SQLite.
  This is intentionally simple for the prototype and avoids a new package,
  shared-drive storage dependency, or separate file server.

### Follow-Ups

- Add delete/archive controls for obsolete source documents if governance allows.
- Add a polished wiki page editor/reader in the UI.
- Add source-document version history if replacement needs audit trails beyond
  the current SQLite metadata.
- Add a future user/privacy mode before broad deployment if every user should
  not share the same visible session and knowledge history.

## 2026-06-29 - Knowledge Drawer Simplification And Thinking Progress

### Request

Simplify the knowledge-base drawer so users do not need to fill out process,
document type, tags, and summary manually during upload. Add a way for the UI
to surface what the agent is doing while a long request is running.

### Changes

- Simplified the drawer upload form to a single file picker.
- Added automatic backend metadata inference for documentation uploads:
  - process hints such as EVA, MACS, PRM, Dynamic CRT Cost, Spot CRT Cost,
  - document-type hints such as user guide, methodology, model review, model
    use, model register, runbook, script, and change memo,
  - tag hints,
  - version/date hints such as `Version 1.5`, `v2.0`, or `_v7`.
- Added tag filter buttons in the knowledge drawer, derived from document tags
  and inferred metadata.
- Kept source uploads separate from wiki correction logic:
  - source documents remain raw/indexed documentation,
  - wiki pages and wiki issues remain the interpretation, supplement,
    correction, and change-memo layer.
- Added guidance to upload responses when a version is detected: use wiki pages
  to create a change memo linking document IDs and summarizing differences.
- Added automatic drawer opening when a user asks knowledge-base inventory
  questions such as "show the knowledge base" or "what documentation is
  indexed." Simple chat file uploads do not open the drawer automatically.
- Changed the running assistant indicator from `Working...` to `Thinking...`.
- Added visible live progress hints during long-running requests, for example:
  reading thread/memory, preparing attachments, checking whether knowledge/wiki
  tools are needed, and waiting for FredAI to choose tools or answer.

### Design Notes

- The live progress UI does not expose hidden chain-of-thought. It only shows
  operational status so users are not staring at an unexplained spinner.
- True real-time tool progress would require a streaming response, polling job,
  or server-sent events endpoint. The current implementation provides frontend
  progress hints during the blocking `/agent/respond` call and preserves the
  actual tool/progress metadata after the response completes.
- Multiple versions of a guide should coexist as separate source documents.
  The source-document metadata can identify version hints, while wiki change
  memo pages should explain what changed and link related document IDs.

## 2026-06-29 - Answerability Guard For Definition Questions

### Request

Avoid awkward circular answers where the source document only mentions a term
but does not define it. Example: the EVA guide lists `Loan List.xlsx` as an
input file, then the user asks "What is Loan List.xlsx?" The agent should not
answer only "it is one of the required input files."

### Changes

- Added a concise answerability policy to `.runtime/memories/MEMORY.md`.
- Added the same policy to the default curated-memory seed in
  `app/memory_manager.py`.
- Added runtime instruction text in `app/orchestrator.py` so the rule remains
  visible even if curated memory changes later.
- Added a lightweight knowledge-gap detector inside `app/tools.py` for the
  knowledge tools:
  - detects definition-style questions such as `what is X`, `define X`, and
    `what does X mean`;
  - checks the already-retrieved chunk text for definition cues versus
    mention/list-only cues;
  - returns a `knowledge_gap` object from `knowledge_search`;
  - lets `knowledge_read` accept `user_question` and return a deeper
    `knowledge_gap` result from the chunks it read.
- Updated the `knowledge_read` tool schema to include `user_question`.
- Added a regression test proving mention-only evidence is flagged as
  `definition_not_found`.

### Runtime Design

- This is intentionally not a new model call.
- No additional OCR, embedding search, or long summarization happens.
- The detector only runs over text already retrieved by `knowledge_search` or
  `knowledge_read`.
- When `evidence_status=definition_not_found`, FredAI should say the indexed
  documentation mentions the term but does not define it, provide only clearly
  labeled inference if useful, and create or suggest a wiki issue/glossary
  correction.

### Historical Drawer Trigger Superseded On 2026-06-30

This section describes an older frontend-only approach. The active
implementation now uses backend-returned `ui_events`; see
`2026-06-30 - Backend UI Events For Knowledge Drawer Auto-Open`.

The knowledge drawer auto-open trigger is frontend-only JavaScript in
`web/app.js` (`isKnowledgeIntent`). It is not a model tool call. Before sending
a chat message, the UI checks the typed text for inventory-style wording such
as:

- `show/open knowledge base`
- `what documentation is indexed`
- `what is uploaded/stored`
- `documentation folder/library/inventory`
- `wiki pages/issues/corrections`

If the message has no file attachments and matches those patterns, the drawer
opens while the normal `/agent/respond` request proceeds.

### Current Pending-Correction Logic

- Pending corrections are backend records in the `wiki_issues` SQLite table.
- The LLM can create them with the `wiki_issue` tool when a user reports wrong,
  missing, contradictory, or stale process knowledge.
- The drawer lists pending issues through `GET /agent/knowledge/documents`.
- Today there is no manual drawer form for creating a correction. A user can
  ask the agent to "log a correction" or backend code can call the tool/store.
- Future work should add a review UI for daily session auditing: flag answer,
  draft correction, approve into wiki glossary/change memo.

## 2026-06-28 - Context Window And Token-Budget Concern

### Concern

The current agent limits recent conversation context by message count, but it
does not yet enforce an approximate total prompt/token budget before calling
FredAI. This is acceptable for the prototype, but it should be tracked before
broader multi-user rollout.

### Current Mitigation

- `WORKSPACE_AGENT_SESSION_CONTEXT_MESSAGES` limits recent user/assistant
  history sent to FredAI. Default is `16`.
- Curated memory has a character limit through
  `WORKSPACE_AGENT_MEMORY_CHAR_LIMIT`.
- Attachment extraction is bounded:
  - `MAX_INLINE_ATTACHMENT_BYTES = 6 * 1024 * 1024`
  - `MAX_EXTRACTED_CHARS = 60000`
  - table extraction limits rows, columns, and sheets.
- Tool loop iterations are bounded by `WORKSPACE_AGENT_MAX_AGENT_ITERATIONS`.
  Default is `4`.
- FredAI response length is bounded by `max_tokens=4096`.

These controls prevent ordinary sessions from growing forever.

### Remaining Risk

A single request can still become too large if several sources combine:

- a long user message,
- one or more large extracted attachments,
- recent messages that already contain attachment text,
- automatic memory prefetch,
- large tool results such as `workspace_read_file`,
- multiple tool-result messages inside one agent loop.

The main risk is not unlimited historical chat growth. The main risk is one
large turn exceeding the FredAI model context window.

### Recommended Follow-Up

Add a lightweight prompt-budget guard before each FredAI call:

1. Estimate request size by character count or approximate tokens.
2. Log prompt-size metrics in request traces.
3. Cap total attachment text included in one user message.
4. Cap total tool-result text appended back into the model loop.
5. If the prompt is too large, return a clear message asking the user to narrow
   the file/question, or summarize/truncate lower-priority context.

Token-perfect counting is not required for the first version. A conservative
character budget is enough to catch the risky cases.

### Suggested Initial Defaults

- Approximate prompt warning threshold: `120000` characters.
- Approximate prompt hard limit: `160000` characters.
- Maximum total extracted attachment text per request: `80000` characters.
- Maximum tool result content appended per call: `30000` characters.

These should be revisited after confirming the actual FredAI model context
window available in the work environment.

## 2026-06-28 - Shared Session History And Privacy Mode Concern

### Current Behavior

The UI currently uses shared identity values for everyone:

- workspace: `shared_workspace`
- user: `shared`

That means every user who opens the same deployed CRT Analytics Agent instance
sees the same thread list and can open the same session history. The project is
currently separated by session/thread only, not by individual user.

### Why This Is Acceptable For Prototype

This is useful for early team testing because the thread list behaves like a
shared collaboration board:

- anyone can start a thread,
- anyone can reopen prior shared threads,
- anyone can share selected exchanges through a session URL.

It keeps setup simple while the backend, FredAI calls, attachments, memory, and
UI workflow are still being validated.

### Privacy Concern

For broader use, shared history may expose one user's questions, uploaded-file
contents, or analysis results to other users. This is not appropriate if the
agent is used for sensitive borrower, deal, model, portfolio, or operational
data.

### Recommended Follow-Up

Add a user-scoped mode alongside the current shared mode:

1. Keep `shared` mode for team-wide collaboration.
2. Add `private` mode where sessions are filtered by user identity.
3. Automatically derive user identity where possible, for example from an
   authenticated reverse proxy header, Windows/IIS header, SSO integration, or
   another approved company identity source.
4. Store `workspace_id`, `user_id`, and `visibility` or `mode` on each session.
5. In private mode, list only the current user's sessions.
6. In shared mode, clearly label the UI so users know the history is visible to
   everyone using that instance.

Do not rely on browser-only localStorage for privacy. It is useful for UI
preferences, but backend authorization and session filtering should enforce any
privacy boundary.

## 2026-06-28 - Hook And Skill Hub Needed For User-Scoped Orchestration

### Current Behavior

The current code does not yet have a structured orchestration-layer hook hub or
skill hub.

The closest implementation is the `routine_rule` tool. It can store records with
types such as `hook`, `skill`, and `tool_request` in SQLite. However, these are
currently records and classifications, not a full runtime system for managing
ordered hooks, reusable skills, permissions, ownership, enablement, versioning,
or user-specific execution.

Only one hook-like behavior is active today: `pre_llm` routine rules can be
matched and injected into automatic memory prefetch. Other hook events such as
`post_llm`, `pre_tool_call`, and `post_tool_call` are marked as planned/builder
work rather than executed by a central hook engine.

Similarly, `skill` routine rules are saved as reusable workflow candidates for
later promotion into formal skills. They are not loaded as executable skill
modules by the current agent runtime.

### Why This Matters

If the project later separates users for privacy, personalization, or team
ownership, fixed code embedded in the whole agent will become too rigid. The
agent will need a structured way to decide:

- which hooks apply globally,
- which hooks apply to one workspace,
- which hooks apply to one user,
- which skills are available to a user or workspace,
- which hooks run before/after model calls or tool calls,
- how hook/skill changes are reviewed, enabled, disabled, and audited.

### Recommended Follow-Up

Add a traditional agent orchestration layer with explicit registries:

1. `HookRegistry` for `pre_llm`, `post_llm`, `pre_tool_call`, and
   `post_tool_call` hooks.
2. `SkillRegistry` or `SkillHub` for reusable workflow modules.
3. Scope fields for each hook/skill: global, workspace, user, or shared team.
4. Enable/disable status, version, owner, and audit metadata.
5. A deterministic execution order so hooks are predictable.
6. Guardrails so user-scoped hooks cannot leak into another user's private mode.

For now, hooks and skills should be understood as fixed code plus stored
routine-rule metadata, not as a complete dynamic orchestration system.

## 2026-06-28 - PDF, Image, And OCR Intake Follow-Up

### Current Behavior

The agent now accepts common image formats and PDFs through the UI attachment
flow.

- Browser image files and pasted screenshots are sent to the backend as inline
  base64 bytes.
- The backend converts image bytes into OpenAI-compatible `image_url` content
  parts for FredAI vision.
- PDFs first try direct text extraction through `pypdf`.
- If a PDF appears image-heavy or has too little extractable text, the backend
  can render PDF pages to PNG images through `PyMuPDF` and pass those images to
  FredAI vision.

### Important Limitation

This is not a complete professional OCR/PDF understanding pipeline yet.

For images, the current code relies on FredAI's multimodal model to inspect the
image. It does not run local OCR before the model call.

For PDFs, direct text extraction works for selectable-text PDFs, but scanned
PDFs, screenshots inside PDFs, complex tables, rotated pages, handwritten
content, charts, forms, and multi-column layouts may need stronger processing.
The current PDF path is intentionally simple and bounded for prototype use.

### Why This Matters

FredAI vision may be good enough for screenshots and many scanned pages, but
OCR/layout extraction is a separate engineering concern. A production-quality
document agent may need:

- OCR for scanned text,
- layout-aware parsing for tables and forms,
- page range selection,
- file-size and page-count policy,
- password/encrypted PDF handling,
- better table reconstruction,
- explicit confidence/warning messages,
- trace metadata showing which pages were text-extracted vs rendered.

### Recommended Follow-Up

Evaluate one of these approaches before broad rollout:

1. Keep the current lightweight path for screenshots and simple PDFs.
2. Add optional local OCR if the work environment allows it, such as
   Tesseract/pytesseract or another approved OCR engine.
3. Add a more professional PDF pipeline for table/layout extraction, with
   page-level tracing and clear failure messages.
4. Add a policy switch so sensitive documents can be processed by local text/OCR
   extraction before any image bytes are sent to FredAI vision.

Until that work is done, PDF and image intake should be considered functional
prototype support, not final document-intelligence infrastructure.

## 2026-06-28 - WeKnora-Style Knowledge Memory System

### Goal

Add a durable EVA/Macs/process knowledge layer inspired by Tencent WeKnora,
without bringing in WeKnora's Go backend, Redis/asynq queue, vector databases,
Neo4j graph stack, or heavy OCR/parser dependencies.

The design separates:

- conversation/session memory,
- curated operating memory,
- workspace notes,
- source-document knowledge chunks,
- curated wiki pages,
- wiki correction issues.

This distinction is important. Chat history remembers what users said. The new
knowledge layer remembers governed source material and wiki synthesis with
references.

### Source Pattern Followed From WeKnora

The implementation follows these WeKnora patterns:

1. Knowledge bases, documents, chunks, wiki pages, and issues are separate
   durable records.
2. Documents are normalized into chunks before retrieval.
3. Chunking is adaptive:
   - heading-aware when Markdown headings exist,
   - heuristic for PDF/report-style boundaries,
   - recursive separator splitting as fallback.
4. Parent-child retrieval is supported:
   - child chunks are small search targets,
   - parent chunks are larger context blocks for deep read.
5. Search and deep read are distinct:
   - `knowledge_search` / `knowledge_grep` return candidates,
   - `knowledge_read` loads full source context before answer generation.
6. Wiki pages are curated synthesis, not raw source.
7. Wiki pages keep `source_refs` and `chunk_refs`.
8. Corrections are logged as issues instead of silently overwriting governed
   process knowledge.

### Files Added

- `app/knowledge_chunker.py`
  - Python implementation of WeKnora-style adaptive chunking.
  - Provides `split`, `split_parent_child`, `SplitterConfig`, and document
    profiling.
  - Supports heading, heuristic, and recursive fallback strategies.
  - Protects tables, Markdown links/images, fenced code blocks, and LaTeX
    blocks from careless splitting.

- `app/knowledge_store.py`
  - SQLite-backed knowledge/wikis/issues store.
  - Adds tables for:
    - `knowledge_bases`
    - `knowledge_documents`
    - `knowledge_chunks`
    - `knowledge_chunks_fts`
    - `wiki_pages`
    - `wiki_page_revisions`
    - `wiki_issues`
    - `retrieval_events`
  - Implements ingest, FTS search, regex grep, deep read, wiki write/read/search,
    wiki issue create/list/update, and retrieval-event logging.

- `tests/test_knowledge_memory.py`
  - Covers heading/parent-child chunking.
  - Covers ingest, search, deep read, wiki write/read, and correction issues.
  - Covers tool-registry integration for knowledge tools.

### Files Changed

- `app/tools.py`
  - Adds `KnowledgeStore` to `ToolContext`.
  - Registers new concise knowledge/wiki tool set:
    - `knowledge_ingest`
    - `knowledge_search`
    - `knowledge_grep`
    - `knowledge_read`
    - `wiki_search`
    - `wiki_read`
    - `wiki_write`
    - `wiki_issue`
  - Tool descriptions explicitly tell the model when search is enough and when
    deep read is mandatory.

- `app/orchestrator.py`
  - Owns `self.knowledge_store`.
  - Passes the store into tool execution context.
  - Adds knowledge-specific instructions to the system prompt.
  - Adds a small automatic knowledge prefetch block with candidate wiki pages
    and source chunks when the query matches existing knowledge.
  - Adds user-facing progress messages for knowledge/wiki tools.

- `app/api_server.py`
  - Adds knowledge counts to `/health`.
  - Adds `/agent/tools` to inspect the exact tool schemas passed to FredAI.

### Runtime Call Round

For a normal user request:

1. `/agent/respond` receives message, session ID, workspace ID, user ID, and
   attachments.
2. `WorkspaceAgentOrchestrator.respond()` stores the user message.
3. Recent session messages are loaded.
4. Curated memory and SQLite memory prefetch run.
5. Knowledge prefetch searches wiki pages and source chunks for lightweight
   candidate hints.
6. FredAI receives:
   - system instructions,
   - recent session context,
   - memory/knowledge prefetch hints,
   - tool schemas.
7. If FredAI calls a tool, Python executes it through `ToolRegistry`.
8. Tool results are appended as `role=tool` messages.
9. FredAI is called again until it answers or reaches the iteration limit.
10. The final answer, tool calls, traces, and request metrics are stored.

### Example: Digest A Document

User:

> Digest this EVA user guide and add it to the knowledge base.

Expected model behavior:

1. The attachment text is visible in the user message.
2. FredAI calls `knowledge_ingest` with:
   - `title`: `EVA User Guide`
   - `content`: extracted attachment text
   - `process`: `EVA`
   - `doc_type`: `user_guide`
   - `source_type`: `attachment`
   - `knowledge_base`: `CRT Analytics`
3. The tool creates document/chunk records.
4. FredAI may call `knowledge_read` on a few returned chunks.
5. FredAI may call `wiki_write` to create or update pages such as:
   - `eva-overview`
   - `eva-runbook`
   - `eva-inputs`
6. Final answer tells the user what was ingested and how many chunks/pages were
   created.

### Example: Ask For Reference

User:

> What does the EVA guide say about Macs upstream outputs? Give references.

Expected model behavior:

1. FredAI calls `wiki_search` for `EVA Macs upstream outputs`.
2. If a relevant wiki page exists, FredAI calls `wiki_read`.
3. FredAI calls `knowledge_search` or `knowledge_grep` for source evidence.
4. FredAI calls `knowledge_read` on the selected `chunk_ids`.
5. Final answer cites document title, section path, source URI, and chunk index.

### Tool Presentation To FredAI

Tools are passed as OpenAI-compatible chat-completions function schemas:

```json
{
  "type": "function",
  "function": {
    "name": "knowledge_search",
    "description": "Search source-document chunks ... call knowledge_read ...",
    "parameters": {
      "type": "object",
      "properties": {
        "query": {"type": "string"},
        "limit": {"type": "integer"}
      },
      "required": ["query"]
    }
  }
}
```

Use `/agent/tools` to inspect the exact live schemas.

### Known Limitations

- No vector embeddings yet. Retrieval is SQLite FTS5 plus LIKE/regex fallback.
- No true semantic reranker yet. FredAI can reason over returned candidates, but
  the backend does not yet perform model-based reranking.
- No automatic large-scale wiki map/reduce ingestion yet. `wiki_write` is
  available for agent-created pages after source reads.
- PDF/image OCR is still governed by the earlier prototype limitations.
- Knowledge bases are not privacy boundaries. The current design treats
  `knowledge_base` as a retrieval namespace; wiki links can cross processes.
- Factual answer quality depends on the prompt obeying the search -> deep-read
  discipline. This is now documented in the system prompt and tool descriptions,
  but should be evaluated with real FredAI behavior.

## 2026-06-28 - Knowledge Prefetch Performance Guard And Waiting Animation

### Concern

The first WeKnora-style runtime wiring ran lightweight knowledge prefetch on
every non-empty user turn. The prefetch is local SQLite work, not a FredAI/model
call, so it is much cheaper than a normal answer round. However, as the
knowledge store grows, even local retrieval should stay bounded and optional.

### Change

- Added `WORKSPACE_AGENT_KNOWLEDGE_PREFETCH_ENABLED`.
- Default remains `true`.
- If disabled, the orchestrator skips knowledge prefetch entirely.
- If enabled, the orchestrator now:
  - skips prefetch for very short queries,
  - first checks whether any retrievable chunks/wiki pages exist,
  - truncates the prefetch query to 240 characters,
  - returns at most 2 wiki-page hints and 2 source-chunk hints.

This keeps prefetch as a small hinting mechanism. The authoritative retrieval
path is still explicit tool use: `wiki_search` / `wiki_read` or
`knowledge_search` / `knowledge_grep` followed by `knowledge_read`.

### UI Change

The assistant waiting indicator changed from one pulsing dot to three staggered
blinking dots after the word `Thinking`. This is only a visual state change in
the browser UI and does not affect backend execution.

## 2026-06-28 - Sidebar Identity And Future Execution Visualization

### UI Change

- Removed the duplicate chat-pane title block that showed `Current thread` and
  the thread name above the conversation.
- The active thread identity now lives in the left sidebar thread list, where
  the selected thread is highlighted.
- Made the desktop sidebar sticky with its own 100vh layout so the thread list,
  status area, and logo stay visible while the conversation scrolls.
- Replaced the square letter-mark placeholder with a wide logo slot designed
  around a 3.5:1 image ratio. The current placeholder is
  `web/logo-placeholder.svg`; a real work logo can replace it later.

### Design Note

An interactive visualization of what the agent is doing could make this UI much
stronger. A future panel could show the live execution timeline: knowledge
prefetch, tool calls, file parsing, retrieved chunks, wiki reads, FredAI calls,
and final answer assembly. This should be designed as an operational trace view
for users, not only a developer/debug log.

## 2026-06-28 - Compact Attachment History Display

### Problem

The backend expands uploaded attachments into model-readable text such as
`[Attachment 1: ...]` followed by extracted document/table content. That is
useful for FredAI and tool execution, but it made reloaded chat history look
like a very long wall of raw attachment metadata and parsed file text.

### Change

- User messages now store lightweight display metadata alongside the expanded
  model-facing content:
  - `display_text`
  - lightweight `attachments` records with name, type/kind, size, extension,
    media type, and transfer mode
- `/agent/sessions/{session_id}` returns the compact display text and
  attachment chip metadata for user-visible history.
- The expanded content remains in the message `content` field and in the
  database so FredAI can still receive parsed attachment content as context.
- The API also compacts older historical messages that already contain embedded
  `[Attachment ...]` blocks by showing only the user text before the block and
  reconstructing basic attachment chips from the attachment headers.

### Future Work

True file re-download after page reload needs a real upload/storage endpoint.
The current change preserves attachment identity in chat history without storing
large file bytes in the session list response.
