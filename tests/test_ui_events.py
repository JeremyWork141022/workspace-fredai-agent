from __future__ import annotations

import sys
import types
import unittest

dotenv = types.ModuleType("dotenv")
dotenv.load_dotenv = lambda *args, **kwargs: None
sys.modules.setdefault("dotenv", dotenv)

from app.ui_events import ui_events_for_turn


class UiEventRoutingTests(unittest.TestCase):
    def test_knowledge_source_question_opens_documents_drawer(self) -> None:
        events = ui_events_for_turn(
            query_text="what is my knowledge source",
            tool_names=[],
            attachments=[],
            status="success",
        )

        self.assertEqual(events[0]["type"], "open_drawer")
        self.assertEqual(events[0]["view"], "knowledge")
        self.assertEqual(events[0]["section"], "documents")

    def test_short_source_question_opens_documents_drawer(self) -> None:
        events = ui_events_for_turn(
            query_text="what is my source",
            tool_names=[],
            attachments=[],
            status="success",
        )

        self.assertEqual(events[0]["view"], "knowledge")
        self.assertEqual(events[0]["section"], "documents")

    def test_wiki_issue_question_opens_pending_corrections(self) -> None:
        events = ui_events_for_turn(
            query_text="show me pending wiki issues",
            tool_names=["wiki_issue"],
            attachments=[],
            status="success",
        )

        self.assertEqual(events[0]["view"], "knowledge")
        self.assertEqual(events[0]["section"], "pending_corrections")

    def test_unrelated_question_does_not_open_drawer(self) -> None:
        events = ui_events_for_turn(
            query_text="how are you agent",
            tool_names=[],
            attachments=[],
            status="success",
        )

        self.assertEqual(events, [])


if __name__ == "__main__":
    unittest.main()
