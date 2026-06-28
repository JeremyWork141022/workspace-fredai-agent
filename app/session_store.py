from __future__ import annotations

import json
import re
import sqlite3
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional

from app.config import ensure_dirs, state_db_path


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def json_loads(value: str, default: Any) -> Any:
    try:
        return json.loads(value)
    except Exception:
        return default


def extract_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, dict):
        return json.dumps(content, ensure_ascii=False)
    if not isinstance(content, list):
        return ""
    parts: List[str] = []
    for item in content:
        if isinstance(item, str):
            parts.append(item)
            continue
        if not isinstance(item, dict):
            continue
        text = item.get("text") or item.get("content")
        if text:
            parts.append(str(text))
        elif item.get("type") in {"image_url", "input_image"}:
            parts.append("[image input]")
    return "\n".join(parts).strip()


@dataclass
class SessionRecord:
    id: str
    workspace_id: str
    user_id: str
    title: str
    created_at: str
    updated_at: str
    metadata: Dict[str, Any]


@dataclass
class MessageRecord:
    id: int
    session_id: str
    role: str
    content: Any
    text: str
    name: str
    tool_call_id: str
    created_at: str
    metadata: Dict[str, Any]


@dataclass
class SessionSearchResult:
    message_id: int
    session_id: str
    role: str
    text: str
    snippet: str
    created_at: str
    workspace_id: str
    user_id: str
    session_title: str
    context: List[Dict[str, str]]


class SessionStore:
    """SQLite-backed sessions, messages, request metrics, and traces."""

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
                CREATE TABLE IF NOT EXISTS sessions (
                    id TEXT PRIMARY KEY,
                    workspace_id TEXT NOT NULL,
                    user_id TEXT NOT NULL,
                    title TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    metadata_json TEXT NOT NULL DEFAULT '{}'
                )
                """
            )
            conn.execute("CREATE INDEX IF NOT EXISTS idx_sessions_workspace_user ON sessions(workspace_id, user_id, updated_at)")
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
                    role TEXT NOT NULL,
                    content_json TEXT NOT NULL,
                    text TEXT NOT NULL DEFAULT '',
                    name TEXT NOT NULL DEFAULT '',
                    tool_call_id TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    metadata_json TEXT NOT NULL DEFAULT '{}'
                )
                """
            )
            conn.execute("CREATE INDEX IF NOT EXISTS idx_messages_session_id ON messages(session_id, id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_messages_text ON messages(text)")
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS request_metrics (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    request_id TEXT NOT NULL DEFAULT '',
                    session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
                    workspace_id TEXT NOT NULL,
                    user_id TEXT NOT NULL,
                    user_message_id INTEGER,
                    assistant_message_id INTEGER,
                    started_at TEXT NOT NULL,
                    finished_at TEXT NOT NULL,
                    duration_ms INTEGER NOT NULL,
                    status TEXT NOT NULL,
                    tool_call_count INTEGER NOT NULL DEFAULT 0,
                    tool_names_json TEXT NOT NULL DEFAULT '[]',
                    progress_messages_json TEXT NOT NULL DEFAULT '[]',
                    error TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL
                )
                """
            )
            conn.execute("CREATE INDEX IF NOT EXISTS idx_request_metrics_session ON request_metrics(session_id, id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_request_metrics_request ON request_metrics(request_id)")
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS api_call_traces (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    request_id TEXT NOT NULL,
                    session_id TEXT NOT NULL,
                    workspace_id TEXT NOT NULL,
                    user_id TEXT NOT NULL,
                    event_index INTEGER NOT NULL,
                    event_type TEXT NOT NULL,
                    title TEXT NOT NULL DEFAULT '',
                    payload_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL
                )
                """
            )
            conn.execute("CREATE INDEX IF NOT EXISTS idx_api_call_traces_request ON api_call_traces(request_id, event_index)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_api_call_traces_type ON api_call_traces(event_type, id)")
            self._ensure_fts(conn)
            self._rebuild_fts(conn)

    def _ensure_fts(self, conn: sqlite3.Connection) -> None:
        conn.execute("CREATE VIRTUAL TABLE IF NOT EXISTS messages_fts USING fts5(content)")
        conn.execute(
            """
            CREATE TRIGGER IF NOT EXISTS messages_fts_insert AFTER INSERT ON messages BEGIN
                INSERT INTO messages_fts(rowid, content)
                VALUES (new.id, COALESCE(new.text, '') || ' ' || COALESCE(new.name, ''));
            END;
            """
        )
        conn.execute(
            """
            CREATE TRIGGER IF NOT EXISTS messages_fts_delete AFTER DELETE ON messages BEGIN
                DELETE FROM messages_fts WHERE rowid = old.id;
            END;
            """
        )
        conn.execute(
            """
            CREATE TRIGGER IF NOT EXISTS messages_fts_update AFTER UPDATE ON messages BEGIN
                DELETE FROM messages_fts WHERE rowid = old.id;
                INSERT INTO messages_fts(rowid, content)
                VALUES (new.id, COALESCE(new.text, '') || ' ' || COALESCE(new.name, ''));
            END;
            """
        )
        try:
            conn.execute("CREATE VIRTUAL TABLE IF NOT EXISTS messages_fts_trigram USING fts5(content, tokenize='trigram')")
            conn.execute(
                """
                CREATE TRIGGER IF NOT EXISTS messages_fts_trigram_insert AFTER INSERT ON messages BEGIN
                    INSERT INTO messages_fts_trigram(rowid, content)
                    VALUES (new.id, COALESCE(new.text, '') || ' ' || COALESCE(new.name, ''));
                END;
                """
            )
            conn.execute(
                """
                CREATE TRIGGER IF NOT EXISTS messages_fts_trigram_delete AFTER DELETE ON messages BEGIN
                    DELETE FROM messages_fts_trigram WHERE rowid = old.id;
                END;
                """
            )
            conn.execute(
                """
                CREATE TRIGGER IF NOT EXISTS messages_fts_trigram_update AFTER UPDATE ON messages BEGIN
                    DELETE FROM messages_fts_trigram WHERE rowid = old.id;
                    INSERT INTO messages_fts_trigram(rowid, content)
                    VALUES (new.id, COALESCE(new.text, '') || ' ' || COALESCE(new.name, ''));
                END;
                """
            )
        except sqlite3.OperationalError:
            pass

    def _rebuild_fts(self, conn: sqlite3.Connection) -> None:
        try:
            conn.execute("DELETE FROM messages_fts")
            conn.execute(
                """
                INSERT INTO messages_fts(rowid, content)
                SELECT id, COALESCE(text, '') || ' ' || COALESCE(name, '')
                FROM messages
                """
            )
        except sqlite3.OperationalError:
            pass
        try:
            conn.execute("DELETE FROM messages_fts_trigram")
            conn.execute(
                """
                INSERT INTO messages_fts_trigram(rowid, content)
                SELECT id, COALESCE(text, '') || ' ' || COALESCE(name, '')
                FROM messages
                """
            )
        except sqlite3.OperationalError:
            pass

    def get_or_create_session(
        self,
        *,
        workspace_id: str,
        user_id: str,
        session_id: Optional[str] = None,
        title: str = "",
        metadata: Optional[Dict[str, Any]] = None,
    ) -> SessionRecord:
        workspace_id = workspace_id.strip() or "default"
        user_id = user_id.strip() or "unknown"
        now = utc_now()
        with self._connection() as conn:
            if session_id:
                existing = conn.execute("SELECT * FROM sessions WHERE id = ?", (session_id,)).fetchone()
                if existing:
                    conn.execute("UPDATE sessions SET updated_at = ? WHERE id = ?", (now, session_id))
                    return self._row_to_session(conn.execute("SELECT * FROM sessions WHERE id = ?", (session_id,)).fetchone())

            session_id = session_id.strip() if session_id else f"sess_{uuid.uuid4().hex}"
            conn.execute(
                """
                INSERT INTO sessions (id, workspace_id, user_id, title, created_at, updated_at, metadata_json)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (session_id, workspace_id, user_id, title, now, now, json_dumps(metadata or {})),
            )
            row = conn.execute("SELECT * FROM sessions WHERE id = ?", (session_id,)).fetchone()
            return self._row_to_session(row)

    def get_session(self, session_id: str) -> Optional[SessionRecord]:
        session_id = session_id.strip()
        if not session_id:
            return None
        with self._connection() as conn:
            row = conn.execute("SELECT * FROM sessions WHERE id = ?", (session_id,)).fetchone()
        return self._row_to_session(row) if row else None

    def rename_session(self, session_id: str, title: str) -> Optional[SessionRecord]:
        session_id = session_id.strip()
        clean_title = " ".join(title.strip().split())[:120]
        if not session_id or not clean_title:
            return None
        now = utc_now()
        with self._connection() as conn:
            existing = conn.execute("SELECT id FROM sessions WHERE id = ?", (session_id,)).fetchone()
            if not existing:
                return None
            conn.execute(
                """
                UPDATE sessions
                SET title = ?, updated_at = ?
                WHERE id = ?
                """,
                (clean_title, now, session_id),
            )
            row = conn.execute("SELECT * FROM sessions WHERE id = ?", (session_id,)).fetchone()
        return self._row_to_session(row) if row else None

    def append_message(
        self,
        *,
        session_id: str,
        role: str,
        content: Any,
        name: str = "",
        tool_call_id: str = "",
        metadata: Optional[Dict[str, Any]] = None,
    ) -> MessageRecord:
        now = utc_now()
        text = extract_text(content)
        with self._connection() as conn:
            cursor = conn.execute(
                """
                INSERT INTO messages
                    (session_id, role, content_json, text, name, tool_call_id, created_at, metadata_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (session_id, role, json_dumps(content), text, name, tool_call_id, now, json_dumps(metadata or {})),
            )
            conn.execute("UPDATE sessions SET updated_at = ? WHERE id = ?", (now, session_id))
            row = conn.execute("SELECT * FROM messages WHERE id = ?", (cursor.lastrowid,)).fetchone()
            return self._row_to_message(row)

    def record_request_metric(
        self,
        *,
        request_id: str,
        session_id: str,
        workspace_id: str,
        user_id: str,
        user_message_id: Optional[int],
        assistant_message_id: Optional[int],
        started_at: str,
        finished_at: str,
        duration_ms: int,
        status: str,
        tool_names: Optional[List[str]] = None,
        progress_messages: Optional[List[str]] = None,
        error: str = "",
    ) -> None:
        with self._connection() as conn:
            conn.execute(
                """
                INSERT INTO request_metrics
                    (request_id, session_id, workspace_id, user_id, user_message_id, assistant_message_id,
                     started_at, finished_at, duration_ms, status, tool_call_count,
                     tool_names_json, progress_messages_json, error, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    request_id,
                    session_id,
                    workspace_id,
                    user_id,
                    user_message_id,
                    assistant_message_id,
                    started_at,
                    finished_at,
                    max(0, int(duration_ms)),
                    status,
                    len(tool_names or []),
                    json_dumps(tool_names or []),
                    json_dumps(progress_messages or []),
                    error[:1000],
                    utc_now(),
                ),
            )

    def record_trace_event(
        self,
        *,
        request_id: str,
        session_id: str,
        workspace_id: str,
        user_id: str,
        event_index: int,
        event_type: str,
        title: str,
        payload: Any,
    ) -> None:
        with self._connection() as conn:
            conn.execute(
                """
                INSERT INTO api_call_traces
                    (request_id, session_id, workspace_id, user_id, event_index,
                     event_type, title, payload_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (request_id, session_id, workspace_id, user_id, event_index, event_type, title, json_dumps(payload), utc_now()),
            )

    def recent_messages(self, session_id: str, *, limit: int = 20) -> List[MessageRecord]:
        with self._connection() as conn:
            rows = conn.execute(
                """
                SELECT * FROM messages
                WHERE session_id = ?
                ORDER BY id DESC
                LIMIT ?
                """,
                (session_id, max(1, limit)),
            ).fetchall()
        return [self._row_to_message(row) for row in reversed(rows)]

    def recent_model_messages(self, session_id: str, *, limit: int = 16) -> List[Dict[str, Any]]:
        messages: List[Dict[str, Any]] = []
        for message in self.recent_messages(session_id, limit=limit):
            if message.role in {"user", "assistant"}:
                messages.append({"role": message.role, "content": message.content})
        return messages

    def list_sessions(self, *, workspace_id: str = "", user_id: str = "", limit: int = 20) -> List[SessionRecord]:
        where: List[str] = []
        params: List[Any] = []
        if workspace_id:
            where.append("workspace_id = ?")
            params.append(workspace_id)
        if user_id:
            where.append("user_id = ?")
            params.append(user_id)
        params.append(max(1, limit))
        clause = "WHERE " + " AND ".join(where) if where else ""
        with self._connection() as conn:
            rows = conn.execute(
                f"""
                SELECT * FROM sessions
                {clause}
                ORDER BY updated_at DESC
                LIMIT ?
                """,
                params,
            ).fetchall()
        return [self._row_to_session(row) for row in rows]

    def get_trace_events(self, request_id: str) -> List[Dict[str, Any]]:
        with self._connection() as conn:
            rows = conn.execute(
                """
                SELECT * FROM api_call_traces
                WHERE request_id = ?
                ORDER BY event_index ASC
                """,
                (request_id,),
            ).fetchall()
        return [
            {
                "event_index": int(row["event_index"]),
                "event_type": str(row["event_type"]),
                "title": str(row["title"]),
                "payload": json_loads(str(row["payload_json"]), {}),
                "created_at": str(row["created_at"]),
            }
            for row in rows
        ]

    def search_message_context(
        self,
        *,
        query: str,
        session_id: Optional[str] = None,
        workspace_id: Optional[str] = None,
        role_filter: Optional[List[str]] = None,
        limit: int = 10,
    ) -> List[SessionSearchResult]:
        query = query.strip()
        if not query:
            return []
        with self._connection() as conn:
            try:
                rows = self._search_message_context_fts(
                    conn,
                    query=query,
                    session_id=session_id,
                    workspace_id=workspace_id,
                    role_filter=role_filter,
                    limit=limit,
                )
            except sqlite3.OperationalError:
                rows = self._search_message_context_like(
                    conn,
                    query=query,
                    session_id=session_id,
                    workspace_id=workspace_id,
                    role_filter=role_filter,
                    limit=limit,
                )
            return [self._row_to_search_result(conn, row) for row in rows]

    def get_messages_as_conversation(self, session_id: str, *, limit: int = 300) -> List[Dict[str, str]]:
        conversation: List[Dict[str, str]] = []
        for message in self.recent_messages(session_id, limit=limit):
            if message.role in {"user", "assistant"}:
                conversation.append({"role": message.role, "content": message.text})
            elif message.role in {"tool_call", "tool_result"}:
                label = message.name or message.role
                conversation.append({"role": message.role, "content": f"[{label}] {message.text}"})
        return conversation

    def _search_message_context_fts(
        self,
        conn: sqlite3.Connection,
        *,
        query: str,
        session_id: Optional[str],
        workspace_id: Optional[str],
        role_filter: Optional[List[str]],
        limit: int,
    ) -> List[sqlite3.Row]:
        fts_query = self._sanitize_fts_query(query)
        if not fts_query:
            return []
        where = ["messages_fts MATCH ?"]
        params: List[Any] = [fts_query]
        if session_id:
            where.append("m.session_id = ?")
            params.append(session_id)
        if workspace_id:
            where.append("s.workspace_id = ?")
            params.append(workspace_id)
        if role_filter:
            placeholders = ",".join("?" for _ in role_filter)
            where.append(f"m.role IN ({placeholders})")
            params.extend(role_filter)
        table = "messages_fts"
        if self._contains_cjk(query) and self._trigram_available(conn):
            table = "messages_fts_trigram"
            where[0] = f"{table} MATCH ?"
            params[0] = '"' + query.replace('"', '""') + '"'
        params.append(max(1, limit))
        return conn.execute(
            f"""
            SELECT
                m.id, m.session_id, m.role, m.text, m.created_at,
                s.workspace_id, s.user_id, s.title,
                snippet({table}, 0, '>>>', '<<<', '...', 32) AS snippet
            FROM {table}
            JOIN messages m ON m.id = {table}.rowid
            JOIN sessions s ON s.id = m.session_id
            WHERE {' AND '.join(where)}
            ORDER BY rank
            LIMIT ?
            """,
            params,
        ).fetchall()

    def _search_message_context_like(
        self,
        conn: sqlite3.Connection,
        *,
        query: str,
        session_id: Optional[str],
        workspace_id: Optional[str],
        role_filter: Optional[List[str]],
        limit: int,
    ) -> List[sqlite3.Row]:
        where = ["m.text LIKE ?"]
        params: List[Any] = [f"%{query}%"]
        if session_id:
            where.append("m.session_id = ?")
            params.append(session_id)
        if workspace_id:
            where.append("s.workspace_id = ?")
            params.append(workspace_id)
        if role_filter:
            placeholders = ",".join("?" for _ in role_filter)
            where.append(f"m.role IN ({placeholders})")
            params.extend(role_filter)
        params.append(max(1, limit))
        return conn.execute(
            f"""
            SELECT
                m.id, m.session_id, m.role, m.text, m.created_at,
                s.workspace_id, s.user_id, s.title,
                substr(m.text, max(1, instr(lower(m.text), lower(?)) - 32), 160) AS snippet
            FROM messages m
            JOIN sessions s ON s.id = m.session_id
            WHERE {' AND '.join(where)}
            ORDER BY m.id DESC
            LIMIT ?
            """,
            [query] + params,
        ).fetchall()

    def _row_to_search_result(self, conn: sqlite3.Connection, row: sqlite3.Row) -> SessionSearchResult:
        context_rows = conn.execute(
            """
            SELECT role, text
            FROM messages
            WHERE session_id = ? AND id BETWEEN ? AND ?
            ORDER BY id ASC
            """,
            (row["session_id"], int(row["id"]) - 1, int(row["id"]) + 1),
        ).fetchall()
        return SessionSearchResult(
            message_id=int(row["id"]),
            session_id=str(row["session_id"]),
            role=str(row["role"]),
            text=str(row["text"]),
            snippet=str(row["snippet"] or row["text"] or "")[:500],
            created_at=str(row["created_at"]),
            workspace_id=str(row["workspace_id"]),
            user_id=str(row["user_id"]),
            session_title=str(row["title"]),
            context=[{"role": str(item["role"]), "content": str(item["text"])[:300]} for item in context_rows],
        )

    @staticmethod
    def _sanitize_fts_query(query: str) -> str:
        query = query.strip()
        quoted = re.findall(r'"[^"]+"', query)
        scrubbed = re.sub(r'"[^"]+"', " ", query)
        scrubbed = re.sub(r"[+{}()^~:/\\]", " ", scrubbed)
        tokens = [token.strip("*") for token in scrubbed.split() if token.strip("*")]
        parts = quoted[:]
        for token in tokens:
            upper = token.upper()
            if upper in {"AND", "OR", "NOT"}:
                continue
            if re.search(r"[-.]", token):
                parts.append('"' + token.replace('"', '""') + '"')
            else:
                parts.append(token)
        return " OR ".join(parts[:12])

    @staticmethod
    def _contains_cjk(text: str) -> bool:
        return any(
            "\u4e00" <= ch <= "\u9fff"
            or "\u3400" <= ch <= "\u4dbf"
            or "\u3040" <= ch <= "\u30ff"
            or "\uac00" <= ch <= "\ud7af"
            for ch in text
        )

    @staticmethod
    def _trigram_available(conn: sqlite3.Connection) -> bool:
        try:
            conn.execute("SELECT rowid FROM messages_fts_trigram LIMIT 0")
            return True
        except sqlite3.OperationalError:
            return False

    @staticmethod
    def _row_to_session(row: sqlite3.Row) -> SessionRecord:
        return SessionRecord(
            id=str(row["id"]),
            workspace_id=str(row["workspace_id"]),
            user_id=str(row["user_id"]),
            title=str(row["title"]),
            created_at=str(row["created_at"]),
            updated_at=str(row["updated_at"]),
            metadata=json_loads(str(row["metadata_json"]), {}),
        )

    @staticmethod
    def _row_to_message(row: sqlite3.Row) -> MessageRecord:
        return MessageRecord(
            id=int(row["id"]),
            session_id=str(row["session_id"]),
            role=str(row["role"]),
            content=json_loads(str(row["content_json"]), ""),
            text=str(row["text"]),
            name=str(row["name"]),
            tool_call_id=str(row["tool_call_id"]),
            created_at=str(row["created_at"]),
            metadata=json_loads(str(row["metadata_json"]), {}),
        )
