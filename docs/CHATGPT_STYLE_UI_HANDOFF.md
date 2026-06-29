# ChatGPT-Style FredAI UI Handoff

Date: 2026-06-27

This handoff describes the new package-free ChatGPT-style UI built for the CRT Analytics Agent. It intentionally does not import `assistant-ui`, React, Radix, lucide, Tailwind, AI SDK, or any other frontend package. It uses assistant-ui's ChatGPT example as a design reference only.

## Files Changed

- `web/index.html`
  - Reworked the chat markup around a ChatGPT-like thread, sticky composer, hidden file input, attachment list, stop button, and scroll-to-latest button.

- `web/app.js`
  - Replaced append-only DOM logic with a small local UI state model.
  - Adds message rendering, copy/edit/regenerate actions, pending assistant response, browser-side stop, scroll management, auto-growing textarea, drag/drop files, and attachment payload preparation.

- `web/styles.css`
  - Reworked styling around a quiet ChatGPT-like layout: left context rail, centered conversation width, right-aligned user bubbles, plain assistant messages, sticky composer, attachment chips, and responsive behavior.

## What This UI Does

- Calls the existing `POST /agent/respond` endpoint.
- Keeps `workspace_id`, `user_id`, and `session_id` in the request.
- Shows user messages and assistant messages in a ChatGPT-like thread.
- Shows assistant metadata:
  - request duration
  - status
  - tool names
  - trace link
  - progress messages in a collapsible block
- Supports:
  - Enter to send
  - Shift+Enter for newline
  - file picker
  - drag/drop attachment input
  - copy message
  - edit user message text
  - regenerate assistant response from the previous user message
  - browser-side stop through `AbortController`
  - auto-scroll that respects whether the user scrolled upward

## What This UI Does Not Do Yet

- It does not import or require assistant-ui.
- It does not require npm.
- It does not implement true backend cancellation unless the backend stops work when the browser disconnects.
- It does not stream token deltas yet.
- It does not load prior session messages from the backend.
- It does not render structured tool-call cards unless the backend returns structured tool events later.
- It does not parse binary files in the browser. Binary file parsing belongs in the backend tools.

## Current Backend Contract Used By The UI

The UI sends:

```json
{
  "workspace_id": "workspace_123",
  "user_id": "user_456",
  "session_id": null,
  "message": "Please analyze this file",
  "attachments": []
}
```

The UI expects:

```json
{
  "answer": "Assistant answer text",
  "request_id": "req_...",
  "session_id": "sess_...",
  "tool_names": ["tool_a"],
  "duration_ms": 1200,
  "status": "success",
  "progress_messages": ["optional progress"]
}
```

If `status` is not `success`, the UI renders the assistant message as an error but still displays the answer text.

## Attachment Payload Contract

The UI is built to work with a backend that can convert conventional non-image, non-PDF files into prompt text before calling FredAI.

Supported in this implementation step:

- `.txt`, `.md`, `.log`, `.ini`, `.cfg`, `.toml`, `.yaml`, `.yml`, `.sql`
- `.csv`, `.tsv`
- `.json`, `.xml`, `.html`, `.htm`, `.rtf`
- `.docx`
- `.xlsx`
- `.pptx`

Deferred for the next step:

- PDF
- image formats
- legacy binary `.doc`
- legacy binary `.xls`
- legacy binary `.ppt`

Each attachment sent to `/agent/respond` has this shape:

```json
{
  "id": "att_...",
  "name": "example.xlsx",
  "size": 12345,
  "type": "spreadsheet",
  "media_type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
  "content_type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
  "extension": ".xlsx",
  "transfer": "metadata_only",
  "last_modified": 1782570000000
}
```

Possible `type` values:

- `text`
- `spreadsheet`
- `document`
- `file`

Possible `transfer` values:

- `inline_text`
  - Used for text-like files under the frontend text limit.
  - Payload includes `text`.
  - The backend parses CSV/TSV/JSON/XML/HTML/RTF where applicable.

- `inline_base64`
  - Used for binary files only when the backend advertises inline binary support through `/health`.
  - Payload includes:

```json
{
  "encoding": "base64",
  "data_base64": "..."
}
```

- `metadata_only`
  - Used when file bytes are not sent.
  - Backend tells FredAI that no document bytes were supplied.

## Important Attachment Safety Choice

The UI only sends binary base64 when the backend advertises support. The backend must decode and parse the file before FredAI sees it. Raw base64 should never be passed directly into the LLM prompt.

The backend now returns this from `GET /health`:

```json
{
  "attachment_capabilities": {
    "inline_base64": true,
    "max_inline_bytes": 6291456,
    "accepted_extensions": [".cfg", ".csv", ".docx", ".htm", ".html", ".ini", ".json", ".log", ".md", ".pptx", ".rtf", ".sql", ".toml", ".tsv", ".txt", ".xlsx", ".xml", ".yaml", ".yml"]
  }
}
```

When `inline_base64` is true, the UI sends base64 for binary files up to `max_inline_bytes`. The backend decodes and converts supported files to text before the FredAI call.

## Backend Deliverables Assumed For Work Computer

The work-computer backend can be more advanced than this local repo. For full compatibility, it should deliver the following.

### 1. Keep `POST /agent/respond`

The UI already calls this endpoint. Keep the JSON contract stable.

Required request fields:

- `workspace_id`
- `user_id`
- `session_id`
- `message`
- `attachments`

Required response fields:

- `answer`
- `request_id`
- `session_id`
- `tool_names`
- `duration_ms`
- `status`
- `progress_messages`

### 2. Support Attachment Parsing

Backend should parse attachments by `transfer`:

- `inline_text`
  - Use `text` directly.
  - Good for `.txt`, `.md`, `.csv`, `.tsv`, `.json`, `.xml`, `.yaml`, `.yml`, `.html`, `.htm`, `.rtf`, `.sql`, `.log`, `.ini`, `.cfg`, `.toml`.

- `inline_base64`
  - Decode `data_base64`.
  - Use `extension` and `media_type` to choose parser.
  - Current standard-library backend supports:
    - CSV / TSV
    - JSON
    - XML
    - HTML
    - RTF
    - DOCX
    - XLSX
    - PPTX
    - plain text-like files

- `metadata_only`
  - Treat as a file reference without bytes.
  - Ask for a path, tell the user bytes were not supplied, or use another upload channel.

Unsupported in this step:

- PDF
- images
- legacy binary `.doc`
- legacy binary `.xls`
- legacy binary `.ppt`

### 3. Advertise Capabilities Through `/health`

Recommended shape:

```json
{
  "ok": true,
  "model": "azure-openai-chat",
  "memory": {
    "tools": ["..."]
  },
  "attachment_capabilities": {
    "inline_base64": true,
    "max_inline_bytes": 6291456,
    "accepted_extensions": [".cfg", ".csv", ".docx", ".htm", ".html", ".ini", ".json", ".log", ".md", ".pptx", ".rtf", ".sql", ".toml", ".tsv", ".txt", ".xlsx", ".xml", ".yaml", ".yml"]
  },
  "capabilities": {
    "streaming": false,
    "session_history": false,
    "server_cancel": false
  }
}
```

The UI currently reads:

- `model`
- `memory.tools`
- `attachment_capabilities.inline_base64`
- `attachment_capabilities.max_inline_bytes`
- `attachment_capabilities.accepted_extensions`

### 4. Memory Orchestration Assumptions

The UI does not directly manage memory. It passes stable identity/context so the backend can manage memory:

- `workspace_id`
- `user_id`
- `session_id`

The backend memory system should:

- use `workspace_id` to isolate workspace memory
- use `user_id` to isolate user memory/profile facts
- use `session_id` to continue a thread
- return the new/active `session_id` on every response
- include memory-related tool usage in `tool_names` or `progress_messages` when useful

No frontend change is needed if the new orchestration runs behind `/agent/respond`.

### 5. Optional Streaming Deliverable

The UI is currently non-streaming. For a more ChatGPT-like experience, add:

- `POST /agent/respond-stream`

Recommended server-sent events:

- `start`
- `progress`
- `delta`
- `tool`
- `final`
- `error`

Then update `web/app.js` transport only; the renderer can already update a running assistant message.

### 6. Optional Session History Deliverable

For opening old sessions on page reload, add one of:

- `GET /agent/sessions/{session_id}/messages`
- or include recent messages in `GET /agent/sessions`

Expected message shape can be simple:

```json
{
  "messages": [
    {
      "role": "user",
      "content": "question",
      "created_at": "..."
    },
    {
      "role": "assistant",
      "content": "answer",
      "metadata": {
        "request_id": "req_...",
        "tool_names": []
      }
    }
  ]
}
```

## Frontend Extension Points

Future work should mostly touch these functions in `web/app.js`:

- `refreshHealth()`
  - Add backend capability detection.

- `prepareAttachment(file)`
  - Change upload strategy or add multipart upload.

- `toPayloadAttachment(attachment)`
  - Change attachment JSON schema if backend requires a different field name.

- `runAssistantRequest(userMessage, options)`
  - Swap non-streaming `fetch` with streaming transport.

- `renderMessageMeta(message)`
  - Add richer memory/tool trace display.

- `renderMarkdown(container, text)`
  - Add richer safe markdown behavior if needed.

## Run Instructions

From the project root:

```powershell
python -m uvicorn app.api_server:app --host 127.0.0.1 --port 8000
```

Open:

```text
http://127.0.0.1:8000
```

No frontend build step is required.

## UI-Only Mock Mode

If FredAI is not available on the computer, open the UI with:

```text
http://127.0.0.1:8000/?mock=1
```

Mock mode:

- does not call `/health`
- does not call `/agent/respond`
- does not require FredAI credentials
- generates a local browser-only assistant response in `web/app.js`
- still exercises the real thread rendering, composer, pending state, file chips, copy/edit/regenerate buttons, trace metadata, and progress details
- advertises mock inline binary support so CSV, XLSX, DOCX, PPTX, JSON, XML, RTF, HTML, and text attachments can be visually tested

Mock mode does not prove backend orchestration, memory, FredAI auth, real tools, file parsing, or server cancellation. It proves the frontend UX and request-shaping logic.
