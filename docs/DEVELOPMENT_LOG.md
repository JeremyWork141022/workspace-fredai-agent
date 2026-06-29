# Development Log

This log records implementation decisions, known concerns, and follow-up work for
the CRT Analytics Agent / FredAI workspace agent.

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

### Current Drawer Trigger

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
