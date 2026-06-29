# Architecture

Request pipeline:

```text
internal API request
-> create/find session
-> save user message
-> load short-term session context
-> load curated memory
-> run automatic memory prefetch
-> build FredAI chat-completions payload with tools
-> stream model response when enabled
-> execute tool calls
-> append tool results
-> call FredAI again
-> repeat until final answer or loop limit
-> save assistant answer
-> save request metrics
-> save full trace
-> post-turn memory sync
-> return API response
```

## Runtime Layers

- `app/api_server.py`: internal FastAPI API.
- `app/orchestrator.py`: session, memory, FredAI, tool-loop, trace, and scheduler orchestration.
- `app/fredai_auth.py`: FredAI OAuth token resolution.
- `app/fredai_client.py`: FredAI-only chat-completions client with streaming assembly.
- `app/session_store.py`: SQLite sessions, messages, FTS/trigram search, request metrics, traces.
- `app/memory_manager.py`: curated memory and provider fan-out.
- `app/memory_store.py`: generic SQLite memory, turns, routine rules, workspace notes.
- `app/tools.py`: JSON-schema tool registry and handlers.
- `app/scheduler.py`: recurring background jobs.

## Memory Layers

Short-term session memory is stored in SQLite and the latest `WORKSPACE_AGENT_SESSION_CONTEXT_MESSAGES` user/assistant messages are included directly in each model call.

Curated long-term memory lives only in `.runtime/memories/MEMORY.md`; it is loaded into the instructions every turn. User-specific or session-specific recall belongs in SQLite session history, workspace notes, or a future user-separation mode, not in a separate curated Markdown file.

Automatic prefetch runs before the first FredAI call. It injects relevant local memory, triggered hooks, prior turns, and workspace notes into a temporary `<memory-context>` block on the latest user message.

All-history search is exposed as `session_search`, backed by SQLite FTS5 and a trigram fallback for CJK text.

## FredAI Tool Loop

Tools are passed as chat-completions JSON schemas. FredAI chooses tool calls; Python executes handlers; tool results return as `role=tool` messages; then FredAI is called again. The default max loop count is `4`.

## Trace Tables

`request_metrics` stores the request summary. `api_call_traces` stores ordered events:

- `request_start`
- `stored_user_message`
- `instructions`
- `prefetch`
- `tool_options`
- `model_request`
- `model_response`
- `tool_call`
- `tool_result`
- `final_answer`
- `request_error`
