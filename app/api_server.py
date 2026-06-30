from __future__ import annotations

import base64
import binascii
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from app.attachment_extractors import attachment_capabilities, extract_attachment
from app.config import load_config
from app.knowledge_store import DEFAULT_KNOWLEDGE_BASE
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


class MessageFeedbackRequest(BaseModel):
    user_id: str = Field(default="shared", max_length=160)
    label: str = Field(default="comment", max_length=40)
    comment: str = Field(min_length=1, max_length=4000)


class KnowledgeDocumentUploadRequest(BaseModel):
    workspace_id: str = Field(default="default")
    knowledge_base: str = Field(default=DEFAULT_KNOWLEDGE_BASE)
    title: str = Field(default="", max_length=240)
    process: str = Field(default="", max_length=120)
    doc_type: str = Field(default="", max_length=120)
    tags: List[str] = Field(default_factory=list)
    summary: str = Field(default="", max_length=2000)
    file_name: str = Field(min_length=1, max_length=260)
    file_extension: str = Field(default="", max_length=24)
    media_type: str = Field(default="", max_length=160)
    data_base64: str = Field(min_length=1)
    chunk_strategy: str = Field(default="auto")


config = load_config()
orchestrator = WorkspaceAgentOrchestrator(config)
scheduler = CronScheduler()

app = FastAPI(
    title="CRT Analytics Agent",
    version="0.1.0",
    description="Internal API runtime for the CRT Analytics FredAI agent.",
)

WEB_ROOT = Path(__file__).resolve().parents[1] / "web"
app.mount("/static", StaticFiles(directory=WEB_ROOT), name="static")

ATTACHMENT_HEADER_RE = re.compile(
    r"\[Attachment\s+(?P<index>\d+):\s+(?P<name>.*?),\s+extension=(?P<extension>.*?),\s+media_type=(?P<media_type>.*?),\s+source=(?P<source>[^\]]*)\]",
    re.IGNORECASE,
)

VERSION_RE = re.compile(
    r"(?:\bversion\b|\bver\.?\b|(?:^|[^A-Za-z0-9])v)\s*[:_-]?\s*(?P<version>\d+(?:\.\d+){0,3}[a-zA-Z]?)",
    re.IGNORECASE,
)
DATE_VERSION_RE = re.compile(r"\b(?P<date>20\d{2}[-_/\.](?:0?[1-9]|1[0-2])[-_/\.](?:0?[1-9]|[12]\d|3[01]))\b")


def _message_display_text(message: Any) -> str:
    if message.role != "user":
        return message.text
    display_text = str(message.metadata.get("display_text") or "").strip()
    if display_text:
        return display_text
    marker = "\n\n[Attachment "
    if marker in message.text:
        return message.text.split(marker, 1)[0].strip() or "Please analyze the attached file(s)."
    return message.text


def _message_display_attachments(message: Any) -> List[Dict[str, Any]]:
    if message.role != "user":
        return []
    metadata_attachments = message.metadata.get("attachments")
    if isinstance(metadata_attachments, list):
        return [item for item in metadata_attachments if isinstance(item, dict)]

    attachments: List[Dict[str, Any]] = []
    for match in ATTACHMENT_HEADER_RE.finditer(message.text or ""):
        index = match.group("index")
        extension = (match.group("extension") or "").strip()
        media_type = (match.group("media_type") or "").strip()
        attachments.append(
            {
                "id": f"historic_attachment_{message.id}_{index}",
                "name": (match.group("name") or f"attachment_{index}").strip(),
                "size": 0,
                "kind": _kind_from_attachment_header(extension, media_type),
                "extension": "" if extension == "unknown" else extension,
                "media_type": "" if media_type == "unknown" else media_type,
                "transfer": "historic_metadata",
            }
        )
    return attachments


def _kind_from_attachment_header(extension: str, media_type: str) -> str:
    extension = extension.lower().strip()
    media_type = media_type.lower().strip()
    if media_type.startswith("image/") or extension in {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp", ".tif", ".tiff"}:
        return "image"
    if extension == ".pdf" or media_type == "application/pdf":
        return "pdf"
    if extension in {".csv", ".tsv", ".xlsx", ".xls"}:
        return "spreadsheet"
    if extension in {".docx", ".doc", ".rtf"}:
        return "document"
    if extension in {".pptx", ".ppt"}:
        return "presentation"
    if media_type.startswith("text/"):
        return "text"
    return "file"


def _infer_knowledge_metadata(file_name: str, content: str, *, existing: Any = None) -> Dict[str, Any]:
    haystack = f"{file_name}\n{content[:5000]}".lower()
    processes = [
        ("Dynamic CRT Cost", ["dynamic crt cost", "dynamic cost"]),
        ("Spot CRT Cost", ["spot crt cost", "spot cost"]),
        ("EVA", [" eva ", "eva_", "euc eva", "eva euc"]),
        ("MACS", [" macs ", "macs_", "macs output"]),
        ("PRM", [" prm ", "pricing/risk", "pricing risk"]),
    ]
    doc_types = [
        ("change_memo", ["change memo", "change log", "release notes", "revision history"]),
        ("user_guide", ["user guide", "operating guide", "euc user", "how to run"]),
        ("methodology", ["methodology", "methodological", "model methodology"]),
        ("model_review", ["model review", "review document"]),
        ("model_use", ["model use", "use document"]),
        ("model_register", ["model register", "registration"]),
        ("runbook", ["runbook", "run book", "operating procedure", "workflow"]),
        ("script", [".py", "python script"]),
    ]

    process = ""
    for candidate, needles in processes:
        if any(needle in f" {haystack} " for needle in needles):
            process = candidate
            break
    if not process and existing is not None:
        process = str(getattr(existing, "process", "") or "")

    doc_type = ""
    for candidate, needles in doc_types:
        if any(needle in haystack for needle in needles):
            doc_type = candidate
            break
    if not doc_type and existing is not None:
        doc_type = str(getattr(existing, "doc_type", "") or "")

    version = ""
    version_match = VERSION_RE.search(f"{file_name}\n{content[:1000]}")
    if version_match:
        version = version_match.group("version")
    date_match = DATE_VERSION_RE.search(f"{file_name}\n{content[:1000]}")
    effective_date = date_match.group("date").replace("_", "-").replace("/", "-").replace(".", "-") if date_match else ""

    tags = []
    for value in [process, doc_type]:
        if value:
            tags.append(value)
    for keyword, tag in [
        ("eva", "EVA"),
        ("macs", "MACS"),
        ("intex", "Intex"),
        ("denodo", "Denodo"),
        ("crt", "CRT"),
        ("prm", "PRM"),
        ("euc", "EUC"),
        ("methodology", "methodology"),
        ("user guide", "user_guide"),
        ("change memo", "change_memo"),
    ]:
        if keyword in haystack and tag not in tags:
            tags.append(tag)
    if version:
        tags.append(f"version:{version}")
    if effective_date:
        tags.append(f"date:{effective_date}")

    return {
        "process": process,
        "doc_type": doc_type,
        "version": version,
        "effective_date": effective_date,
        "tags": tags[:30],
        "inference_method": "filename_and_text_heuristic",
        "wiki_guidance": (
            "If this document updates a prior version, create or update a wiki change memo page "
            "that links the old and new document IDs and summarizes differences."
        ),
    }


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
    feedback_by_message_id = orchestrator.session_store.feedback_for_messages([message.id for message in visible_messages])
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
                "text": _message_display_text(message),
                "content": message.content,
                "attachments": _message_display_attachments(message),
                "created_at": message.created_at,
                "metadata": message.metadata,
                "feedback": [
                    orchestrator.session_store.feedback_to_dict(feedback)
                    for feedback in feedback_by_message_id.get(message.id, [])
                ],
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


@app.post("/agent/messages/{message_id}/feedback")
async def add_message_feedback(message_id: int, request: MessageFeedbackRequest) -> Dict[str, Any]:
    try:
        feedback = orchestrator.session_store.add_message_feedback(
            message_id=message_id,
            user_id=request.user_id,
            label=request.label,
            comment=request.comment,
        )
    except ValueError as exc:
        detail = str(exc) or "could not save message feedback"
        status_code = 404 if "not found" in detail else 400
        raise HTTPException(status_code=status_code, detail=detail)
    return {"feedback": orchestrator.session_store.feedback_to_dict(feedback)}


@app.get("/agent/feedback")
async def list_message_feedback(
    workspace_id: str = "",
    session_id: str = "",
    limit: int = 200,
) -> Dict[str, Any]:
    feedback = orchestrator.session_store.list_message_feedback(
        workspace_id=workspace_id,
        session_id=session_id,
        limit=limit,
    )
    return {
        "count": len(feedback),
        "feedback": [orchestrator.session_store.feedback_to_dict(item) for item in feedback],
    }


def _decode_upload(data_base64: str) -> bytes:
    value = data_base64.strip()
    if "," in value and value[:64].lower().startswith("data:"):
        value = value.split(",", 1)[1]
    try:
        return base64.b64decode(value, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise HTTPException(status_code=400, detail="data_base64 is not valid base64") from exc


def _extract_upload_text(request: KnowledgeDocumentUploadRequest) -> tuple[str, str, bytes]:
    raw = _decode_upload(request.data_base64)
    extension = request.file_extension.strip() or Path(request.file_name).suffix.lower()
    attachment = {
        "name": request.file_name,
        "size": len(raw),
        "extension": extension,
        "media_type": request.media_type,
        "content_type": request.media_type,
        "encoding": "base64",
        "data_base64": base64.b64encode(raw).decode("ascii"),
        "transfer": "inline_base64",
    }
    extraction = extract_attachment(attachment, index=1, workspace_root=WEB_ROOT.parent)
    if not extraction.text.strip():
        raise HTTPException(status_code=400, detail="The uploaded file could not be converted into searchable text.")
    return extraction.text, extraction.warning, raw


@app.get("/agent/knowledge/documents")
async def list_knowledge_documents(
    workspace_id: str = "",
    knowledge_base: str = "",
    process: str = "",
    limit: int = 200,
) -> Dict[str, Any]:
    documents = orchestrator.knowledge_store.list_documents(
        workspace_id=workspace_id or "default",
        knowledge_base=knowledge_base,
        process=process,
        limit=max(1, min(limit, 500)),
    )
    wiki_pages = orchestrator.knowledge_store.search_wiki(
        workspace_id=workspace_id or "default",
        query="",
        limit=200,
    )
    issues = orchestrator.knowledge_store.list_wiki_issues(
        workspace_id=workspace_id or "default",
        status="pending",
        limit=100,
    )
    return {
        "count": len(documents),
        "documents": documents,
        "wiki_pages": [orchestrator.knowledge_store.wiki_to_dict(page) for page in wiki_pages],
        "wiki_issues": [orchestrator.knowledge_store.issue_to_dict(issue) for issue in issues],
        "guidance": (
            "Source documents are the raw knowledge base. If interpretation is wrong, keep the raw document "
            "constant and add corrections or clarifications through wiki_write/wiki_issue."
        ),
    }


@app.post("/agent/knowledge/documents")
async def upload_knowledge_document(request: KnowledgeDocumentUploadRequest) -> Dict[str, Any]:
    content, warning, raw = _extract_upload_text(request)
    file_hash = orchestrator.knowledge_store.hash_bytes(raw)
    title = request.title.strip() or Path(request.file_name).stem or request.file_name
    inferred = _infer_knowledge_metadata(request.file_name, content)
    process = request.process.strip() or inferred["process"]
    doc_type = request.doc_type.strip() or inferred["doc_type"]
    tags = list(dict.fromkeys([*request.tags, *inferred["tags"]]))
    result = orchestrator.knowledge_store.ingest_document(
        workspace_id=request.workspace_id,
        knowledge_base=request.knowledge_base or DEFAULT_KNOWLEDGE_BASE,
        title=title,
        content=content,
        source_type="attachment",
        source_uri=f"upload:{request.file_name}:{file_hash[:12]}",
        file_name=request.file_name,
        file_extension=request.file_extension or Path(request.file_name).suffix.lower(),
        process=process,
        doc_type=doc_type,
        tags=tags,
        metadata={"uploaded_via": "knowledge_browser", "raw_file_hash": file_hash, "auto_metadata": inferred},
        summary=request.summary,
        chunk_strategy=request.chunk_strategy or "auto",
    )
    document_id = str(result.get("document", {}).get("id") or "")
    orchestrator.knowledge_store.save_document_file(
        workspace_id=request.workspace_id,
        document_id=document_id,
        file_name=request.file_name,
        media_type=request.media_type,
        content=raw,
    )
    if warning:
        result["parser_warning"] = warning
    if inferred.get("version"):
        result["wiki_change_memo_guidance"] = (
            "Version hint detected. If this supersedes or differs from another source document, "
            "use wiki_write to create a change memo page linking document IDs and summarizing changes."
        )
    return result


@app.put("/agent/knowledge/documents/{document_id}")
async def replace_knowledge_document(document_id: str, request: KnowledgeDocumentUploadRequest) -> Dict[str, Any]:
    existing = orchestrator.knowledge_store.get_document(workspace_id=request.workspace_id, document_id=document_id)
    if not existing:
        raise HTTPException(status_code=404, detail="knowledge document not found")
    content, warning, raw = _extract_upload_text(request)
    file_hash = orchestrator.knowledge_store.hash_bytes(raw)
    inferred = _infer_knowledge_metadata(request.file_name, content, existing=existing)
    process = request.process.strip() or inferred["process"] or existing.process
    doc_type = request.doc_type.strip() or inferred["doc_type"] or existing.doc_type
    tags = list(dict.fromkeys([*(request.tags or existing.tags), *inferred["tags"]]))
    result = orchestrator.knowledge_store.ingest_document(
        workspace_id=request.workspace_id,
        knowledge_base=request.knowledge_base or DEFAULT_KNOWLEDGE_BASE,
        title=request.title.strip() or existing.title,
        content=content,
        source_type="attachment",
        source_uri=existing.source_uri,
        file_name=request.file_name,
        file_extension=request.file_extension or Path(request.file_name).suffix.lower(),
        process=process,
        doc_type=doc_type,
        tags=tags,
        metadata={
            **existing.metadata,
            "uploaded_via": "knowledge_browser",
            "raw_file_hash": file_hash,
            "replaced_document_id": document_id,
            "auto_metadata": inferred,
        },
        summary=request.summary or existing.summary,
        chunk_strategy=request.chunk_strategy or "auto",
    )
    orchestrator.knowledge_store.save_document_file(
        workspace_id=request.workspace_id,
        document_id=document_id,
        file_name=request.file_name,
        media_type=request.media_type,
        content=raw,
    )
    if warning:
        result["parser_warning"] = warning
    result["replaced"] = True
    if inferred.get("version"):
        result["wiki_change_memo_guidance"] = (
            "Version hint detected. If this supersedes or differs from another source document, "
            "use wiki_write to create a change memo page linking document IDs and summarizing changes."
        )
    return result


@app.get("/agent/knowledge/documents/{document_id}/download")
async def download_knowledge_document(document_id: str, workspace_id: str = "") -> Response:
    workspace_id = workspace_id or "default"
    stored = orchestrator.knowledge_store.get_document_file(workspace_id=workspace_id, document_id=document_id)
    if stored:
        return Response(
            content=stored["content"],
            media_type=stored["media_type"] or "application/octet-stream",
            headers={"Content-Disposition": f"attachment; filename={stored['file_name']}"},
        )
    fallback = orchestrator.knowledge_store.document_text_export(workspace_id=workspace_id, document_id=document_id)
    if not fallback:
        raise HTTPException(status_code=404, detail="knowledge document not found")
    return Response(
        content=fallback["content"].encode("utf-8"),
        media_type="text/plain; charset=utf-8",
        headers={"Content-Disposition": f"attachment; filename={fallback['file_name']}"},
    )


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
