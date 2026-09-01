import asyncio
from pathlib import Path

from app.config import Settings
from app.sandbox_backends import LocalSandboxBackend
from app.session_store import SessionRecord, SessionStore


def test_local_backend_upload_download_and_path_guard(tmp_path: Path):
    settings = Settings(
        project_root=tmp_path,
        state_dir=tmp_path / ".state",
        backend="local",
        max_sandboxes=20,
        workspace_dir="/workspace",
        default_agent_command="python /workspace/agent.py",
        default_command_timeout=10,
        daytona_api_key=None,
        daytona_api_url=None,
        daytona_target=None,
        daytona_image=None,
        daytona_snapshot=None,
        daytona_sdk_path=None,
        daytona_auto_stop_minutes=60,
        daytona_ephemeral=True,
    )

    async def run():
        backend = LocalSandboxBackend(settings)
        ref = await backend.create(session_id="abc", env={}, labels={})
        await backend.upload_file(ref.sandbox_id, "hello.txt", b"hello")
        assert await backend.download_file(ref.sandbox_id, "hello.txt") == b"hello"
        try:
            await backend.upload_file(ref.sandbox_id, "../escape.txt", b"no")
        except ValueError:
            return
        raise AssertionError("path escape should fail")

    asyncio.run(run())


def test_session_store_reserves_capacity_before_create():
    async def run():
        store = SessionStore(max_sessions=1)
        await store.reserve_create_slot()
        try:
            await store.reserve_create_slot()
        except RuntimeError:
            pass
        else:
            raise AssertionError("second create slot should be rejected")
        await store.add_reserved(
            SessionRecord(
                session_id="s1",
                sandbox_id="sbx1",
                backend="local",
                workspace_dir="/workspace",
            )
        )
        try:
            await store.reserve_create_slot()
        except RuntimeError:
            return
        raise AssertionError("active session should count against capacity")

    asyncio.run(run())
