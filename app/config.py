from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


def _bool_env(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None or raw == "":
        return default
    return raw.strip().lower() in {"1", "true", "yes", "y", "on"}


@dataclass(frozen=True)
class Settings:
    project_root: Path
    state_dir: Path
    backend: str
    max_sandboxes: int
    workspace_dir: str
    default_agent_command: str
    default_command_timeout: int
    daytona_api_key: Optional[str]
    daytona_api_url: Optional[str]
    daytona_target: Optional[str]
    daytona_image: Optional[str]
    daytona_snapshot: Optional[str]
    daytona_sdk_path: Optional[str]
    daytona_auto_stop_minutes: int
    daytona_ephemeral: bool


def load_settings() -> Settings:
    project_root = Path(__file__).resolve().parents[1]
    state_dir = Path(os.getenv("MVP_STATE_DIR", str(project_root / ".state"))).resolve()
    backend = os.getenv("MVP_BACKEND", "auto").strip().lower()
    if backend == "auto":
        backend = "daytona" if os.getenv("DAYTONA_API_KEY") else "local"

    return Settings(
        project_root=project_root,
        state_dir=state_dir,
        backend=backend,
        max_sandboxes=int(os.getenv("MVP_MAX_SANDBOXES", "20")),
        workspace_dir=os.getenv("MVP_WORKSPACE_DIR", "/workspace"),
        default_agent_command=os.getenv(
            "MVP_AGENT_COMMAND",
            "python /workspace/agent.py",
        ),
        default_command_timeout=int(os.getenv("MVP_COMMAND_TIMEOUT_SECONDS", "900")),
        daytona_api_key=os.getenv("DAYTONA_API_KEY"),
        daytona_api_url=os.getenv("DAYTONA_API_URL"),
        daytona_target=os.getenv("DAYTONA_TARGET"),
        daytona_image=os.getenv("DAYTONA_IMAGE"),
        daytona_snapshot=os.getenv("DAYTONA_SNAPSHOT"),
        daytona_sdk_path=os.getenv("DAYTONA_SDK_PATH"),
        daytona_auto_stop_minutes=int(os.getenv("DAYTONA_AUTO_STOP_MINUTES", "60")),
        daytona_ephemeral=_bool_env("DAYTONA_EPHEMERAL", True),
    )


settings = load_settings()
