from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Sequence


UiEvent = Dict[str, Any]
HookHandler = Callable[["RuntimeHookContext"], List[UiEvent]]


@dataclass(frozen=True)
class RuntimeHook:
    """A small in-process lifecycle hook."""

    name: str
    event: str
    handler: HookHandler


@dataclass(frozen=True)
class RuntimeHookContext:
    event: str
    workspace_id: str
    user_id: str
    session_id: str
    request_id: str
    status: str
    query_text: str = ""
    tool_names: Sequence[str] = field(default_factory=list)
    attachments: Sequence[Dict[str, Any]] = field(default_factory=list)


class RuntimeHookRegistry:
    """Ordered runtime hooks for non-model side effects such as UI events."""

    def __init__(self) -> None:
        self._hooks: List[RuntimeHook] = []

    def register(self, hook: RuntimeHook) -> None:
        if not hook.name.strip():
            raise ValueError("hook name is required")
        if not hook.event.strip():
            raise ValueError("hook event is required")
        self._hooks.append(hook)

    def run(self, event: str, context: RuntimeHookContext) -> List[UiEvent]:
        ui_events: List[UiEvent] = []
        for hook in self._hooks:
            if hook.event != event:
                continue
            ui_events.extend(hook.handler(context))
        return ui_events


KNOWLEDGE_DRAWER_SECTIONS = {
    "knowledge_ingest": "documents",
    "knowledge_search": "documents",
    "knowledge_grep": "documents",
    "knowledge_read": "documents",
    "wiki_search": "wiki_pages",
    "wiki_read": "wiki_pages",
    "wiki_write": "wiki_pages",
    "wiki_issue": "pending_corrections",
}

KNOWLEDGE_SECTION_PRIORITY = ("pending_corrections", "wiki_pages", "documents")

DASHBOARD_DRAWER_TOOLS = {
    "crt_cost_dataset_catalog",
    "crt_cost_dashboard_spec",
}


def build_default_hook_registry() -> RuntimeHookRegistry:
    registry = RuntimeHookRegistry()
    registry.register(
        RuntimeHook(
            name="knowledge_drawer_on_tool_use",
            event="turn_completed",
            handler=knowledge_drawer_on_tool_use,
        )
    )
    registry.register(
        RuntimeHook(
            name="dashboard_drawer_on_tool_use",
            event="turn_completed",
            handler=dashboard_drawer_on_tool_use,
        )
    )
    return registry


def knowledge_drawer_on_tool_use(context: RuntimeHookContext) -> List[UiEvent]:
    if context.status not in {"success", "model_error", "auth_error", "error"}:
        return []

    matched_tools = [name for name in context.tool_names if name in KNOWLEDGE_DRAWER_SECTIONS]
    if not matched_tools:
        return []

    matched_sections = {KNOWLEDGE_DRAWER_SECTIONS[name] for name in matched_tools}
    section = next((candidate for candidate in KNOWLEDGE_SECTION_PRIORITY if candidate in matched_sections), "documents")
    return [
        {
            "type": "open_drawer",
            "view": "knowledge",
            "section": section,
            "reason": f"Knowledge hook opened because these tools ran: {', '.join(matched_tools)}.",
            "source": "runtime_hook:knowledge_drawer_on_tool_use",
            "matched_tools": matched_tools,
            "tool_names": list(context.tool_names),
            "attachment_count": len(context.attachments),
        }
    ]


def dashboard_drawer_on_tool_use(context: RuntimeHookContext) -> List[UiEvent]:
    if context.status not in {"success", "model_error", "auth_error", "error"}:
        return []

    matched_tools = [name for name in context.tool_names if name in DASHBOARD_DRAWER_TOOLS]
    if not matched_tools:
        return []

    return [
        {
            "type": "open_drawer",
            "view": "dashboard",
            "section": "specs",
            "reason": f"Dashboard hook opened because these tools ran: {', '.join(matched_tools)}.",
            "source": "runtime_hook:dashboard_drawer_on_tool_use",
            "matched_tools": matched_tools,
            "tool_names": list(context.tool_names),
            "attachment_count": len(context.attachments),
        }
    ]
