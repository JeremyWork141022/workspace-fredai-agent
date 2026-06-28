from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from app.attachment_extractors import attachment_capabilities
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
    user_message_id: Optional[int] = None
    assistant_message_id: Optional[int] = None


class SessionRenameRequest(BaseModel):
    title: str = Field(min_length=1, max_length=120)


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
    attachments = attachment_capabilities()
    return {
        "ok": True,
        "model": config.model,
        "fredai_base_url": config.fredai_base_url,
        "scheduler_enabled": config.scheduler_enabled,
        "memory": orchestrator.memory_manager.debug_state(),
        "knowledge": orchestrator.knowledge_store.debug_state(),
        "attachment_capabilities": attachments,
        "capabilities": {
            "streaming": False,
            "session_history": False,
            "server_cancel": False,
            "attachments": attachments,
        },
    }


@app.get("/")
async def ui() -> FileResponse:
    return FileResponse(WEB_ROOT / "index.html")


@app.get("/agent/tools")
async def list_tools() -> Dict[str, Any]:
    tools = orchestrator.tool_registry.definitions()
    return {
        "count": len(tools),
        "tools": [
            {
                "name": str(tool.get("function", {}).get("name") or ""),
                "description": str(tool.get("function", {}).get("description") or ""),
                "parameters": tool.get("function", {}).get("parameters") or {},
            }
            for tool in tools
        ],
    }


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


@app.get("/agent/sessions/{session_id}")
async def get_session(session_id: str, limit: int = 500) -> Dict[str, Any]:
    session = orchestrator.session_store.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="session not found")
    messages = orchestrator.session_store.recent_messages(session.id, limit=max(1, min(limit, 2000)))
    visible_messages = [message for message in messages if message.role in {"user", "assistant"}]
    return {
        "session": {
            "id": session.id,
            "workspace_id": session.workspace_id,
            "user_id": session.user_id,
            "title": session.title,
            "created_at": session.created_at,
            "updated_at": session.updated_at,
            "metadata": session.metadata,
        },
        "messages": [
            {
                "id": message.id,
                "session_id": message.session_id,
                "role": message.role,
                "text": message.text,
                "content": message.content,
                "created_at": message.created_at,
                "metadata": message.metadata,
            }
            for message in visible_messages
        ],
    }


@app.patch("/agent/sessions/{session_id}")
async def rename_session(session_id: str, request: SessionRenameRequest) -> Dict[str, Any]:
    session = orchestrator.session_store.rename_session(session_id, request.title)
    if not session:
        raise HTTPException(status_code=404, detail="session not found")
    return {
        "session": {
            "id": session.id,
            "workspace_id": session.workspace_id,
            "user_id": session.user_id,
            "title": session.title,
            "created_at": session.created_at,
            "updated_at": session.updated_at,
            "metadata": session.metadata,
        }
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
