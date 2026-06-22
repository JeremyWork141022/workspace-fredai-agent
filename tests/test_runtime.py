from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from app.fredai_client import FredAIClient
from app.memory_store import MemoryStore
from app.session_store import SessionStore


class SessionStoreTests(unittest.TestCase):
    def test_session_messages_and_search(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "state.sqlite3"
            store = SessionStore(db_path)
            session = store.get_or_create_session(workspace_id="ws", user_id="u")
            user = store.append_message(session_id=session.id, role="user", content="Find the June operating statement.")
            store.append_message(session_id=session.id, role="assistant", content="I will look for it.")

            recent = store.recent_model_messages(session.id, limit=8)
            matches = store.search_message_context(query="June operating", workspace_id="ws", limit=5)

            self.assertEqual(user.role, "user")
            self.assertEqual(len(recent), 2)
            self.assertTrue(matches)
            self.assertEqual(matches[0].session_id, session.id)


class MemoryStoreTests(unittest.TestCase):
    def test_workspace_notes_and_routines(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "state.sqlite3"
            store = MemoryStore(db_path)
            note = store.save_workspace_note(workspace_id="ws", title="Closing rule", body="Always check totals.", tags=["ops"])
            found = store.search_workspace_notes(workspace_id="ws", query="totals")
            rule = store.save_routine_rule(
                workspace_id="ws",
                user_id="u",
                rule_type="hook",
                title="Check totals",
                trigger_text="operating statement",
                action_text="Verify line item totals.",
                metadata={"hook_event": "pre_llm"},
            )
            triggered = store.triggered_routine_rules(
                workspace_id="ws",
                user_id="u",
                event="pre_llm",
                text="Please review this operating statement.",
            )

            self.assertEqual(note.id, found[0].id)
            self.assertEqual(rule.id, triggered[0].id)


class FredAIStreamParserTests(unittest.TestCase):
    def test_stream_message_with_tool_call(self) -> None:
        chunks = [
            {"choices": [{"delta": {"role": "assistant"}}]},
            {
                "choices": [
                    {
                        "delta": {
                            "tool_calls": [
                                {
                                    "index": 0,
                                    "id": "call_1",
                                    "type": "function",
                                    "function": {"name": "memory", "arguments": "{\"action\":\"add\","},
                                }
                            ]
                        }
                    }
                ]
            },
            {"choices": [{"delta": {"tool_calls": [{"index": 0, "function": {"arguments": "\"target\":\"user\"}"}}]}}]},
        ]
        message, text_chunks = FredAIClient._message_from_stream_chunks(chunks)

        self.assertEqual(text_chunks, [])
        self.assertEqual(message["tool_calls"][0]["id"], "call_1")
        self.assertEqual(message["tool_calls"][0]["function"]["name"], "memory")
        self.assertEqual(message["tool_calls"][0]["function"]["arguments"], "{\"action\":\"add\",\"target\":\"user\"}")


if __name__ == "__main__":
    unittest.main()

