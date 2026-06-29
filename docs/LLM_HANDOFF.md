# CRT Analytics Agent - LLM Handoff

This document is the detailed handoff for the next LLM session on Jeremy's work computer. It explains what the project is, why it exists, how the files connect, how the runtime works, how to run it, and where to continue safely.

Repository:

```text
https://github.com/JeremyWork141022/workspace-fredai-agent
```

Expected work-computer copy location used during setup:

```text
Z:\hq42p2v5\MF_PORTVAL\EVA_User\Agent
```

The repository was intentionally built as a FredAI-only workspace agent. It should connect to the internal FredAI API gateway and should not call OpenAI, Anthropic, LangChain, Weixin, or Codex services directly.

## 1. Executive Summary

The project is a Python FastAPI application that exposes a local workspace agent over HTTP and a browser UI.

The main user-facing path is:

```text
Browser UI at http://127.0.0.1:8000/
    -> POST /agent/respond
    -> WorkspaceAgentOrchestrator
    -> FredAI OAuth token
    -> FredAI chat/completions API
    -> local tools and memory if FredAI requests tool calls
    -> response stored in SQLite and returned to UI
```

The agent can currently:

- Chat with the user through a browser UI.
- Call FredAI through the internal OAuth/FredAI gateway.
- Keep session history in SQLite.
- Search older session history with SQLite FTS.
- Keep curated memory in Markdown files.
- Keep workspace notes and routine rules in SQLite.
- Execute a small set of safe local workspace tools.
- Create and run scheduled recurring prompt jobs.
- Record debug traces for every request.

The implementation is intentionally lightweight:

- No LangChain.
- No external vector database.
- No direct OpenAI API.
- No direct Anthropic API.
- No hidden web app framework beyond FastAPI plus static HTML/CSS/JS.

## 2. Runtime Entry Points

There are three practical ways to interact with the agent.

### 2.1 Browser UI

Start the server:

```powershell
cd Z:\hq42p2v5\MF_PORTVAL\EVA_User\Agent
.\.venv\Scripts\Activate.ps1
python -m uvicorn app.api_server:app --host 127.0.0.1 --port 8000
```

Open:

```text
http://127.0.0.1:8000/
```

The UI sends requests to `/agent/respond`. It also shows:

- server status from `/health`
- model name
- memory tool count
- current session id
- last request id
- a trace link for each agent response

### 2.2 Direct PowerShell API Call

```powershell
$body = @{
  workspace_id = "workspace_123"
  user_id = "jeremy"
  session_id = $null
  message = "Remember that I prefer concise workspace summaries."
  attachments = @()
} | ConvertTo-Json -Depth 10

Invoke-RestMethod `
  -Method Post `
  -Uri "http://127.0.0.1:8000/agent/respond" `
  -ContentType "application/json" `
  -Body $body
```

### 2.3 Scheduler / Automation

The agent can create scheduled jobs through the `routine_rule` tool when FredAI chooses that tool. Jobs are stored in SQLite. The server starts a background scheduler when:

```env
WORKSPACE_AGENT_SCHEDULER_ENABLED=true
```

Manual endpoints:

```text
GET  /scheduler/jobs
POST /scheduler/run-pending
```

## 3. Required Environment

### 3.1 Python

Use Python 3.11 if possible. The work computer screenshots showed:

```text
C:\Program Files\Python311\...
```

That version is fine.

Create the virtual environment from the project root:

```powershell
cd Z:\hq42p2v5\MF_PORTVAL\EVA_User\Agent
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

If the prompt shows `(.venv)`, the virtual environment is active.

### 3.2 Python Packages

`requirements.txt` should contain:

```text
fastapi
uvicorn[standard]
httpx
python-dotenv
pydantic
```

These are enough for the current runtime. If startup fails after these install successfully, the issue is usually code/import/env, not missing packages.

### 3.3 `.env`

The application reads `.env` from the project root using `python-dotenv`. The current GitHub version accepts both the new `FREDAI_*` names and the older legacy FredAI names.

Preferred names:

```env
WORKSPACE_AGENT_HOME=.runtime
WORKSPACE_AGENT_STATE_DB=.runtime/state.sqlite3
WORKSPACE_AGENT_MEMORY_DIR=.runtime/memories
WORKSPACE_AGENT_ROOT=.

FREDAI_PRESET=Direct_Azure
FREDAI_MODEL=azure-openai-chat
FREDAI_STREAM=true
FREDAI_VERIFY_SSL=false
FREDAI_TIMEOUT_SECONDS=120

FREDAI_OAUTH_URL=https://auth.fhlmc.com/as/token.oauth2
FREDAI_CLIENT_ID=your-client-id
FREDAI_CLIENT_SECRET=your-client-secret
FREDAI_OAUTH_USERNAME=your-oauth-username
FREDAI_OAUTH_PASSWORD_B64=base64-encoded-password
FREDAI_JWT_TOKEN=

WORKSPACE_AGENT_MAX_AGENT_ITERATIONS=4
WORKSPACE_AGENT_SESSION_CONTEXT_MESSAGES=16
WORKSPACE_AGENT_MEMORY_PREFETCH_ENABLED=true
WORKSPACE_AGENT_SESSION_SEARCH_AUX_ENABLED=true
WORKSPACE_AGENT_TRACE_ENABLED=true
WORKSPACE_AGENT_TRACE_FULL_MEDIA=false
WORKSPACE_AGENT_SCHEDULER_ENABLED=true
```

Legacy names also accepted by the GitHub `main` version:

```env
OAUTH_URL=https://auth.fhlmc.com/as/token.oauth2
CLIENT_ID=your-client-id
CLIENT_SECRET=your-client-secret
OAUTH_USERNAME=your-oauth-username
OAUTH_PASSWORD=base64-encoded-password
JWT_TOKEN=
```

Important:

- `FREDAI_OAUTH_USERNAME` must be the OAuth username.
- `FREDAI_OAUTH_PASSWORD_B64` must be the base64-encoded password.
- In one work-computer screenshot, these two appeared swapped. That will pass the "env exists" check but fail real OAuth.
- `FREDAI_JWT_TOKEN` / `JWT_TOKEN` is optional. If blank, the client simply does not send the `x-jwt-token` header.

Encode a password in PowerShell:

```powershell
[Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes("your-password"))
```

Never paste real secrets into committed docs. Rotate credentials if a screenshot with secrets was shared outside the trusted environment.

## 4. Project Tree

Current important repository structure:

```text
.
|-- README.md
|-- requirements.txt
|-- .env.example
|-- app
|   |-- __init__.py
|   |-- __main__.py
|   |-- api_server.py
|   |-- config.py
|   |-- fredai_auth.py
|   |-- fredai_client.py
|   |-- memory_manager.py
|   |-- memory_store.py
|   |-- orchestrator.py
|   |-- scheduler.py
|   |-- session_store.py
|   `-- tools.py
|-- docs
|   |-- ARCHITECTURE.md
|   |-- FEATURES.md
|   |-- TEST_PLAN.md
|   |-- TROUBLESHOOTING.md
|   |-- WORK_COMPUTER_SETUP.md
|   `-- LLM_HANDOFF.md
|-- scripts
|   `-- repair_work_copy.ps1
|-- tests
|   `-- test_runtime.py
|-- web
|   |-- index.html
|   |-- app.js
|   `-- styles.css
`-- .runtime
    |-- state.sqlite3
    |-- cache
    |   `-- .keep
    `-- memories
        `-- MEMORY.md
```

`.runtime` is runtime state. This repo currently does not use `.gitignore` because the user explicitly requested all files be uploaded. Still, on the work computer, treat `.env` and production runtime DB contents as sensitive.

## 5. Module Responsibilities

### 5.1 `app/api_server.py`

This is the FastAPI entry point used by Uvicorn:

```powershell
python -m uvicorn app.api_server:app --host 127.0.0.1 --port 8000
```

Responsibilities:

- Load config with `load_config()`.
- Construct `WorkspaceAgentOrchestrator`.
- Construct `CronScheduler`.
- Serve static UI files from `web/`.
- Expose agent and scheduler API endpoints.
- Start/stop the scheduler during FastAPI startup/shutdown.

Important globals created at import time:

```python
config = load_config()
orchestrator = WorkspaceAgentOrchestrator(config)
scheduler = CronScheduler()
app = FastAPI(...)
```

Because these objects are created at import time, missing env or broken imports can appear immediately when running:

```powershell
python -c "import app.api_server; print('api server ok')"
```

Endpoints:

- `GET /`: returns `web/index.html`.
- `GET /health`: returns model/base URL/scheduler/memory/auth config status.
- `POST /agent/respond`: one agent turn.
- `GET /agent/sessions`: list sessions.
- `GET /agent/traces/{request_id}`: retrieve trace events.
- `GET /scheduler/jobs`: list jobs.
- `POST /scheduler/run-pending`: run due jobs once.

### 5.2 `app/config.py`

Configuration owner. It loads `.env`, computes runtime paths, and returns an immutable `AppConfig`.

Important functions:

- `project_root()`: repo root.
- `_read_dotenv()`: loads `.env` from repo root.
- `app_home()`: default `.runtime`.
- `state_db_path()`: default `.runtime/state.sqlite3`.
- `memory_dir()`: default `.runtime/memories`.
- `workspace_root()`: default project root, controlled by `WORKSPACE_AGENT_ROOT`.
- `load_env_file()`: simple parser for env file diagnostics.
- `_fredai_base_url()`: maps FredAI presets to base URLs.
- `load_config()`: returns the `AppConfig` dataclass.

FredAI preset URLs:

```python
FREDAI_PRESET_URLS = {
    "Proxy_Azure": "http://localhost:3003/v1/",
    "Local_Azure": "http://localhost:3000/fredai-orchestration-service/api",
    "Direct_Azure": "https://apigee-prod.itp01.p.fhlmc.com/genpop-virtual-expert/api/user/",
}
```

The GitHub `main` version includes `_env_first()`, so this works:

```python
fredai_client_id=_env_first("FREDAI_CLIENT_ID", "CLIENT_ID")
```

This compatibility matters because Jeremy's older working FredAI project used names like `CLIENT_ID`, `CLIENT_SECRET`, `OAUTH_USERNAME`, and `OAUTH_PASSWORD`.

### 5.3 `app/fredai_auth.py`

FredAI OAuth token helper.

Classes:

- `FredAIAuthError`: raised when auth config/token fetch fails.
- `FredAIToken`: token object with expiry helpers.
- `FredAIAuth`: performs password-grant OAuth and caches the token.

Flow:

```text
FredAIClient._headers()
    -> FredAIAuth.token()
        -> FredAIAuth._fetch_token()
            -> verify required config
            -> base64-decode password
            -> POST FREDAI_OAUTH_URL form data
            -> parse access_token
```

OAuth request form data:

```text
grant_type=password
client_id=<config value>
client_secret=<config value>
username=<config value>
password=<decoded base64 password>
```

Headers:

```text
Content-Type: application/x-www-form-urlencoded
```

`FredAIAuthError` should be imported by both `fredai_client.py` and `orchestrator.py`. If the work copy says:

```text
ImportError: cannot import name 'FredAIAuthError'
```

then `app/fredai_auth.py` is stale and must be updated from GitHub.

### 5.4 `app/fredai_client.py`

Small OpenAI-compatible chat-completions client pointed only at FredAI.

Classes:

- `FredAIClientError`
- `ChatCompletionResult`
- `FredAIClient`

The chat URL is:

```text
<fredai_base_url>/chat/completions
```

Headers:

```text
Authorization: Bearer <oauth_access_token>
Content-Type: application/json
x-jwt-token: <optional JWT only if configured>
```

Payload shape:

```json
{
  "model": "azure-openai-chat",
  "messages": [],
  "temperature": 0.1,
  "max_tokens": 4096,
  "stream": true,
  "tools": [],
  "tool_choice": "auto"
}
```

Streaming:

- Uses Server-Sent Events lines.
- Parses `data: ...`.
- Ignores `[DONE]`.
- Reassembles assistant text deltas.
- Reassembles streamed tool-call deltas by tool call index.

The unit test `FredAIStreamParserTests` verifies streamed tool call assembly.

### 5.5 `app/orchestrator.py`

This is the core agent runtime.

Main class:

```python
WorkspaceAgentOrchestrator
```

Owned objects:

- `SessionStore`
- `MemoryStore`
- `AgentMemoryManager`
- `ToolRegistry`
- `FredAIClient`

Important public methods:

- `respond(...)`: handles one user turn.
- `run_scheduled_job(job)`: converts a scheduled job into an agent turn.

High-level `respond()` flow:

```text
1. Create request_id.
2. Get or create session.
3. Create trace recorder.
4. Read recent model messages.
5. Store current user message.
6. Build model input messages.
7. Run agent loop.
8. Catch auth/model/internal errors and convert them into user-visible answers.
9. Store assistant answer.
10. Store request metrics.
11. Sync memory and queue prefetch if successful.
12. Return AgentResponse.
```

Agent loop:

```text
1. Build ToolContext.
2. Build system instructions.
3. Load tool schemas.
4. Prefetch memory and inject it into the latest user message.
5. Send messages/tools to FredAI.
6. If FredAI returns no tool calls, return final answer.
7. If FredAI returns tool calls:
   a. Store tool_call message.
   b. Execute local tool via ToolRegistry.
   c. Store tool_result message.
   d. Append tool result back into working messages.
   e. Continue loop.
8. Stop at WORKSPACE_AGENT_MAX_AGENT_ITERATIONS.
```

Error statuses returned to UI/API:

- `success`
- `auth_error`
- `model_error`
- `error`

The UI marks non-success messages as error-styled messages.

### 5.6 `app/tools.py`

Tool registry and tool implementation.

Core classes:

- `ToolContext`
- `ToolSpec`
- `ToolRegistry`

`ToolRegistry.definitions()` converts local `ToolSpec` objects into OpenAI-compatible function tool schemas for FredAI.

`ToolRegistry.execute()` dispatches tool calls by name and wraps results:

```json
{
  "ok": true,
  "tool": "tool_name",
  "result": {}
}
```

Registered tools:

- `memory`
- `workspace_note_save`
- `workspace_note_search`
- `session_search`
- `routine_rule`
- `workspace_read_file`
- `workspace_list_files`
- `workspace_find_files`

Workspace file tools are intentionally read-only and constrained under `WORKSPACE_AGENT_ROOT`.

Important safety function:

```python
_resolve_workspace_path(...)
```

It resolves absolute/relative paths and rejects paths outside `WORKSPACE_AGENT_ROOT`.

### 5.7 `app/session_store.py`

SQLite-backed session/message/trace/request-metric store.

Primary tables:

- `sessions`
- `messages`
- `request_metrics`
- `api_call_traces`
- `messages_fts`
- `messages_fts_trigram` when supported

Important methods:

- `get_or_create_session()`
- `append_message()`
- `recent_model_messages()`
- `list_sessions()`
- `record_request_metric()`
- `record_trace_event()`
- `get_trace_events()`
- `search_message_context()`
- `get_messages_as_conversation()`

`messages_fts` is FTS5-backed search. `messages_fts_trigram` helps CJK / substring-style matching if SQLite supports the trigram tokenizer.

The store writes to:

```text
.runtime/state.sqlite3
```

unless `WORKSPACE_AGENT_STATE_DB` overrides it.

### 5.8 `app/memory_store.py`

SQLite memory primitives.

Tables:

- `memories`
- `memory_turns`
- `routine_rules`
- `workspace_notes`

Data types:

- `MemoryRecord`
- `MemoryTurnRecord`
- `RoutineRuleRecord`
- `WorkspaceNoteRecord`

Important methods:

- `remember()`
- `search()`
- `record_turn()`
- `search_turns()`
- `save_routine_rule()`
- `search_routine_rules()`
- `triggered_routine_rules()`
- `save_workspace_note()`
- `search_workspace_notes()`

`routine_rules` can represent:

- hooks
- scheduled jobs
- reusable workflow candidates
- future tool requests
- curated memory requests
- SQLite memory requests
- unsupported requests

### 5.9 `app/memory_manager.py`

High-level memory orchestration layer.

It combines:

- file-backed curated memory
- SQLite memories
- workspace notes
- triggered routine rules

Key classes:

- `CuratedMemoryStore`
- `MemoryProvider`
- `BuiltinCuratedMemoryProvider`
- `LocalSQLiteMemoryProvider`
- `WorkspaceMemoryProvider`
- `AgentMemoryManager`

Curated memory files:

```text
.runtime/memories/MEMORY.md
```

`MEMORY.md` is the only curated always-on Markdown memory. It is for stable agent/project operating facts, retrieval discipline, and correction policy.

Large facts, raw logs, old conversations, document extracts, and project notes should go to SQLite workspace notes or session history, not curated memory.

The manager exposes memory-related tool schemas to `tools.py`, then `tools.py` registers them into the `ToolRegistry`.

### 5.10 `app/scheduler.py`

SQLite-backed scheduler for recurring prompts.

Tables:

- `cron_jobs`
- `cron_runs`

Job types:

- interval job
- daily job

Important methods:

- `add_interval_job()`
- `add_daily_job()`
- `list_jobs()`
- `set_status()`
- `delete_job()`
- `start()`
- `stop()`
- `run_pending_once()`

When FastAPI starts and scheduler is enabled, `api_server.py` runs:

```python
await scheduler.start(orchestrator.run_scheduled_job)
```

When a job is due:

```text
CronScheduler
    -> WorkspaceAgentOrchestrator.run_scheduled_job()
        -> WorkspaceAgentOrchestrator.respond()
```

### 5.11 `app/__init__.py`

Package initializer.

Current healthy version should be lightweight. It should not import the whole orchestrator unless there is a real need, because importing too much from `__init__.py` can create circular or stale import failures.

It currently exports config helpers.

If a work copy has:

```python
from app.orchestrator import WorkspaceAgentOrchestrator
```

inside `app/__init__.py` and startup fails, it may be a stale or risky copy. Use GitHub `main` as source of truth.

### 5.12 `app/__main__.py`

Convenience entry point. It exists so the package can be invoked as:

```powershell
python -m app
```

Check the file before relying on it for production startup. The most reliable startup command is still:

```powershell
python -m uvicorn app.api_server:app --host 127.0.0.1 --port 8000
```

### 5.13 `web/index.html`, `web/app.js`, `web/styles.css`

Static browser UI.

The FastAPI server mounts:

```python
app.mount("/static", StaticFiles(directory=WEB_ROOT), name="static")
```

The root endpoint returns:

```python
FileResponse(WEB_ROOT / "index.html")
```

`web/app.js`:

- calls `/health`
- sends JSON to `/agent/respond`
- stores returned `session_id`
- displays `request_id`
- links to `/agent/traces/{request_id}`
- supports "New Session"
- supports "Copy Session"
- submits with normal form submit or Ctrl+Enter

The UI is intentionally a local operational UI, not a public SaaS-style app. It has no login/auth layer yet.

### 5.14 `tests/test_runtime.py`

Offline tests.

They do not call FredAI.

Current coverage:

- session creation, message append, and search
- workspace notes and routine trigger matching
- streamed FredAI tool-call delta parser

Run:

```powershell
python -m unittest discover -s tests
```

### 5.15 `scripts/repair_work_copy.ps1`

Repair script for manually copied work-computer folders.

It downloads current source/docs/web/test files from GitHub raw URLs and overwrites stale local files. It intentionally does not overwrite `.env` or `.runtime`.

Run from project root:

```powershell
Invoke-WebRequest `
  -Uri "https://raw.githubusercontent.com/JeremyWork141022/workspace-fredai-agent/main/scripts/repair_work_copy.ps1" `
  -OutFile ".\repair_work_copy.ps1" `
  -UseBasicParsing

.\repair_work_copy.ps1
```

Then test:

```powershell
python -c "from app.fredai_auth import FredAIAuthError; print('auth import ok')"
python -c "from app.orchestrator import WorkspaceAgentOrchestrator; print('orchestrator ok')"
python -c "import app.api_server; print('api server ok')"
```

## 6. Import Graph

Approximate import relationships:

```text
app.api_server
|-- app.config.load_config
|-- app.orchestrator.WorkspaceAgentOrchestrator
`-- app.scheduler.CronScheduler

app.orchestrator
|-- app.config.AppConfig, workspace_root
|-- app.fredai_auth.FredAIAuthError
|-- app.fredai_client.FredAIClient, FredAIClientError
|-- app.memory_manager.AgentMemoryManager
|-- app.memory_store.MemoryStore
|-- app.scheduler.ScheduledJob
|-- app.session_store.SessionStore, utc_now
`-- app.tools.ToolContext, ToolRegistry, build_core_tool_registry

app.fredai_client
|-- app.config.AppConfig
`-- app.fredai_auth.FredAIAuth, FredAIAuthError

app.tools
|-- app.config.AppConfig, workspace_root
|-- app.memory_manager.AgentMemoryManager
`-- app.session_store.SessionStore

app.memory_manager
|-- app.config.AppConfig, memory_dir
`-- app.memory_store.MemoryStore

app.memory_store
|-- app.config.ensure_dirs, state_db_path
`-- app.session_store.json_dumps, json_loads, utc_now

app.scheduler
|-- app.config.ensure_dirs, state_db_path
`-- app.session_store.utc_now
```

Common stale-copy breakpoints:

- `app.orchestrator` imports `FredAIAuthError`, but stale `fredai_auth.py` does not define it.
- `app.api_server` imports `WorkspaceAgentOrchestrator`, but stale `orchestrator.py` does not define it.
- `app.__init__` imports too much and creates a failure before the actual module import.

## 7. Request Flow in Detail

### 7.1 Browser to API

`web/app.js` sends:

```json
{
  "workspace_id": "workspace_123",
  "user_id": "user_456",
  "session_id": null,
  "message": "user text",
  "attachments": []
}
```

to:

```text
POST /agent/respond
```

`api_server.agent_respond()` validates that `message` is not blank, then calls:

```python
await orchestrator.respond(...)
```

### 7.2 Session and Trace Setup

`WorkspaceAgentOrchestrator.respond()`:

- creates `request_id` like `req_<uuid>`
- creates or loads a session
- reads recent model messages
- creates trace recorder
- stores the user message

Stored user content may include attachments. Text attachments are inlined up to 12,000 characters. Path attachments are only read if they resolve inside `WORKSPACE_AGENT_ROOT`.

### 7.3 Prompt Assembly

The orchestrator builds:

- system prompt from `config.system_prompt`
- curated memory from `MEMORY.md`
- runtime architecture instructions
- recent messages from SQLite
- current user message
- automatic `<memory-context>` block if prefetch returns useful context
- tool schemas from `ToolRegistry`

### 7.4 FredAI Call

The orchestrator calls:

```python
self._client.create_chat_completion(messages=request_messages, tools=tools, max_tokens=4096)
```

The client:

1. obtains OAuth token via `FredAIAuth`
2. constructs headers
3. posts to FredAI `/chat/completions`
4. parses non-stream or streaming result
5. returns `ChatCompletionResult`

### 7.5 Tool Calls

If FredAI returns tool calls:

1. `_extract_function_calls()` parses name, arguments, and call id.
2. A `tool_call` message is stored.
3. `ToolRegistry.execute()` runs the local handler.
4. A `tool_result` message is stored.
5. The tool result is appended into the model conversation as role `tool`.
6. The loop sends the updated conversation back to FredAI.

This repeats up to:

```env
WORKSPACE_AGENT_MAX_AGENT_ITERATIONS=4
```

### 7.6 Finalization

After final answer:

- assistant answer is stored in `messages`
- request metric is stored in `request_metrics`
- trace events are stored in `api_call_traces`
- memory turn is recorded
- automatic prefetch can be queued
- API returns `AgentRespondResponse`

## 8. API Contracts

### 8.1 `POST /agent/respond`

Request:

```json
{
  "workspace_id": "workspace_123",
  "user_id": "user_456",
  "session_id": null,
  "message": "Hello",
  "attachments": []
}
```

Response:

```json
{
  "answer": "Assistant answer",
  "request_id": "req_...",
  "session_id": "sess_...",
  "tool_names": ["session_search"],
  "duration_ms": 1234,
  "status": "success",
  "progress_messages": ["Checking earlier messages."]
}
```

Error-like statuses are returned as normal 200 responses when the orchestrator catches them:

```json
{
  "answer": "FredAI authentication is not configured or failed: ...",
  "status": "auth_error",
  "error": "..."
}
```

### 8.2 `GET /health`

Useful for debugging. It returns booleans showing whether auth config values are present. It does not expose actual secrets.

Expected useful fields:

```json
{
  "ok": true,
  "model": "azure-openai-chat",
  "fredai_base_url": "...",
  "scheduler_enabled": true,
  "fredai_auth_config": {
    "oauth_url": true,
    "client_id": true,
    "client_secret": true,
    "oauth_username": true,
    "oauth_password_b64": true,
    "jwt_token_optional": false
  },
  "memory": {
    "memory_dir": "...",
    "providers": ["builtin", "local_sqlite", "workspace_memory"],
    "tools": ["memory", "workspace_note_save", "workspace_note_search"]
  }
}
```

If `client_id`, `client_secret`, `oauth_username`, or `oauth_password_b64` is false, the `.env` was not read, the variable names are wrong for that code version, or the server was not restarted after editing `.env`.

### 8.3 `GET /agent/traces/{request_id}`

Returns every trace event recorded by the orchestrator. This is the primary debugging endpoint for:

- model request payload
- model response shape
- tool calls
- tool results
- auth/model/internal errors

Secrets are redacted by `_trace_safe_payload()` for keys such as authorization, password, client_secret, and x-jwt-token.

## 9. Data Storage

Default runtime state:

```text
.runtime/state.sqlite3
.runtime/memories/MEMORY.md
.runtime/cache/
```

SQLite tables:

```text
sessions
messages
request_metrics
api_call_traces
messages_fts
messages_fts_trigram
memories
memory_turns
routine_rules
workspace_notes
knowledge_files
cron_jobs
cron_runs
```

All stores share the same SQLite file by default.

This is convenient for local/single-workspace use. For multi-user deployment, decide whether to:

- keep one shared SQLite DB for the whole team, or
- set different `WORKSPACE_AGENT_STATE_DB` per workspace/user, or
- move to a server database later.

## 10. Memory Model

The project has four memory layers.

### 10.1 Recent Context

Recent user/assistant messages are loaded directly into the model input from SQLite:

```env
WORKSPACE_AGENT_SESSION_CONTEXT_MESSAGES=16
```

### 10.2 Curated Memory

Always-on Markdown memory:

```text
.runtime/memories/MEMORY.md
```

Updated by the `memory` tool.

Use for compact stable operating facts only. Source documents belong in the knowledge base; interpretation fixes belong in wiki pages/issues.

### 10.3 SQLite Memory and Turns

`LocalSQLiteMemoryProvider` records prior turns and searches:

- `memories`
- `memory_turns`
- `routine_rules`

Prefetch runs before the model call and injects relevant facts into a `<memory-context>` block.

### 10.4 Workspace Notes

`WorkspaceMemoryProvider` stores larger durable notes:

- project facts
- decisions
- source-backed findings
- facts too large for curated memory

Tools:

- `workspace_note_save`
- `workspace_note_search`

## 11. Tool Details

### 11.1 `memory`

Purpose:

Save durable curated memory in `MEMORY.md`.

Actions:

- `add`
- `replace`
- `remove`

Targets:

- `memory`

Safety:

- rejects empty content
- blocks obvious prompt-injection or secret-exfiltration text
- enforces char limits
- atomic file writes

### 11.2 `workspace_note_save`

Purpose:

Save larger workspace note into SQLite.

Args:

- `title`
- `body`
- `source`
- `tags`

### 11.3 `workspace_note_search`

Purpose:

Search durable workspace notes for a query.

### 11.4 `session_search`

Purpose:

Search prior conversation history when the user refers to something outside recent context.

Scopes:

- `current_session`
- `workspace`
- `all`

If `WORKSPACE_AGENT_SESSION_SEARCH_AUX_ENABLED=true`, the orchestrator asks FredAI to summarize matching sessions for better retrieval output.

### 11.5 `routine_rule`

Purpose:

Classify and save future-behavior requests safely.

Rule types:

- `hook`
- `cron_job`
- `skill`
- `tool_request`
- `curated_memory`
- `sqlite_memory`
- `not_supported`

Special behavior:

- `hook` with `pre_llm` can be injected later when its trigger matches.
- `cron_job` can create a real scheduled job if enough schedule fields are provided.
- `curated_memory` writes to curated memory as a side effect.
- `sqlite_memory` writes to SQLite memory as a side effect.

### 11.6 `workspace_read_file`

Purpose:

Read a UTF-8 text file inside `WORKSPACE_AGENT_ROOT`.

Limits:

- `max_chars` capped at 100,000.
- rejects files outside workspace root.

### 11.7 `workspace_list_files`

Purpose:

List files inside `WORKSPACE_AGENT_ROOT`.

Skips:

- `.git`
- `.venv`
- `__pycache__`
- `.runtime`
- `.pytest_cache`
- `node_modules`

### 11.8 `workspace_find_files`

Purpose:

Find likely files inside `WORKSPACE_AGENT_ROOT` by filename/path tokens.

## 12. FredAI Boundary and Auth Details

The code must use FredAI only.

Do not replace `FredAIClient` with direct OpenAI or Anthropic SDK calls unless the user explicitly changes the project requirement.

The internal API shape is expected to be OpenAI-compatible:

```text
POST <base_url>/chat/completions
```

The auth shape is:

```text
POST <oauth_url>
Content-Type: application/x-www-form-urlencoded

grant_type=password
client_id=...
client_secret=...
username=...
password=...
```

Then model call:

```text
Authorization: Bearer <oauth access token>
x-jwt-token: <optional, only if configured>
```

JWT notes:

- JWT is not required by this code.
- If configured, it is sent.
- If blank, the header is omitted.
- Missing JWT alone should not trigger "Missing FredAI auth configuration".

OAuth username/password notes:

- `FREDAI_OAUTH_USERNAME` is not base64.
- `FREDAI_OAUTH_PASSWORD_B64` is base64.
- If these are swapped, `/health` may show both present, but OAuth will fail.

## 13. Known Setup Story and Fixes

The work-computer setup hit several issues. Future LLM sessions should understand these before changing code.

### 13.1 Typo in Command

Problem:

```powershell
pytho -m pip install -r requirements.txt
```

Error:

```text
The term 'pytho' is not recognized...
```

Fix:

```powershell
python -m pip install -r requirements.txt
```

### 13.2 Wrong Folder for `requirements.txt`

Problem:

```text
ERROR: Could not open requirements file: No such file or directory: 'requirements.txt'
```

Cause:

PowerShell was not in the project root, or the file was not copied there.

Fix:

```powershell
cd Z:\hq42p2v5\MF_PORTVAL\EVA_User\Agent
dir
python -m pip install -r requirements.txt
```

### 13.3 ImportError for `WorkspaceAgentOrchestrator`

Problem:

```text
ImportError: cannot import name 'WorkspaceAgentOrchestrator' from 'app.orchestrator'
```

Likely cause:

Stale or incomplete `app/orchestrator.py` from manual copy.

Fix:

- Pull latest GitHub files.
- Or run `scripts/repair_work_copy.ps1`.
- Delete `__pycache__`.

Diagnostic:

```powershell
Select-String -Path .\app\orchestrator.py -Pattern "class WorkspaceAgentOrchestrator"
python -c "from app.orchestrator import WorkspaceAgentOrchestrator; print('orchestrator ok')"
```

### 13.4 ImportError for `FredAIAuthError`

Problem:

```text
ImportError: cannot import name 'FredAIAuthError' from 'app.fredai_auth'
```

Likely cause:

Stale `app/fredai_auth.py`.

Fix:

```powershell
python -c "from app.fredai_auth import FredAIAuthError; print('auth import ok')"
```

If that fails, update from GitHub.

### 13.5 Missing FredAI Auth Config Despite `.env`

UI message:

```text
FredAI authentication is not configured or failed: Missing FredAI auth configuration:
FREDAI_CLIENT_ID, FREDAI_CLIENT_SECRET, FREDAI_OAUTH_USERNAME, FREDAI_OAUTH_PASSWORD_B64
```

Possible causes:

1. Server was started before `.env` was saved.
2. Server was not restarted after `.env` edit.
3. `.env` is not in project root.
4. Variable names do not match the code version.
5. Local code is stale and does not have legacy alias support.
6. Values were assigned to wrong variables.

Diagnostics from project root:

```powershell
python -c "from app.config import load_env_file; print(load_env_file().keys())"
python -c "from app.config import load_config; c=load_config(); print(bool(c.fredai_client_id), bool(c.fredai_client_secret), bool(c.fredai_oauth_username), bool(c.fredai_oauth_password_b64), bool(c.fredai_jwt_token))"
python -c "import app.api_server; print('api server ok')"
```

Then restart server:

```powershell
python -m uvicorn app.api_server:app --host 127.0.0.1 --port 8000
```

Open:

```text
http://127.0.0.1:8000/health
```

## 14. Work-Computer Setup Checklist

From scratch:

```powershell
cd Z:\hq42p2v5\MF_PORTVAL\EVA_User
git clone https://github.com/JeremyWork141022/workspace-fredai-agent.git Agent
cd Agent
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
Copy-Item .env.example .env
notepad .env
```

Edit `.env`, then verify:

```powershell
python -c "from app.fredai_auth import FredAIAuthError; print('auth import ok')"
python -c "from app.orchestrator import WorkspaceAgentOrchestrator; print('orchestrator ok')"
python -c "from app.config import load_config; c=load_config(); print(c.model, c.fredai_base_url)"
python -c "import app.api_server; print('api server ok')"
python -m unittest discover -s tests
```

Start:

```powershell
python -m uvicorn app.api_server:app --host 127.0.0.1 --port 8000
```

Open:

```text
http://127.0.0.1:8000/
```

If running from manual copy rather than `git clone`, repair first:

```powershell
.\scripts\repair_work_copy.ps1
```

## 15. Validation Commands

Run these before claiming the project works:

```powershell
python -m compileall app tests
python -m unittest discover -s tests
python -c "from app.fredai_auth import FredAIAuthError; print('auth import ok')"
python -c "from app.orchestrator import WorkspaceAgentOrchestrator; print('orchestrator ok')"
python -c "import app.api_server; print('api server ok')"
```

While server is running:

```powershell
Invoke-RestMethod http://127.0.0.1:8000/health
```

Then test a chat request:

```powershell
$body = @{
  workspace_id = "workspace_123"
  user_id = "jeremy"
  session_id = $null
  message = "Say hello and tell me which tools you have."
  attachments = @()
} | ConvertTo-Json -Depth 10

Invoke-RestMethod `
  -Method Post `
  -Uri "http://127.0.0.1:8000/agent/respond" `
  -ContentType "application/json" `
  -Body $body
```

If this fails with `auth_error`, inspect `.env` and `/health`.

If this fails with `model_error`, OAuth likely succeeded but FredAI chat/completions failed. Check:

- base URL
- model name
- streaming support
- SSL verify setting
- gateway access

Try:

```env
FREDAI_STREAM=false
```

then restart the server.

## 16. Development Rules for the Next LLM

Follow these rules unless Jeremy explicitly changes them.

1. Keep FredAI as the only model boundary.
2. Do not add OpenAI SDK, Anthropic SDK, LangChain, or external hosted model calls.
3. Do not commit real `.env` secrets.
4. Do not remove legacy env alias support unless Jeremy confirms work env no longer needs it.
5. Do not make `app/__init__.py` import large runtime modules unless necessary.
6. Keep workspace file tools read-only unless Jeremy explicitly asks for write tools.
7. Keep paths under `WORKSPACE_AGENT_ROOT`.
8. Preserve `.runtime` semantics; do not wipe state DB or memory files during repairs.
9. Prefer adding focused tests when changing session, memory, stream parsing, scheduler, or tools.
10. Use the GitHub `main` branch as source of truth if the work copy was manually copied.

## 17. Extension Points

### 17.1 Add PDF / Excel Tools

Likely next business need: read PDFs, Excel, CSV, operating statements, loan/borrower files, and summarize/validate them.

Recommended approach:

- Add new handlers in `app/tools.py` or split into `app/workspace_tools.py` if it grows.
- Keep `ToolRegistry` as the dispatch layer.
- Use structured libraries where possible:
  - CSV: Python `csv`
  - Excel: `openpyxl` or `pandas` if approved
  - PDF text: `pypdf` or `pdfplumber` if approved
- Keep file access under `WORKSPACE_AGENT_ROOT`.
- Add tests with small sample files under `tests/fixtures/`.

Do not stuff large extracted documents into curated memory. Use workspace notes or session history.

### 17.2 Add Authentication for Work Users

Currently the UI has no login layer. Anyone who can reach the server can use the agent.

For team use, add one of:

- reverse proxy with work SSO
- internal network ACL
- simple API key header
- per-user session field controlled by upstream auth

Do not treat `user_id` typed in the UI as real authentication.

### 17.3 Deployment Beyond Localhost

Local-only:

```powershell
python -m uvicorn app.api_server:app --host 127.0.0.1 --port 8000
```

Network-accessible:

```powershell
python -m uvicorn app.api_server:app --host 0.0.0.0 --port 8000
```

If binding to `0.0.0.0`, the work computer firewall and network controls matter. The app should not be exposed beyond trusted internal users.

### 17.4 Improve UI

Current UI is functional. Obvious improvements:

- session list and resume picker
- upload/attach local files
- show scheduler jobs
- trace viewer with collapsible JSON
- status details from `/health`
- safer user/workspace identity handling

Keep the UI operational and compact. It is a work tool, not a marketing landing page.

## 18. Security and Secrets

Secrets involved:

- `FREDAI_CLIENT_SECRET`
- OAuth password before/after base64
- OAuth access token
- optional JWT token

The code tries to avoid returning secrets:

- `/health` returns booleans, not values.
- traces redact `authorization`, `x-jwt-token`, `client_secret`, and `password`.

Known risk:

- Screenshots of `.env` can expose secrets. If secrets appeared in screenshots, rotate them according to work security policy.
- The repository intentionally has no `.gitignore` because Jeremy asked to upload everything. Be careful not to commit `.env`.

## 19. Troubleshooting Decision Tree

### Server Will Not Start

Run:

```powershell
python -c "from app.fredai_auth import FredAIAuthError; print('auth import ok')"
python -c "from app.orchestrator import WorkspaceAgentOrchestrator; print('orchestrator ok')"
python -c "import app.api_server; print('api server ok')"
```

If an import fails:

- update that file from GitHub
- delete `__pycache__`
- rerun the command

Delete `__pycache__` safely:

```powershell
Get-ChildItem -Recurse -Directory -Filter "__pycache__" | Remove-Item -Recurse -Force
```

### UI Says Server Offline

Check server terminal. Then open:

```text
http://127.0.0.1:8000/health
```

If not reachable, Uvicorn is not running or bound to another port.

### UI Says Missing FredAI Auth Configuration

Check:

```powershell
python -c "from app.config import load_env_file; print(load_env_file())"
```

Do not paste secrets into chat. Just verify keys exist.

Then:

```powershell
python -c "from app.config import load_config; c=load_config(); print(bool(c.fredai_client_id), bool(c.fredai_client_secret), bool(c.fredai_oauth_username), bool(c.fredai_oauth_password_b64))"
```

Restart Uvicorn after editing `.env`.

### OAuth Fails

If config booleans are true but auth still fails:

- username/password may be swapped
- password may not be valid base64
- password may be expired
- client secret may be wrong
- `FREDAI_OAUTH_URL` may be wrong
- network access to auth server may be blocked

### FredAI Chat Fails

If OAuth succeeds but model call fails:

- check `FREDAI_PRESET`
- check `FREDAI_BASE_URL` or preset override URLs
- check `FREDAI_MODEL`
- try `FREDAI_STREAM=false`
- check `FREDAI_VERIFY_SSL`
- inspect `/agent/traces/{request_id}`

## 20. Current Limitations

The project is useful but still early.

Limitations:

- No login or authorization layer.
- No file upload UI yet.
- File tools are read-only.
- No built-in PDF/Excel parsing yet.
- No background Windows service wrapper.
- Scheduler runs only while the FastAPI process is running.
- SQLite is fine for local/team pilot, not a heavy concurrent enterprise deployment.
- No formal migration system for SQLite schema changes.
- No packaging installer.

## 21. Minimal Mental Model

If you remember only one thing, remember this:

```text
FastAPI is just the shell.
WorkspaceAgentOrchestrator is the brain.
FredAIClient is the only LLM boundary.
ToolRegistry is how FredAI can act.
SessionStore and MemoryStore are durable state.
web/app.js is the browser chat surface.
```

When debugging, isolate in this order:

```text
1. Python import works?
2. .env loaded?
3. OAuth token works?
4. FredAI chat/completions works?
5. Tool loop works?
6. UI works?
```

## 22. Starting Point for the Next LLM

When this project is opened on the work computer, do this first:

```powershell
cd Z:\hq42p2v5\MF_PORTVAL\EVA_User\Agent
git status
python -m pip show fastapi uvicorn httpx python-dotenv pydantic
python -c "from app.config import load_config; c=load_config(); print(c.model, c.fredai_base_url)"
python -c "from app.fredai_auth import FredAIAuthError; print('auth import ok')"
python -c "from app.orchestrator import WorkspaceAgentOrchestrator; print('orchestrator ok')"
python -c "import app.api_server; print('api server ok')"
python -m unittest discover -s tests
```

Then start:

```powershell
python -m uvicorn app.api_server:app --host 127.0.0.1 --port 8000
```

Then open:

```text
http://127.0.0.1:8000/
```

If anything fails, use the decision tree above before changing code.
