from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional

import httpx

from app.attachment_extractors import extract_attachment
from app.config import AppConfig, workspace_root
from app.fredai_auth import FredAIAuthError
from app.fredai_client import ChatCompletionResult, FredAIClient, FredAIClientError
from app.knowledge_store import KnowledgeStore
from app.memory_manager import AgentMemoryManager, render_memory_context_block
from app.memory_store import MemoryStore
from app.scheduler import ScheduledJob
from app.session_store import SessionStore, utc_now
from app.tools import ToolContext, ToolRegistry, build_core_tool_registry


logger = logging.getLogger(__name__)


@dataclass
class FunctionCall:
    name: str
    arguments: Dict[str, Any]
    call_id: str
    raw_item: Dict[str, Any]


@dataclass
class AgentLoopResult:
    answer: str
    tool_names: List[str]
    progress_messages: List[str]


@dataclass
class AgentResponse:
    answer: str
    request_id: str
    session_id: str
    tool_names: List[str]
    duration_ms: int
    status: str
    progress_messages: List[str]
    error: str = ""
    user_message_id: Optional[int] = None
    assistant_message_id: Optional[int] = None

    def as_dict(self) -> Dict[str, Any]:
        payload = {
            "answer": self.answer,
            "request_id": self.request_id,
            "session_id": self.session_id,
            "tool_names": self.tool_names,
            "duration_ms": self.duration_ms,
            "status": self.status,
            "progress_messages": self.progress_messages,
            "user_message_id": self.user_message_id,
            "assistant_message_id": self.assistant_message_id,
        }
        if self.error:
            payload["error"] = self.error
        return payload


class WorkspaceAgentOrchestrator:
    """Runtime owner for sessions, memory, tools, FredAI calls, traces, and scheduled jobs."""

    def __init__(
        self,
        config: AppConfig,
        *,
        session_store: Optional[SessionStore] = None,
        memory_store: Optional[MemoryStore] = None,
        tool_registry: Optional[ToolRegistry] = None,
        fredai_client: Optional[FredAIClient] = None,
    ):
        self._config = config
        self.session_store = session_store or SessionStore()
        self.memory_store = memory_store or MemoryStore()
        self.memory_manager = AgentMemoryManager(config, self.memory_store)
        self.knowledge_store = KnowledgeStore(self.memory_store.db_path)
        self.tool_registry = tool_registry or build_core_tool_registry(
            session_store=self.session_store,
            memory_manager=self.memory_manager,
            knowledge_store=self.knowledge_store,
            config=config,
        )
        self._client = fredai_client or FredAIClient(config)

    async def respond(
        self,
        *,
        workspace_id: str,
        user_id: str,
        message: str,
        session_id: Optional[str] = None,
        attachments: Optional[List[Dict[str, Any]]] = None,
    ) -> AgentResponse:
        request_id = f"req_{uuid.uuid4().hex}"
        started_at = utc_now()
        timer_start = time.perf_counter()
        status = "success"
        error_text = ""
        tool_names: List[str] = []
        progress_messages: List[str] = []
        user_message_id: Optional[int] = None
        assistant_message_id: Optional[int] = None

        session = self.session_store.get_or_create_session(
            workspace_id=workspace_id,
            user_id=user_id,
            session_id=session_id,
            title=self._session_title(message),
            metadata={"source": "internal_api"},
        )
        trace = self._new_trace_recorder(
            request_id=request_id,
            session_id=session.id,
            workspace_id=workspace_id,
            user_id=user_id,
        )
        recent_messages = self.session_store.recent_model_messages(
            session.id,
            limit=self._config.session_context_messages,
        )
        trace(
            "request_start",
            "Request received",
            {
                "request_id": request_id,
                "workspace_id": workspace_id,
                "user_id": user_id,
                "session_id": session.id,
                "message": message,
                "attachments": attachments or [],
                "recent_context_message_count": len(recent_messages),
            },
        )

        content = self._build_user_content(message, attachments or [])
        user_record = self.session_store.append_message(
            session_id=session.id,
            role="user",
            content=content,
            metadata=self._user_display_metadata(message, attachments or []),
        )
        user_message_id = user_record.id
        trace(
            "stored_user_message",
            "User message stored",
            {"message_id": user_message_id, "stored_content": content},
        )

        input_messages = recent_messages + [{"role": "user", "content": content}]
        answer = ""
        try:
            loop_result = await self._run_agent_loop(
                session_id=session.id,
                workspace_id=workspace_id,
                user_id=user_id,
                input_messages=input_messages,
                query_text=message,
                request_id=request_id,
                trace=trace,
            )
            answer = loop_result.answer
            tool_names = loop_result.tool_names
            progress_messages = loop_result.progress_messages
        except FredAIAuthError as exc:
            status = "auth_error"
            error_text = str(exc)
            answer = f"FredAI authentication is not configured or failed: {exc}"
        except FredAIClientError as exc:
            status = "model_error"
            error_text = str(exc)
            answer = f"FredAI request failed: {exc}"
        except Exception as exc:
            status = "error"
            error_text = f"{type(exc).__name__}: {exc}"
            answer = "The workspace agent hit an internal error while processing this request."
            logger.exception("Workspace agent request failed: %s", exc)

        answer = (answer or "").strip() or "I received your message, but I could not generate a clear reply."
        finished_at = utc_now()
        duration_ms = int((time.perf_counter() - timer_start) * 1000)

        assistant_record = self.session_store.append_message(
            session_id=session.id,
            role="assistant",
            content=answer,
            metadata={
                "request_duration_ms": duration_ms,
                "tool_names": tool_names,
                "progress_messages": progress_messages,
                "status": status,
            },
        )
        assistant_message_id = assistant_record.id
        trace(
            "final_answer",
            "Final answer",
            {
                "request_id": request_id,
                "answer": answer,
                "duration_ms": duration_ms,
                "status": status,
                "tool_names": tool_names,
                "progress_messages": progress_messages,
                "assistant_message_id": assistant_message_id,
            },
        )
        if error_text:
            trace(
                "request_error",
                "Request completed with error status",
                {"request_id": request_id, "status": status, "error": error_text},
            )
        self.session_store.record_request_metric(
            request_id=request_id,
            session_id=session.id,
            workspace_id=workspace_id,
            user_id=user_id,
            user_message_id=user_message_id,
            assistant_message_id=assistant_message_id,
            started_at=started_at,
            finished_at=finished_at,
            duration_ms=duration_ms,
            status=status,
            tool_names=tool_names,
            progress_messages=progress_messages,
            error=error_text,
        )
        if status == "success":
            self.memory_manager.sync_all(message, answer, session_id=session.id, workspace_id=workspace_id, user_id=user_id)
            self.memory_manager.queue_prefetch_all(message, session_id=session.id, workspace_id=workspace_id, user_id=user_id)

        return AgentResponse(
            answer=answer,
            request_id=request_id,
            session_id=session.id,
            tool_names=tool_names,
            duration_ms=duration_ms,
            status=status,
            progress_messages=progress_messages,
            error=error_text,
            user_message_id=user_message_id,
            assistant_message_id=assistant_message_id,
        )

    async def run_scheduled_job(self, job: ScheduledJob) -> str:
        session_id = job.session_id or f"cron_{job.id}"
        prompt = f"[Scheduled job: {job.name}]\n{job.prompt}"
        result = await self.respond(
            workspace_id=job.workspace_id or "scheduler",
            user_id=job.user_id or "scheduler",
            session_id=session_id,
            message=prompt,
            attachments=[],
        )
        if job.deliver_result and self._config.delivery_url:
            await self._deliver_scheduled_result(job, result)
        return result.answer

    async def _run_agent_loop(
        self,
        *,
        session_id: str,
        workspace_id: str,
        user_id: str,
        input_messages: List[Dict[str, Any]],
        query_text: str,
        request_id: str,
        trace: Callable[[str, str, Any], None],
    ) -> AgentLoopResult:
        summarize_session_search = None
        if self._config.session_search_aux_enabled:

            async def summarize_session_search(query: str, sessions: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
                return await self._summarize_session_search(query, sessions, trace=trace)

        context = ToolContext(
            session_id=session_id,
            workspace_id=workspace_id,
            user_id=user_id,
            config=self._config,
            session_store=self.session_store,
            memory_manager=self.memory_manager,
            knowledge_store=self.knowledge_store,
            summarize_session_search=summarize_session_search,
        )
        instructions = self._build_instructions(workspace_id=workspace_id, user_id=user_id)
        tools = self.tool_registry.definitions()
        prefetch_context = self.memory_manager.prefetch_all(
            query_text,
            session_id=session_id,
            workspace_id=workspace_id,
            user_id=user_id,
        )
        knowledge_context = self._knowledge_prefetch_context(query_text, workspace_id=workspace_id)
        combined_prefetch_context = "\n\n".join(part for part in [prefetch_context, knowledge_context] if part.strip())
        working_messages = self._inject_prefetch_context(input_messages, combined_prefetch_context)
        tool_names: List[str] = []
        progress_messages: List[str] = []

        trace(
            "instructions",
            "System prompt and curated memory",
            {
                "instructions": instructions,
                "tool_count": len(tools),
                "tool_names": [str(tool.get("function", {}).get("name") or "") for tool in tools],
            },
        )
        trace(
            "prefetch",
            "Automatic memory prefetch",
            {
                "query_text": query_text,
                "prefetch_context": prefetch_context,
                "knowledge_context": knowledge_context,
                "injected": bool(combined_prefetch_context.strip()),
            },
        )
        trace("tool_options", "Tool schemas passed to model", {"tools": tools})

        for iteration in range(self._config.max_agent_iterations):
            request_messages = [{"role": "system", "content": instructions}] + working_messages
            request_payload = self._chat_payload(request_messages, tools)
            trace(
                "model_request",
                f"FredAI request iteration {iteration + 1}",
                {"iteration": iteration + 1, "payload": self._trace_safe_payload(request_payload)},
            )
            response = await asyncio.to_thread(self._create_chat_completion_sync, request_messages, tools)
            trace(
                "model_response",
                f"FredAI response iteration {iteration + 1}",
                {
                    "iteration": iteration + 1,
                    "event_count": response.event_count,
                    "message": self._trace_safe_payload(response.message),
                    "raw_response": self._trace_safe_payload(response.raw_response),
                },
            )
            function_calls = self._extract_function_calls(response.message)
            if not function_calls:
                return AgentLoopResult(
                    answer=self._extract_message_text(response.message),
                    tool_names=tool_names,
                    progress_messages=progress_messages,
                )

            assistant_message = {
                "role": "assistant",
                "content": response.message.get("content") or "",
                "tool_calls": [call.raw_item for call in function_calls],
            }
            working_messages.append(assistant_message)

            for call in function_calls:
                tool_names.append(call.name)
                trace(
                    "tool_call",
                    f"Tool call: {call.name}",
                    {
                        "iteration": iteration + 1,
                        "name": call.name,
                        "arguments": call.arguments,
                        "call_id": call.call_id,
                        "raw_item": self._trace_safe_payload(call.raw_item),
                    },
                )
                progress_message = self._progress_message_for_tool(call.name)
                if progress_message:
                    progress_messages.append(progress_message)
                self.session_store.append_message(
                    session_id=session_id,
                    role="tool_call",
                    content={"name": call.name, "arguments": call.arguments, "call_id": call.call_id},
                    name=call.name,
                    tool_call_id=call.call_id,
                    metadata={"iteration": iteration + 1},
                )
                result = await self.tool_registry.execute(name=call.name, arguments=call.arguments, context=context)
                trace(
                    "tool_result",
                    f"Tool result: {call.name}",
                    {
                        "iteration": iteration + 1,
                        "name": call.name,
                        "call_id": call.call_id,
                        "result": self._trace_safe_payload(result),
                    },
                )
                self.session_store.append_message(
                    session_id=session_id,
                    role="tool_result",
                    content=result,
                    name=call.name,
                    tool_call_id=call.call_id,
                    metadata={"iteration": iteration + 1},
                )
                working_messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": call.call_id,
                        "content": json.dumps(result, ensure_ascii=False),
                    }
                )

        return AgentLoopResult(
            answer=(
                "I saved the conversation context, but reached the current tool-loop limit before "
                "finishing a clean response. Please send one more message and I will continue."
            ),
            tool_names=tool_names,
            progress_messages=progress_messages,
        )

    def _create_chat_completion_sync(self, messages: List[Dict[str, Any]], tools: List[Dict[str, Any]]) -> ChatCompletionResult:
        return self._client.create_chat_completion(messages=messages, tools=tools, max_tokens=4096)

    async def _summarize_session_search(
        self,
        query: str,
        sessions: List[Dict[str, Any]],
        *,
        trace: Callable[[str, str, Any], None],
    ) -> List[Dict[str, Any]]:
        summarized: List[Dict[str, Any]] = []
        for index, session in enumerate(sessions, start=1):
            summary = await asyncio.to_thread(self._summarize_one_session_sync, query, session, trace, index)
            item = {
                "session_id": session.get("session_id"),
                "workspace_id": session.get("workspace_id"),
                "user_id": session.get("user_id"),
                "title": session.get("title"),
                "matches": session.get("matches", [])[:5],
                "summary": summary,
            }
            summarized.append(item)
        return summarized

    def _summarize_one_session_sync(
        self,
        query: str,
        session: Dict[str, Any],
        trace: Callable[[str, str, Any], None],
        index: int,
    ) -> str:
        conversation = session.get("conversation") or []
        transcript_parts = []
        for message in conversation[-120:]:
            role = str(message.get("role") or "unknown").upper()
            content = str(message.get("content") or "")
            if content:
                transcript_parts.append(f"[{role}] {content}")
        transcript = "\n\n".join(transcript_parts)
        matches = json.dumps(session.get("matches", [])[:8], ensure_ascii=False)
        messages = [
            {
                "role": "system",
                "content": (
                    "You summarize retrieved workspace conversation history for the main agent. "
                    "Focus only on the search topic. Preserve concrete facts, names, dates, "
                    "decisions, and unresolved questions. Do not add unsupported facts."
                ),
            },
            {
                "role": "user",
                "content": (
                    f"Search topic: {query}\n\n"
                    f"Matched snippets:\n{matches}\n\n"
                    f"Conversation transcript:\n{transcript[:80000]}\n\n"
                    "Return a concise but specific summary."
                ),
            },
        ]
        payload = self._chat_payload(messages, [])
        trace(
            "model_request",
            f"Auxiliary session-search summary request {index}",
            {"auxiliary": True, "purpose": "session_search_summary", "payload": self._trace_safe_payload(payload)},
        )
        response = self._client.create_chat_completion(messages=messages, tools=[], stream=False, max_tokens=1200)
        trace(
            "model_response",
            f"Auxiliary session-search summary response {index}",
            {
                "auxiliary": True,
                "purpose": "session_search_summary",
                "message": self._trace_safe_payload(response.message),
            },
        )
        return self._extract_message_text(response.message) or "[No auxiliary summary returned.]"

    def _build_instructions(self, *, workspace_id: str, user_id: str) -> str:
        parts = [self._config.system_prompt.strip()]
        self.memory_manager.reload_curated_memory()
        curated_memory = self.memory_manager.build_system_prompt()
        if curated_memory:
            parts.append(f"Curated persistent memory loaded at session start:\n{curated_memory}")
        parts.append(
            f"""
Runtime architecture:
- Requests arrive through the internal API for workspace_id={workspace_id} and user_id={user_id}.
- Recent session messages are included directly as short-term context.
- Curated memory is always present in these instructions.
- Automatic memory prefetch may be attached to the latest user message in a <memory-context> block.
- Use memory for stable always-on facts and preferences.
- Use workspace_note_save/search for durable workspace notes that are useful but too large or specific for curated memory.
- Use session_search when older conversation details are needed beyond the recent context window.
- Use routine_rule for future behavior, standing preferences, scheduled work, reusable workflows, or missing tool requests.
- Use workspace file tools only for files under WORKSPACE_AGENT_ROOT.
- Use knowledge_ingest when the user asks to digest, index, add, or remember an attached/source document.
- Use wiki_search/wiki_read first for conceptual process questions when curated wiki pages exist.
- Use knowledge_search for broad source-document retrieval and knowledge_grep for exact terms, script names, metrics, field names, or IDs.
- After knowledge_search or knowledge_grep, call knowledge_read before giving factual answers from source documents.
- Cite document titles, source paths, sections, and chunk indexes in user-facing answers when source memory is used.
- Use wiki_write only after reading source evidence or when the user explicitly supplies a correction; keep source_refs/chunk_refs.
- Use wiki_issue when a user reports wrong, missing, contradictory, or stale wiki/process knowledge.
- Do not claim storage or scheduling succeeded unless a tool result confirms it.
- Do not expose tool JSON unless the user asks for implementation-level details.
""".strip()
        )
        return "\n\n".join(parts)

    def _knowledge_prefetch_context(self, query: str, *, workspace_id: str) -> str:
        clean_query = " ".join(query.split())[:240]
        if not self._config.knowledge_prefetch_enabled or len(clean_query) < 3:
            return ""
        try:
            if not self.knowledge_store.has_retrievable_knowledge(workspace_id=workspace_id):
                return ""
            wiki_pages = self.knowledge_store.search_wiki(workspace_id=workspace_id, query=clean_query, limit=2)
            chunk_results = self.knowledge_store.search_chunks(workspace_id=workspace_id, query=clean_query, limit=2)
        except Exception as exc:
            logger.debug("Knowledge prefetch skipped: %s", exc)
            return ""
        if not wiki_pages and not chunk_results:
            return ""
        lines = [
            "[knowledge_memory]",
            "Relevant knowledge candidates were found. Use wiki_read or knowledge_read before relying on them for factual answers.",
        ]
        if wiki_pages:
            lines.append("Candidate wiki pages:")
            for page in wiki_pages:
                lines.append(f"- [[{page.slug}|{page.title}]] ({page.page_type}): {page.summary[:300]}")
        if chunk_results:
            lines.append("Candidate source chunks:")
            for item in chunk_results:
                chunk = item.chunk
                lines.append(
                    f"- chunk_id={chunk.id} document={chunk.document_title} "
                    f"section={chunk.section_path or '-'} snippet={item.snippet[:300]}"
                )
        return "\n".join(lines)

    @staticmethod
    def _inject_prefetch_context(input_messages: List[Dict[str, Any]], prefetch_context: str) -> List[Dict[str, Any]]:
        working_messages: List[Dict[str, Any]] = [dict(message) for message in input_messages]
        block = render_memory_context_block(prefetch_context)
        if not block or not working_messages:
            return working_messages
        for index in range(len(working_messages) - 1, -1, -1):
            message = working_messages[index]
            if message.get("role") != "user":
                continue
            content = message.get("content")
            if isinstance(content, str):
                message["content"] = content + "\n\n" + block
            elif isinstance(content, list):
                updated = [dict(item) if isinstance(item, dict) else item for item in content]
                for part in updated:
                    if isinstance(part, dict) and part.get("type") == "text":
                        part["text"] = str(part.get("text") or "") + "\n\n" + block
                        message["content"] = updated
                        break
                else:
                    message["content"] = [{"type": "text", "text": block}, *updated]
            else:
                message["content"] = json.dumps(content, ensure_ascii=False) + "\n\n" + block
            break
        return working_messages

    def _build_user_content(self, message: str, attachments: List[Dict[str, Any]]) -> Any:
        parts = [message.strip() or "[Empty user message]"]
        media_parts: List[Dict[str, Any]] = []
        root = workspace_root()
        for index, attachment in enumerate(attachments, start=1):
            if not isinstance(attachment, dict):
                parts.append(f"[Attachment {index}: {attachment}]")
                continue
            extraction = extract_attachment(attachment, index=index, workspace_root=root)
            parts.append(extraction.render(index))
            media_parts.extend(extraction.media_parts)
        text = "\n\n".join(parts)
        if not media_parts:
            return text
        return [{"type": "text", "text": text}, *media_parts]

    def _user_display_metadata(self, message: str, attachments: List[Dict[str, Any]]) -> Dict[str, Any]:
        display_attachments: List[Dict[str, Any]] = []
        for index, attachment in enumerate(attachments, start=1):
            if not isinstance(attachment, dict):
                display_attachments.append(
                    {
                        "id": f"attachment_{index}",
                        "name": f"attachment_{index}",
                        "size": 0,
                        "kind": "file",
                        "extension": "",
                        "media_type": "",
                        "transfer": "metadata_only",
                    }
                )
                continue
            display_attachments.append(
                {
                    "id": str(attachment.get("id") or f"attachment_{index}"),
                    "name": str(attachment.get("name") or attachment.get("filename") or f"attachment_{index}"),
                    "size": self._safe_attachment_size(attachment.get("size")),
                    "kind": str(attachment.get("type") or attachment.get("kind") or "file"),
                    "extension": str(attachment.get("extension") or ""),
                    "media_type": str(
                        attachment.get("media_type")
                        or attachment.get("content_type")
                        or attachment.get("mime_type")
                        or ""
                    ),
                    "transfer": str(attachment.get("transfer") or "metadata_only"),
                }
            )
        return {
            "display_text": message.strip() or "Please analyze the attached file(s).",
            "attachments": display_attachments,
        }

    @staticmethod
    def _safe_attachment_size(value: Any) -> int:
        try:
            return max(0, int(value or 0))
        except (TypeError, ValueError):
            return 0

    def _chat_payload(self, messages: List[Dict[str, Any]], tools: List[Dict[str, Any]]) -> Dict[str, Any]:
        payload: Dict[str, Any] = {
            "model": self._config.model,
            "messages": messages,
            "temperature": 0.1,
            "max_tokens": 4096,
            "stream": self._config.fredai_stream,
        }
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"
        return payload

    def _new_trace_recorder(
        self,
        *,
        request_id: str,
        session_id: str,
        workspace_id: str,
        user_id: str,
    ) -> Callable[[str, str, Any], None]:
        counter = {"index": 0}

        def record(event_type: str, title: str, payload: Any) -> None:
            if not self._config.trace_enabled or not request_id:
                return
            counter["index"] += 1
            self.session_store.record_trace_event(
                request_id=request_id,
                session_id=session_id,
                workspace_id=workspace_id,
                user_id=user_id,
                event_index=counter["index"],
                event_type=event_type,
                title=title,
                payload=self._trace_safe_payload(payload),
            )

        return record

    def _trace_safe_payload(self, value: Any) -> Any:
        if isinstance(value, dict):
            safe: Dict[str, Any] = {}
            for key, item in value.items():
                lowered = str(key).lower()
                if lowered in {"authorization", "x-jwt-token", "client_secret", "password"}:
                    safe[str(key)] = "[secret omitted]"
                elif (
                    not self._config.trace_full_media
                    and lowered in {"image_url", "data_url", "url"}
                    and isinstance(item, str)
                    and item.startswith("data:")
                ):
                    safe[str(key)] = f"[data URL omitted; {len(item)} chars]"
                else:
                    safe[str(key)] = self._trace_safe_payload(item)
            return safe
        if isinstance(value, list):
            return [self._trace_safe_payload(item) for item in value]
        if hasattr(value, "model_dump"):
            try:
                return self._trace_safe_payload(value.model_dump(exclude_none=True))
            except Exception:
                return str(value)
        return value

    @staticmethod
    def _extract_function_calls(message: Dict[str, Any]) -> List[FunctionCall]:
        calls: List[FunctionCall] = []
        for raw_call in message.get("tool_calls", []) or []:
            if not isinstance(raw_call, dict):
                continue
            function = raw_call.get("function") or {}
            if not isinstance(function, dict):
                continue
            name = str(function.get("name") or "")
            call_id = str(raw_call.get("id") or "")
            raw_args = function.get("arguments") or "{}"
            try:
                args = json.loads(raw_args) if isinstance(raw_args, str) else dict(raw_args)
            except Exception:
                args = {}
            if name and call_id:
                calls.append(FunctionCall(name=name, arguments=args, call_id=call_id, raw_item=dict(raw_call)))
        return calls

    @staticmethod
    def _extract_message_text(message: Dict[str, Any]) -> str:
        content = message.get("content") or ""
        if isinstance(content, str):
            return content.strip()
        if isinstance(content, list):
            parts = []
            for item in content:
                if isinstance(item, dict) and item.get("text"):
                    parts.append(str(item["text"]))
                elif isinstance(item, str):
                    parts.append(item)
            return "".join(parts).strip()
        return str(content).strip()

    @staticmethod
    def _progress_message_for_tool(tool_name: str) -> str:
        messages = {
            "memory": "Saving that memory.",
            "workspace_note_save": "Saving a workspace note.",
            "workspace_note_search": "Checking workspace notes.",
            "session_search": "Checking earlier messages.",
            "routine_rule": "Saving that routine.",
            "workspace_read_file": "Reading a workspace file.",
            "workspace_list_files": "Listing workspace files.",
            "workspace_find_files": "Finding workspace files.",
            "knowledge_ingest": "Digesting the source into knowledge memory.",
            "knowledge_search": "Searching source knowledge.",
            "knowledge_grep": "Searching exact source terms.",
            "knowledge_read": "Reading source context.",
            "wiki_search": "Searching the process wiki.",
            "wiki_read": "Reading wiki pages.",
            "wiki_write": "Updating the process wiki.",
            "wiki_issue": "Logging a wiki knowledge issue.",
        }
        return messages.get(tool_name, "Using a workspace tool.")

    @staticmethod
    def _session_title(message: str) -> str:
        clean = " ".join((message or "").strip().split())
        return clean[:80]

    async def _deliver_scheduled_result(self, job: ScheduledJob, result: AgentResponse) -> None:
        payload = {
            "job_id": job.id,
            "job_name": job.name,
            "workspace_id": job.workspace_id,
            "user_id": job.user_id,
            "session_id": result.session_id,
            "request_id": result.request_id,
            "answer": result.answer,
            "tool_names": result.tool_names,
            "duration_ms": result.duration_ms,
        }
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                await client.post(self._config.delivery_url, json=payload)
        except Exception as exc:
            logger.warning("Scheduled result delivery failed for %s: %s", job.id, exc)
