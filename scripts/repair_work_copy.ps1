param(
    [string]$RepoRawBase = "https://raw.githubusercontent.com/JeremyWork141022/workspace-fredai-agent/main"
)

$ErrorActionPreference = "Stop"

$root = (Get-Location).Path
Write-Host "Repairing Workspace FredAI Agent project in: $root"

$files = @(
    "README.md",
    "requirements.txt",
    ".env.example",
    "app/__init__.py",
    "app/__main__.py",
    "app/api_server.py",
    "app/config.py",
    "app/fredai_auth.py",
    "app/fredai_client.py",
    "app/memory_manager.py",
    "app/memory_store.py",
    "app/orchestrator.py",
    "app/scheduler.py",
    "app/session_store.py",
    "app/tools.py",
    "docs/ARCHITECTURE.md",
    "docs/FEATURES.md",
    "docs/TEST_PLAN.md",
    "docs/WORK_COMPUTER_SETUP.md",
    "tests/test_runtime.py",
    "web/app.js",
    "web/index.html",
    "web/styles.css"
)

foreach ($file in $files) {
    $target = Join-Path $root $file
    $targetDir = Split-Path -Parent $target
    if ($targetDir -and -not (Test-Path $targetDir)) {
        New-Item -ItemType Directory -Force -Path $targetDir | Out-Null
    }
    $url = "$RepoRawBase/$file"
    Write-Host "Updating $file"
    Invoke-WebRequest -Uri $url -OutFile $target -UseBasicParsing
}

Get-ChildItem -Path $root -Recurse -Directory -Filter "__pycache__" |
    Remove-Item -Recurse -Force

Write-Host ""
Write-Host "Repair complete. Your .env and .runtime folders were not overwritten."
Write-Host "Next:"
Write-Host "  python -c `"from app.fredai_auth import FredAIAuthError; print('auth import ok')`""
Write-Host "  python -c `"from app.orchestrator import WorkspaceAgentOrchestrator; print('orchestrator ok')`""
Write-Host "  python -c `"import app.api_server; print('api server ok')`""

