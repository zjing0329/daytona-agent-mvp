from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, Optional

from .models import SessionResponse


@dataclass
class SessionRecord:
    session_id: str
    sandbox_id: str
    backend: str
    workspace_dir: str
    status: str = "ready"
    created_at: datetime = field(default_factory=datetime.utcnow)
    updated_at: datetime = field(default_factory=datetime.utcnow)
    active_run_id: Optional[str] = None
    last_error: Optional[str] = None
    agent_command: Optional[str] = None

    def touch(self) -> None:
        self.updated_at = datetime.utcnow()

    def to_response(self) -> SessionResponse:
        return SessionResponse(
            session_id=self.session_id,
            sandbox_id=self.sandbox_id,
            backend=self.backend,
            status=self.status,
            workspace_dir=self.workspace_dir,
            created_at=self.created_at,
            updated_at=self.updated_at,
            active_run_id=self.active_run_id,
            last_error=self.last_error,
        )


class SessionStore:
    def __init__(self, max_sessions: int):
        self._max_sessions = max_sessions
        self._records: Dict[str, SessionRecord] = {}
        self._pending_creates = 0
        self._lock = asyncio.Lock()

    async def reserve_id(self) -> str:
        return uuid.uuid4().hex

    async def reserve_create_slot(self) -> None:
        async with self._lock:
            active = [r for r in self._records.values() if r.status != "deleted"]
            if len(active) + self._pending_creates >= self._max_sessions:
                raise RuntimeError(f"active sandbox limit reached ({self._max_sessions})")
            self._pending_creates += 1

    async def add_reserved(self, record: SessionRecord) -> None:
        async with self._lock:
            if self._pending_creates > 0:
                self._pending_creates -= 1
            self._records[record.session_id] = record

    async def release_create_slot(self) -> None:
        async with self._lock:
            if self._pending_creates > 0:
                self._pending_creates -= 1

    async def get(self, session_id: str) -> SessionRecord:
        async with self._lock:
            record = self._records.get(session_id)
            if record is None or record.status == "deleted":
                raise KeyError(session_id)
            return record

    async def list(self) -> list[SessionRecord]:
        async with self._lock:
            return [r for r in self._records.values() if r.status != "deleted"]

    async def update(self, session_id: str, **changes) -> SessionRecord:
        async with self._lock:
            record = self._records.get(session_id)
            if record is None or record.status == "deleted":
                raise KeyError(session_id)
            for key, value in changes.items():
                setattr(record, key, value)
            record.touch()
            return record

    async def mark_deleted(self, session_id: str) -> None:
        async with self._lock:
            record = self._records.get(session_id)
            if record is not None:
                record.status = "deleted"
                record.touch()
