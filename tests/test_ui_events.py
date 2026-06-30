from __future__ import annotations

import sys
import types
import unittest

dotenv = types.ModuleType("dotenv")
dotenv.load_dotenv = lambda *args, **kwargs: None
sys.modules.setdefault("dotenv", dotenv)

from app.runtime_hooks import RuntimeHookContext, build_default_hook_registry


class RuntimeHookRoutingTests(unittest.TestCase):
    def run_hooks(self, *, query_text: str = "", tool_names: list[str], status: str = "success") -> list[dict]:
        registry = build_default_hook_registry()
        return registry.run(
            "turn_completed",
            RuntimeHookContext(
                event="turn_completed",
                workspace_id="workspace",
                user_id="user",
                session_id="session",
                request_id="request",
                status=status,
                query_text=query_text,
                tool_names=tool_names,
                attachments=[],
            ),
        )

    def test_knowledge_tool_opens_documents_drawer(self) -> None:
        events = self.run_hooks(
            query_text="what is my knowledge source",
            tool_names=["knowledge_search", "knowledge_read"],
        )

        self.assertEqual(events[0]["type"], "open_drawer")
        self.assertEqual(events[0]["view"], "knowledge")
        self.assertEqual(events[0]["section"], "documents")
        self.assertEqual(events[0]["source"], "runtime_hook:knowledge_drawer_on_tool_use")

    def test_query_without_knowledge_tool_does_not_open_drawer(self) -> None:
        events = self.run_hooks(
            query_text="what is my source",
            tool_names=[],
        )

        self.assertEqual(events, [])

    def test_wiki_issue_tool_opens_pending_corrections(self) -> None:
        events = self.run_hooks(
            query_text="show me pending wiki issues",
            tool_names=["knowledge_search", "wiki_issue"],
        )

        self.assertEqual(events[0]["view"], "knowledge")
        self.assertEqual(events[0]["section"], "pending_corrections")

    def test_unrelated_tool_does_not_open_drawer(self) -> None:
        events = self.run_hooks(
            query_text="how are you agent",
            tool_names=["session_search"],
        )

        self.assertEqual(events, [])


if __name__ == "__main__":
    unittest.main()
