from __future__ import annotations

import asyncio
import base64
import io
import os
import shutil
import subprocess
import sys
import tarfile
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Optional, Protocol

from .config import Settings


@dataclass
class SandboxRef:
    sandbox_id: str
    backend: str
    workspace_dir: str


@dataclass
class ExecOutcome:
    exit_code: int
    stdout: str
    stderr: str
    duration_ms: float


class SandboxBackend(Protocol):
    async def create(self, *, session_id: str, env: Dict[str, str], labels: Dict[str, str]) -> SandboxRef:
        ...

    async def delete(self, sandbox_id: str) -> None:
        ...

    async def upload_file(self, sandbox_id: str, path: str, data: bytes) -> None:
        ...

    async def download_file(self, sandbox_id: str, path: str) -> bytes:
        ...

    async def list_files(self, sandbox_id: str, path: str, max_depth: int) -> list[str]:
        ...

    async def archive_workspace(self, sandbox_id: str) -> bytes:
        ...

    async def exec(
        self,
        sandbox_id: str,
        command: str,
        *,
        cwd: Optional[str],
        env: Dict[str, str],
        timeout_seconds: int,
    ) -> ExecOutcome:
        ...


class LocalSandboxBackend:
    """Filesystem-backed dry-run backend for developing the service without Daytona."""

    def __init__(self, settings: Settings):
        self._root = settings.state_dir / "local-sandboxes"
        self._root.mkdir(parents=True, exist_ok=True)
        self._workspace_dir = settings.workspace_dir

    async def create(self, *, session_id: str, env: Dict[str, str], labels: Dict[str, str]) -> SandboxRef:
        sandbox_id = f"local-{session_id[:12]}"
        root = self._sandbox_root(sandbox_id)
        root.mkdir(parents=True, exist_ok=True)
        return SandboxRef(sandbox_id=sandbox_id, backend="local", workspace_dir=self._workspace_dir)

    async def delete(self, sandbox_id: str) -> None:
        await asyncio.to_thread(shutil.rmtree, self._sandbox_root(sandbox_id), True)

    async def upload_file(self, sandbox_id: str, path: str, data: bytes) -> None:
        target = self._resolve(sandbox_id, path)
        target.parent.mkdir(parents=True, exist_ok=True)
        await asyncio.to_thread(target.write_bytes, data)

    async def download_file(self, sandbox_id: str, path: str) -> bytes:
        target = self._resolve(sandbox_id, path)
        if not target.is_file():
            raise FileNotFoundError(path)
        return await asyncio.to_thread(target.read_bytes)

    async def list_files(self, sandbox_id: str, path: str, max_depth: int) -> list[str]:
        root = self._resolve(sandbox_id, path or ".")
        if not root.exists():
            return []
        base = root if root.is_dir() else root.parent

        def walk() -> list[str]:
            out: list[str] = []
            for item in base.rglob("*"):
                rel = item.relative_to(base)
                if len(rel.parts) <= max_depth:
                    out.append(str(rel) + ("/" if item.is_dir() else ""))
            return sorted(out)

        return await asyncio.to_thread(walk)

    async def archive_workspace(self, sandbox_id: str) -> bytes:
        root = self._sandbox_root(sandbox_id)

        def pack() -> bytes:
            buf = io.BytesIO()
            with tarfile.open(fileobj=buf, mode="w:gz") as tar:
                tar.add(root, arcname="workspace")
            return buf.getvalue()

        return await asyncio.to_thread(pack)

    async def exec(
        self,
        sandbox_id: str,
        command: str,
        *,
        cwd: Optional[str],
        env: Dict[str, str],
        timeout_seconds: int,
    ) -> ExecOutcome:
        root = self._sandbox_root(sandbox_id)
        workdir = self._resolve(sandbox_id, cwd or ".") if cwd else root
        started = time.perf_counter()

        def run() -> subprocess.CompletedProcess[str]:
            merged_env = dict(os.environ)
            merged_env.update(env)
            return subprocess.run(
                command,
                shell=True,
                cwd=str(workdir),
                env=merged_env,
                text=True,
                capture_output=True,
                timeout=timeout_seconds,
            )

        try:
            proc = await asyncio.to_thread(run)
            return ExecOutcome(
                exit_code=proc.returncode,
                stdout=proc.stdout,
                stderr=proc.stderr,
                duration_ms=(time.perf_counter() - started) * 1000,
            )
        except subprocess.TimeoutExpired as exc:
            return ExecOutcome(
                exit_code=124,
                stdout=exc.stdout or "",
                stderr=exc.stderr or f"Command timed out after {timeout_seconds}s",
                duration_ms=(time.perf_counter() - started) * 1000,
            )

    def _sandbox_root(self, sandbox_id: str) -> Path:
        return self._root / sandbox_id / "workspace"

    def _resolve(self, sandbox_id: str, path: str) -> Path:
        root = self._sandbox_root(sandbox_id).resolve()
        raw = (path or ".").strip()
        if raw.startswith(self._workspace_dir + "/"):
            raw = raw[len(self._workspace_dir) + 1 :]
        elif raw == self._workspace_dir:
            raw = "."
        target = (root / raw.lstrip("/")).resolve()
        if target != root and root not in target.parents:
            raise ValueError("path escapes workspace")
        return target


class DaytonaSandboxBackend:
    def __init__(self, settings: Settings):
        self._settings = settings
        self._daytona = None
        self._sandboxes: Dict[str, object] = {}
        self._create_lock = asyncio.Lock()

    async def create(self, *, session_id: str, env: Dict[str, str], labels: Dict[str, str]) -> SandboxRef:
        daytona = await self._client()
        params = self._create_params(session_id, env, labels)
        sandbox = await daytona.create(params, timeout=180)
        self._sandboxes[sandbox.id] = sandbox
        await sandbox.process.exec(f"mkdir -p {self._quote(self._settings.workspace_dir)}", timeout=30)
        return SandboxRef(
            sandbox_id=sandbox.id,
            backend="daytona",
            workspace_dir=self._settings.workspace_dir,
        )

    async def delete(self, sandbox_id: str) -> None:
        sandbox = await self._get_sandbox(sandbox_id)
        await sandbox.delete(timeout=60)
        self._sandboxes.pop(sandbox_id, None)

    async def upload_file(self, sandbox_id: str, path: str, data: bytes) -> None:
        sandbox = await self._get_sandbox(sandbox_id)
        await sandbox.fs.upload_file(data, self._remote_path(path))

    async def download_file(self, sandbox_id: str, path: str) -> bytes:
        sandbox = await self._get_sandbox(sandbox_id)
        return await sandbox.fs.download_file(self._remote_path(path))

    async def list_files(self, sandbox_id: str, path: str, max_depth: int) -> list[str]:
        sandbox = await self._get_sandbox(sandbox_id)
        target = self._remote_path(path or ".")
        command = (
            f"cd {self._quote(target)} 2>/dev/null && "
            f"find . -mindepth 1 -maxdepth {max(1, min(max_depth, 20))} "
            "-printf '%y %p\\n' | sed 's#^d ./#dir #; s#^f ./#file #'"
        )
        result = await sandbox.process.exec(command, timeout=30)
        if result.exit_code:
            return []
        return [line.strip() for line in (result.result or "").splitlines() if line.strip()]

    async def archive_workspace(self, sandbox_id: str) -> bytes:
        sandbox = await self._get_sandbox(sandbox_id)
        archive_path = f"/tmp/daytona-agent-mvp-{uuid.uuid4().hex}.tar.gz"
        command = (
            f"tar -czf {self._quote(archive_path)} "
            f"-C {self._quote(self._settings.workspace_dir)} ."
        )
        result = await sandbox.process.exec(command, timeout=300)
        if result.exit_code:
            raise RuntimeError(result.result or "failed to archive workspace")
        return await sandbox.fs.download_file(archive_path)

    async def exec(
        self,
        sandbox_id: str,
        command: str,
        *,
        cwd: Optional[str],
        env: Dict[str, str],
        timeout_seconds: int,
    ) -> ExecOutcome:
        sandbox = await self._get_sandbox(sandbox_id)
        started = time.perf_counter()
        result = await sandbox.process.exec(
            command,
            cwd=self._remote_path(cwd or "."),
            env=env or None,
            timeout=timeout_seconds,
        )
        return ExecOutcome(
            exit_code=result.exit_code or 0,
            stdout=result.result or "",
            stderr=getattr(result, "stderr", "") or "",
            duration_ms=(time.perf_counter() - started) * 1000,
        )

    async def _client(self):
        if self._daytona is not None:
            return self._daytona
        async with self._create_lock:
            if self._daytona is not None:
                return self._daytona
            sdk_path = self._settings.daytona_sdk_path
            if sdk_path and Path(sdk_path).exists() and sdk_path not in sys.path:
                sys.path.insert(0, sdk_path)
            try:
                from daytona import (  # type: ignore
                    AsyncDaytona,
                    CreateSandboxFromImageParams,
                    CreateSandboxFromSnapshotParams,
                    DaytonaConfig,
                    Resources,
                )
            except Exception as exc:
                raise RuntimeError(
                    "Could not import Daytona SDK. Install `daytona` or set "
                    "DAYTONA_SDK_PATH to the local SDK source path."
                ) from exc
            self._sdk_types = {
                "CreateSandboxFromImageParams": CreateSandboxFromImageParams,
                "CreateSandboxFromSnapshotParams": CreateSandboxFromSnapshotParams,
                "Resources": Resources,
            }
            self._daytona = AsyncDaytona(
                DaytonaConfig(
                    api_key=self._settings.daytona_api_key,
                    api_url=self._settings.daytona_api_url,
                    target=self._settings.daytona_target,
                    connection_pool_maxsize=None,
                )
            )
            return self._daytona

    def _create_params(self, session_id: str, env: Dict[str, str], labels: Dict[str, str]):
        params_labels = {
            "managed_by": "daytona-agent-mvp",
            "mvp_session_id": session_id,
        }
        params_labels.update(labels or {})
        common = {
            "name": f"mvp-{session_id[:12]}",
            "language": "python",
            "env_vars": env or {},
            "labels": params_labels,
            "auto_stop_interval": self._settings.daytona_auto_stop_minutes,
            "ephemeral": self._settings.daytona_ephemeral,
        }
        if self._settings.daytona_snapshot:
            cls = self._sdk_types["CreateSandboxFromSnapshotParams"]
            return cls(snapshot=self._settings.daytona_snapshot, **common)
        cls = self._sdk_types["CreateSandboxFromImageParams"]
        image = self._settings.daytona_image or "python:3.12-slim"
        resources = self._sdk_types["Resources"](cpu=2, memory=4)
        return cls(image=image, resources=resources, **common)

    async def _get_sandbox(self, sandbox_id: str):
        cached = self._sandboxes.get(sandbox_id)
        if cached is not None:
            return cached
        daytona = await self._client()
        sandbox = await daytona.get(sandbox_id)
        self._sandboxes[sandbox_id] = sandbox
        return sandbox

    def _remote_path(self, path: str) -> str:
        raw = (path or ".").strip()
        if raw.startswith("/"):
            return raw
        if raw == ".":
            return self._settings.workspace_dir
        clean = raw.lstrip("/")
        if ".." in clean.split("/"):
            raise ValueError("path escapes workspace")
        return f"{self._settings.workspace_dir.rstrip('/')}/{clean}"

    @staticmethod
    def _quote(value: str) -> str:
        return "'" + value.replace("'", "'\"'\"'") + "'"


def decode_upload(item) -> bytes:
    if item.content_base64 is not None:
        return base64.b64decode(item.content_base64)
    if item.content_text is not None:
        return item.content_text.encode("utf-8")
    raise ValueError(f"{item.path}: content_text or content_base64 is required")


def make_backend(settings: Settings) -> SandboxBackend:
    if settings.backend == "local":
        return LocalSandboxBackend(settings)
    if settings.backend == "daytona":
        return DaytonaSandboxBackend(settings)
    raise ValueError(f"Unsupported MVP_BACKEND={settings.backend!r}")

