# Features

- Internal API entry point: `POST /agent/respond`.
- FredAI-only model boundary with OAuth and JWT headers.
- Streaming chat-completions support with non-stream auxiliary calls.
- SQLite sessions and message history.
- FTS5/trigram all-history conversation search through `session_search`.
- Curated file-backed memory with `memory` add/replace/remove actions.
- Automatic memory prefetch before each model call.
- Workspace notes with `workspace_note_save` and `workspace_note_search`.
- Routine capture with `routine_rule`.
- Read-only workspace file tools:
  - `workspace_list_files`
  - `workspace_find_files`
  - `workspace_read_file`
- Request metrics and full trace storage.
- SQLite-backed interval and daily scheduler.
- Optional scheduled-result delivery to `WORKSPACE_AGENT_DELIVERY_URL`.

The reference FredAI project was used only to learn the FredAI base URL preset and auth/header pattern. The new runtime code is built in this workspace.

