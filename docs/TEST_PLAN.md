# Test Plan

## Offline Checks

Run:

```powershell
python -m compileall app
python -m unittest discover -s tests
```

These checks verify:

- modules compile
- sessions/messages persist in SQLite
- FTS-backed session search returns saved turns
- workspace notes and routine hooks persist
- FredAI streaming tool-call chunks are reconstructed

## Local API Smoke Test

Start:

```powershell
uvicorn app.api_server:app --host 127.0.0.1 --port 8000
```

Then call:

```powershell
Invoke-RestMethod -Method Get http://127.0.0.1:8000/health
```

After `.env` FredAI credentials are configured, call:

```powershell
Invoke-RestMethod -Method Post http://127.0.0.1:8000/agent/respond -ContentType 'application/json' -Body '{
  "workspace_id": "workspace_123",
  "user_id": "user_456",
  "message": "Say hello and remember that I like short answers.",
  "attachments": []
}'
```

Then inspect traces with the returned `request_id`:

```powershell
Invoke-RestMethod http://127.0.0.1:8000/agent/traces/<request_id>
```

## FredAI Integration Risks

- If FredAI does not support streaming tool-call deltas, set `FREDAI_STREAM=false`.
- If the gateway path differs from `FREDAI_BASE_URL/chat/completions`, update `FREDAI_BASE_URL`.
- If SSL inspection is required locally, keep `FREDAI_VERIFY_SSL=false`; otherwise set it to `true`.

