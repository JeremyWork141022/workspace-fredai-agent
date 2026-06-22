from __future__ import annotations

import inspect
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, List, Optional

from app.config import AppConfig, workspace_root
from app.memory_manager import AgentMemoryManager
from app.session_store import SessionStore


ToolHandler = Callable[["ToolContext", Dict[str, Any]], Dict[str, Any] | Awaitable[Dict[str, Any]]]
SessionSearchSummarizer = Callable[[str, List[Dict[str, Any]]], Awaitable[List[Dict[str, Any]]]]


@dataclass
class ToolContext:
    session_id: str
    workspace_id: str
    user_id: str
    config: AppConfig
    session_store: SessionStore
    memory_manager: AgentMemoryManager
    summarize_session_search: Optional[SessionSearchSummarizer] = None

    @property
    def workspace_memory_scope(self) -> str:
        return f"workspace:{self.workspace_id}"

    @property
    def user_memory_scope(self) -> str:
        return f"user:{self.user_id}"


@dataclass
class ToolSpec:
    name: str
    description: str
    parameters: Dict[str, Any]
    handler: ToolHandler
    toolset: str = "core"

    def to_chat_completion_tool(self) -> Dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


class ToolRegistry:
    """Central JSON-schema tool registry and dispatch layer."""

    def __init__(self):
        self._tools: Dict[str, ToolSpec] = {}

    def register(self, spec: ToolSpec) -> None:
        if not spec.name.strip():
            raise ValueError("tool name is required")
        self._tools[spec.name] = spec

    def definitions(self) -> List[Dict[str, Any]]:
        return [spec.to_chat_completion_tool() for spec in self._tools.values()]

    def names(self) -> List[str]:
        return sorted(self._tools)

    async def execute(self, *, name: str, arguments: Dict[str, Any], context: ToolContext) -> Dict[str, Any]:
        spec = self._tools.get(name)
        if not spec:
            return {"ok": False, "error": f"Unknown tool: {name}"}
        try:
            result = spec.handler(context, arguments)
            if inspect.isawaitable(result):
                result = await result
            if not isinstance(result, dict):
                return {"ok": False, "tool": name, "error": f"Tool {name} returned non-dict result"}
            return {"ok": True, "tool": name, "result": result}
        except Exception as exc:
            return {"ok": False, "tool": name, "error": f"{type(exc).__name__}: {exc}"}


def build_core_tool_registry(*, session_store: SessionStore, memory_manager: AgentMemoryManager, config: AppConfig) -> ToolRegistry:
    registry = ToolRegistry()

    def make_memory_tool(tool_name: str) -> ToolHandler:
        async def memory_tool(context: ToolContext, args: Dict[str, Any]) -> Dict[str, Any]:
            return context.memory_manager.handle_tool_call(
                tool_name,
                args,
                workspace_id=context.workspace_id,
                user_id=context.user_id,
            )

        return memory_tool

    async def session_search(context: ToolContext, args: Dict[str, Any]) -> Dict[str, Any]:
        query = str(args.get("query") or "").strip()
        if not query:
            sessions = context.session_store.list_sessions(
                workspace_id=context.workspace_id,
                user_id=context.user_id if str(args.get("scope") or "") != "workspace" else "",
                limit=int(args.get("limit") or 5),
            )
            return {
                "mode": "recent",
                "sessions": [
                    {
                        "id": session.id,
                        "workspace_id": session.workspace_id,
                        "user_id": session.user_id,
                        "title": session.title,
                        "updated_at": session.updated_at,
                    }
                    for session in sessions
                ],
            }
        role_filter = args.get("role_filter")
        role_list = [item.strip() for item in str(role_filter or "").split(",") if item.strip()] or None
        limit = max(1, min(int(args.get("limit") or 3), 5))
        scope = str(args.get("scope") or "current_session").strip().lower()
        session_id = context.session_id if scope == "current_session" else None
        workspace_id = context.workspace_id if scope in {"current_session", "workspace"} else None
        matches = context.session_store.search_message_context(
            query=query,
            session_id=session_id,
            workspace_id=workspace_id,
            role_filter=role_list,
            limit=50,
        )
        grouped: Dict[str, Dict[str, Any]] = {}
        for match in matches:
            if match.session_id not in grouped:
                grouped[match.session_id] = {
                    "session_id": match.session_id,
                    "workspace_id": match.workspace_id,
                    "user_id": match.user_id,
                    "title": match.session_title,
                    "matches": [],
                    "conversation": context.session_store.get_messages_as_conversation(match.session_id, limit=240),
                }
            grouped[match.session_id]["matches"].append(
                {
                    "message_id": match.message_id,
                    "role": match.role,
                    "snippet": match.snippet,
                    "created_at": match.created_at,
                    "context": match.context,
                }
            )
            if len(grouped) >= limit:
                break
        sessions = list(grouped.values())[:limit]
        if context.summarize_session_search and sessions:
            sessions = await context.summarize_session_search(query, sessions)
        return {"mode": "search", "query": query, "count": len(sessions), "results": sessions}

    async def routine_rule(context: ToolContext, args: Dict[str, Any]) -> Dict[str, Any]:
        allowed_types = {
            "hook",
            "cron_job",
            "skill",
            "tool_request",
            "curated_memory",
            "sqlite_memory",
            "not_supported",
        }
        rule_type = str(args.get("rule_type") or "sqlite_memory").strip()
        if rule_type not in allowed_types:
            rule_type = "sqlite_memory"
        status = str(args.get("status") or "active").strip()
        metadata = {
            "rationale": str(args.get("rationale") or "").strip(),
            "needs_builder_work": bool(args.get("needs_builder_work") or False),
        }
        side_effects: List[Dict[str, Any]] = []

        if rule_type == "hook":
            hook_event = str(args.get("hook_event") or "pre_llm").strip()
            if hook_event != "pre_llm":
                status = "planned"
                metadata["needs_builder_work"] = True
            metadata["hook_event"] = hook_event

        if rule_type == "cron_job":
            created_job = _create_cron_job_from_args(args, context=context)
            if created_job["created"]:
                metadata["cron_job_id"] = created_job["job_id"]
                side_effects.append(created_job)
            else:
                status = "planned"
                metadata["needs_builder_work"] = True
                side_effects.append(created_job)

        if rule_type == "curated_memory":
            content = str(args.get("memory_content") or args.get("action") or args.get("source_request") or "").strip()
            target = str(args.get("memory_target") or "user").strip()
            result = context.memory_manager.handle_tool_call(
                "memory",
                {"action": "add", "target": target, "content": content},
                workspace_id=context.workspace_id,
                user_id=context.user_id,
            )
            side_effects.append({"type": "curated_memory", "result": result})

        if rule_type == "sqlite_memory":
            key = str(args.get("memory_key") or args.get("title") or "").strip()
            value = str(args.get("memory_content") or args.get("action") or args.get("source_request") or "").strip()
            if key and value:
                record = context.memory_manager.sqlite_store.remember(
                    scope=context.workspace_memory_scope,
                    key=key,
                    value=value,
                    tags=["routine_rule", "workspace_preference"],
                    source="routine_rule",
                )
                side_effects.append({"type": "sqlite_memory", "id": record.id, "key": record.key})

        record = context.memory_manager.sqlite_store.save_routine_rule(
            workspace_id=context.workspace_id,
            user_id=context.user_id,
            rule_type=rule_type,
            title=str(args.get("title") or "").strip(),
            trigger_text=str(args.get("trigger") or "").strip(),
            action_text=str(args.get("action") or "").strip(),
            source_request=str(args.get("source_request") or "").strip(),
            status=status,
            metadata=metadata,
        )
        return {
            "saved": True,
            "id": record.id,
            "rule_type": record.rule_type,
            "title": record.title,
            "trigger": record.trigger_text,
            "action": record.action_text,
            "status": record.status,
            "guidance": _routine_rule_guidance(record.rule_type),
            "side_effects": side_effects,
        }

    async def workspace_read_file(context: ToolContext, args: Dict[str, Any]) -> Dict[str, Any]:
        path = _resolve_workspace_path(str(args.get("path") or ""), must_exist=True, file_only=True)
        max_chars = max(1, min(int(args.get("max_chars") or 20000), 100000))
        text = path.read_text(encoding="utf-8", errors="replace")
        root = workspace_root()
        return {
            "path": str(path.relative_to(root)),
            "truncated": len(text) > max_chars,
            "content": text[:max_chars],
        }

    async def workspace_list_files(context: ToolContext, args: Dict[str, Any]) -> Dict[str, Any]:
        root = workspace_root()
        limit = max(1, min(int(args.get("limit") or 300), 5000))
        files: List[str] = []
        skip = {".git", ".venv", "__pycache__", ".runtime", ".pytest_cache", "node_modules"}
        for path in root.rglob("*"):
            if not path.is_file():
                continue
            if any(part in skip for part in path.parts):
                continue
            files.append(str(path.relative_to(root)))
            if len(files) >= limit:
                break
        return {"root": str(root), "count": len(files), "files": files}

    async def workspace_find_files(context: ToolContext, args: Dict[str, Any]) -> Dict[str, Any]:
        query = str(args.get("query") or "").strip().lower()
        if not query:
            return {"error": "query is required"}
        limit = max(1, min(int(args.get("limit") or 20), 100))
        tokens = [token for token in query.replace("_", " ").replace("-", " ").split() if len(token) >= 2] or [query]
        root = workspace_root()
        skip = {".git", ".venv", "__pycache__", ".runtime", ".pytest_cache", "node_modules"}
        candidates: List[Dict[str, Any]] = []
        for path in root.rglob("*"):
            if not path.is_file() or any(part in skip for part in path.parts):
                continue
            rel = str(path.relative_to(root))
            rel_lower = rel.lower()
            name_lower = path.name.lower()
            score = 0
            for token in tokens:
                if token in name_lower:
                    score += 5
                if token in rel_lower:
                    score += 2
            if score:
                candidates.append({"path": rel, "score": score})
        candidates.sort(key=lambda item: (-int(item["score"]), str(item["path"])))
        return {"query": args.get("query"), "matches": candidates[:limit]}

    for schema in memory_manager.get_tool_schemas():
        registry.register(
            ToolSpec(
                name=str(schema["name"]),
                toolset="memory",
                description=str(schema.get("description") or ""),
                parameters=dict(schema.get("parameters") or {"type": "object", "properties": {}}),
                handler=make_memory_tool(str(schema["name"])),
            )
        )

    registry.register(
        ToolSpec(
            name="session_search",
            toolset="session",
            description=(
                "Search saved conversation history using FTS5/trigram retrieval. Use when the user refers to "
                "previous messages, past decisions, or details outside the recent context window."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "scope": {"type": "string", "enum": ["current_session", "workspace", "all"], "default": "current_session"},
                    "role_filter": {"type": "string", "description": "Optional comma-separated roles, e.g. user,assistant."},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 5},
                },
                "required": [],
                "additionalProperties": False,
            },
            handler=session_search,
        )
    )
    registry.register(
        ToolSpec(
            name="routine_rule",
            toolset="routine",
            description=(
                "Classify and save a request about future behavior, standing preferences, warnings, reminders, "
                "reusable workflows, or new tool needs. This records the rule safely; it does not execute arbitrary code."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "rule_type": {
                        "type": "string",
                        "enum": ["hook", "cron_job", "skill", "tool_request", "curated_memory", "sqlite_memory", "not_supported"],
                    },
                    "title": {"type": "string"},
                    "trigger": {"type": "string"},
                    "action": {"type": "string"},
                    "hook_event": {"type": "string", "enum": ["pre_llm", "post_llm", "pre_tool_call", "post_tool_call"]},
                    "schedule_type": {"type": "string", "enum": ["interval", "daily"]},
                    "interval_seconds": {"type": "integer", "minimum": 60},
                    "daily_time": {"type": "string", "description": "Local 24-hour HH:MM."},
                    "job_prompt": {"type": "string"},
                    "deliver_result": {"type": "boolean"},
                    "memory_target": {"type": "string", "enum": ["memory", "user"]},
                    "memory_key": {"type": "string"},
                    "memory_content": {"type": "string"},
                    "source_request": {"type": "string"},
                    "rationale": {"type": "string"},
                    "status": {"type": "string", "enum": ["active", "paused", "planned", "dismissed"], "default": "active"},
                    "needs_builder_work": {"type": "boolean"},
                },
                "required": ["rule_type", "title", "trigger", "action"],
                "additionalProperties": False,
            },
            handler=routine_rule,
        )
    )
    registry.register(
        ToolSpec(
            name="workspace_read_file",
            toolset="workspace",
            description="Read a UTF-8 text file inside WORKSPACE_AGENT_ROOT.",
            parameters={
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "max_chars": {"type": "integer", "minimum": 1, "maximum": 100000},
                },
                "required": ["path"],
                "additionalProperties": False,
            },
            handler=workspace_read_file,
        )
    )
    registry.register(
        ToolSpec(
            name="workspace_list_files",
            toolset="workspace",
            description="List files inside WORKSPACE_AGENT_ROOT.",
            parameters={
                "type": "object",
                "properties": {"limit": {"type": "integer", "minimum": 1, "maximum": 5000}},
                "required": [],
                "additionalProperties": False,
            },
            handler=workspace_list_files,
        )
    )
    registry.register(
        ToolSpec(
            name="workspace_find_files",
            toolset="workspace",
            description="Find likely files inside WORKSPACE_AGENT_ROOT from a natural language query.",
            parameters={
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 100},
                },
                "required": ["query"],
                "additionalProperties": False,
            },
            handler=workspace_find_files,
        )
    )
    return registry


def _routine_rule_guidance(rule_type: str) -> str:
    if rule_type == "hook":
        return "Saved as a hook-style rule. pre_llm hooks are injected into future model context when their trigger matches."
    if rule_type == "cron_job":
        return "Saved as a cron-job rule. If schedule fields were complete, a real scheduler job was created."
    if rule_type == "skill":
        return "Saved as a reusable workflow candidate for later promotion into a formal skill."
    if rule_type == "tool_request":
        return "Saved as a future executable capability request."
    if rule_type == "curated_memory":
        return "Saved as a routine rule and written to curated memory."
    if rule_type == "not_supported":
        return "Saved as not currently supported so it can be reviewed later."
    return "Saved as searchable local memory for future retrieval."


def _create_cron_job_from_args(args: Dict[str, Any], *, context: ToolContext) -> Dict[str, Any]:
    from app.scheduler import CronScheduler

    schedule_type = str(args.get("schedule_type") or "").strip()
    name = str(args.get("title") or "").strip()
    prompt = str(args.get("job_prompt") or args.get("action") or "").strip()
    if not schedule_type or not name or not prompt:
        return {
            "type": "cron_job",
            "created": False,
            "error": "schedule_type, title, and job_prompt/action are required to create a real scheduled job.",
        }
    try:
        scheduler = CronScheduler()
        deliver_result = bool(args.get("deliver_result") or False)
        if schedule_type == "interval":
            job = scheduler.add_interval_job(
                name=name,
                prompt=prompt,
                interval_seconds=int(args.get("interval_seconds") or 0),
                workspace_id=context.workspace_id,
                user_id=context.user_id,
                session_id=context.session_id,
                deliver_result=deliver_result,
            )
        elif schedule_type == "daily":
            job = scheduler.add_daily_job(
                name=name,
                prompt=prompt,
                daily_time=str(args.get("daily_time") or ""),
                workspace_id=context.workspace_id,
                user_id=context.user_id,
                session_id=context.session_id,
                deliver_result=deliver_result,
            )
        else:
            raise ValueError("schedule_type must be interval or daily")
        return {
            "type": "cron_job",
            "created": True,
            "job_id": job.id,
            "schedule_type": job.schedule_type,
            "next_run_at": job.next_run_at,
            "deliver_result": job.deliver_result,
        }
    except Exception as exc:
        return {"type": "cron_job", "created": False, "error": f"{type(exc).__name__}: {exc}"}


def _resolve_workspace_path(path: str, *, must_exist: bool, file_only: bool) -> Path:
    root = workspace_root()
    raw = Path(path.strip().strip('"').strip("'"))
    if not path.strip():
        raise ValueError("path is required")
    candidate = raw if raw.is_absolute() else root / raw
    resolved = candidate.resolve()
    if resolved != root and root not in resolved.parents:
        raise ValueError(f"Path is outside WORKSPACE_AGENT_ROOT: {path}")
    if must_exist and not resolved.exists():
        raise FileNotFoundError(f"File not found: {path}")
    if file_only and resolved.exists() and not resolved.is_file():
        raise ValueError(f"Path is not a file: {path}")
    return resolved

