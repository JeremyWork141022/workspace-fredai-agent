from __future__ import annotations

import asyncio
import logging
import sqlite3
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, time, timedelta, timezone
from pathlib import Path
from typing import Awaitable, Callable, Iterator, List, Optional

from app.config import ensure_dirs, state_db_path
from app.session_store import utc_now


logger = logging.getLogger(__name__)

JobHandler = Callable[["ScheduledJob"], Awaitable[str]]


@dataclass
class ScheduledJob:
    id: str
    name: str
    prompt: str
    schedule_type: str
    interval_seconds: int
    daily_time: str
    workspace_id: str
    user_id: str
    session_id: str
    deliver_result: bool
    status: str
    next_run_at: str
    last_run_at: str
    last_error: str
    created_at: str
    updated_at: str


class CronScheduler:
    """Small SQLite-backed scheduler for recurring workspace prompts."""

    def __init__(self, db_path: Optional[Path] = None, poll_seconds: float = 5.0):
        ensure_dirs()
        self._db_path = db_path or state_db_path()
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._poll_seconds = max(1.0, poll_seconds)
        self._task: Optional[asyncio.Task] = None
        self._running = False
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
                CREATE TABLE IF NOT EXISTS cron_jobs (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    prompt TEXT NOT NULL,
                    schedule_type TEXT NOT NULL,
                    interval_seconds INTEGER NOT NULL DEFAULT 0,
                    daily_time TEXT NOT NULL DEFAULT '',
                    workspace_id TEXT NOT NULL DEFAULT '',
                    user_id TEXT NOT NULL DEFAULT '',
                    session_id TEXT NOT NULL DEFAULT '',
                    deliver_result INTEGER NOT NULL DEFAULT 0,
                    status TEXT NOT NULL DEFAULT 'ACTIVE',
                    next_run_at TEXT NOT NULL,
                    last_run_at TEXT NOT NULL DEFAULT '',
                    last_error TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS cron_runs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    job_id TEXT NOT NULL,
                    started_at TEXT NOT NULL,
                    finished_at TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL,
                    result TEXT NOT NULL DEFAULT '',
                    error TEXT NOT NULL DEFAULT ''
                )
                """
            )
            conn.execute("CREATE INDEX IF NOT EXISTS idx_cron_jobs_due ON cron_jobs(status, next_run_at)")

    def add_interval_job(
        self,
        *,
        name: str,
        prompt: str,
        interval_seconds: int,
        workspace_id: str = "",
        user_id: str = "",
        session_id: str = "",
        deliver_result: bool = False,
    ) -> ScheduledJob:
        interval_seconds = max(60, int(interval_seconds))
        next_run_at = (datetime.now(timezone.utc) + timedelta(seconds=interval_seconds)).isoformat().replace("+00:00", "Z")
        return self._insert_job(
            name=name,
            prompt=prompt,
            schedule_type="interval",
            interval_seconds=interval_seconds,
            daily_time="",
            workspace_id=workspace_id,
            user_id=user_id,
            session_id=session_id,
            deliver_result=deliver_result,
            next_run_at=next_run_at,
        )

    def add_daily_job(
        self,
        *,
        name: str,
        prompt: str,
        daily_time: str,
        workspace_id: str = "",
        user_id: str = "",
        session_id: str = "",
        deliver_result: bool = False,
    ) -> ScheduledJob:
        parsed = self._parse_daily_time(daily_time)
        next_run_at = self._next_daily_run(parsed).isoformat().replace("+00:00", "Z")
        return self._insert_job(
            name=name,
            prompt=prompt,
            schedule_type="daily",
            interval_seconds=0,
            daily_time=daily_time,
            workspace_id=workspace_id,
            user_id=user_id,
            session_id=session_id,
            deliver_result=deliver_result,
            next_run_at=next_run_at,
        )

    def _insert_job(
        self,
        *,
        name: str,
        prompt: str,
        schedule_type: str,
        interval_seconds: int,
        daily_time: str,
        workspace_id: str,
        user_id: str,
        session_id: str,
        deliver_result: bool,
        next_run_at: str,
    ) -> ScheduledJob:
        name = name.strip()
        prompt = prompt.strip()
        if not name:
            raise ValueError("job name is required")
        if not prompt:
            raise ValueError("job prompt is required")
        now = utc_now()
        job_id = f"job_{uuid.uuid4().hex}"
        with self._connection() as conn:
            conn.execute(
                """
                INSERT INTO cron_jobs
                    (id, name, prompt, schedule_type, interval_seconds, daily_time,
                     workspace_id, user_id, session_id, deliver_result, status,
                     next_run_at, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'ACTIVE', ?, ?, ?)
                """,
                (
                    job_id,
                    name,
                    prompt,
                    schedule_type,
                    interval_seconds,
                    daily_time,
                    workspace_id.strip(),
                    user_id.strip(),
                    session_id.strip(),
                    1 if deliver_result else 0,
                    next_run_at,
                    now,
                    now,
                ),
            )
            row = conn.execute("SELECT * FROM cron_jobs WHERE id = ?", (job_id,)).fetchone()
            return self._row_to_job(row)

    def list_jobs(self) -> List[ScheduledJob]:
        with self._connection() as conn:
            rows = conn.execute("SELECT * FROM cron_jobs ORDER BY created_at DESC").fetchall()
        return [self._row_to_job(row) for row in rows]

    def set_status(self, *, job_id: str, status: str) -> bool:
        status = status.strip().upper()
        if status not in {"ACTIVE", "PAUSED"}:
            raise ValueError("status must be ACTIVE or PAUSED")
        with self._connection() as conn:
            cursor = conn.execute("UPDATE cron_jobs SET status = ?, updated_at = ? WHERE id = ?", (status, utc_now(), job_id))
            return cursor.rowcount > 0

    def delete_job(self, *, job_id: str) -> bool:
        with self._connection() as conn:
            cursor = conn.execute("DELETE FROM cron_jobs WHERE id = ?", (job_id,))
            return cursor.rowcount > 0

    async def start(self, handler: JobHandler) -> None:
        if self._task and not self._task.done():
            return
        self._running = True
        self._task = asyncio.create_task(self._run_loop(handler), name="workspace-agent-cron-scheduler")

    async def stop(self) -> None:
        self._running = False
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        self._task = None

    async def run_pending_once(self, handler: JobHandler) -> int:
        count = 0
        for job in self._due_jobs():
            count += 1
            await self._run_job(job, handler)
        return count

    async def _run_loop(self, handler: JobHandler) -> None:
        while self._running:
            try:
                await self.run_pending_once(handler)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.exception("Workspace scheduler loop error: %s", exc)
            await asyncio.sleep(self._poll_seconds)

    def _due_jobs(self) -> List[ScheduledJob]:
        now = utc_now()
        with self._connection() as conn:
            rows = conn.execute(
                """
                SELECT * FROM cron_jobs
                WHERE status = 'ACTIVE' AND next_run_at <= ?
                ORDER BY next_run_at ASC
                """,
                (now,),
            ).fetchall()
        return [self._row_to_job(row) for row in rows]

    async def _run_job(self, job: ScheduledJob, handler: JobHandler) -> None:
        started_at = utc_now()
        with self._connection() as conn:
            cursor = conn.execute(
                "INSERT INTO cron_runs (job_id, started_at, status) VALUES (?, ?, 'RUNNING')",
                (job.id, started_at),
            )
            run_id = cursor.lastrowid
        try:
            result = await handler(job)
            finished_at = utc_now()
            self._mark_job_complete(job=job, error="")
            with self._connection() as conn:
                conn.execute(
                    """
                    UPDATE cron_runs
                    SET finished_at = ?, status = 'SUCCESS', result = ?
                    WHERE id = ?
                    """,
                    (finished_at, result[:4000], run_id),
                )
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"
            finished_at = utc_now()
            self._mark_job_complete(job=job, error=error)
            with self._connection() as conn:
                conn.execute(
                    """
                    UPDATE cron_runs
                    SET finished_at = ?, status = 'ERROR', error = ?
                    WHERE id = ?
                    """,
                    (finished_at, error[:1000], run_id),
                )

    def _mark_job_complete(self, *, job: ScheduledJob, error: str) -> None:
        now_dt = datetime.now(timezone.utc)
        next_run = self._compute_next_run(job, now_dt).isoformat().replace("+00:00", "Z")
        now = now_dt.isoformat().replace("+00:00", "Z")
        with self._connection() as conn:
            conn.execute(
                """
                UPDATE cron_jobs
                SET next_run_at = ?, last_run_at = ?, last_error = ?, updated_at = ?
                WHERE id = ?
                """,
                (next_run, now, error, now, job.id),
            )

    def _compute_next_run(self, job: ScheduledJob, after: datetime) -> datetime:
        if job.schedule_type == "daily":
            return self._next_daily_run(self._parse_daily_time(job.daily_time), after=after)
        return after + timedelta(seconds=max(60, job.interval_seconds or 60))

    @staticmethod
    def _parse_daily_time(value: str) -> time:
        parts = value.strip().split(":")
        if len(parts) != 2:
            raise ValueError("daily time must be HH:MM")
        hour = int(parts[0])
        minute = int(parts[1])
        if hour < 0 or hour > 23 or minute < 0 or minute > 59:
            raise ValueError("daily time must be HH:MM in 24-hour local time")
        return time(hour=hour, minute=minute)

    @staticmethod
    def _next_daily_run(value: time, *, after: Optional[datetime] = None) -> datetime:
        base = (after or datetime.now(timezone.utc)).astimezone()
        candidate_local = base.replace(hour=value.hour, minute=value.minute, second=0, microsecond=0)
        if candidate_local <= base:
            candidate_local += timedelta(days=1)
        return candidate_local.astimezone(timezone.utc)

    @staticmethod
    def _row_to_job(row: sqlite3.Row) -> ScheduledJob:
        return ScheduledJob(
            id=str(row["id"]),
            name=str(row["name"]),
            prompt=str(row["prompt"]),
            schedule_type=str(row["schedule_type"]),
            interval_seconds=int(row["interval_seconds"]),
            daily_time=str(row["daily_time"]),
            workspace_id=str(row["workspace_id"]),
            user_id=str(row["user_id"]),
            session_id=str(row["session_id"]),
            deliver_result=bool(row["deliver_result"]),
            status=str(row["status"]),
            next_run_at=str(row["next_run_at"]),
            last_run_at=str(row["last_run_at"]),
            last_error=str(row["last_error"]),
            created_at=str(row["created_at"]),
            updated_at=str(row["updated_at"]),
        )

