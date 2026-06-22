from __future__ import annotations

import re
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional

from app.config import ensure_dirs, state_db_path
from app.session_store import json_dumps, json_loads, utc_now


@dataclass
class MemoryRecord:
    id: int
    scope: str
    key: str
    value: str
    tags: List[str]
    source: str
    created_at: str
    updated_at: str


@dataclass
class MemoryTurnRecord:
    id: int
    session_id: str
    workspace_id: str
    user_id: str
    user_text: str
    assistant_text: str
    created_at: str


@dataclass
class RoutineRuleRecord:
    id: int
    workspace_id: str
    user_id: str
    rule_type: str
    title: str
    trigger_text: str
    action_text: str
    status: str
    source_request: str
    metadata: Dict[str, Any]
    created_at: str
    updated_at: str


@dataclass
class WorkspaceNoteRecord:
    id: int
    workspace_id: str
    title: str
    body: str
    source: str
    tags: List[str]
    created_at: str
    updated_at: str


class MemoryStore:
    """Generic SQLite memory primitives and workspace-domain notes."""

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
                CREATE TABLE IF NOT EXISTS memories (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    scope TEXT NOT NULL,
                    key TEXT NOT NULL,
                    value TEXT NOT NULL,
                    tags_json TEXT NOT NULL DEFAULT '[]',
                    source TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(scope, key)
                )
                """
            )
            conn.execute("CREATE INDEX IF NOT EXISTS idx_memories_scope ON memories(scope)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_memories_text ON memories(key, value)")
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS memory_turns (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT NOT NULL,
                    workspace_id TEXT NOT NULL,
                    user_id TEXT NOT NULL,
                    user_text TEXT NOT NULL DEFAULT '',
                    assistant_text TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL
                )
                """
            )
            conn.execute("CREATE INDEX IF NOT EXISTS idx_memory_turns_workspace ON memory_turns(workspace_id, user_id, id)")
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS routine_rules (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    workspace_id TEXT NOT NULL,
                    user_id TEXT NOT NULL,
                    rule_type TEXT NOT NULL,
                    title TEXT NOT NULL,
                    trigger_text TEXT NOT NULL DEFAULT '',
                    action_text TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL DEFAULT 'active',
                    source_request TEXT NOT NULL DEFAULT '',
                    metadata_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            conn.execute("CREATE INDEX IF NOT EXISTS idx_routine_rules_owner ON routine_rules(workspace_id, user_id, status)")
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS workspace_notes (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    workspace_id TEXT NOT NULL,
                    title TEXT NOT NULL,
                    body TEXT NOT NULL DEFAULT '',
                    source TEXT NOT NULL DEFAULT '',
                    tags_json TEXT NOT NULL DEFAULT '[]',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            conn.execute("CREATE INDEX IF NOT EXISTS idx_workspace_notes_workspace ON workspace_notes(workspace_id, updated_at)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_workspace_notes_text ON workspace_notes(title, body, tags_json)")

    def remember(
        self,
        *,
        scope: str,
        key: str,
        value: str,
        tags: Optional[List[str]] = None,
        source: str = "",
    ) -> MemoryRecord:
        scope = scope.strip() or "global"
        key = key.strip()
        value = value.strip()
        if not key:
            raise ValueError("memory key is required")
        if not value:
            raise ValueError("memory value is required")
        clean_tags = [str(tag).strip() for tag in tags or [] if str(tag).strip()]
        now = utc_now()
        with self._connection() as conn:
            existing = conn.execute("SELECT * FROM memories WHERE scope = ? AND key = ?", (scope, key)).fetchone()
            if existing:
                conn.execute(
                    """
                    UPDATE memories
                    SET value = ?, tags_json = ?, source = ?, updated_at = ?
                    WHERE scope = ? AND key = ?
                    """,
                    (value, json_dumps(clean_tags), source, now, scope, key),
                )
                row = conn.execute("SELECT * FROM memories WHERE scope = ? AND key = ?", (scope, key)).fetchone()
                return self._row_to_memory(row)
            cursor = conn.execute(
                """
                INSERT INTO memories (scope, key, value, tags_json, source, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (scope, key, value, json_dumps(clean_tags), source, now, now),
            )
            row = conn.execute("SELECT * FROM memories WHERE id = ?", (cursor.lastrowid,)).fetchone()
            return self._row_to_memory(row)

    def search(self, *, query: str = "", scopes: Optional[List[str]] = None, limit: int = 10) -> List[MemoryRecord]:
        query = query.strip()
        scopes = [scope.strip() for scope in scopes or [] if scope.strip()]
        where: List[str] = []
        params: List[Any] = []
        if scopes:
            where.append(f"scope IN ({','.join('?' for _ in scopes)})")
            params.extend(scopes)
        if query:
            pattern = f"%{query}%"
            where.append("(key LIKE ? OR value LIKE ? OR tags_json LIKE ?)")
            params.extend([pattern, pattern, pattern])
        params.append(max(1, limit))
        clause = "WHERE " + " AND ".join(where) if where else ""
        with self._connection() as conn:
            rows = conn.execute(
                f"""
                SELECT * FROM memories
                {clause}
                ORDER BY updated_at DESC, id DESC
                LIMIT ?
                """,
                params,
            ).fetchall()
        return [self._row_to_memory(row) for row in rows]

    def record_turn(
        self,
        *,
        session_id: str,
        workspace_id: str,
        user_id: str,
        user_text: str,
        assistant_text: str,
    ) -> MemoryTurnRecord:
        now = utc_now()
        with self._connection() as conn:
            cursor = conn.execute(
                """
                INSERT INTO memory_turns (session_id, workspace_id, user_id, user_text, assistant_text, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (session_id, workspace_id, user_id, user_text.strip(), assistant_text.strip(), now),
            )
            row = conn.execute("SELECT * FROM memory_turns WHERE id = ?", (cursor.lastrowid,)).fetchone()
            return self._row_to_turn(row)

    def search_turns(self, *, query: str, workspace_id: str, user_id: str = "", limit: int = 5) -> List[MemoryTurnRecord]:
        query = query.strip()
        if not query:
            return []
        where = ["workspace_id = ?", "(user_text LIKE ? OR assistant_text LIKE ?)"]
        params: List[Any] = [workspace_id, f"%{query}%", f"%{query}%"]
        if user_id:
            where.append("user_id = ?")
            params.append(user_id)
        params.append(max(1, limit))
        with self._connection() as conn:
            rows = conn.execute(
                f"""
                SELECT * FROM memory_turns
                WHERE {' AND '.join(where)}
                ORDER BY id DESC
                LIMIT ?
                """,
                params,
            ).fetchall()
        return [self._row_to_turn(row) for row in rows]

    def save_routine_rule(
        self,
        *,
        workspace_id: str,
        user_id: str,
        rule_type: str,
        title: str,
        trigger_text: str = "",
        action_text: str = "",
        source_request: str = "",
        status: str = "active",
        metadata: Optional[Dict[str, Any]] = None,
    ) -> RoutineRuleRecord:
        title = title.strip()
        if not title:
            raise ValueError("routine rule title is required")
        clean_status = status.strip().lower() or "active"
        if clean_status not in {"active", "paused", "planned", "dismissed"}:
            clean_status = "active"
        now = utc_now()
        with self._connection() as conn:
            cursor = conn.execute(
                """
                INSERT INTO routine_rules
                    (workspace_id, user_id, rule_type, title, trigger_text, action_text,
                     status, source_request, metadata_json, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    workspace_id.strip() or "default",
                    user_id.strip() or "unknown",
                    rule_type.strip() or "sqlite_memory",
                    title,
                    trigger_text.strip(),
                    action_text.strip(),
                    clean_status,
                    source_request.strip(),
                    json_dumps(metadata or {}),
                    now,
                    now,
                ),
            )
            row = conn.execute("SELECT * FROM routine_rules WHERE id = ?", (cursor.lastrowid,)).fetchone()
            return self._row_to_routine_rule(row)

    def search_routine_rules(
        self,
        *,
        workspace_id: str,
        user_id: str = "",
        query: str = "",
        status: str = "active",
        limit: int = 5,
    ) -> List[RoutineRuleRecord]:
        where = ["workspace_id = ?"]
        params: List[Any] = [workspace_id]
        if user_id:
            where.append("user_id = ?")
            params.append(user_id)
        if status:
            where.append("status = ?")
            params.append(status.strip().lower())
        if query.strip():
            pattern = f"%{query.strip()}%"
            where.append("(title LIKE ? OR trigger_text LIKE ? OR action_text LIKE ? OR source_request LIKE ?)")
            params.extend([pattern, pattern, pattern, pattern])
        params.append(max(1, limit))
        with self._connection() as conn:
            rows = conn.execute(
                f"""
                SELECT * FROM routine_rules
                WHERE {' AND '.join(where)}
                ORDER BY updated_at DESC, id DESC
                LIMIT ?
                """,
                params,
            ).fetchall()
        return [self._row_to_routine_rule(row) for row in rows]

    def triggered_routine_rules(
        self,
        *,
        workspace_id: str,
        user_id: str,
        event: str,
        text: str,
        limit: int = 5,
    ) -> List[RoutineRuleRecord]:
        candidates = [
            rule
            for rule in self.search_routine_rules(workspace_id=workspace_id, user_id=user_id, query="", status="active", limit=50)
            if rule.rule_type == "hook"
        ]
        triggered: List[RoutineRuleRecord] = []
        for rule in candidates:
            hook_event = str(rule.metadata.get("hook_event") or "pre_llm")
            if hook_event != event:
                continue
            if self._rule_matches_text(rule, text):
                triggered.append(rule)
            if len(triggered) >= limit:
                break
        return triggered

    def save_workspace_note(
        self,
        *,
        workspace_id: str,
        title: str,
        body: str,
        source: str = "",
        tags: Optional[List[str]] = None,
    ) -> WorkspaceNoteRecord:
        title = title.strip()
        body = body.strip()
        if not title:
            raise ValueError("workspace note title is required")
        if not body:
            raise ValueError("workspace note body is required")
        now = utc_now()
        clean_tags = [str(tag).strip() for tag in tags or [] if str(tag).strip()]
        with self._connection() as conn:
            cursor = conn.execute(
                """
                INSERT INTO workspace_notes (workspace_id, title, body, source, tags_json, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (workspace_id.strip() or "default", title, body, source.strip(), json_dumps(clean_tags), now, now),
            )
            row = conn.execute("SELECT * FROM workspace_notes WHERE id = ?", (cursor.lastrowid,)).fetchone()
            return self._row_to_workspace_note(row)

    def search_workspace_notes(self, *, workspace_id: str, query: str = "", limit: int = 8) -> List[WorkspaceNoteRecord]:
        where = ["workspace_id = ?"]
        params: List[Any] = [workspace_id.strip() or "default"]
        if query.strip():
            pattern = f"%{query.strip()}%"
            where.append("(title LIKE ? OR body LIKE ? OR tags_json LIKE ?)")
            params.extend([pattern, pattern, pattern])
        params.append(max(1, limit))
        with self._connection() as conn:
            rows = conn.execute(
                f"""
                SELECT * FROM workspace_notes
                WHERE {' AND '.join(where)}
                ORDER BY updated_at DESC, id DESC
                LIMIT ?
                """,
                params,
            ).fetchall()
        return [self._row_to_workspace_note(row) for row in rows]

    @staticmethod
    def _row_to_memory(row: sqlite3.Row) -> MemoryRecord:
        tags = json_loads(str(row["tags_json"]), [])
        return MemoryRecord(
            id=int(row["id"]),
            scope=str(row["scope"]),
            key=str(row["key"]),
            value=str(row["value"]),
            tags=[str(tag) for tag in tags if str(tag)],
            source=str(row["source"]),
            created_at=str(row["created_at"]),
            updated_at=str(row["updated_at"]),
        )

    @staticmethod
    def _row_to_turn(row: sqlite3.Row) -> MemoryTurnRecord:
        return MemoryTurnRecord(
            id=int(row["id"]),
            session_id=str(row["session_id"]),
            workspace_id=str(row["workspace_id"]),
            user_id=str(row["user_id"]),
            user_text=str(row["user_text"]),
            assistant_text=str(row["assistant_text"]),
            created_at=str(row["created_at"]),
        )

    @staticmethod
    def _row_to_routine_rule(row: sqlite3.Row) -> RoutineRuleRecord:
        return RoutineRuleRecord(
            id=int(row["id"]),
            workspace_id=str(row["workspace_id"]),
            user_id=str(row["user_id"]),
            rule_type=str(row["rule_type"]),
            title=str(row["title"]),
            trigger_text=str(row["trigger_text"]),
            action_text=str(row["action_text"]),
            status=str(row["status"]),
            source_request=str(row["source_request"]),
            metadata=json_loads(str(row["metadata_json"]), {}),
            created_at=str(row["created_at"]),
            updated_at=str(row["updated_at"]),
        )

    @staticmethod
    def _row_to_workspace_note(row: sqlite3.Row) -> WorkspaceNoteRecord:
        tags = json_loads(str(row["tags_json"]), [])
        return WorkspaceNoteRecord(
            id=int(row["id"]),
            workspace_id=str(row["workspace_id"]),
            title=str(row["title"]),
            body=str(row["body"]),
            source=str(row["source"]),
            tags=[str(tag) for tag in tags if str(tag)],
            created_at=str(row["created_at"]),
            updated_at=str(row["updated_at"]),
        )

    @staticmethod
    def _rule_matches_text(rule: RoutineRuleRecord, text: str) -> bool:
        haystack = " ".join([rule.title, rule.trigger_text, rule.action_text, rule.source_request]).lower()
        needle = text.lower().strip()
        if not needle or not haystack:
            return False
        if needle in haystack or haystack in needle:
            return True
        tokens = set(re.findall(r"[a-zA-Z0-9]{4,}|[\u4e00-\u9fff]{2,}", needle))
        return any(token.lower() in haystack for token in tokens)

