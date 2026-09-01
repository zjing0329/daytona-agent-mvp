from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field


class CreateSessionRequest(BaseModel):
    env: Dict[str, str] = Field(default_factory=dict)
    labels: Dict[str, str] = Field(default_factory=dict)
    agent_command: Optional[str] = None


class SessionResponse(BaseModel):
    session_id: str
    sandbox_id: str
    backend: str
    status: str
    workspace_dir: str
    created_at: datetime
    updated_at: datetime
    active_run_id: Optional[str] = None
    last_error: Optional[str] = None


class FileUploadItem(BaseModel):
    path: str
    content_text: Optional[str] = None
    content_base64: Optional[str] = None


class UploadFilesRequest(BaseModel):
    files: List[FileUploadItem]


class ExecRequest(BaseModel):
    command: str
    cwd: Optional[str] = None
    env: Dict[str, str] = Field(default_factory=dict)
    timeout_seconds: Optional[int] = None


class RunAgentRequest(BaseModel):
    command: Optional[str] = None
    cwd: Optional[str] = None
    env: Dict[str, str] = Field(default_factory=dict)
    timeout_seconds: Optional[int] = None


class RunResponse(BaseModel):
    run_id: str
    session_id: str
    status: Literal["queued", "running"]


class ExecResponse(BaseModel):
    exit_code: int
    stdout: str
    stderr: str = ""
    command: str
    duration_ms: float


class EventRecord(BaseModel):
    type: str
    session_id: str
    timestamp: datetime
    data: Dict[str, Any] = Field(default_factory=dict)

