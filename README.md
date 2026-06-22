# Workspace FredAI Agent

Internal API agent runtime for a workspace. It follows the Agent B handoff architecture while replacing Weixin/Codex/OpenAI direct calls with FredAI-only chat completions.

The project also includes a browser UI served by the same FastAPI app.

## Run

1. Create and fill `.env` from `.env.example`.
2. Install dependencies:

```powershell
pip install -r requirements.txt
```

3. Start the API:

```powershell
uvicorn app.api_server:app --reload --host 127.0.0.1 --port 8000
```

4. Open the UI:

```text
http://127.0.0.1:8000/
```

5. Or send a turn by API:

```powershell
Invoke-RestMethod -Method Post http://127.0.0.1:8000/agent/respond -ContentType 'application/json' -Body '{
  "workspace_id": "workspace_123",
  "user_id": "user_456",
  "session_id": null,
  "message": "Remember that I prefer concise workspace summaries.",
  "attachments": []
}'
```

## FredAI Boundary

The runtime uses the FredAI OAuth/JWT pattern from the reference project:

- password-grant OAuth token from `FREDAI_OAUTH_URL`
- `Authorization: Bearer <oauth>`
- optional `x-jwt-token`
- OpenAI-compatible `POST <FREDAI_BASE_URL>/chat/completions`

The implementation does not call OpenAI or Anthropic services directly.

## Core Endpoints

- `GET /` opens the chat UI.
- `POST /agent/respond` runs one agent turn.
- `GET /agent/sessions` lists stored sessions.
- `GET /agent/traces/{request_id}` returns debug trace events.
- `GET /scheduler/jobs` lists recurring jobs.
- `POST /scheduler/run-pending` runs due jobs once.
