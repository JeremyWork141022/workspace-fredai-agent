from __future__ import annotations

import sqlite3
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional

from app.config import ensure_dirs, state_db_path
from app.session_store import json_dumps, json_loads, utc_now


@dataclass(frozen=True)
class DashboardSpecRecord:
    id: str
    workspace_id: str
    session_id: str
    request_id: str
    title: str
    kind: str
    status: str
    pinned: bool
    spec: Dict[str, Any]
    created_at: str
    updated_at: str


class DashboardStore:
    """SQLite-backed dashboard specifications and session-linked prototypes."""

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
                CREATE TABLE IF NOT EXISTS dashboard_specs (
                    id TEXT PRIMARY KEY,
                    workspace_id TEXT NOT NULL,
                    session_id TEXT NOT NULL DEFAULT '',
                    request_id TEXT NOT NULL DEFAULT '',
                    title TEXT NOT NULL DEFAULT '',
                    kind TEXT NOT NULL DEFAULT 'dashboard',
                    status TEXT NOT NULL DEFAULT 'draft',
                    pinned INTEGER NOT NULL DEFAULT 1,
                    spec_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_dashboard_specs_workspace
                ON dashboard_specs(workspace_id, updated_at)
                """
            )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_dashboard_specs_session
                ON dashboard_specs(session_id, updated_at)
                """
            )

    def save_spec(
        self,
        *,
        workspace_id: str,
        session_id: str = "",
        request_id: str = "",
        title: str,
        kind: str = "dashboard",
        status: str = "draft",
        pinned: bool = True,
        spec: Dict[str, Any],
        spec_id: str = "",
    ) -> DashboardSpecRecord:
        now = utc_now()
        clean_id = spec_id.strip() or f"dash_{uuid.uuid4().hex}"
        clean_workspace = workspace_id.strip() or "default"
        clean_title = " ".join((title or "Untitled dashboard").split())[:180]
        clean_kind = (kind or "dashboard").strip()[:60] or "dashboard"
        clean_status = (status or "draft").strip()[:60] or "draft"
        with self._connection() as conn:
            existing = conn.execute("SELECT created_at FROM dashboard_specs WHERE id = ?", (clean_id,)).fetchone()
            created_at = str(existing["created_at"]) if existing else now
            conn.execute(
                """
                INSERT INTO dashboard_specs
                    (id, workspace_id, session_id, request_id, title, kind, status, pinned, spec_json, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    workspace_id = excluded.workspace_id,
                    session_id = excluded.session_id,
                    request_id = excluded.request_id,
                    title = excluded.title,
                    kind = excluded.kind,
                    status = excluded.status,
                    pinned = excluded.pinned,
                    spec_json = excluded.spec_json,
                    updated_at = excluded.updated_at
                """,
                (
                    clean_id,
                    clean_workspace,
                    session_id.strip(),
                    request_id.strip(),
                    clean_title,
                    clean_kind,
                    clean_status,
                    1 if pinned else 0,
                    json_dumps(spec),
                    created_at,
                    now,
                ),
            )
            row = conn.execute("SELECT * FROM dashboard_specs WHERE id = ?", (clean_id,)).fetchone()
        return self._row_to_record(row)

    def list_specs(
        self,
        *,
        workspace_id: str,
        session_id: str = "",
        pinned_only: bool = False,
        limit: int = 100,
    ) -> List[DashboardSpecRecord]:
        where = ["workspace_id = ?"]
        params: List[Any] = [workspace_id.strip() or "default"]
        if session_id:
            where.append("session_id = ?")
            params.append(session_id.strip())
        if pinned_only:
            where.append("pinned = 1")
        params.append(max(1, min(int(limit or 100), 500)))
        with self._connection() as conn:
            rows = conn.execute(
                f"""
                SELECT *
                FROM dashboard_specs
                WHERE {' AND '.join(where)}
                ORDER BY updated_at DESC, created_at DESC
                LIMIT ?
                """,
                params,
            ).fetchall()
        return [self._row_to_record(row) for row in rows]

    def debug_state(self) -> Dict[str, Any]:
        with self._connection() as conn:
            row = conn.execute("SELECT COUNT(*) AS count FROM dashboard_specs").fetchone()
        return {"db_path": str(self._db_path), "dashboard_specs": int(row["count"] if row else 0)}

    @staticmethod
    def to_dict(record: DashboardSpecRecord) -> Dict[str, Any]:
        return {
            "id": record.id,
            "workspace_id": record.workspace_id,
            "session_id": record.session_id,
            "request_id": record.request_id,
            "title": record.title,
            "kind": record.kind,
            "status": record.status,
            "pinned": record.pinned,
            "spec": record.spec,
            "created_at": record.created_at,
            "updated_at": record.updated_at,
        }

    @staticmethod
    def _row_to_record(row: sqlite3.Row) -> DashboardSpecRecord:
        return DashboardSpecRecord(
            id=str(row["id"]),
            workspace_id=str(row["workspace_id"]),
            session_id=str(row["session_id"]),
            request_id=str(row["request_id"]),
            title=str(row["title"]),
            kind=str(row["kind"]),
            status=str(row["status"]),
            pinned=bool(row["pinned"]),
            spec=json_loads(str(row["spec_json"]), {}),
            created_at=str(row["created_at"]),
            updated_at=str(row["updated_at"]),
        )
