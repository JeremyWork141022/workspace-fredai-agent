from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from app.knowledge_chunker import SplitterConfig, split, split_parent_child
from app.knowledge_store import KnowledgeStore
from app.memory_manager import AgentMemoryManager
from app.tools import ToolContext, build_core_tool_registry
from app.config import AppConfig
from app.session_store import SessionStore
from app.memory_store import MemoryStore


def _config() -> AppConfig:
    return AppConfig(
        model="mock",
        fredai_base_url="http://localhost",
        fredai_stream=False,
        fredai_verify_ssl=False,
        fredai_timeout_seconds=30,
        fredai_oauth_url="",
        fredai_client_id="",
        fredai_client_secret="",
        fredai_oauth_username="",
        fredai_oauth_password_b64="",
        fredai_jwt_token="",
        max_agent_iterations=4,
        session_context_messages=8,
        scheduler_enabled=False,
        memory_char_limit=2800,
        user_memory_char_limit=1600,
        memory_prefetch_enabled=True,
        session_search_aux_enabled=False,
        session_search_limit=3,
        trace_enabled=False,
        trace_full_media=False,
        delivery_url="",
    )


class KnowledgeChunkerTests(unittest.TestCase):
    def test_heading_parent_child_chunking_keeps_context(self) -> None:
        text = (
            "# EVA Overview\n"
            "EVA calculates decision metrics from upstream Macs outputs.\n\n"
            "## Inputs\n"
            + "Macs produces scenario data. " * 40
            + "\n\n## Outputs\n"
            + "EVA publishes analytics metrics. " * 30
        )
        chunks = split(text, SplitterConfig(chunk_size=240, chunk_overlap=30, strategy="auto"))
        pc = split_parent_child(
            text,
            parent_config=SplitterConfig(chunk_size=500, chunk_overlap=50, strategy="auto"),
            child_config=SplitterConfig(chunk_size=160, chunk_overlap=25, strategy="auto"),
        )

        self.assertGreater(len(chunks), 1)
        self.assertGreater(len(pc.children), 1)
        self.assertTrue(any("EVA Overview" in child.chunk.context_header for child in pc.children))


class KnowledgeStoreTests(unittest.TestCase):
    def test_ingest_search_read_wiki_and_issue(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = KnowledgeStore(Path(tmp) / "state.sqlite3")
            ingest = store.ingest_document(
                workspace_id="ws",
                title="EVA User Guide",
                content=(
                    "# EVA User Guide\n"
                    "EVA depends on Macs for upstream scenario outputs.\n\n"
                    "## Run Steps\n"
                    "Step 1 loads Macs outputs. Step 2 validates metric inputs. "
                    "Step 3 calculates CRT Analytics metrics."
                ),
                process="EVA",
                doc_type="user_guide",
                source_uri="file://eva-guide.md",
                tags=["eva", "macs"],
            )
            results = store.search_chunks(workspace_id="ws", query="Macs upstream scenario", limit=5)
            read = store.read_context(workspace_id="ws", chunk_ids=[results[0].chunk.id])
            page = store.upsert_wiki_page(
                workspace_id="ws",
                slug="eva-overview",
                title="EVA Overview",
                page_type="process",
                summary="EVA depends on [[macs-overview|Macs]].",
                content="EVA uses [[macs-overview|Macs]] outputs.",
                chunk_refs=[{"chunk_id": results[0].chunk.id, "document_id": results[0].chunk.document_id}],
            )
            wiki = store.read_wiki(workspace_id="ws", slugs=["eva-overview"])
            issue = store.create_wiki_issue(
                workspace_id="ws",
                slug="eva-overview",
                issue_type="missing_info",
                description="Add model register reference.",
                created_by="tester",
            )

            self.assertTrue(ingest["ingested"])
            self.assertGreaterEqual(ingest["child_chunks"], 1)
            self.assertTrue(results)
            self.assertEqual(read["count"] >= 1, True)
            self.assertEqual(page.slug, "eva-overview")
            self.assertEqual(wiki["pages"][0]["slug"], "eva-overview")
            self.assertEqual(issue.status, "pending")


class KnowledgeToolRegistryTests(unittest.IsolatedAsyncioTestCase):
    async def test_knowledge_tools_are_callable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "state.sqlite3"
            session_store = SessionStore(db_path)
            memory_store = MemoryStore(db_path)
            memory_manager = AgentMemoryManager(_config(), memory_store)
            knowledge_store = KnowledgeStore(db_path)
            registry = build_core_tool_registry(
                session_store=session_store,
                memory_manager=memory_manager,
                knowledge_store=knowledge_store,
                config=_config(),
            )
            session = session_store.get_or_create_session(workspace_id="ws", user_id="u")
            context = ToolContext(
                session_id=session.id,
                workspace_id="ws",
                user_id="u",
                config=_config(),
                session_store=session_store,
                memory_manager=memory_manager,
                knowledge_store=knowledge_store,
            )
            ingest = await registry.execute(
                name="knowledge_ingest",
                arguments={
                    "title": "Macs Guide",
                    "content": "# Macs\nMacs feeds EVA upstream model outputs.",
                    "process": "Macs",
                    "doc_type": "user_guide",
                },
                context=context,
            )
            search = await registry.execute(
                name="knowledge_search",
                arguments={"query": "Macs feeds EVA", "limit": 3},
                context=context,
            )

            self.assertIn("knowledge_read", registry.names())
            self.assertTrue(ingest["ok"])
            self.assertTrue(ingest["result"]["ingested"])
            self.assertTrue(search["ok"])
            self.assertGreaterEqual(search["result"]["count"], 1)


if __name__ == "__main__":
    unittest.main()
