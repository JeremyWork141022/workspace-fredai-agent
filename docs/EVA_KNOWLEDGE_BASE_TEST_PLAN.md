# EVA Knowledge Base Runtime Test Plan

This document is a practical handoff for testing the CRT Analytics Agent after ingesting the first EVA source documents. It is intentionally focused on what to do on the work computer, where the real `.runtime/state.sqlite3` knowledge base lives.

## Current Knowledge Snapshot

The local checkout used to write this document has an empty runtime database. That is expected if the EVA upload happened on a different work computer or a different runtime folder. The knowledge base is stored locally in SQLite, so the machine that performed the upload is the source of truth.

From the screenshot of the successful ingestion response, the work-computer runtime currently appears to contain at least this document:

- Knowledge base: `CRT Analytics`
- Title: `EVA EUC User Guide`
- File: `EVA EUC User Guide.docx`
- Version/date in file label: `Version 1.5, 2/25/2026`
- Document type: `user_guide`
- Process: `EVA`
- Source type: `attachment`
- Document ID: `doc_cb15d373aa20441992993d204bb4df24`
- Chunks created: `4 parent chunks / 48 child chunks`
- Chunking strategy reported by the agent: heading-based
- Tags: `EVA`, `EUC`, `CRT`, `MACS`, `Intex`, `DMS`, `MPAS`, `user_guide`

The ingestion response also says the document covers:

- EVA-CRT framework overview and what the EUC automates
- Access and keychain setup, including Denodo MSS, Denodo Pricing Engine, Atlas, Yellow Package, MFCARP, and Python 3.11.8 platform setup
- Preparation steps for `euc_params_setup.xlsx`, structure templates, Intex DM/DMS prep, PC 100 percent tab, AAA collateral balance, Gfee Coupon adjustment, PC spread, backend CRT pricing, retained B1H rule, and MCIP coupon hardcoding
- The three input files and their LAN paths
- Running the EUC through `EUC_EVA_User`, MACS output retrieval, and optional breakeven spread script
- Output control and the `Control_Output` workbook
- Output attributes such as `mn_income_lifetime`, `crt_cost_lifetime`, `mn_writedown`, `mn_edc`, `pc_net_income_lifetime`, `collat_dv01`, `macs_pool_edcpv`, tranche-level write-downs, distribution rule, and related EVA/breakeven spread tabs
- A note that the former step `1d` preparer/reviewer email signoff is deleted in this version

If you also uploaded EVA methodology documentation, confirm it appears as a second row in `knowledge_documents` using the checks below. Do not assume it is indexed until you see the title, doc type, process, and chunks.

## How To Check What Is In The Knowledge Base

### Fast UI Check

Ask the agent:

```text
List the documents currently indexed in the CRT Analytics knowledge base. Include document IDs, titles, doc types, process, tags, and chunk counts. Use the knowledge tools rather than guessing from chat history.
```

Then ask:

```text
Search the knowledge base for EVA, MACS output retrieval, and Control_Output. Read the best matches and tell me which document sections they came from.
```

Expected behavior:

- The agent should call `knowledge_search` or `knowledge_grep`.
- Before giving factual details, it should call `knowledge_read`.
- The final answer should cite document title, section path or chunk index, and source URI/file name.

### API Checks

Run these from PowerShell on the work computer while the server is running:

```powershell
Invoke-RestMethod "http://127.0.0.1:8000/health" | ConvertTo-Json -Depth 10
```

Look at:

- `knowledge.knowledge_bases`
- `knowledge.documents`
- `knowledge.chunks`
- `knowledge.wiki_pages`
- `knowledge.wiki_issues`

List the tool schemas that are passed to FredAI:

```powershell
Invoke-RestMethod "http://127.0.0.1:8000/agent/tools" | ConvertTo-Json -Depth 20
```

If another user opens the app from another machine, replace `127.0.0.1` with the host name, for example:

```powershell
Invoke-RestMethod "http://WXE2025002:8000/health" | ConvertTo-Json -Depth 10
```

### Direct SQLite Check

The default DB is `.runtime/state.sqlite3`, unless `WORKSPACE_AGENT_STATE_DB` points somewhere else.

Run this read-only inspection script from the project folder:

```powershell
@'
import json
import os
import sqlite3
from pathlib import Path

db = Path(os.environ.get("WORKSPACE_AGENT_STATE_DB") or ".runtime/state.sqlite3")
print("DB:", db.resolve())
conn = sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True)
conn.row_factory = sqlite3.Row

for table in [
    "knowledge_bases",
    "knowledge_documents",
    "knowledge_chunks",
    "wiki_pages",
    "wiki_issues",
    "retrieval_events",
    "request_metrics",
    "api_call_traces",
]:
    try:
        count = conn.execute(f"SELECT COUNT(*) AS c FROM {table}").fetchone()["c"]
    except Exception as exc:
        count = f"ERR: {exc}"
    print(f"{table}: {count}")

print("\nKnowledge bases:")
for row in conn.execute("""
    SELECT id, workspace_id, name, description, updated_at
    FROM knowledge_bases
    ORDER BY updated_at DESC
"""):
    print(dict(row))

print("\nDocuments:")
for row in conn.execute("""
    SELECT d.id, d.workspace_id, kb.name AS knowledge_base, d.title,
           d.source_type, d.source_uri, d.file_name, d.doc_type,
           d.process, d.tags_json, d.summary, d.updated_at,
           COUNT(c.id) AS chunk_count
    FROM knowledge_documents d
    JOIN knowledge_bases kb ON kb.id = d.knowledge_base_id
    LEFT JOIN knowledge_chunks c ON c.document_id = d.id
    GROUP BY d.id
    ORDER BY d.updated_at DESC
"""):
    item = dict(row)
    try:
        item["tags_json"] = json.loads(item["tags_json"] or "[]")
    except Exception:
        pass
    print(json.dumps(item, indent=2))

print("\nWiki pages:")
for row in conn.execute("""
    SELECT slug, title, page_type, status, summary, updated_at
    FROM wiki_pages
    ORDER BY updated_at DESC
"""):
    print(json.dumps(dict(row), indent=2))
'@ | python -
```

To inspect the EVA chunks:

```powershell
@'
import sqlite3
from pathlib import Path

db = Path(".runtime/state.sqlite3")
conn = sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True)
conn.row_factory = sqlite3.Row

doc_id = "doc_cb15d373aa20441992993d204bb4df24"
for row in conn.execute("""
    SELECT chunk_index, chunk_type, section_path, substr(content, 1, 500) AS preview
    FROM knowledge_chunks
    WHERE document_id = ?
    ORDER BY chunk_type DESC, chunk_index
    LIMIT 80
""", (doc_id,)):
    print("\n---")
    print("chunk_index:", row["chunk_index"])
    print("chunk_type:", row["chunk_type"])
    print("section:", row["section_path"])
    print(row["preview"])
'@ | python -
```

## What Happens When You Ask About EVA

The runtime path is:

1. Browser sends the message to `POST /agent/respond`.
2. `app.api_server` passes the request to `WorkspaceAgentOrchestrator.respond`.
3. The session is created or updated in SQLite.
4. The most recent user/assistant turns are loaded as short-term context. The default is `WORKSPACE_AGENT_SESSION_CONTEXT_MESSAGES=16`.
5. Long-term memory prefetch runs if enabled.
6. Knowledge prefetch runs if enabled:
   - It checks whether the workspace has any retrievable knowledge.
   - It searches candidate wiki pages with `search_wiki`.
   - It searches candidate source chunks with `search_chunks`.
   - It injects only lightweight hints into the latest user message.
7. FredAI receives:
   - System instructions
   - Recent session context
   - Any memory or knowledge prefetch hints
   - Tool schemas for all registered tools
8. For EVA questions, the model should call tools:
   - `wiki_search` and `wiki_read` first if a curated wiki page exists for EVA
   - `knowledge_search` for broad source retrieval
   - `knowledge_grep` for exact names such as `Control_Output`, `EUC_EVA_User`, `MACS`, `mn_income_lifetime`, or a script name
   - `knowledge_read` before answering from source-document evidence
9. Tool calls and results are added to the model loop.
10. The final assistant answer is stored, and request metrics/traces are recorded.

For a question like:

```text
What are the main steps to run the EVA EUC process and where does MACS fit in? Cite the source.
```

Good behavior is:

- Search source chunks for EVA/EUC/MACS.
- Read the best chunks.
- Answer in a process-step format.
- Cite `EVA EUC User Guide`, section/chunk information, and file/source.
- Say when something is inferred rather than directly stated.

Bad behavior is:

- Answering from general model memory without any retrieval.
- Mentioning specific EVA steps without source citation.
- Using only the current chat transcript if the answer should come from uploaded documentation.

## What Happens For A General Question

For a general prompt like:

```text
How are you, agent?
```

Expected behavior:

- The agent should answer conversationally.
- It usually should not call `knowledge_search`, `knowledge_grep`, `knowledge_read`, `wiki_search`, or `wiki_read`.
- Knowledge prefetch may run internally, but if the query is not related to EVA/process knowledge, it should return no useful candidates and inject nothing.

This matters for cost and speed. General chat should not trigger deep source-document reads.

## How To Monitor Runtime Behavior

### Use The Request ID

After each UI message, the left side of the UI shows the latest request ID. It looks like `req_...`.

Use it here:

```powershell
Invoke-RestMethod "http://127.0.0.1:8000/agent/traces/REQ_ID_HERE" | ConvertTo-Json -Depth 30
```

What to look for:

- `instructions`: confirms tool names and system instructions sent to the model.
- `prefetch`: confirms whether memory or knowledge prefetch injected context.
- `tool_options`: confirms the exact JSON schemas passed to FredAI.
- `model_request`: shows what was sent to FredAI.
- `model_response`: shows whether FredAI requested tool calls.
- `tool_call`: shows tool name and arguments.
- `tool_result`: shows retrieval results or read context returned to the model.

For EVA factual questions, you want to see:

- `knowledge_search`, `knowledge_grep`, or `wiki_search`
- Then `knowledge_read` or `wiki_read`
- Then a final answer with source citations

For a general question, you usually want to see:

- No knowledge tools
- Maybe no tools at all

### Use Request Metrics

Run:

```powershell
@'
import json
import sqlite3
from pathlib import Path

db = Path(".runtime/state.sqlite3")
conn = sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True)
conn.row_factory = sqlite3.Row

for row in conn.execute("""
    SELECT request_id, session_id, workspace_id, user_id, status,
           duration_ms, tool_call_count, tool_names_json, created_at
    FROM request_metrics
    ORDER BY id DESC
    LIMIT 20
"""):
    item = dict(row)
    try:
        item["tool_names_json"] = json.loads(item["tool_names_json"] or "[]")
    except Exception:
        pass
    print(json.dumps(item, indent=2))
'@ | python -
```

This gives a quick audit of whether questions are causing tool calls and how slow they are.

### Use Retrieval Events

The knowledge tools record retrieval events. Check them with:

```powershell
@'
import json
import sqlite3
from pathlib import Path

db = Path(".runtime/state.sqlite3")
conn = sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True)
conn.row_factory = sqlite3.Row

for row in conn.execute("""
    SELECT workspace_id, session_id, user_id, query, tool_name,
           result_refs_json, created_at
    FROM retrieval_events
    ORDER BY id DESC
    LIMIT 30
"""):
    item = dict(row)
    try:
        item["result_refs_json"] = json.loads(item["result_refs_json"] or "[]")
    except Exception:
        pass
    print(json.dumps(item, indent=2))
'@ | python -
```

Use this to verify that EVA questions found EVA chunks, not unrelated documents.

## Can You Look At The Real Wiki?

Yes, but right now the "wiki" is a backend knowledge structure, not a polished separate web page.

Current wiki storage:

- `wiki_pages`
- `wiki_page_revisions`
- `wiki_issues`

Current wiki tools:

- `wiki_search`
- `wiki_read`
- `wiki_write`
- `wiki_issue`

To view wiki pages from the agent UI:

```text
Use wiki_search for EVA. Then wiki_read every relevant page and show me the title, slug, summary, source_refs, chunk_refs, and content.
```

To view wiki pages from SQLite:

```powershell
@'
import json
import sqlite3
from pathlib import Path

db = Path(".runtime/state.sqlite3")
conn = sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True)
conn.row_factory = sqlite3.Row

for row in conn.execute("""
    SELECT slug, title, page_type, status, summary, content,
           source_refs_json, chunk_refs_json, links_json, version, updated_at
    FROM wiki_pages
    ORDER BY updated_at DESC
"""):
    item = dict(row)
    for key in ["source_refs_json", "chunk_refs_json", "links_json"]:
        try:
            item[key] = json.loads(item[key] or "[]")
        except Exception:
            pass
    print(json.dumps(item, indent=2))
'@ | python -
```

Recommended first EVA wiki page to create after ingestion:

```text
Create or update a wiki page named eva-euc-process-overview. Use the EVA EUC User Guide as source evidence. The page should summarize what EVA does, major inputs, setup requirements, MACS dependency, execution steps, outputs, controls, and open questions. Preserve chunk_refs.
```

## How To Correct Wrong Information

Use a correction workflow that separates short-term conversation memory from long-term knowledge.

### Step 1: Ask For The Source

If the agent gives a wrong EVA answer, ask:

```text
Which wiki page or source chunks did you use for that answer? Give me the document title, section path, chunk index, and exact supporting sentence.
```

If it cannot show a source, treat the answer as not grounded.

### Step 2: Choose The Correction Type

Use one of these paths:

- Source document is missing: ingest the missing document.
- Source document is outdated: ingest the newer document with clear metadata, date, version, and tags.
- Wiki summary is wrong: ask the agent to update the wiki page with `wiki_write`, preserving source refs and chunk refs.
- The issue needs review: ask the agent to create a `wiki_issue`.
- The correction is small and user-supplied: ask the agent to save it into a wiki page or manual knowledge document, not only to remember it in chat.

Example correction prompt:

```text
Correction: In the EVA EUC process, [state corrected fact]. Do not just remember this in the current chat. Create a wiki_issue for the incorrect statement, then update the EVA wiki page with this correction. Include my correction as evidence and mark whether it still needs source-document validation.
```

Example source-backed correction prompt:

```text
Correction with evidence: The current EVA user guide says [correct fact] in [document/section]. Use knowledge_search/knowledge_read to verify it, then update the EVA wiki page and preserve the chunk_refs.
```

### Step 3: Test Long-Term Memory, Not Short-Term Chat

Do not test only in the same thread. The same thread still contains your correction in recent chat context.

Test like this:

1. Create a new thread.
2. Ask the same question without mentioning the correction.
3. Check the trace.
4. Confirm the trace includes `wiki_search/wiki_read` or `knowledge_search/knowledge_read`.
5. Confirm the returned source is the corrected wiki page or corrected document.
6. Optional stronger test: restart the server, then ask in a new thread again.

If the answer is correct only in the old thread but wrong in a new thread, the correction did not reach long-term knowledge.

## Full Test Plan

### Phase 0: Runtime Baseline

1. Start the server:

```powershell
python -m uvicorn app.api_server:app --host 0.0.0.0 --port 8000 --reload
```

2. Confirm health:

```powershell
Invoke-RestMethod "http://127.0.0.1:8000/health" | ConvertTo-Json -Depth 10
```

3. Confirm tools:

```powershell
Invoke-RestMethod "http://127.0.0.1:8000/agent/tools" | ConvertTo-Json -Depth 20
```

4. Confirm DB counts using the SQLite script above.

Pass criteria:

- Server starts.
- `knowledge.documents` and `knowledge.chunks` are greater than zero after upload.
- `/agent/tools` returns 16 tools.
- Trace is enabled unless intentionally disabled.

### Phase 1: Document Ingestion Verification

Ask:

```text
List all knowledge documents for process EVA. Include title, doc type, source, tags, document ID, and chunk count.
```

Then ask:

```text
Find the EVA EUC User Guide chunks about MACS output retrieval, Control_Output, and euc_params_setup.xlsx. Read the relevant chunks and summarize only what the source says.
```

Pass criteria:

- It finds the EVA guide.
- It reads chunks before answering.
- It distinguishes the user guide from methodology documentation if both exist.
- It does not invent file paths or steps not in the chunks.

### Phase 2: EVA Reference Questions

Ask these one by one:

```text
What is the purpose of the EVA EUC process? Cite the source.
```

```text
What inputs do I need before running EVA? Cite the guide.
```

```text
Where does MACS fit in the EVA workflow? Cite the guide.
```

```text
What is Control_Output used for?
```

```text
Which output attributes are mentioned in the EVA guide?
```

For each request:

- Capture request ID.
- Open `/agent/traces/{request_id}`.
- Confirm source retrieval and deep read.
- Confirm answer cites title/section/chunk/source.

### Phase 3: General Chat Control Test

Ask:

```text
How are you, agent?
```

Then:

```text
Write a short Teams message saying I will review the EVA guide tomorrow.
```

Pass criteria:

- No knowledge tools unless the model reasonably needs process context.
- Fast response.
- No unnecessary source citations.

### Phase 4: Wiki Creation Test

Ask:

```text
Create a curated wiki page called eva-euc-process-overview from the EVA EUC User Guide. Include overview, prerequisites, inputs, execution, MACS dependency, outputs, controls, and open questions. Preserve chunk_refs.
```

Then ask:

```text
Read the eva-euc-process-overview wiki page and show me its source_refs and chunk_refs.
```

Pass criteria:

- `wiki_pages` count increases.
- Page has `slug`, `title`, `summary`, `content`, and refs.
- Future conceptual EVA questions use `wiki_search/wiki_read` first, then `knowledge_read` for exact evidence when needed.

### Phase 5: Correction Test

Use a safe test correction. For example, choose a minor non-critical wording correction or a deliberate temporary note clearly marked `needs_review`.

Ask:

```text
Create a wiki_issue for eva-euc-process-overview: test correction workflow only. The issue is that I need to verify whether [specific point] is still current in Version 1.5. Mark it pending.
```

Then:

```text
List pending wiki issues for EVA.
```

Then start a new thread and ask:

```text
Are there any open EVA wiki issues I should know about?
```

Pass criteria:

- New thread can retrieve the issue from long-term wiki issue storage.
- Trace shows `wiki_issue` list or relevant wiki retrieval.
- The answer does not depend on the old chat.

### Phase 6: Regression Checklist

After every future ingestion or correction:

- Confirm `/health` counts.
- Confirm document row metadata.
- Confirm chunk count.
- Run one source-backed query.
- Run one general chat query.
- Inspect trace for both.
- If a wiki page was changed, inspect `wiki_page_revisions`.

## Current Tool List Passed To FredAI

The current runtime registers 16 tools:

1. `memory`: save compact curated memory.
2. `workspace_note_save`: save durable workspace notes.
3. `workspace_note_search`: search durable workspace notes.
4. `session_search`: search saved conversation history.
5. `routine_rule`: save future behavior, hooks, reminders, reusable workflows, or tool requests.
6. `workspace_read_file`: read a UTF-8 text file under `WORKSPACE_AGENT_ROOT`.
7. `workspace_list_files`: list files under `WORKSPACE_AGENT_ROOT`.
8. `workspace_find_files`: find likely workspace files from a natural-language query.
9. `knowledge_ingest`: ingest a document into the source knowledge store.
10. `knowledge_search`: broad source-document chunk retrieval.
11. `knowledge_grep`: exact keyword or regex source retrieval.
12. `knowledge_read`: deep-read chunks, parents, and neighbors before factual answers.
13. `wiki_search`: search curated wiki pages.
14. `wiki_read`: read curated wiki pages.
15. `wiki_write`: create or update curated wiki pages.
16. `wiki_issue`: create, list, or update correction issues.

For EVA documentation QA, the most important tools are:

- `knowledge_search`
- `knowledge_grep`
- `knowledge_read`
- `wiki_search`
- `wiki_read`
- `wiki_write`
- `wiki_issue`

## How The Tool Guidance Is Presented To FredAI

The system instructions tell FredAI:

- Use `knowledge_ingest` when the user asks to digest, index, add, or remember a source document.
- Use `wiki_search/wiki_read` first for conceptual process questions when curated wiki pages exist.
- Use `knowledge_search` for broad source-document retrieval.
- Use `knowledge_grep` for exact terms, script names, metrics, field names, model names, dates, or IDs.
- After `knowledge_search` or `knowledge_grep`, call `knowledge_read` before giving factual answers from source documents.
- Cite document titles, source paths, sections, and chunk indexes when source memory is used.
- Use `wiki_write` only after reading source evidence or when the user explicitly supplies a correction.
- Keep `source_refs` and `chunk_refs`.
- Use `wiki_issue` when a user reports wrong, missing, contradictory, or stale wiki/process knowledge.

The tool schemas also repeat the important rule: search returns candidates only; `knowledge_read` is the mandatory deep-read step before source-grounded answers.

## Turning EVA Python Scripts Into Tools

The goal is to let the agent become a work buddy that can execute the EVA process step by step, while still staying auditable and safe.

### Step 1: Inventory The Scripts

Create a table for every script:

- Script path
- EVA process step
- Inputs
- Outputs
- Side effects
- Required network/database access
- Required credentials or keychain dependencies
- Expected runtime
- Whether it can run in dry-run mode
- Whether it writes files
- Whether human approval is required

Suggested first tool groups:

- `eva_validate_environment`: check Python version, packages, folders, permissions, keychain/access prerequisites.
- `eva_locate_inputs`: verify the three required input files and LAN paths.
- `eva_validate_euc_params`: inspect `euc_params_setup.xlsx` for required tabs/fields.
- `eva_prepare_intex_dms`: run or validate Intex DM/DMS preparation steps.
- `eva_fetch_macs_output`: retrieve or validate MACS output availability.
- `eva_run_euc`: run the main `EUC_EVA_User` flow.
- `eva_run_breakeven_spread`: run optional breakeven spread calculation.
- `eva_validate_control_output`: validate `Control_Output` workbook.
- `eva_summarize_outputs`: summarize output attributes and artifacts.

### Step 2: Wrap Scripts In Python Functions

Do not expose raw shell commands directly to the model. Wrap each script in a deterministic Python function:

```python
def eva_validate_euc_params(path: str, *, dry_run: bool = True) -> dict:
    ...
```

Each function should return JSON-like data:

```python
{
    "ok": True,
    "step": "validate_euc_params",
    "inputs": {"path": "..."},
    "outputs": [],
    "warnings": [],
    "errors": [],
    "duration_ms": 1234,
}
```

### Step 3: Add Tool Schemas

Add a concise tool schema in `app/tools.py` for each wrapper. The schema should:

- Use explicit fields.
- Avoid free-form command strings.
- Have `dry_run` where possible.
- Include `workspace_path` or named file inputs.
- Return artifact paths rather than huge file contents.
- Never return secrets.

### Step 4: Add Safety Controls

For tools that write files or launch long jobs:

- Require `dry_run=false` explicitly.
- Require a target folder under an approved workspace root.
- Log request ID, session ID, user ID, script path, start/end time, and artifacts.
- Use timeouts.
- Capture stdout/stderr into runtime logs or artifacts.
- Return a short summary to the model.

### Step 5: Orchestrate Multi-Step EVA

Keep individual tools small. Later, add an orchestration helper such as:

- `eva_plan_run`: create a step plan from available inputs.
- `eva_next_step`: inspect current state and recommend the next tool.
- `eva_run_step`: execute one approved step.

Do not start with a single giant "run everything" tool. That makes debugging, approval, audit, and recovery harder.

### Step 6: Test Without FredAI First

For every EVA tool:

- Unit test the Python wrapper directly.
- Run a dry-run command.
- Run with known sample inputs.
- Confirm output JSON shape.
- Confirm errors are readable.
- Confirm no secrets leak.

Only then expose it to FredAI.

## Ideal Tomorrow Work Order

Use this order in the next work session:

1. Start server on the work computer.
2. Confirm `/health`.
3. Run SQLite KB inventory.
4. Verify both EVA documents are indexed.
5. Ask 5 EVA source-backed questions and inspect traces.
6. Ask 2 general questions and confirm no unnecessary retrieval.
7. Create the first EVA wiki page.
8. Read the wiki page and inspect refs.
9. Run one correction workflow test in a new thread.
10. Inventory EVA Python scripts into a script-to-tool table.
11. Pick one safe read-only script and design its first wrapper tool.

## Pass Or Fail Definition

The EVA knowledge system is working well enough for the next phase when:

- EVA documents are visible in `knowledge_documents`.
- EVA chunks are visible in `knowledge_chunks`.
- EVA questions trigger retrieval and deep read.
- Answers include source citations.
- General chat does not overuse knowledge tools.
- Wiki pages can be created, read, and revised.
- Corrections survive a new thread and preferably a server restart.
- Traces prove what the model did.

If any of those fail, collect:

- The exact user question
- Request ID
- `/agent/traces/{request_id}` output
- Relevant DB rows from `knowledge_documents`, `knowledge_chunks`, `wiki_pages`, `retrieval_events`, and `request_metrics`
- The answer the agent gave
- The answer you expected

That packet is enough to debug whether the issue is ingestion, retrieval, tool selection, FredAI behavior, wiki quality, or UI visibility.
