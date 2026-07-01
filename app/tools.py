from __future__ import annotations

import inspect
import json
import re
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, List, Optional

from app.attachment_extractors import extract_attachment
from app.config import AppConfig, workspace_root
from app.dashboard_store import DashboardStore
from app.knowledge_store import DEFAULT_KNOWLEDGE_BASE, KnowledgeStore
from app.memory_manager import AgentMemoryManager
from app.session_store import SessionStore


ToolHandler = Callable[["ToolContext", Dict[str, Any]], Dict[str, Any] | Awaitable[Dict[str, Any]]]
SessionSearchSummarizer = Callable[[str, List[Dict[str, Any]]], Awaitable[List[Dict[str, Any]]]]


DEFINITION_PATTERNS = [
    re.compile(r"\bwhat\s+is\s+(?:a|an|the)?\s*[\"'`]?([^\"'`?]+?)[\"'`]?\s*\??$", re.IGNORECASE),
    re.compile(r"\bwhat\s+does\s+[\"'`]?([^\"'`?]+?)[\"'`]?\s+mean\b", re.IGNORECASE),
    re.compile(r"\bdefine\s+[\"'`]?([^\"'`?]+?)[\"'`]?", re.IGNORECASE),
    re.compile(r"\bexplain\s+[\"'`]?([^\"'`?]+?)[\"'`]?", re.IGNORECASE),
]
FILE_EXTENSION_TOKENS = {"csv", "doc", "docx", "pdf", "txt", "xls", "xlsx", "xslx", "ppt", "pptx"}
DEFINITION_CUES = [
    "refers to",
    "means",
    "defined as",
    "definition",
    "contains",
    "consists of",
    "comprises",
    "represents",
    "identifies",
    "stores",
    "maps",
    "records",
    "loan id",
    "loan number",
]
MENTION_ONLY_CUES = [
    "one of",
    "required input",
    "input file",
    "input files",
    "prepare",
    "upload",
    "select",
    "provided",
    "listed",
    "needed",
    "include the following",
    "three input",
]


def _definition_subject(query: str) -> str:
    clean = " ".join(str(query or "").split()).strip()
    for pattern in DEFINITION_PATTERNS:
        match = pattern.search(clean)
        if not match:
            continue
        subject = match.group(1).strip(" .,:;!?\"'`")
        subject = re.sub(r"\s+\b(in|for|within|under)\b\s+.*$", "", subject, flags=re.IGNORECASE).strip()
        return subject[:120]
    return ""


def _subject_tokens(subject: str) -> List[str]:
    tokens = [token.lower() for token in re.findall(r"[A-Za-z0-9]+", subject or "") if len(token) >= 2]
    core = [token for token in tokens if token not in FILE_EXTENSION_TOKENS]
    return core or tokens[:2]


def _candidate_sentences(subject: str, text: str, *, limit: int = 5) -> List[str]:
    tokens = _subject_tokens(subject)
    if not tokens:
        return []
    sentences = re.split(r"(?<=[.!?])\s+|\n+", text or "")
    matches: List[str] = []
    for sentence in sentences:
        clean = " ".join(sentence.split()).strip()
        if not clean:
            continue
        lowered = clean.lower()
        if all(token in lowered for token in tokens):
            matches.append(clean[:500])
        if len(matches) >= limit:
            break
    return matches


def _knowledge_gap_hint(user_question: str, evidence_texts: List[str]) -> Dict[str, Any]:
    subject = _definition_subject(user_question)
    if not subject:
        return {}
    joined = "\n".join(text for text in evidence_texts if text).strip()
    mentions = _candidate_sentences(subject, joined)
    if not mentions:
        return {
            "definition_question": True,
            "subject": subject,
            "evidence_status": "term_not_found",
            "guidance": (
                "The retrieved source context does not define or clearly mention this term. "
                "Say the indexed documentation does not define it, avoid circular answers, and suggest a wiki_issue/glossary correction."
            ),
        }
    mention_text = "\n".join(mentions).lower()
    has_definition_cue = any(cue in mention_text for cue in DEFINITION_CUES)
    has_weak_cue = any(cue in mention_text for cue in MENTION_ONLY_CUES)
    status = "definition_candidate_found" if has_definition_cue else "definition_not_found"
    if has_definition_cue and has_weak_cue:
        status = "definition_candidate_found"
    return {
        "definition_question": True,
        "subject": subject,
        "evidence_status": status,
        "mention_only_cues_found": bool(has_weak_cue and not has_definition_cue),
        "mention_snippets": mentions[:3],
        "guidance": (
            "If evidence_status is definition_not_found, do not restate that the term is merely an input/listed item. "
            "Say the indexed source mentions the term but does not define it; provide only labeled inference if helpful; "
            "create or suggest wiki_issue/wiki_write for a glossary correction."
        ),
    }


@dataclass
class ToolContext:
    session_id: str
    workspace_id: str
    user_id: str
    config: AppConfig
    session_store: SessionStore
    memory_manager: AgentMemoryManager
    knowledge_store: KnowledgeStore
    dashboard_store: DashboardStore
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


def _crt_cost_dashboard_catalog() -> Dict[str, Any]:
    """Governed starter catalog for CRT Cost dashboard planning."""

    return {
        "dataset": {
            "name": "crt_cost_deal_level",
            "status": "schema_seed",
            "grain": "one row per deal",
            "source_boundary": "Clean source data is read-only. Agent-created filters, derived columns, and dashboard specs live in session/workspace sandbox records.",
        },
        "fields": [
            {"name": "deal_id", "label": "Deal ID", "type": "string", "role": "identifier", "operators": ["equals", "contains", "in"]},
            {"name": "deal_name", "label": "Deal Name", "type": "string", "role": "dimension", "operators": ["equals", "contains", "in"]},
            {"name": "deal_type", "label": "Deal Type", "type": "string", "role": "dimension", "operators": ["equals", "in"]},
            {"name": "settle_year", "label": "Settle Year", "type": "integer", "role": "dimension", "operators": ["equals", "between", "in", "gte", "lte"]},
            {"name": "payoff_date", "label": "Payoff Date", "type": "date", "role": "dimension", "operators": ["between", "gte", "lte", "is_blank"]},
            {"name": "upb", "label": "UPB", "type": "number", "role": "measure", "operators": ["gte", "lte", "between"]},
            {"name": "crt_cost", "label": "CRT Cost", "type": "number", "role": "measure", "operators": ["gte", "lte", "between"]},
            {"name": "crt_cost_bps", "label": "CRT Cost bps", "type": "number", "role": "derived_measure", "operators": ["gte", "lte", "between"]},
            {"name": "partial_year_factor", "label": "Partial Year Factor", "type": "number", "role": "derived_measure", "operators": ["gte", "lte", "between"]},
        ],
        "metrics": [
            {"name": "sum_crt_cost", "label": "Total CRT Cost", "base_field": "crt_cost", "aggregation": "sum"},
            {"name": "sum_upb", "label": "Total UPB", "base_field": "upb", "aggregation": "sum"},
            {
                "name": "crt_cost_bps",
                "label": "CRT Cost / UPB bps",
                "formula": "10000 * sum(crt_cost) / nullif(sum(upb), 0)",
                "status": "formula_requires_business_validation",
            },
            {
                "name": "normalized_partial_year_crt_cost",
                "label": "Partial-Year Normalized CRT Cost",
                "formula": "pending source-approved formula",
                "status": "knowledge_gap_until_methodology_is_uploaded",
            },
        ],
        "chart_types": ["table", "metric_cards", "bar", "line", "stacked_bar"],
        "dashboard_views": ["deal_table", "aggregation_chart", "metric_summary", "formula_audit"],
        "clarification_policy": (
            "If the user does not specify metric, grouping, filter, timeframe, or formula assumptions, "
            "ask a focused follow-up before presenting a final operational dashboard."
        ),
    }


def _catalog_names(catalog: Dict[str, Any], key: str) -> set[str]:
    return {str(item.get("name") or "") for item in catalog.get(key, []) if isinstance(item, dict)}


def _string_list(value: Any) -> List[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    text = str(value or "").strip()
    if not text:
        return []
    return [part.strip() for part in re.split(r"[,;\n]+", text) if part.strip()]


def _object_list(value: Any) -> List[Dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [dict(item) for item in value if isinstance(item, dict)]


def build_core_tool_registry(
    *,
    session_store: SessionStore,
    memory_manager: AgentMemoryManager,
    knowledge_store: KnowledgeStore,
    dashboard_store: DashboardStore,
    config: AppConfig,
) -> ToolRegistry:
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

    async def crt_cost_dataset_catalog(context: ToolContext, args: Dict[str, Any]) -> Dict[str, Any]:
        catalog = _crt_cost_dashboard_catalog()
        return {
            "catalog": catalog,
            "governance": {
                "clean_source": "Read-only source database/table. The agent must not overwrite it.",
                "sandbox": "Session-linked dashboard specs and derived-column proposals are stored separately.",
                "next_step": "Use crt_cost_dashboard_spec when the user asks to build, customize, pin, or sketch a CRT Cost dashboard/chart.",
            },
        }

    async def crt_cost_dashboard_spec(context: ToolContext, args: Dict[str, Any]) -> Dict[str, Any]:
        catalog = _crt_cost_dashboard_catalog()
        field_names = _catalog_names(catalog, "fields")
        metric_names = _catalog_names(catalog, "metrics")
        chart_types = set(catalog.get("chart_types", []))

        title = str(args.get("title") or "CRT Cost Dashboard Draft").strip()[:180]
        user_request = str(args.get("user_request") or "").strip()
        group_by = _string_list(args.get("group_by"))
        metrics = _string_list(args.get("metrics"))
        filters = _object_list(args.get("filters"))
        chart_type = str(args.get("chart_type") or "table").strip()
        assumptions = _string_list(args.get("assumptions"))
        questions = _string_list(args.get("clarification_questions"))
        notes = str(args.get("notes") or "").strip()
        pin = bool(args.get("pin", True))

        unknown_group_by = [name for name in group_by if name not in field_names]
        unknown_metrics = [name for name in metrics if name not in metric_names and name not in field_names]
        clean_filters: List[Dict[str, Any]] = []
        unknown_filter_fields: List[str] = []
        for item in filters:
            field = str(item.get("field") or "").strip()
            operator = str(item.get("operator") or "").strip()
            value = item.get("value")
            if field and field not in field_names:
                unknown_filter_fields.append(field)
            clean_filters.append({"field": field, "operator": operator, "value": value})

        if chart_type not in chart_types:
            chart_type = "table"

        missing_inputs: List[str] = []
        if not metrics:
            missing_inputs.append("metric")
        if chart_type not in {"metric_cards", "table"} and not group_by:
            missing_inputs.append("group_by")
        if unknown_group_by:
            missing_inputs.append(f"unknown group_by field(s): {', '.join(unknown_group_by)}")
        if unknown_metrics:
            missing_inputs.append(f"unknown metric(s): {', '.join(unknown_metrics)}")
        if unknown_filter_fields:
            missing_inputs.append(f"unknown filter field(s): {', '.join(unknown_filter_fields)}")

        needs_clarification = bool(missing_inputs or questions)
        if missing_inputs and not questions:
            questions = [
                "Which approved CRT Cost metric should this dashboard use?",
                "Which grouping or filter should define the business view?",
            ][: max(1, min(2, len(missing_inputs)))]

        widget_id = f"widget_{uuid.uuid4().hex[:10]}"
        spec = {
            "schema_version": "crt_dashboard_spec_v0",
            "title": title,
            "intent": user_request,
            "dataset": catalog["dataset"]["name"],
            "data_status": "design_spec_only_no_source_rows_executed",
            "governance": {
                "source_table_mode": "clean_read_only",
                "sandbox_scope": "session",
                "session_id": context.session_id,
                "created_by": context.user_id,
            },
            "filters": clean_filters,
            "assumptions": assumptions,
            "clarification_questions": questions,
            "widgets": [
                {
                    "id": widget_id,
                    "type": chart_type,
                    "title": title,
                    "metrics": metrics,
                    "group_by": group_by,
                    "notes": notes,
                    "rendering": {
                        "kind": "spec_preview",
                        "message": "This preview shows requested structure. Real aggregation will require an approved data execution tool.",
                    },
                }
            ],
        }
        record = context.dashboard_store.save_spec(
            workspace_id=context.workspace_id,
            session_id=context.session_id,
            request_id="",
            title=title,
            kind="crt_cost_dashboard",
            status="needs_clarification" if needs_clarification else "draft",
            pinned=pin,
            spec=spec,
        )
        return {
            "saved": True,
            "dashboard": context.dashboard_store.to_dict(record),
            "needs_clarification": needs_clarification,
            "missing_inputs": missing_inputs,
            "clarification_questions": questions,
            "next_step": (
                "Ask the clarification questions before claiming the dashboard is final."
                if needs_clarification
                else "Show the pinned dashboard spec in the dashboard drawer. Real data execution is a later approved tool."
            ),
        }

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
            target = str(args.get("memory_target") or "memory").strip()
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

    async def knowledge_ingest(context: ToolContext, args: Dict[str, Any]) -> Dict[str, Any]:
        title = str(args.get("title") or "").strip()
        content = str(args.get("content") or "").strip()
        source_path = str(args.get("source_path") or "").strip()
        source_uri = str(args.get("source_uri") or "").strip()
        source_type = str(args.get("source_type") or ("workspace_path" if source_path else "manual")).strip()
        file_name = str(args.get("file_name") or "").strip()
        file_extension = str(args.get("file_extension") or "").strip()
        warning = ""

        if source_path and not content:
            path = _resolve_workspace_path(source_path, must_exist=True, file_only=True)
            extraction = extract_attachment(
                {"path": str(path), "name": path.name, "extension": path.suffix},
                index=1,
                workspace_root=workspace_root(),
            )
            content = extraction.text
            title = title or path.stem
            source_uri = source_uri or str(path)
            source_type = "workspace_path"
            file_name = file_name or path.name
            file_extension = file_extension or path.suffix.lower()
            warning = extraction.warning

        if not content:
            return {
                "ingested": False,
                "error": "content or source_path is required. If the user attached a file, pass the extracted attachment text as content.",
            }

        tags = args.get("tags")
        metadata = args.get("metadata")
        result = context.knowledge_store.ingest_document(
            workspace_id=context.workspace_id,
            title=title or "Untitled knowledge document",
            content=content,
            knowledge_base=str(args.get("knowledge_base") or DEFAULT_KNOWLEDGE_BASE),
            source_type=source_type,
            source_uri=source_uri,
            file_name=file_name,
            file_extension=file_extension,
            process=str(args.get("process") or ""),
            doc_type=str(args.get("doc_type") or ""),
            tags=tags if isinstance(tags, list) else [],
            metadata=metadata if isinstance(metadata, dict) else {},
            summary=str(args.get("summary") or ""),
            chunk_strategy=str(args.get("chunk_strategy") or "auto"),
        )
        result["ingested"] = True
        if warning:
            result["parser_warning"] = warning
        return result

    async def knowledge_search(context: ToolContext, args: Dict[str, Any]) -> Dict[str, Any]:
        query = str(args.get("query") or "").strip()
        if not query:
            return {"error": "query is required"}
        limit = max(1, min(int(args.get("limit") or 8), 20))
        results = context.knowledge_store.search_chunks(
            workspace_id=context.workspace_id,
            query=query,
            limit=limit,
            knowledge_base=str(args.get("knowledge_base") or ""),
            process=str(args.get("process") or ""),
            doc_type=str(args.get("doc_type") or ""),
        )
        refs = [
            {"chunk_id": item.chunk.id, "document_id": item.chunk.document_id, "score": item.score}
            for item in results
        ]
        gap_hint = _knowledge_gap_hint(query, [item.chunk.content for item in results[:5]])
        context.knowledge_store.record_retrieval_event(
            workspace_id=context.workspace_id,
            session_id=context.session_id,
            user_id=context.user_id,
            query=query,
            tool_name="knowledge_search",
            result_refs=refs,
            metadata={"limit": limit, "knowledge_gap": gap_hint},
        )
        return {
            "query": query,
            "count": len(results),
            "results": [context.knowledge_store.search_result_to_dict(item) for item in results],
            "knowledge_gap": gap_hint,
            "recommended_read_args": {"chunk_ids": [item.chunk.id for item in results[:5]], "user_question": query},
            "mandatory_next_step": (
                "Call knowledge_read with the relevant chunk_ids and user_question before answering factual EVA/Macs/process questions. "
                "For definition questions, follow knowledge_gap guidance if retrieved chunks only mention/list the term."
            ),
        }

    async def knowledge_grep(context: ToolContext, args: Dict[str, Any]) -> Dict[str, Any]:
        pattern = str(args.get("pattern") or "").strip()
        if not pattern:
            return {"error": "pattern is required"}
        limit = max(1, min(int(args.get("limit") or 8), 20))
        results = context.knowledge_store.grep_chunks(
            workspace_id=context.workspace_id,
            pattern=pattern,
            limit=limit,
            case_sensitive=bool(args.get("case_sensitive") or False),
            knowledge_base=str(args.get("knowledge_base") or ""),
            process=str(args.get("process") or ""),
            doc_type=str(args.get("doc_type") or ""),
        )
        refs = [{"chunk_id": item.chunk.id, "document_id": item.chunk.document_id, "score": item.score} for item in results]
        context.knowledge_store.record_retrieval_event(
            workspace_id=context.workspace_id,
            session_id=context.session_id,
            user_id=context.user_id,
            query=pattern,
            tool_name="knowledge_grep",
            result_refs=refs,
            metadata={"limit": limit, "case_sensitive": bool(args.get("case_sensitive") or False)},
        )
        return {
            "pattern": pattern,
            "count": len(results),
            "results": [context.knowledge_store.search_result_to_dict(item) for item in results],
            "mandatory_next_step": "Call knowledge_read with the relevant chunk_ids before answering from these matches.",
        }

    async def knowledge_read(context: ToolContext, args: Dict[str, Any]) -> Dict[str, Any]:
        chunk_ids = args.get("chunk_ids")
        indexes = args.get("chunk_indexes")
        user_question = str(args.get("user_question") or "").strip()
        result = context.knowledge_store.read_context(
            workspace_id=context.workspace_id,
            chunk_ids=chunk_ids if isinstance(chunk_ids, list) else [],
            document_id=str(args.get("document_id") or ""),
            chunk_indexes=indexes if isinstance(indexes, list) else [],
            include_parent=bool(args.get("include_parent", True)),
            include_neighbors=bool(args.get("include_neighbors", True)),
            max_chars=int(args.get("max_chars") or 60000),
        )
        refs = [{"chunk_id": chunk.get("chunk_id"), "document_id": chunk.get("document_id")} for chunk in result.get("chunks", [])]
        gap_hint = _knowledge_gap_hint(
            user_question,
            [
                "\n".join(
                    [
                        str(chunk.get("document_title") or ""),
                        str(chunk.get("section_path") or ""),
                        str(chunk.get("context_header") or ""),
                        str(chunk.get("content") or ""),
                    ]
                )
                for chunk in result.get("chunks", [])
            ],
        )
        if gap_hint:
            result["knowledge_gap"] = gap_hint
        context.knowledge_store.record_retrieval_event(
            workspace_id=context.workspace_id,
            session_id=context.session_id,
            user_id=context.user_id,
            query=user_question or str(args.get("document_id") or ",".join(chunk_ids if isinstance(chunk_ids, list) else [])),
            tool_name="knowledge_read",
            result_refs=refs,
            metadata={"count": result.get("count"), "knowledge_gap": gap_hint},
        )
        return result

    async def wiki_search(context: ToolContext, args: Dict[str, Any]) -> Dict[str, Any]:
        query = str(args.get("query") or "").strip()
        pages = context.knowledge_store.search_wiki(
            workspace_id=context.workspace_id,
            query=query,
            limit=max(1, min(int(args.get("limit") or 8), 20)),
            page_type=str(args.get("page_type") or ""),
        )
        return {
            "query": query,
            "count": len(pages),
            "pages": [
                {
                    "slug": page.slug,
                    "title": page.title,
                    "page_type": page.page_type,
                    "summary": page.summary,
                    "links": page.links,
                    "chunk_ref_count": len(page.chunk_refs),
                    "updated_at": page.updated_at,
                }
                for page in pages
            ],
            "next_step": "Call wiki_read for relevant slugs. Use knowledge_read on chunk_refs when exact source evidence is needed.",
        }

    async def wiki_read(context: ToolContext, args: Dict[str, Any]) -> Dict[str, Any]:
        slugs = args.get("slugs")
        if isinstance(slugs, str):
            slugs = [slugs]
        return context.knowledge_store.read_wiki(
            workspace_id=context.workspace_id,
            slugs=slugs if isinstance(slugs, list) else [],
            include_linked=bool(args.get("include_linked", True)),
        )

    async def wiki_write(context: ToolContext, args: Dict[str, Any]) -> Dict[str, Any]:
        source_refs = args.get("source_refs")
        chunk_refs = args.get("chunk_refs")
        aliases = args.get("aliases")
        metadata = args.get("metadata")
        page = context.knowledge_store.upsert_wiki_page(
            workspace_id=context.workspace_id,
            slug=str(args.get("slug") or args.get("title") or ""),
            title=str(args.get("title") or ""),
            page_type=str(args.get("page_type") or "concept"),
            summary=str(args.get("summary") or ""),
            content=str(args.get("content") or ""),
            aliases=aliases if isinstance(aliases, list) else [],
            status=str(args.get("status") or "active"),
            source_refs=source_refs if isinstance(source_refs, list) else [],
            chunk_refs=chunk_refs if isinstance(chunk_refs, list) else [],
            metadata=metadata if isinstance(metadata, dict) else {},
        )
        return {
            "saved": True,
            "page": context.knowledge_store.wiki_to_dict(page),
            "guidance": "Wiki page saved. For corrections, preserve source_refs/chunk_refs and log unresolved uncertainty with wiki_issue.",
        }

    async def wiki_issue(context: ToolContext, args: Dict[str, Any]) -> Dict[str, Any]:
        action = str(args.get("action") or "create").strip()
        if action == "list":
            issues = context.knowledge_store.list_wiki_issues(
                workspace_id=context.workspace_id,
                slug=str(args.get("slug") or ""),
                status=str(args.get("status") or "pending"),
                limit=int(args.get("limit") or 20),
            )
            return {"count": len(issues), "issues": [context.knowledge_store.issue_to_dict(issue) for issue in issues]}
        if action == "update":
            issue = context.knowledge_store.update_wiki_issue(
                workspace_id=context.workspace_id,
                issue_id=str(args.get("issue_id") or ""),
                status=str(args.get("status") or "pending"),
                metadata=args.get("metadata") if isinstance(args.get("metadata"), dict) else {},
            )
            if not issue:
                return {"updated": False, "error": "issue not found"}
            return {"updated": True, "issue": context.knowledge_store.issue_to_dict(issue)}
        issue = context.knowledge_store.create_wiki_issue(
            workspace_id=context.workspace_id,
            slug=str(args.get("slug") or ""),
            issue_type=str(args.get("issue_type") or "other"),
            description=str(args.get("description") or ""),
            evidence=str(args.get("evidence") or ""),
            created_by=context.user_id,
            metadata=args.get("metadata") if isinstance(args.get("metadata"), dict) else {},
        )
        return {
            "created": True,
            "issue": context.knowledge_store.issue_to_dict(issue),
            "next_step": "Use knowledge_search/knowledge_read to verify the correction, then wiki_write to update the page if supported.",
        }

    registry.register(
        ToolSpec(
            name="crt_cost_dataset_catalog",
            toolset="dashboard",
            description=(
                "Return the governed CRT Cost dashboard catalog: deal-level fields, approved starter metrics, "
                "filter operators, chart types, and clean-source/sandbox boundaries. Use before creating a CRT Cost "
                "dashboard or answering implementation questions about dashboardable fields."
            ),
            parameters={
                "type": "object",
                "properties": {},
                "required": [],
                "additionalProperties": False,
            },
            handler=crt_cost_dataset_catalog,
        )
    )
    registry.register(
        ToolSpec(
            name="crt_cost_dashboard_spec",
            toolset="dashboard",
            description=(
                "Create and pin a CRT Cost dashboard/chart specification from the user's request. This records the "
                "requested metrics, grouping, filters, assumptions, and needed follow-up questions without modifying "
                "source data. Use when the user asks to build, customize, visualize, chart, filter, or pin a CRT Cost dashboard."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "title": {"type": "string", "description": "Short dashboard or chart title."},
                    "user_request": {"type": "string", "description": "Original user intent in plain language."},
                    "chart_type": {
                        "type": "string",
                        "enum": ["table", "metric_cards", "bar", "line", "stacked_bar"],
                        "default": "table",
                    },
                    "metrics": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Approved metric names such as sum_crt_cost, sum_upb, crt_cost_bps.",
                    },
                    "group_by": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Approved field names such as settle_year, deal_type, payoff_date.",
                    },
                    "filters": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "field": {"type": "string"},
                                "operator": {"type": "string"},
                                "value": {"description": "Filter value; string, number, boolean, array, or null."},
                            },
                            "required": ["field", "operator"],
                            "additionalProperties": True,
                        },
                    },
                    "assumptions": {"type": "array", "items": {"type": "string"}},
                    "clarification_questions": {"type": "array", "items": {"type": "string"}},
                    "notes": {"type": "string"},
                    "pin": {"type": "boolean", "default": True},
                },
                "required": ["title", "user_request"],
                "additionalProperties": False,
            },
            handler=crt_cost_dashboard_spec,
        )
    )

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
                    "memory_target": {"type": "string", "enum": ["memory"]},
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
    registry.register(
        ToolSpec(
            name="knowledge_ingest",
            toolset="knowledge",
            description=(
                "Ingest a source document into the WeKnora-style knowledge memory. Use when the user asks to digest, index, "
                "remember, or add a document/wiki/source file. Prefer source_path for workspace files; use content when the "
                "document text is already present in the current message or attachment. The tool creates parent/child chunks "
                "with source metadata for later citation-backed retrieval."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "title": {"type": "string", "description": "Human-readable source title."},
                    "content": {"type": "string", "description": "Extracted document text/Markdown when available in context."},
                    "source_path": {"type": "string", "description": "Optional file path under WORKSPACE_AGENT_ROOT to parse and ingest."},
                    "source_uri": {"type": "string", "description": "Stable source identifier such as file path, wiki URL, or document URL."},
                    "source_type": {"type": "string", "enum": ["manual", "attachment", "workspace_path", "wiki", "url", "other"]},
                    "knowledge_base": {"type": "string", "default": DEFAULT_KNOWLEDGE_BASE},
                    "process": {"type": "string", "description": "Process/model family, e.g. EVA, Macs, Model Governance."},
                    "doc_type": {"type": "string", "description": "Document type, e.g. user_guide, methodology, model_review, runbook, script."},
                    "tags": {"type": "array", "items": {"type": "string"}},
                    "metadata": {"type": "object"},
                    "summary": {"type": "string"},
                    "file_name": {"type": "string"},
                    "file_extension": {"type": "string"},
                    "chunk_strategy": {"type": "string", "enum": ["auto", "heading", "heuristic", "legacy", "recursive"], "default": "auto"},
                },
                "required": ["title"],
                "additionalProperties": False,
            },
            handler=knowledge_ingest,
        )
    )
    registry.register(
        ToolSpec(
            name="knowledge_search",
            toolset="knowledge",
            description=(
                "Search source-document chunks for semantic or broad factual evidence. Use for EVA/Macs/process questions "
                "when the answer should be grounded in ingested documents. This returns candidate snippets only; call "
                "knowledge_read on the selected chunk_ids before answering. For definition questions, inspect knowledge_gap "
                "and avoid circular answers when sources only mention/list the term."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 20},
                    "knowledge_base": {"type": "string"},
                    "process": {"type": "string"},
                    "doc_type": {"type": "string"},
                },
                "required": ["query"],
                "additionalProperties": False,
            },
            handler=knowledge_search,
        )
    )
    registry.register(
        ToolSpec(
            name="knowledge_grep",
            toolset="knowledge",
            description=(
                "Exact keyword/regex retrieval over ingested source chunks. Use for script names, metric names, field names, "
                "control IDs, model names, dates, or quoted terms. This is an index-style search; call knowledge_read before "
                "answering from the result."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "pattern": {"type": "string"},
                    "case_sensitive": {"type": "boolean", "default": False},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 20},
                    "knowledge_base": {"type": "string"},
                    "process": {"type": "string"},
                    "doc_type": {"type": "string"},
                },
                "required": ["pattern"],
                "additionalProperties": False,
            },
            handler=knowledge_grep,
        )
    )
    registry.register(
        ToolSpec(
            name="knowledge_read",
            toolset="knowledge",
            description=(
                "Deep-read full source context after knowledge_search or knowledge_grep. Fetches selected chunks plus optional "
                "parent and neighboring chunks so answers can cite the document, section, source, and chunk. Use this before "
                "final factual answers about EVA/Macs/process documentation. Pass user_question for definition questions so "
                "the lightweight knowledge-gap detector can warn when source evidence only mentions the term."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "chunk_ids": {"type": "array", "items": {"type": "string"}},
                    "document_id": {"type": "string"},
                    "chunk_indexes": {"type": "array", "items": {"type": "integer"}},
                    "include_parent": {"type": "boolean", "default": True},
                    "include_neighbors": {"type": "boolean", "default": True},
                    "max_chars": {"type": "integer", "minimum": 1000, "maximum": 120000},
                    "user_question": {
                        "type": "string",
                        "description": "Original user question. Use for answerability checks, especially what-is/define/explain questions.",
                    },
                },
                "required": [],
                "additionalProperties": False,
            },
            handler=knowledge_read,
        )
    )
    registry.register(
        ToolSpec(
            name="wiki_search",
            toolset="wiki",
            description=(
                "Search curated wiki pages that synthesize process knowledge. Use first for conceptual questions, process maps, "
                "relationships, and known summaries. Then call wiki_read for the relevant pages."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "page_type": {"type": "string"},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 20},
                },
                "required": ["query"],
                "additionalProperties": False,
            },
            handler=wiki_search,
        )
    )
    registry.register(
        ToolSpec(
            name="wiki_read",
            toolset="wiki",
            description=(
                "Read full curated wiki pages by slug, including linked-page summaries. Use for high-level EVA/Macs answers; "
                "if the page has chunk_refs and exact evidence is needed, call knowledge_read next."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "slugs": {"type": "array", "items": {"type": "string"}},
                    "include_linked": {"type": "boolean", "default": True},
                },
                "required": ["slugs"],
                "additionalProperties": False,
            },
            handler=wiki_read,
        )
    )
    registry.register(
        ToolSpec(
            name="wiki_write",
            toolset="wiki",
            description=(
                "Create or update a curated wiki page. Use after reading source chunks or when the user explicitly supplies a "
                "correction. Preserve source_refs/chunk_refs so the wiki remains auditable and linked across processes."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "slug": {"type": "string"},
                    "title": {"type": "string"},
                    "page_type": {
                        "type": "string",
                        "enum": ["process", "model", "metric", "script", "dataset", "control", "runbook", "concept", "index", "issue_log"],
                    },
                    "summary": {"type": "string"},
                    "content": {"type": "string", "description": "Markdown. Use [[slug|label]] links to connect pages."},
                    "aliases": {"type": "array", "items": {"type": "string"}},
                    "status": {"type": "string", "enum": ["draft", "active", "needs_review"], "default": "active"},
                    "source_refs": {"type": "array", "items": {"type": "object"}},
                    "chunk_refs": {"type": "array", "items": {"type": "object"}},
                    "metadata": {"type": "object"},
                },
                "required": ["title", "summary", "content"],
                "additionalProperties": False,
            },
            handler=wiki_write,
        )
    )
    registry.register(
        ToolSpec(
            name="wiki_issue",
            toolset="wiki",
            description=(
                "Create, list, or update wiki correction issues. Use when a user says the agent/wiki is wrong, missing, "
                "contradictory, stale, or mixed across entities. Do not silently overwrite governed process knowledge."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "action": {"type": "string", "enum": ["create", "list", "update"], "default": "create"},
                    "issue_id": {"type": "string"},
                    "slug": {"type": "string"},
                    "issue_type": {
                        "type": "string",
                        "enum": ["wrong_fact", "missing_info", "contradiction", "out_of_date", "mixed_entities", "other"],
                    },
                    "description": {"type": "string"},
                    "evidence": {"type": "string"},
                    "status": {"type": "string", "enum": ["pending", "resolved", "rejected", "deferred"]},
                    "metadata": {"type": "object"},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 100},
                },
                "required": ["action"],
                "additionalProperties": False,
            },
            handler=wiki_issue,
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
