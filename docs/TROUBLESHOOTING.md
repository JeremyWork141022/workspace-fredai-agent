# Troubleshooting

## ImportError From A Local Work Copy

If setup was done by manually copying files, a single stale Python file can break startup.

Example:

```text
ImportError: cannot import name 'FredAIAuthError' from 'app.fredai_auth'
```

This means the local `app/fredai_auth.py` file is not the current GitHub version. Repair the copied project from the project root:

```powershell
Invoke-WebRequest `
  -Uri "https://raw.githubusercontent.com/JeremyWork141022/workspace-fredai-agent/main/scripts/repair_work_copy.ps1" `
  -OutFile ".\repair_work_copy.ps1" `
  -UseBasicParsing

.\repair_work_copy.ps1
```

Then test imports:

```powershell
python -c "from app.fredai_auth import FredAIAuthError; print('auth import ok')"
python -c "from app.orchestrator import WorkspaceAgentOrchestrator; print('orchestrator ok')"
python -c "import app.api_server; print('api server ok')"
```

Start the server:

```powershell
python -m uvicorn app.api_server:app --host 127.0.0.1 --port 8000
```

