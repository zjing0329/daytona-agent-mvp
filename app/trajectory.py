from __future__ import annotations

import asyncio
import json
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List


class TrajectoryStore:
    def __init__(self, state_dir: Path):
        self._dir = state_dir / "trajectories"
        self._dir.mkdir(parents=True, exist_ok=True)
        self._locks: Dict[str, asyncio.Lock] = {}

    def _path(self, session_id: str) -> Path:
        return self._dir / f"{session_id}.jsonl"

    def _lock_for(self, session_id: str) -> asyncio.Lock:
        lock = self._locks.get(session_id)
        if lock is None:
            lock = asyncio.Lock()
            self._locks[session_id] = lock
        return lock

    async def append(self, session_id: str, event_type: str, data: Dict[str, Any]) -> None:
        record = {
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "session_id": session_id,
            "type": event_type,
            "data": data,
        }
        line = json.dumps(record, ensure_ascii=False, default=str) + "\n"
        async with self._lock_for(session_id):
            await asyncio.to_thread(self._append_sync, self._path(session_id), line)

    async def read_jsonl(self, session_id: str) -> str:
        path = self._path(session_id)
        if not path.exists():
            return ""
        return await asyncio.to_thread(path.read_text, "utf-8")

    async def read_records(self, session_id: str) -> List[Dict[str, Any]]:
        text = await self.read_jsonl(session_id)
        records: List[Dict[str, Any]] = []
        for line in text.splitlines():
            if not line.strip():
                continue
            records.append(json.loads(line))
        return records

    @staticmethod
    def _append_sync(path: Path, line: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as f:
            f.write(line)

