from __future__ import annotations

import base64
import hashlib
import json
import re
import sqlite3
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Sequence

from app.config import ensure_dirs, state_db_path
from app.knowledge_chunker import (
    DEFAULT_CHILD_CHUNK_SIZE,
    DEFAULT_CHUNK_OVERLAP,
    DEFAULT_PARENT_CHUNK_SIZE,
    SplitterConfig,
    split_parent_child,
)
from app.session_store import json_dumps, json_loads, utc_now


DEFAULT_KNOWLEDGE_BASE = "CRT Cost"
MAX_TOOL_CONTENT_CHARS = 60000
LINK_RE = re.compile(r"\[\[([a-zA-Z0-9_.:-]+)(?:\|([^\]]+))?\]\]")


@dataclass(frozen=True)
class KnowledgeBaseRecord:
    id: str
    workspace_id: str
    name: str
    description: str
    metadata: Dict[str, Any]
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class KnowledgeDocumentRecord:
    id: str
    workspace_id: str
    knowledge_base_id: str
    title: str
    source_type: str
    source_uri: str
    file_name: str
    file_extension: str
    content_hash: str
    doc_type: str
    process: str
    tags: List[str]
    metadata: Dict[str, Any]
    status: str
    summary: str
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class KnowledgeChunkRecord:
    id: str
    workspace_id: str
    knowledge_base_id: str
    document_id: str
    parent_chunk_id: str
    chunk_type: str
    chunk_index: int
    content: str
    context_header: str
    start_offset: int
    end_offset: int
    page_start: int
    page_end: int
    section_path: str
    metadata: Dict[str, Any]
    content_hash: str
    created_at: str
    document_title: str = ""
    source_uri: str = ""
    process: str = ""
    doc_type: str = ""


@dataclass(frozen=True)
class KnowledgeSearchResult:
    chunk: KnowledgeChunkRecord
    score: float
    match_type: str
    snippet: str


@dataclass(frozen=True)
class WikiPageRecord:
    id: str
    workspace_id: str
    slug: str
    title: str
    page_type: str
    summary: str
    content: str
    aliases: List[str]
    status: str
    source_refs: List[Dict[str, Any]]
    chunk_refs: List[Dict[str, Any]]
    links: List[str]
    metadata: Dict[str, Any]
    version: int
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class WikiIssueRecord:
    id: str
    workspace_id: str
    slug: str
    issue_type: str
    description: str
    evidence: str
    status: str
    created_by: str
    metadata: Dict[str, Any]
    created_at: str
    updated_at: str


class KnowledgeStore:
    """SQLite knowledge base, chunk retrieval, wiki, and correction store."""

    def __init__(self, db_path: Optional[Path] = None):
        ensure_dirs()
        self._db_path = db_path or state_db_path()
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._ensure_schema()

    @property
    def db_path(self) -> Path:
        return self._db_path

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA journal_mode = WAL")
        return conn

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        conn = self._connect()
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def _ensure_schema(self) -> None:
        with self._connection() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS knowledge_bases (
                    id TEXT PRIMARY KEY,
                    workspace_id TEXT NOT NULL,
                    name TEXT NOT NULL,
                    description TEXT NOT NULL DEFAULT '',
                    metadata_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(workspace_id, name)
                )
                """
            )
            conn.execute("CREATE INDEX IF NOT EXISTS idx_kb_workspace ON knowledge_bases(workspace_id, updated_at)")
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS knowledge_documents (
                    id TEXT PRIMARY KEY,
                    workspace_id TEXT NOT NULL,
                    knowledge_base_id TEXT NOT NULL,
                    title TEXT NOT NULL,
                    source_type TEXT NOT NULL DEFAULT '',
                    source_uri TEXT NOT NULL DEFAULT '',
                    file_name TEXT NOT NULL DEFAULT '',
                    file_extension TEXT NOT NULL DEFAULT '',
                    content_hash TEXT NOT NULL DEFAULT '',
                    doc_type TEXT NOT NULL DEFAULT '',
                    process TEXT NOT NULL DEFAULT '',
                    tags_json TEXT NOT NULL DEFAULT '[]',
                    metadata_json TEXT NOT NULL DEFAULT '{}',
                    status TEXT NOT NULL DEFAULT 'active',
                    summary TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(workspace_id, knowledge_base_id, source_uri)
                )
                """
            )
            conn.execute("CREATE INDEX IF NOT EXISTS idx_kdocs_workspace ON knowledge_documents(workspace_id, updated_at)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_kdocs_process ON knowledge_documents(workspace_id, process, doc_type)")
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS knowledge_files (
                    id TEXT PRIMARY KEY,
                    workspace_id TEXT NOT NULL,
                    document_id TEXT NOT NULL,
                    file_name TEXT NOT NULL DEFAULT '',
                    media_type TEXT NOT NULL DEFAULT '',
                    size_bytes INTEGER NOT NULL DEFAULT 0,
                    content_base64 TEXT NOT NULL DEFAULT '',
                    content_hash TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(document_id)
                )
                """
            )
            conn.execute("CREATE INDEX IF NOT EXISTS idx_kfiles_workspace ON knowledge_files(workspace_id, updated_at)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_kfiles_document ON knowledge_files(document_id)")
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS knowledge_chunks (
                    id TEXT PRIMARY KEY,
                    workspace_id TEXT NOT NULL,
                    knowledge_base_id TEXT NOT NULL,
                    document_id TEXT NOT NULL,
                    parent_chunk_id TEXT NOT NULL DEFAULT '',
                    chunk_type TEXT NOT NULL DEFAULT 'text',
                    chunk_index INTEGER NOT NULL,
                    content TEXT NOT NULL DEFAULT '',
                    context_header TEXT NOT NULL DEFAULT '',
                    start_offset INTEGER NOT NULL DEFAULT 0,
                    end_offset INTEGER NOT NULL DEFAULT 0,
                    page_start INTEGER NOT NULL DEFAULT 0,
                    page_end INTEGER NOT NULL DEFAULT 0,
                    section_path TEXT NOT NULL DEFAULT '',
                    metadata_json TEXT NOT NULL DEFAULT '{}',
                    content_hash TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL
                )
                """
            )
            conn.execute("CREATE INDEX IF NOT EXISTS idx_kchunks_doc ON knowledge_chunks(document_id, chunk_index)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_kchunks_parent ON knowledge_chunks(parent_chunk_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_kchunks_workspace ON knowledge_chunks(workspace_id, knowledge_base_id)")
            conn.execute(
                """
                CREATE VIRTUAL TABLE IF NOT EXISTS knowledge_chunks_fts
                USING fts5(chunk_id UNINDEXED, workspace_id UNINDEXED, knowledge_base_id UNINDEXED,
                           document_id UNINDEXED, title, context, content)
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS wiki_pages (
                    id TEXT PRIMARY KEY,
                    workspace_id TEXT NOT NULL,
                    slug TEXT NOT NULL,
                    title TEXT NOT NULL,
                    page_type TEXT NOT NULL DEFAULT 'concept',
                    summary TEXT NOT NULL DEFAULT '',
                    content TEXT NOT NULL DEFAULT '',
                    aliases_json TEXT NOT NULL DEFAULT '[]',
                    status TEXT NOT NULL DEFAULT 'active',
                    source_refs_json TEXT NOT NULL DEFAULT '[]',
                    chunk_refs_json TEXT NOT NULL DEFAULT '[]',
                    links_json TEXT NOT NULL DEFAULT '[]',
                    metadata_json TEXT NOT NULL DEFAULT '{}',
                    version INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(workspace_id, slug)
                )
                """
            )
            conn.execute("CREATE INDEX IF NOT EXISTS idx_wiki_workspace ON wiki_pages(workspace_id, status, updated_at)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_wiki_type ON wiki_pages(workspace_id, page_type, status)")
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS wiki_page_revisions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    page_id TEXT NOT NULL,
                    workspace_id TEXT NOT NULL,
                    slug TEXT NOT NULL,
                    version INTEGER NOT NULL,
                    title TEXT NOT NULL,
                    summary TEXT NOT NULL DEFAULT '',
                    content TEXT NOT NULL DEFAULT '',
                    source_refs_json TEXT NOT NULL DEFAULT '[]',
                    chunk_refs_json TEXT NOT NULL DEFAULT '[]',
                    created_at TEXT NOT NULL
                )
                """
            )
            conn.execute("CREATE INDEX IF NOT EXISTS idx_wiki_revisions_page ON wiki_page_revisions(page_id, version)")
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS wiki_issues (
                    id TEXT PRIMARY KEY,
                    workspace_id TEXT NOT NULL,
                    slug TEXT NOT NULL DEFAULT '',
                    issue_type TEXT NOT NULL DEFAULT 'other',
                    description TEXT NOT NULL,
                    evidence TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL DEFAULT 'pending',
                    created_by TEXT NOT NULL DEFAULT '',
                    metadata_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            conn.execute("CREATE INDEX IF NOT EXISTS idx_wiki_issues_workspace ON wiki_issues(workspace_id, status, updated_at)")
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS retrieval_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    workspace_id TEXT NOT NULL,
                    session_id TEXT NOT NULL DEFAULT '',
                    user_id TEXT NOT NULL DEFAULT '',
                    query TEXT NOT NULL DEFAULT '',
                    tool_name TEXT NOT NULL DEFAULT '',
                    result_refs_json TEXT NOT NULL DEFAULT '[]',
                    metadata_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL
                )
                """
            )
            conn.execute("CREATE INDEX IF NOT EXISTS idx_retrieval_events_workspace ON retrieval_events(workspace_id, created_at)")

    def debug_state(self, *, workspace_id: str = "") -> Dict[str, Any]:
        with self._connection() as conn:
            where = "WHERE workspace_id = ?" if workspace_id else ""
            params: List[Any] = [workspace_id] if workspace_id else []
            kb_count = conn.execute(f"SELECT COUNT(*) FROM knowledge_bases {where}", params).fetchone()[0]
            doc_count = conn.execute(f"SELECT COUNT(*) FROM knowledge_documents {where}", params).fetchone()[0]
            file_count = conn.execute(f"SELECT COUNT(*) FROM knowledge_files {where}", params).fetchone()[0]
            chunk_count = conn.execute(f"SELECT COUNT(*) FROM knowledge_chunks {where}", params).fetchone()[0]
            wiki_count = conn.execute(f"SELECT COUNT(*) FROM wiki_pages {where}", params).fetchone()[0]
            issue_count = conn.execute(f"SELECT COUNT(*) FROM wiki_issues {where}", params).fetchone()[0]
        return {
            "db_path": str(self._db_path),
            "knowledge_bases": int(kb_count),
            "documents": int(doc_count),
            "files": int(file_count),
            "chunks": int(chunk_count),
            "wiki_pages": int(wiki_count),
            "wiki_issues": int(issue_count),
        }

    def has_retrievable_knowledge(self, *, workspace_id: str) -> bool:
        workspace_id = workspace_id.strip() or "default"
        with self._connection() as conn:
            chunk_row = conn.execute(
                """
                SELECT 1
                FROM knowledge_chunks
                WHERE workspace_id = ? AND chunk_type = 'text'
                LIMIT 1
                """,
                (workspace_id,),
            ).fetchone()
            if chunk_row:
                return True
            wiki_row = conn.execute(
                """
                SELECT 1
                FROM wiki_pages
                WHERE workspace_id = ? AND status != 'deleted'
                LIMIT 1
                """,
                (workspace_id,),
            ).fetchone()
            return bool(wiki_row)

    def ensure_knowledge_base(
        self,
        *,
        workspace_id: str,
        name: str = DEFAULT_KNOWLEDGE_BASE,
        description: str = "",
        metadata: Optional[Dict[str, Any]] = None,
    ) -> KnowledgeBaseRecord:
        workspace_id = workspace_id.strip() or "default"
        name = name.strip() or DEFAULT_KNOWLEDGE_BASE
        now = utc_now()
        with self._connection() as conn:
            row = conn.execute(
                "SELECT * FROM knowledge_bases WHERE workspace_id = ? AND name = ?",
                (workspace_id, name),
            ).fetchone()
            if row:
                if description or metadata:
                    conn.execute(
                        """
                        UPDATE knowledge_bases
                        SET description = COALESCE(NULLIF(?, ''), description),
                            metadata_json = ?,
                            updated_at = ?
                        WHERE id = ?
                        """,
                        (description.strip(), json_dumps(metadata or self._json(row, "metadata_json", {})), now, row["id"]),
                    )
                    row = conn.execute("SELECT * FROM knowledge_bases WHERE id = ?", (row["id"],)).fetchone()
                return self._row_to_kb(row)
            kb_id = f"kb_{uuid.uuid4().hex}"
            conn.execute(
                """
                INSERT INTO knowledge_bases
                    (id, workspace_id, name, description, metadata_json, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (kb_id, workspace_id, name, description.strip(), json_dumps(metadata or {}), now, now),
            )
            row = conn.execute("SELECT * FROM knowledge_bases WHERE id = ?", (kb_id,)).fetchone()
            return self._row_to_kb(row)

    def ingest_document(
        self,
        *,
        workspace_id: str,
        title: str,
        content: str,
        knowledge_base: str = DEFAULT_KNOWLEDGE_BASE,
        source_type: str = "manual",
        source_uri: str = "",
        file_name: str = "",
        file_extension: str = "",
        process: str = "",
        doc_type: str = "",
        tags: Optional[List[str]] = None,
        metadata: Optional[Dict[str, Any]] = None,
        summary: str = "",
        chunk_strategy: str = "auto",
    ) -> Dict[str, Any]:
        clean_content = content.strip()
        if not clean_content:
            raise ValueError("content is required")
        clean_title = title.strip() or file_name.strip() or "Untitled knowledge document"
        workspace_id = workspace_id.strip() or "default"
        kb = self.ensure_knowledge_base(workspace_id=workspace_id, name=knowledge_base)
        content_hash = hashlib.sha256(clean_content.encode("utf-8", errors="replace")).hexdigest()
        source_uri = source_uri.strip() or f"manual:{_slugify(clean_title)}:{content_hash[:12]}"
        clean_tags = [str(tag).strip() for tag in tags or [] if str(tag).strip()]
        now = utc_now()

        with self._connection() as conn:
            existing = conn.execute(
                """
                SELECT * FROM knowledge_documents
                WHERE workspace_id = ? AND knowledge_base_id = ? AND source_uri = ?
                """,
                (workspace_id, kb.id, source_uri),
            ).fetchone()
            doc_id = str(existing["id"]) if existing else f"doc_{uuid.uuid4().hex}"
            if existing:
                self._delete_document_chunks(conn, doc_id)
                conn.execute(
                    """
                    UPDATE knowledge_documents
                    SET title = ?, source_type = ?, file_name = ?, file_extension = ?,
                        content_hash = ?, doc_type = ?, process = ?, tags_json = ?,
                        metadata_json = ?, status = 'active', summary = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    (
                        clean_title,
                        source_type.strip(),
                        file_name.strip(),
                        file_extension.strip().lower(),
                        content_hash,
                        doc_type.strip(),
                        process.strip(),
                        json_dumps(clean_tags),
                        json_dumps(metadata or {}),
                        summary.strip(),
                        now,
                        doc_id,
                    ),
                )
            else:
                conn.execute(
                    """
                    INSERT INTO knowledge_documents
                        (id, workspace_id, knowledge_base_id, title, source_type, source_uri,
                         file_name, file_extension, content_hash, doc_type, process, tags_json,
                         metadata_json, status, summary, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'active', ?, ?, ?)
                    """,
                    (
                        doc_id,
                        workspace_id,
                        kb.id,
                        clean_title,
                        source_type.strip(),
                        source_uri,
                        file_name.strip(),
                        file_extension.strip().lower(),
                        content_hash,
                        doc_type.strip(),
                        process.strip(),
                        json_dumps(clean_tags),
                        json_dumps(metadata or {}),
                        summary.strip(),
                        now,
                        now,
                    ),
                )

            parent_cfg = SplitterConfig(
                chunk_size=DEFAULT_PARENT_CHUNK_SIZE,
                chunk_overlap=DEFAULT_CHUNK_OVERLAP,
                strategy=chunk_strategy or "auto",
            )
            child_cfg = SplitterConfig(
                chunk_size=DEFAULT_CHILD_CHUNK_SIZE,
                chunk_overlap=DEFAULT_CHILD_CHUNK_SIZE // 5,
                strategy=chunk_strategy or "auto",
            )
            chunk_result = split_parent_child(clean_content, parent_config=parent_cfg, child_config=child_cfg)
            parent_ids: List[str] = []
            parent_records = []
            for parent in chunk_result.parents:
                chunk_id = f"chk_{uuid.uuid4().hex}"
                parent_ids.append(chunk_id)
                parent_records.append(
                    self._chunk_row_values(
                        chunk_id=chunk_id,
                        workspace_id=workspace_id,
                        kb_id=kb.id,
                        doc_id=doc_id,
                        parent_id="",
                        chunk_type="parent_text",
                        chunk_index=parent.seq,
                        content=parent.content,
                        context_header=parent.context_header,
                        start=parent.start,
                        end=parent.end,
                        section_path=_section_path(parent.context_header),
                        metadata={"embedding_indexed": False},
                        now=now,
                    )
                )
            child_records = []
            fts_records = []
            for child in chunk_result.children:
                parsed = child.chunk
                chunk_id = f"chk_{uuid.uuid4().hex}"
                parent_id = parent_ids[child.parent_index] if child.parent_index >= 0 and child.parent_index < len(parent_ids) else ""
                metadata_payload = {"embedding_content_preview": parsed.embedding_content()[:500]}
                values = self._chunk_row_values(
                    chunk_id=chunk_id,
                    workspace_id=workspace_id,
                    kb_id=kb.id,
                    doc_id=doc_id,
                    parent_id=parent_id,
                    chunk_type="text",
                    chunk_index=parsed.seq,
                    content=parsed.content,
                    context_header=parsed.context_header,
                    start=parsed.start,
                    end=parsed.end,
                    section_path=_section_path(parsed.context_header),
                    metadata=metadata_payload,
                    now=now,
                )
                child_records.append(values)
                fts_records.append((chunk_id, workspace_id, kb.id, doc_id, clean_title, parsed.context_header, parsed.content))

            conn.executemany(
                """
                INSERT INTO knowledge_chunks
                    (id, workspace_id, knowledge_base_id, document_id, parent_chunk_id,
                     chunk_type, chunk_index, content, context_header, start_offset,
                     end_offset, page_start, page_end, section_path, metadata_json,
                     content_hash, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                parent_records + child_records,
            )
            conn.executemany(
                """
                INSERT INTO knowledge_chunks_fts
                    (chunk_id, workspace_id, knowledge_base_id, document_id, title, context, content)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                fts_records,
            )
            row = conn.execute("SELECT * FROM knowledge_documents WHERE id = ?", (doc_id,)).fetchone()
            document = self._row_to_document(row)

        return {
            "ingested": True,
            "document": self.document_to_dict(document),
            "knowledge_base": {"id": kb.id, "name": kb.name},
            "parent_chunks": len(parent_records),
            "child_chunks": len(child_records),
            "content_hash": content_hash,
            "next_step": (
                "Use knowledge_search to find candidate chunks, then knowledge_read to deep-read full source context. "
                "Use wiki_write to create or update curated wiki pages with chunk_refs from this document."
            ),
        }

    @staticmethod
    def hash_bytes(content: bytes) -> str:
        return hashlib.sha256(content).hexdigest()

    def get_document(self, *, workspace_id: str, document_id: str) -> Optional[KnowledgeDocumentRecord]:
        workspace_id = workspace_id.strip() or "default"
        document_id = document_id.strip()
        if not document_id:
            return None
        with self._connection() as conn:
            row = conn.execute(
                "SELECT * FROM knowledge_documents WHERE workspace_id = ? AND id = ? AND status != 'deleted'",
                (workspace_id, document_id),
            ).fetchone()
        return self._row_to_document(row) if row else None

    def list_documents(
        self,
        *,
        workspace_id: str,
        knowledge_base: str = "",
        process: str = "",
        limit: int = 200,
    ) -> List[Dict[str, Any]]:
        workspace_id = workspace_id.strip() or "default"
        where = ["d.workspace_id = ?", "d.status != 'deleted'"]
        params: List[Any] = [workspace_id]
        if knowledge_base.strip():
            where.append("kb.name = ?")
            params.append(knowledge_base.strip())
        if process.strip():
            where.append("d.process = ?")
            params.append(process.strip())
        params.append(max(1, min(limit, 500)))
        with self._connection() as conn:
            rows = conn.execute(
                f"""
                SELECT
                    d.*,
                    kb.name AS knowledge_base_name,
                    COUNT(CASE WHEN c.chunk_type = 'text' THEN 1 END) AS child_chunk_count,
                    COUNT(c.id) AS total_chunk_count,
                    f.file_name AS stored_file_name,
                    f.media_type AS stored_media_type,
                    f.size_bytes AS stored_size_bytes,
                    f.updated_at AS file_updated_at
                FROM knowledge_documents d
                JOIN knowledge_bases kb ON kb.id = d.knowledge_base_id
                LEFT JOIN knowledge_chunks c ON c.document_id = d.id
                LEFT JOIN knowledge_files f ON f.document_id = d.id
                WHERE {' AND '.join(where)}
                GROUP BY d.id
                ORDER BY d.updated_at DESC
                LIMIT ?
                """,
                params,
            ).fetchall()
        documents: List[Dict[str, Any]] = []
        for row in rows:
            document = self.document_to_dict(self._row_to_document(row))
            has_file = row["stored_file_name"] is not None
            document.update(
                {
                    "workspace_id": str(row["workspace_id"]),
                    "knowledge_base": str(row["knowledge_base_name"] or ""),
                    "chunk_count": int(row["child_chunk_count"] or 0),
                    "total_chunk_count": int(row["total_chunk_count"] or 0),
                    "metadata": self._json(row, "metadata_json", {}),
                    "created_at": str(row["created_at"]),
                    "has_original_file": bool(has_file),
                    "stored_file_name": str(row["stored_file_name"] or ""),
                    "stored_media_type": str(row["stored_media_type"] or ""),
                    "stored_size_bytes": int(row["stored_size_bytes"] or 0),
                    "file_updated_at": str(row["file_updated_at"] or ""),
                    "download_url": f"/agent/knowledge/documents/{document['id']}/download?workspace_id={workspace_id}",
                }
            )
            documents.append(document)
        return documents

    def save_document_file(
        self,
        *,
        workspace_id: str,
        document_id: str,
        file_name: str,
        media_type: str,
        content: bytes,
    ) -> Dict[str, Any]:
        workspace_id = workspace_id.strip() or "default"
        document_id = document_id.strip()
        if not document_id:
            raise ValueError("document_id is required")
        now = utc_now()
        content_hash = self.hash_bytes(content)
        encoded = base64.b64encode(content).decode("ascii")
        with self._connection() as conn:
            existing = conn.execute("SELECT id FROM knowledge_files WHERE document_id = ?", (document_id,)).fetchone()
            file_id = str(existing["id"]) if existing else f"kfile_{uuid.uuid4().hex}"
            if existing:
                conn.execute(
                    """
                    UPDATE knowledge_files
                    SET workspace_id = ?, file_name = ?, media_type = ?, size_bytes = ?,
                        content_base64 = ?, content_hash = ?, updated_at = ?
                    WHERE document_id = ?
                    """,
                    (workspace_id, file_name.strip(), media_type.strip(), len(content), encoded, content_hash, now, document_id),
                )
            else:
                conn.execute(
                    """
                    INSERT INTO knowledge_files
                        (id, workspace_id, document_id, file_name, media_type, size_bytes,
                         content_base64, content_hash, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        file_id,
                        workspace_id,
                        document_id,
                        file_name.strip(),
                        media_type.strip(),
                        len(content),
                        encoded,
                        content_hash,
                        now,
                        now,
                    ),
                )
        return {
            "id": file_id,
            "document_id": document_id,
            "file_name": file_name.strip(),
            "media_type": media_type.strip(),
            "size_bytes": len(content),
            "content_hash": content_hash,
            "updated_at": now,
        }

    def get_document_file(self, *, workspace_id: str, document_id: str) -> Optional[Dict[str, Any]]:
        workspace_id = workspace_id.strip() or "default"
        with self._connection() as conn:
            row = conn.execute(
                """
                SELECT *
                FROM knowledge_files
                WHERE workspace_id = ? AND document_id = ?
                """,
                (workspace_id, document_id),
            ).fetchone()
        if not row:
            return None
        return {
            "document_id": str(row["document_id"]),
            "file_name": str(row["file_name"] or "knowledge-document.bin"),
            "media_type": str(row["media_type"] or "application/octet-stream"),
            "size_bytes": int(row["size_bytes"] or 0),
            "content_hash": str(row["content_hash"] or ""),
            "updated_at": str(row["updated_at"] or ""),
            "content": base64.b64decode(str(row["content_base64"] or "")),
        }

    def document_text_export(self, *, workspace_id: str, document_id: str) -> Optional[Dict[str, Any]]:
        document = self.get_document(workspace_id=workspace_id, document_id=document_id)
        if not document:
            return None
        with self._connection() as conn:
            rows = conn.execute(
                """
                SELECT chunk_index, context_header, content
                FROM knowledge_chunks
                WHERE workspace_id = ? AND document_id = ? AND chunk_type = 'text'
                ORDER BY chunk_index
                """,
                (workspace_id.strip() or "default", document_id),
            ).fetchall()
        lines = [
            f"# {document.title}",
            "",
            f"Document ID: {document.id}",
            f"Source URI: {document.source_uri}",
            f"Process: {document.process}",
            f"Doc type: {document.doc_type}",
            "",
        ]
        for row in rows:
            header = str(row["context_header"] or "").strip()
            if header:
                lines.append(f"## {header}")
            lines.append(str(row["content"] or "").strip())
            lines.append("")
        stem = re.sub(r"[^A-Za-z0-9_.-]+", "_", document.file_name or document.title).strip("_") or document.id
        if not stem.lower().endswith(".txt"):
            stem = f"{stem}.txt"
        return {"file_name": stem, "content": "\n".join(lines).strip() + "\n"}

    def search_chunks(
        self,
        *,
        workspace_id: str,
        query: str,
        limit: int = 8,
        knowledge_base: str = "",
        process: str = "",
        doc_type: str = "",
    ) -> List[KnowledgeSearchResult]:
        query = query.strip()
        if not query:
            return []
        limit = max(1, min(limit, 30))
        try:
            results = self._search_chunks_fts(
                workspace_id=workspace_id,
                query=query,
                limit=limit,
                knowledge_base=knowledge_base,
                process=process,
                doc_type=doc_type,
            )
            if results:
                return results
        except sqlite3.OperationalError:
            pass
        return self._search_chunks_like(
            workspace_id=workspace_id,
            query=query,
            limit=limit,
            knowledge_base=knowledge_base,
            process=process,
            doc_type=doc_type,
        )

    def grep_chunks(
        self,
        *,
        workspace_id: str,
        pattern: str,
        limit: int = 8,
        case_sensitive: bool = False,
        knowledge_base: str = "",
        process: str = "",
        doc_type: str = "",
    ) -> List[KnowledgeSearchResult]:
        pattern = pattern.strip()
        if not pattern:
            return []
        flags = 0 if case_sensitive else re.IGNORECASE
        try:
            regex = re.compile(pattern, flags)
        except re.error:
            regex = re.compile(re.escape(pattern), flags)
        token = _query_terms(pattern)[0] if _query_terms(pattern) else pattern[:20]
        rows = self._candidate_chunk_rows(
            workspace_id=workspace_id,
            needle=token,
            max_rows=500,
            knowledge_base=knowledge_base,
            process=process,
            doc_type=doc_type,
        )
        results: List[KnowledgeSearchResult] = []
        for row in rows:
            chunk = self._row_to_chunk(row)
            haystack = "\n".join([chunk.document_title, chunk.context_header, chunk.content])
            match = regex.search(haystack)
            if not match:
                continue
            score = 1000.0 - float(match.start())
            results.append(KnowledgeSearchResult(chunk, score, "regex", _snippet(haystack, match.start(), match.end())))
        results.sort(key=lambda item: (-item.score, item.chunk.document_title, item.chunk.chunk_index))
        return results[: max(1, min(limit, 30))]

    def read_context(
        self,
        *,
        workspace_id: str,
        chunk_ids: Optional[List[str]] = None,
        document_id: str = "",
        chunk_indexes: Optional[List[int]] = None,
        include_parent: bool = True,
        include_neighbors: bool = True,
        max_chars: int = MAX_TOOL_CONTENT_CHARS,
    ) -> Dict[str, Any]:
        chunk_ids = [chunk_id.strip() for chunk_id in chunk_ids or [] if chunk_id.strip()]
        chunk_indexes = [int(index) for index in chunk_indexes or []]
        max_chars = max(1000, min(max_chars, 120000))
        with self._connection() as conn:
            rows: List[sqlite3.Row] = []
            if chunk_ids:
                placeholders = ",".join("?" for _ in chunk_ids)
                rows = conn.execute(
                    f"{self._chunk_select_sql()} WHERE c.workspace_id = ? AND c.id IN ({placeholders})",
                    [workspace_id, *chunk_ids],
                ).fetchall()
            elif document_id and chunk_indexes:
                placeholders = ",".join("?" for _ in chunk_indexes)
                rows = conn.execute(
                    f"""
                    {self._chunk_select_sql()}
                    WHERE c.workspace_id = ? AND c.document_id = ? AND c.chunk_index IN ({placeholders})
                    ORDER BY c.chunk_index
                    """,
                    [workspace_id, document_id, *chunk_indexes],
                ).fetchall()
            elif document_id:
                rows = conn.execute(
                    f"""
                    {self._chunk_select_sql()}
                    WHERE c.workspace_id = ? AND c.document_id = ? AND c.chunk_type = 'text'
                    ORDER BY c.chunk_index
                    LIMIT 30
                    """,
                    (workspace_id, document_id),
                ).fetchall()
            base_chunks = [self._row_to_chunk(row) for row in rows]
            expanded: Dict[str, KnowledgeChunkRecord] = {chunk.id: chunk for chunk in base_chunks}
            if include_parent:
                parent_ids = sorted({chunk.parent_chunk_id for chunk in base_chunks if chunk.parent_chunk_id})
                if parent_ids:
                    placeholders = ",".join("?" for _ in parent_ids)
                    parent_rows = conn.execute(
                        f"{self._chunk_select_sql()} WHERE c.workspace_id = ? AND c.id IN ({placeholders})",
                        [workspace_id, *parent_ids],
                    ).fetchall()
                    for row in parent_rows:
                        chunk = self._row_to_chunk(row)
                        expanded[chunk.id] = chunk
            if include_neighbors:
                for chunk in base_chunks:
                    neighbor_rows = conn.execute(
                        f"""
                        {self._chunk_select_sql()}
                        WHERE c.workspace_id = ? AND c.document_id = ? AND c.chunk_type = 'text'
                          AND c.chunk_index BETWEEN ? AND ?
                        ORDER BY c.chunk_index
                        """,
                        (workspace_id, chunk.document_id, chunk.chunk_index - 1, chunk.chunk_index + 1),
                    ).fetchall()
                    for row in neighbor_rows:
                        neighbor = self._row_to_chunk(row)
                        expanded[neighbor.id] = neighbor

        ordered = sorted(expanded.values(), key=lambda item: (item.document_title, item.chunk_type != "parent_text", item.chunk_index))
        rendered: List[Dict[str, Any]] = []
        used = 0
        truncated = False
        for chunk in ordered:
            content = chunk.content.strip()
            if used + len(content) > max_chars:
                content = content[: max(0, max_chars - used)].rstrip()
                truncated = True
            if not content:
                break
            used += len(content)
            rendered.append(self.chunk_to_dict(chunk, content_override=content))
            if used >= max_chars:
                truncated = True
                break
        return {
            "count": len(rendered),
            "truncated": truncated,
            "chunks": rendered,
            "usage": f"{used}/{max_chars} chars",
            "citation_guidance": (
                "Cite document title plus section_path/chunk_index/source_uri in the final answer. "
                "Do not expose internal chunk IDs unless the user asks for implementation details."
            ),
        }

    def upsert_wiki_page(
        self,
        *,
        workspace_id: str,
        slug: str,
        title: str,
        page_type: str = "concept",
        summary: str = "",
        content: str = "",
        aliases: Optional[List[str]] = None,
        status: str = "active",
        source_refs: Optional[List[Dict[str, Any]]] = None,
        chunk_refs: Optional[List[Dict[str, Any]]] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> WikiPageRecord:
        workspace_id = workspace_id.strip() or "default"
        clean_slug = _slugify(slug or title)
        if not clean_slug:
            raise ValueError("slug or title is required")
        clean_title = title.strip() or clean_slug.replace("-", " ").title()
        links = sorted(set(_extract_wiki_links(content)))
        now = utc_now()
        with self._connection() as conn:
            row = conn.execute(
                "SELECT * FROM wiki_pages WHERE workspace_id = ? AND slug = ?",
                (workspace_id, clean_slug),
            ).fetchone()
            if row:
                current = self._row_to_wiki(row)
                new_version = current.version + 1
                conn.execute(
                    """
                    INSERT INTO wiki_page_revisions
                        (page_id, workspace_id, slug, version, title, summary, content,
                         source_refs_json, chunk_refs_json, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        current.id,
                        workspace_id,
                        current.slug,
                        current.version,
                        current.title,
                        current.summary,
                        current.content,
                        json_dumps(current.source_refs),
                        json_dumps(current.chunk_refs),
                        now,
                    ),
                )
                conn.execute(
                    """
                    UPDATE wiki_pages
                    SET title = ?, page_type = ?, summary = ?, content = ?, aliases_json = ?,
                        status = ?, source_refs_json = ?, chunk_refs_json = ?, links_json = ?,
                        metadata_json = ?, version = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    (
                        clean_title,
                        page_type.strip() or "concept",
                        summary.strip(),
                        content.strip(),
                        json_dumps(_clean_list(aliases)),
                        status.strip() or "active",
                        json_dumps(source_refs or []),
                        json_dumps(chunk_refs or []),
                        json_dumps(links),
                        json_dumps(metadata or {}),
                        new_version,
                        now,
                        current.id,
                    ),
                )
                page_id = current.id
            else:
                page_id = f"wiki_{uuid.uuid4().hex}"
                conn.execute(
                    """
                    INSERT INTO wiki_pages
                        (id, workspace_id, slug, title, page_type, summary, content,
                         aliases_json, status, source_refs_json, chunk_refs_json,
                         links_json, metadata_json, version, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)
                    """,
                    (
                        page_id,
                        workspace_id,
                        clean_slug,
                        clean_title,
                        page_type.strip() or "concept",
                        summary.strip(),
                        content.strip(),
                        json_dumps(_clean_list(aliases)),
                        status.strip() or "active",
                        json_dumps(source_refs or []),
                        json_dumps(chunk_refs or []),
                        json_dumps(links),
                        json_dumps(metadata or {}),
                        now,
                        now,
                    ),
                )
            row = conn.execute("SELECT * FROM wiki_pages WHERE id = ?", (page_id,)).fetchone()
            return self._row_to_wiki(row)

    def search_wiki(self, *, workspace_id: str, query: str, limit: int = 8, page_type: str = "") -> List[WikiPageRecord]:
        query = query.strip()
        limit = max(1, min(limit, 30))
        where = ["workspace_id = ?", "status != 'deleted'"]
        params: List[Any] = [workspace_id]
        if page_type.strip():
            where.append("page_type = ?")
            params.append(page_type.strip())
        if query:
            pattern = f"%{query}%"
            where.append("(slug LIKE ? OR title LIKE ? OR summary LIKE ? OR content LIKE ? OR aliases_json LIKE ?)")
            params.extend([pattern, pattern, pattern, pattern, pattern])
        params.append(limit)
        with self._connection() as conn:
            rows = conn.execute(
                f"""
                SELECT * FROM wiki_pages
                WHERE {' AND '.join(where)}
                ORDER BY updated_at DESC, version DESC
                LIMIT ?
                """,
                params,
            ).fetchall()
        return [self._row_to_wiki(row) for row in rows]

    def read_wiki(self, *, workspace_id: str, slugs: List[str], include_linked: bool = True) -> Dict[str, Any]:
        clean_slugs = [_slugify(slug) for slug in slugs if _slugify(slug)]
        pages: List[WikiPageRecord] = []
        linked: List[WikiPageRecord] = []
        with self._connection() as conn:
            for slug in clean_slugs:
                row = conn.execute(
                    "SELECT * FROM wiki_pages WHERE workspace_id = ? AND slug = ? AND status != 'deleted'",
                    (workspace_id, slug),
                ).fetchone()
                if row:
                    pages.append(self._row_to_wiki(row))
            if include_linked:
                linked_slugs = sorted({link for page in pages for link in page.links if link not in clean_slugs})[:20]
                for slug in linked_slugs:
                    row = conn.execute(
                        "SELECT * FROM wiki_pages WHERE workspace_id = ? AND slug = ? AND status != 'deleted'",
                        (workspace_id, slug),
                    ).fetchone()
                    if row:
                        linked.append(self._row_to_wiki(row))
        return {
            "count": len(pages),
            "pages": [self.wiki_to_dict(page) for page in pages],
            "linked_pages": [
                {"slug": page.slug, "title": page.title, "page_type": page.page_type, "summary": page.summary}
                for page in linked
            ],
            "next_step": "Use knowledge_read on chunk_refs when exact source wording, numbers, or audit evidence are needed.",
        }

    def create_wiki_issue(
        self,
        *,
        workspace_id: str,
        slug: str,
        issue_type: str,
        description: str,
        evidence: str = "",
        created_by: str = "",
        metadata: Optional[Dict[str, Any]] = None,
    ) -> WikiIssueRecord:
        description = description.strip()
        if not description:
            raise ValueError("description is required")
        now = utc_now()
        issue_id = f"issue_{uuid.uuid4().hex}"
        clean_type = issue_type.strip() or "other"
        if clean_type not in {"wrong_fact", "missing_info", "contradiction", "out_of_date", "mixed_entities", "other"}:
            clean_type = "other"
        with self._connection() as conn:
            conn.execute(
                """
                INSERT INTO wiki_issues
                    (id, workspace_id, slug, issue_type, description, evidence,
                     status, created_by, metadata_json, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, 'pending', ?, ?, ?, ?)
                """,
                (
                    issue_id,
                    workspace_id,
                    _slugify(slug) if slug else "",
                    clean_type,
                    description,
                    evidence.strip(),
                    created_by.strip(),
                    json_dumps(metadata or {}),
                    now,
                    now,
                ),
            )
            row = conn.execute("SELECT * FROM wiki_issues WHERE id = ?", (issue_id,)).fetchone()
            return self._row_to_issue(row)

    def update_wiki_issue(self, *, workspace_id: str, issue_id: str, status: str, metadata: Optional[Dict[str, Any]] = None) -> Optional[WikiIssueRecord]:
        clean_status = status.strip() or "pending"
        if clean_status not in {"pending", "resolved", "rejected", "deferred"}:
            clean_status = "pending"
        now = utc_now()
        with self._connection() as conn:
            row = conn.execute("SELECT * FROM wiki_issues WHERE workspace_id = ? AND id = ?", (workspace_id, issue_id)).fetchone()
            if not row:
                return None
            merged = self._json(row, "metadata_json", {})
            merged.update(metadata or {})
            conn.execute(
                "UPDATE wiki_issues SET status = ?, metadata_json = ?, updated_at = ? WHERE id = ?",
                (clean_status, json_dumps(merged), now, issue_id),
            )
            row = conn.execute("SELECT * FROM wiki_issues WHERE id = ?", (issue_id,)).fetchone()
            return self._row_to_issue(row)

    def list_wiki_issues(self, *, workspace_id: str, slug: str = "", status: str = "pending", limit: int = 20) -> List[WikiIssueRecord]:
        where = ["workspace_id = ?"]
        params: List[Any] = [workspace_id]
        if slug:
            where.append("slug = ?")
            params.append(_slugify(slug))
        if status:
            where.append("status = ?")
            params.append(status)
        params.append(max(1, min(limit, 100)))
        with self._connection() as conn:
            rows = conn.execute(
                f"SELECT * FROM wiki_issues WHERE {' AND '.join(where)} ORDER BY updated_at DESC LIMIT ?",
                params,
            ).fetchall()
        return [self._row_to_issue(row) for row in rows]

    def record_retrieval_event(
        self,
        *,
        workspace_id: str,
        session_id: str,
        user_id: str,
        query: str,
        tool_name: str,
        result_refs: List[Dict[str, Any]],
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        with self._connection() as conn:
            conn.execute(
                """
                INSERT INTO retrieval_events
                    (workspace_id, session_id, user_id, query, tool_name,
                     result_refs_json, metadata_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    workspace_id,
                    session_id,
                    user_id,
                    query,
                    tool_name,
                    json_dumps(result_refs),
                    json_dumps(metadata or {}),
                    utc_now(),
                ),
            )

    def _search_chunks_fts(
        self,
        *,
        workspace_id: str,
        query: str,
        limit: int,
        knowledge_base: str,
        process: str,
        doc_type: str,
    ) -> List[KnowledgeSearchResult]:
        fts_query = _fts_query(query)
        if not fts_query:
            return []
        rows = self._filtered_fts_rows(
            workspace_id=workspace_id,
            fts_query=fts_query,
            limit=limit,
            knowledge_base=knowledge_base,
            process=process,
            doc_type=doc_type,
        )
        results: List[KnowledgeSearchResult] = []
        for row in rows:
            chunk = self._row_to_chunk(row)
            score = 1.0 / (1.0 + abs(float(row["rank"] or 0.0)))
            results.append(KnowledgeSearchResult(chunk, score, "fts", _snippet_for_terms(chunk, _query_terms(query))))
        return results

    def _filtered_fts_rows(
        self,
        *,
        workspace_id: str,
        fts_query: str,
        limit: int,
        knowledge_base: str,
        process: str,
        doc_type: str,
    ) -> List[sqlite3.Row]:
        where = ["f.workspace_id = ?", "knowledge_chunks_fts MATCH ?"]
        params: List[Any] = [workspace_id, fts_query]
        if knowledge_base:
            where.append("kb.name = ?")
            params.append(knowledge_base)
        if process:
            where.append("d.process = ?")
            params.append(process)
        if doc_type:
            where.append("d.doc_type = ?")
            params.append(doc_type)
        params.append(limit)
        with self._connection() as conn:
            return conn.execute(
                f"""
                SELECT c.*, d.title AS document_title, d.source_uri, d.process, d.doc_type,
                       bm25(knowledge_chunks_fts) AS rank
                FROM knowledge_chunks_fts f
                JOIN knowledge_chunks c ON c.id = f.chunk_id
                JOIN knowledge_documents d ON d.id = c.document_id
                JOIN knowledge_bases kb ON kb.id = c.knowledge_base_id
                WHERE {' AND '.join(where)}
                ORDER BY rank
                LIMIT ?
                """,
                params,
            ).fetchall()

    def _search_chunks_like(
        self,
        *,
        workspace_id: str,
        query: str,
        limit: int,
        knowledge_base: str,
        process: str,
        doc_type: str,
    ) -> List[KnowledgeSearchResult]:
        terms = _query_terms(query)
        rows = self._candidate_chunk_rows(
            workspace_id=workspace_id,
            needle=terms[0] if terms else query,
            max_rows=500,
            knowledge_base=knowledge_base,
            process=process,
            doc_type=doc_type,
        )
        results: List[KnowledgeSearchResult] = []
        for row in rows:
            chunk = self._row_to_chunk(row)
            haystack = " ".join([chunk.document_title, chunk.context_header, chunk.content]).lower()
            score = sum(haystack.count(term.lower()) for term in terms) or 0
            if score:
                results.append(KnowledgeSearchResult(chunk, float(score), "like", _snippet_for_terms(chunk, terms)))
        results.sort(key=lambda item: (-item.score, item.chunk.document_title, item.chunk.chunk_index))
        return results[:limit]

    def _candidate_chunk_rows(
        self,
        *,
        workspace_id: str,
        needle: str,
        max_rows: int,
        knowledge_base: str,
        process: str,
        doc_type: str,
    ) -> List[sqlite3.Row]:
        where = ["c.workspace_id = ?", "c.chunk_type = 'text'"]
        params: List[Any] = [workspace_id]
        if needle:
            pattern = f"%{needle}%"
            where.append("(c.content LIKE ? OR c.context_header LIKE ? OR d.title LIKE ?)")
            params.extend([pattern, pattern, pattern])
        if knowledge_base:
            where.append("kb.name = ?")
            params.append(knowledge_base)
        if process:
            where.append("d.process = ?")
            params.append(process)
        if doc_type:
            where.append("d.doc_type = ?")
            params.append(doc_type)
        params.append(max_rows)
        with self._connection() as conn:
            return conn.execute(
                f"""
                {self._chunk_select_sql()}
                JOIN knowledge_bases kb ON kb.id = c.knowledge_base_id
                WHERE {' AND '.join(where)}
                ORDER BY d.updated_at DESC, c.chunk_index
                LIMIT ?
                """,
                params,
            ).fetchall()

    def _delete_document_chunks(self, conn: sqlite3.Connection, document_id: str) -> None:
        conn.execute("DELETE FROM knowledge_chunks WHERE document_id = ?", (document_id,))
        conn.execute("DELETE FROM knowledge_chunks_fts WHERE document_id = ?", (document_id,))

    def _chunk_row_values(
        self,
        *,
        chunk_id: str,
        workspace_id: str,
        kb_id: str,
        doc_id: str,
        parent_id: str,
        chunk_type: str,
        chunk_index: int,
        content: str,
        context_header: str,
        start: int,
        end: int,
        section_path: str,
        metadata: Dict[str, Any],
        now: str,
    ) -> tuple[Any, ...]:
        content_hash = hashlib.sha256(content.encode("utf-8", errors="replace")).hexdigest()
        return (
            chunk_id,
            workspace_id,
            kb_id,
            doc_id,
            parent_id,
            chunk_type,
            int(chunk_index),
            content,
            context_header,
            int(start),
            int(end),
            0,
            0,
            section_path,
            json_dumps(metadata),
            content_hash,
            now,
        )

    @staticmethod
    def _chunk_select_sql() -> str:
        return """
            SELECT c.*, d.title AS document_title, d.source_uri, d.process, d.doc_type
            FROM knowledge_chunks c
            JOIN knowledge_documents d ON d.id = c.document_id
        """

    @staticmethod
    def _json(row: sqlite3.Row, key: str, default: Any) -> Any:
        return json_loads(str(row[key]), default)

    def _row_to_kb(self, row: sqlite3.Row) -> KnowledgeBaseRecord:
        return KnowledgeBaseRecord(
            id=str(row["id"]),
            workspace_id=str(row["workspace_id"]),
            name=str(row["name"]),
            description=str(row["description"]),
            metadata=self._json(row, "metadata_json", {}),
            created_at=str(row["created_at"]),
            updated_at=str(row["updated_at"]),
        )

    def _row_to_document(self, row: sqlite3.Row) -> KnowledgeDocumentRecord:
        return KnowledgeDocumentRecord(
            id=str(row["id"]),
            workspace_id=str(row["workspace_id"]),
            knowledge_base_id=str(row["knowledge_base_id"]),
            title=str(row["title"]),
            source_type=str(row["source_type"]),
            source_uri=str(row["source_uri"]),
            file_name=str(row["file_name"]),
            file_extension=str(row["file_extension"]),
            content_hash=str(row["content_hash"]),
            doc_type=str(row["doc_type"]),
            process=str(row["process"]),
            tags=[str(item) for item in self._json(row, "tags_json", []) if str(item)],
            metadata=self._json(row, "metadata_json", {}),
            status=str(row["status"]),
            summary=str(row["summary"]),
            created_at=str(row["created_at"]),
            updated_at=str(row["updated_at"]),
        )

    def _row_to_chunk(self, row: sqlite3.Row) -> KnowledgeChunkRecord:
        return KnowledgeChunkRecord(
            id=str(row["id"]),
            workspace_id=str(row["workspace_id"]),
            knowledge_base_id=str(row["knowledge_base_id"]),
            document_id=str(row["document_id"]),
            parent_chunk_id=str(row["parent_chunk_id"]),
            chunk_type=str(row["chunk_type"]),
            chunk_index=int(row["chunk_index"]),
            content=str(row["content"]),
            context_header=str(row["context_header"]),
            start_offset=int(row["start_offset"]),
            end_offset=int(row["end_offset"]),
            page_start=int(row["page_start"]),
            page_end=int(row["page_end"]),
            section_path=str(row["section_path"]),
            metadata=self._json(row, "metadata_json", {}),
            content_hash=str(row["content_hash"]),
            created_at=str(row["created_at"]),
            document_title=str(row["document_title"] if "document_title" in row.keys() else ""),
            source_uri=str(row["source_uri"] if "source_uri" in row.keys() else ""),
            process=str(row["process"] if "process" in row.keys() else ""),
            doc_type=str(row["doc_type"] if "doc_type" in row.keys() else ""),
        )

    def _row_to_wiki(self, row: sqlite3.Row) -> WikiPageRecord:
        return WikiPageRecord(
            id=str(row["id"]),
            workspace_id=str(row["workspace_id"]),
            slug=str(row["slug"]),
            title=str(row["title"]),
            page_type=str(row["page_type"]),
            summary=str(row["summary"]),
            content=str(row["content"]),
            aliases=[str(item) for item in self._json(row, "aliases_json", []) if str(item)],
            status=str(row["status"]),
            source_refs=list(self._json(row, "source_refs_json", [])),
            chunk_refs=list(self._json(row, "chunk_refs_json", [])),
            links=[str(item) for item in self._json(row, "links_json", []) if str(item)],
            metadata=self._json(row, "metadata_json", {}),
            version=int(row["version"]),
            created_at=str(row["created_at"]),
            updated_at=str(row["updated_at"]),
        )

    def _row_to_issue(self, row: sqlite3.Row) -> WikiIssueRecord:
        return WikiIssueRecord(
            id=str(row["id"]),
            workspace_id=str(row["workspace_id"]),
            slug=str(row["slug"]),
            issue_type=str(row["issue_type"]),
            description=str(row["description"]),
            evidence=str(row["evidence"]),
            status=str(row["status"]),
            created_by=str(row["created_by"]),
            metadata=self._json(row, "metadata_json", {}),
            created_at=str(row["created_at"]),
            updated_at=str(row["updated_at"]),
        )

    @staticmethod
    def document_to_dict(document: KnowledgeDocumentRecord) -> Dict[str, Any]:
        return {
            "id": document.id,
            "title": document.title,
            "knowledge_base_id": document.knowledge_base_id,
            "source_type": document.source_type,
            "source_uri": document.source_uri,
            "file_name": document.file_name,
            "file_extension": document.file_extension,
            "content_hash": document.content_hash,
            "doc_type": document.doc_type,
            "process": document.process,
            "tags": document.tags,
            "status": document.status,
            "summary": document.summary,
            "updated_at": document.updated_at,
        }

    @staticmethod
    def chunk_to_dict(chunk: KnowledgeChunkRecord, *, content_override: Optional[str] = None) -> Dict[str, Any]:
        return {
            "chunk_id": chunk.id,
            "document_id": chunk.document_id,
            "document_title": chunk.document_title,
            "source_uri": chunk.source_uri,
            "process": chunk.process,
            "doc_type": chunk.doc_type,
            "chunk_type": chunk.chunk_type,
            "chunk_index": chunk.chunk_index,
            "parent_chunk_id": chunk.parent_chunk_id,
            "section_path": chunk.section_path,
            "context_header": chunk.context_header,
            "page_start": chunk.page_start or None,
            "page_end": chunk.page_end or None,
            "content": content_override if content_override is not None else chunk.content,
            "citation": _citation(chunk),
        }

    @staticmethod
    def search_result_to_dict(result: KnowledgeSearchResult) -> Dict[str, Any]:
        chunk = result.chunk
        return {
            "chunk_id": chunk.id,
            "document_id": chunk.document_id,
            "document_title": chunk.document_title,
            "source_uri": chunk.source_uri,
            "process": chunk.process,
            "doc_type": chunk.doc_type,
            "chunk_index": chunk.chunk_index,
            "section_path": chunk.section_path,
            "score": round(result.score, 4),
            "match_type": result.match_type,
            "snippet": result.snippet,
            "citation": _citation(chunk),
        }

    @staticmethod
    def wiki_to_dict(page: WikiPageRecord) -> Dict[str, Any]:
        return {
            "id": page.id,
            "slug": page.slug,
            "title": page.title,
            "page_type": page.page_type,
            "summary": page.summary,
            "content": page.content,
            "aliases": page.aliases,
            "status": page.status,
            "source_refs": page.source_refs,
            "chunk_refs": page.chunk_refs,
            "links": page.links,
            "version": page.version,
            "updated_at": page.updated_at,
        }

    @staticmethod
    def issue_to_dict(issue: WikiIssueRecord) -> Dict[str, Any]:
        return {
            "id": issue.id,
            "slug": issue.slug,
            "issue_type": issue.issue_type,
            "description": issue.description,
            "evidence": issue.evidence,
            "status": issue.status,
            "created_by": issue.created_by,
            "metadata": issue.metadata,
            "updated_at": issue.updated_at,
        }


def _query_terms(query: str) -> List[str]:
    return [term.lower() for term in re.findall(r"[A-Za-z0-9_./-]{2,}|[\u4e00-\u9fff]{2,}", query or "")]


def _fts_query(query: str) -> str:
    terms = _query_terms(query)
    if not terms:
        return ""
    quoted = []
    for term in terms[:12]:
        escaped = term.replace('"', '""')
        quoted.append(f'"{escaped}"')
    return " OR ".join(quoted)


def _snippet_for_terms(chunk: KnowledgeChunkRecord, terms: Sequence[str]) -> str:
    haystack = "\n".join([chunk.document_title, chunk.context_header, chunk.content])
    lowered = haystack.lower()
    for term in terms:
        index = lowered.find(term.lower())
        if index >= 0:
            return _snippet(haystack, index, index + len(term))
    return haystack[:700].strip()


def _snippet(text: str, start: int, end: int, radius: int = 280) -> str:
    left = max(0, start - radius)
    right = min(len(text), end + radius)
    prefix = "..." if left > 0 else ""
    suffix = "..." if right < len(text) else ""
    return (prefix + text[left:right].strip() + suffix).replace("\n", " ")


def _citation(chunk: KnowledgeChunkRecord) -> str:
    pieces = [chunk.document_title or chunk.document_id]
    if chunk.section_path:
        pieces.append(chunk.section_path)
    pieces.append(f"chunk {chunk.chunk_index}")
    if chunk.source_uri:
        pieces.append(chunk.source_uri)
    return " | ".join(pieces)


def _section_path(context_header: str) -> str:
    lines = [line.strip("# ").strip() for line in context_header.splitlines() if line.strip()]
    return " > ".join(lines)


def _clean_list(items: Optional[List[str]]) -> List[str]:
    return [str(item).strip() for item in items or [] if str(item).strip()]


def _extract_wiki_links(content: str) -> List[str]:
    return [_slugify(match.group(1)) for match in LINK_RE.finditer(content or "") if _slugify(match.group(1))]


def _slugify(value: str) -> str:
    value = (value or "").strip().lower()
    value = re.sub(r"[^a-z0-9_.:-]+", "-", value)
    value = re.sub(r"-{2,}", "-", value).strip("-")
    return value[:120]
