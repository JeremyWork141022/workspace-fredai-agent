# Work Computer Setup

## Install

Clone the GitHub repository on the work computer, then from the project folder:

```powershell
python -m pip install -r requirements.txt
Copy-Item .env.example .env
notepad .env
```

Fill in the FredAI values in `.env`:

```env
FREDAI_PRESET=Direct_Azure
FREDAI_MODEL=azure-openai-chat
FREDAI_STREAM=true
FREDAI_VERIFY_SSL=false
FREDAI_OAUTH_URL=https://auth.fhlmc.com/as/token.oauth2
FREDAI_CLIENT_ID=your-client-id
FREDAI_CLIENT_SECRET=your-client-secret
FREDAI_OAUTH_USERNAME=your-username
FREDAI_OAUTH_PASSWORD_B64=base64-password
FREDAI_JWT_TOKEN=your-jwt-token
```

Encode the password:

```powershell
[Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes("your-password"))
```

## Run For Yourself

```powershell
python -m uvicorn app.api_server:app --host 127.0.0.1 --port 8000
```

Open:

```text
http://127.0.0.1:8000/
```

## Run For Work Users

Run the server on a work-accessible host and bind to the network interface:

```powershell
python -m uvicorn app.api_server:app --host 0.0.0.0 --port 8000
```

Users open:

```text
http://<work-host-name>:8000/
```

Each user should have a stable `user_id`. Use the same `workspace_id` for a shared workspace, or separate `workspace_id` values when memory should not cross teams.

## Operational Notes

- `.env` is local machine configuration and should contain work-only FredAI credentials.
- `.runtime/state.sqlite3` stores sessions, messages, traces, routine rules, jobs, and workspace notes.
- `.runtime/memories/MEMORY.md` stores curated always-on memory. Source documents belong in the knowledge base; corrections and clarifications belong in wiki pages/issues.
- `GET /agent/traces/{request_id}` opens request traces from the UI trace link.
- Set `FREDAI_STREAM=false` if the FredAI gateway does not stream tool-call deltas correctly.
