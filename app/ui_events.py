from __future__ import annotations

import re
from typing import Any, Dict, List


def ui_events_for_turn(
    *,
    query_text: str,
    tool_names: List[str],
    attachments: List[Dict[str, Any]],
    status: str,
) -> List[Dict[str, Any]]:
    if status not in {"success", "model_error", "auth_error", "error"}:
        return []
    section = knowledge_drawer_section(query_text)
    if not section:
        return []
    return [
        {
            "type": "open_drawer",
            "view": "knowledge",
            "section": section,
            "reason": "User asked for knowledge-base/source inventory.",
            "source": "backend_ui_event_router",
            "tool_names": tool_names,
            "attachment_count": len(attachments),
        }
    ]


def knowledge_drawer_section(query_text: str) -> str:
    query = " ".join((query_text or "").lower().split())
    if not query:
        return ""
    source_terms = (
        "knowledge source",
        "knowledge sources",
        "knowledge base",
        "knowledge inventory",
        "documentation indexed",
        "indexed documentation",
        "indexed documents",
        "source documents",
        "uploaded documents",
        "document library",
        "documentation folder",
        "my source",
        "my sources",
    )
    wiki_terms = ("wiki pages", "wiki page", "glossary", "curated wiki")
    correction_terms = ("pending correction", "pending corrections", "wiki issue", "wiki issues", "correction issue")
    has_inventory_verb = bool(
        re.search(r"\b(show|open|list|view|check|see|browse|display|what|which|where|how many)\b", query)
    )
    if has_inventory_verb and any(term in query for term in correction_terms):
        return "pending_corrections"
    if has_inventory_verb and any(term in query for term in wiki_terms):
        return "wiki_pages"
    if has_inventory_verb and any(term in query for term in source_terms):
        return "documents"
    if has_inventory_verb and re.search(r"\b(what|which)\s+sources?\s+do\s+you\s+have\b", query):
        return "documents"
    if re.search(r"\bwhat\s+is\s+my\s+knowledge\s+source\b", query):
        return "documents"
    return ""
