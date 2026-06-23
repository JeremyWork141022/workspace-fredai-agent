from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from app.config import load_config
from app.orchestrator import WorkspaceAgentOrchestrator
from app.scheduler import CronScheduler


class AgentRespondRequest(BaseModel):
    workspace_id: str = Field(default="default")
    user_id: str = Field(default="unknown")
    session_id: Optional[str] = None
    message: str
    attachments: List[Dict[str, Any]] = Field(default_factory=list)


class AgentRespondResponse(BaseModel):
    answer: str
    request_id: str
    session_id: str
    tool_names: List[str]
    duration_ms: int
    status: str
    progress_messages: List[str]
    error: Optional[str] = None


config = load_config()
orchestrator = WorkspaceAgentOrchestrator(config)
scheduler = CronScheduler()

app = FastAPI(
    title="Workspace FredAI Agent",
    version="0.1.0",
    description="Internal API runtime for the Workspace FredAI Agent.",
)

WEB_ROOT = Path(__file__).resolve().parents[1] / "web"
app.mount("/static", StaticFiles(directory=WEB_ROOT), name="static")


@app.on_event("startup")
async def _startup() -> None:
    if config.scheduler_enabled:
        await scheduler.start(orchestrator.run_scheduled_job)


@app.on_event("shutdown")
async def _shutdown() -> None:
    await scheduler.stop()


@app.get("/health")
async def health() -> Dict[str, Any]:
    return {
        "ok": True,
        "model": config.model,
        "fredai_base_url": config.fredai_base_url,
        "scheduler_enabled": config.scheduler_enabled,
        "fredai_auth_config": {
            "oauth_url": bool(config.fredai_oauth_url),
            "client_id": bool(config.fredai_client_id),
            "client_secret": bool(config.fredai_client_secret),
            "oauth_username": bool(config.fredai_oauth_username),
            "oauth_password_b64": bool(config.fredai_oauth_password_b64),
            "jwt_token_optional": bool(config.fredai_jwt_token),
        },
        "memory": orchestrator.memory_manager.debug_state(),
    }


@app.get("/")
async def ui() -> FileResponse:
    return FileResponse(WEB_ROOT / "index.html")


@app.post("/agent/respond", response_model=AgentRespondResponse)
async def agent_respond(request: AgentRespondRequest) -> Dict[str, Any]:
    if not request.message.strip():
        raise HTTPException(status_code=400, detail="message is required")
    result = await orchestrator.respond(
        workspace_id=request.workspace_id,
        user_id=request.user_id,
        session_id=request.session_id,
        message=request.message,
        attachments=request.attachments,
    )
    return result.as_dict()


@app.get("/agent/sessions")
async def list_sessions(workspace_id: str = "", user_id: str = "", limit: int = 20) -> Dict[str, Any]:
    sessions = orchestrator.session_store.list_sessions(workspace_id=workspace_id, user_id=user_id, limit=limit)
    return {
        "count": len(sessions),
        "sessions": [
            {
                "id": session.id,
                "workspace_id": session.workspace_id,
                "user_id": session.user_id,
                "title": session.title,
                "created_at": session.created_at,
                "updated_at": session.updated_at,
                "metadata": session.metadata,
            }
            for session in sessions
        ],
    }


@app.get("/agent/traces/{request_id}")
async def get_trace(request_id: str) -> Dict[str, Any]:
    events = orchestrator.session_store.get_trace_events(request_id)
    return {"request_id": request_id, "count": len(events), "events": events}


@app.get("/scheduler/jobs")
async def list_jobs() -> Dict[str, Any]:
    jobs = scheduler.list_jobs()
    return {
        "count": len(jobs),
        "jobs": [
            {
                "id": job.id,
                "name": job.name,
                "schedule_type": job.schedule_type,
                "interval_seconds": job.interval_seconds,
                "daily_time": job.daily_time,
                "workspace_id": job.workspace_id,
                "user_id": job.user_id,
                "session_id": job.session_id,
                "deliver_result": job.deliver_result,
                "status": job.status,
                "next_run_at": job.next_run_at,
                "last_run_at": job.last_run_at,
                "last_error": job.last_error,
                "created_at": job.created_at,
                "updated_at": job.updated_at,
            }
            for job in jobs
        ],
    }


@app.post("/scheduler/run-pending")
async def run_pending_jobs() -> Dict[str, Any]:
    count = await scheduler.run_pending_once(orchestrator.run_scheduled_job)
    return {"ran": count}
