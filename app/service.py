from __future__ import annotations

import asyncio
import uuid
from typing import Dict, Optional

from .config import Settings, settings
from .events import event_bus
from .models import ExecResponse
from .sandbox_backends import SandboxBackend, decode_upload, make_backend
from .session_store import SessionRecord, SessionStore
from .trajectory import TrajectoryStore


class AgentMvpService:
    def __init__(self, cfg: Settings):
        self.settings = cfg
        self.backend: SandboxBackend = make_backend(cfg)
        self.sessions = SessionStore(max_sessions=cfg.max_sandboxes)
        self.trajectories = TrajectoryStore(cfg.state_dir)
        self._create_gate = asyncio.Semaphore(cfg.max_sandboxes)

    async def create_session(self, *, env: Dict[str, str], labels: Dict[str, str], agent_command: Optional[str]) -> SessionRecord:
        session_id = await self.sessions.reserve_id()
        await self.sessions.reserve_create_slot()
        async with self._create_gate:
            try:
                ref = await self.backend.create(session_id=session_id, env=env, labels=labels)
                record = SessionRecord(
                    session_id=session_id,
                    sandbox_id=ref.sandbox_id,
                    backend=ref.backend,
                    workspace_dir=ref.workspace_dir,
                    agent_command=agent_command,
                )
                await self.sessions.add_reserved(record)
            except Exception:
                await self.sessions.release_create_slot()
                raise
        await event_bus.publish(session_id, "session_created", record.to_response().model_dump(mode="json"))
        await self.trajectories.append(session_id, "session_created", record.to_response().model_dump(mode="json"))
        return record

    async def delete_session(self, session_id: str) -> None:
        record = await self.sessions.get(session_id)
        await self.sessions.update(session_id, status="deleting")
        await event_bus.publish(session_id, "session_deleting", {"sandbox_id": record.sandbox_id})
        try:
            await self.backend.delete(record.sandbox_id)
        finally:
            await self.sessions.mark_deleted(session_id)
        await event_bus.publish(session_id, "session_deleted", {"sandbox_id": record.sandbox_id})
        await self.trajectories.append(session_id, "session_deleted", {"sandbox_id": record.sandbox_id})

    async def upload_files(self, session_id: str, files) -> Dict[str, object]:
        record = await self.sessions.get(session_id)
        uploaded = []
        for item in files:
            data = decode_upload(item)
            await self.backend.upload_file(record.sandbox_id, item.path, data)
            uploaded.append({"path": item.path, "bytes": len(data)})
        await event_bus.publish(session_id, "files_uploaded", {"files": uploaded})
        await self.trajectories.append(session_id, "files_uploaded", {"files": uploaded})
        return {"uploaded": uploaded}

    async def download_file(self, session_id: str, path: str) -> bytes:
        record = await self.sessions.get(session_id)
        data = await self.backend.download_file(record.sandbox_id, path)
        await self.trajectories.append(session_id, "file_downloaded", {"path": path, "bytes": len(data)})
        return data

    async def list_files(self, session_id: str, path: str, max_depth: int) -> list[str]:
        record = await self.sessions.get(session_id)
        files = await self.backend.list_files(record.sandbox_id, path, max_depth)
        await self.trajectories.append(session_id, "files_listed", {"path": path, "count": len(files)})
        return files

    async def archive_workspace(self, session_id: str) -> bytes:
        record = await self.sessions.get(session_id)
        data = await self.backend.archive_workspace(record.sandbox_id)
        await self.trajectories.append(session_id, "workspace_archived", {"bytes": len(data)})
        return data

    async def exec(self, session_id: str, command: str, *, cwd: Optional[str], env: Dict[str, str], timeout_seconds: Optional[int]) -> ExecResponse:
        record = await self.sessions.get(session_id)
        await event_bus.publish(session_id, "command_started", {"command": command})
        outcome = await self.backend.exec(
            record.sandbox_id,
            command,
            cwd=cwd,
            env=env,
            timeout_seconds=timeout_seconds or self.settings.default_command_timeout,
        )
        response = ExecResponse(
            exit_code=outcome.exit_code,
            stdout=outcome.stdout,
            stderr=outcome.stderr,
            command=command,
            duration_ms=outcome.duration_ms,
        )
        await event_bus.publish(session_id, "command_finished", response.model_dump(mode="json"))
        await self.trajectories.append(session_id, "command_finished", response.model_dump(mode="json"))
        return response

    async def run_agent(self, session_id: str, *, command: Optional[str], cwd: Optional[str], env: Dict[str, str], timeout_seconds: Optional[int]) -> str:
        record = await self.sessions.get(session_id)
        if record.active_run_id:
            raise RuntimeError(f"session already has active run {record.active_run_id}")
        run_id = uuid.uuid4().hex
        await self.sessions.update(session_id, active_run_id=run_id, status="running")
        asyncio.create_task(
            self._run_agent_task(
                session_id,
                run_id,
                command or record.agent_command or self.settings.default_agent_command,
                cwd,
                env,
                timeout_seconds,
            )
        )
        return run_id

    async def _run_agent_task(
        self,
        session_id: str,
        run_id: str,
        command: str,
        cwd: Optional[str],
        env: Dict[str, str],
        timeout_seconds: Optional[int],
    ) -> None:
        await event_bus.publish(session_id, "run_started", {"run_id": run_id, "command": command})
        await self.trajectories.append(session_id, "run_started", {"run_id": run_id, "command": command})
        try:
            result = await self.exec(
                session_id,
                command,
                cwd=cwd,
                env=env,
                timeout_seconds=timeout_seconds,
            )
            status = "ready" if result.exit_code == 0 else "failed"
            await self.sessions.update(
                session_id,
                active_run_id=None,
                status=status,
                last_error=None if result.exit_code == 0 else result.stderr or result.stdout,
            )
            await event_bus.publish(
                session_id,
                "run_finished",
                {"run_id": run_id, "exit_code": result.exit_code},
            )
            await self.trajectories.append(
                session_id,
                "run_finished",
                {"run_id": run_id, "exit_code": result.exit_code},
            )
        except Exception as exc:
            await self.sessions.update(
                session_id,
                active_run_id=None,
                status="failed",
                last_error=str(exc),
            )
            await event_bus.publish(session_id, "run_failed", {"run_id": run_id, "error": str(exc)})
            await self.trajectories.append(session_id, "run_failed", {"run_id": run_id, "error": str(exc)})


service = AgentMvpService(settings)
