from __future__ import annotations

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import JSONResponse, Response, StreamingResponse

from .config import settings
from .events import event_bus
from .models import (
    CreateSessionRequest,
    ExecRequest,
    RunAgentRequest,
    RunResponse,
    UploadFilesRequest,
)
from .service import service


app = FastAPI(
    title="Daytona Agent MVP",
    version="0.1.0",
    description="Minimal service for running agents in Daytona sandboxes with file I/O and trajectory export.",
)


@app.get("/health")
async def health():
    active = await service.sessions.list()
    return {
        "ok": True,
        "backend": settings.backend,
        "active_sessions": len(active),
        "max_sandboxes": settings.max_sandboxes,
    }


@app.post("/sessions")
async def create_session(request: CreateSessionRequest):
    try:
        record = await service.create_session(
            env=request.env,
            labels=request.labels,
            agent_command=request.agent_command,
        )
        return record.to_response()
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.get("/sessions")
async def list_sessions():
    records = await service.sessions.list()
    return [record.to_response() for record in records]


@app.get("/sessions/{session_id}")
async def get_session(session_id: str):
    try:
        return (await service.sessions.get(session_id)).to_response()
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="session not found") from exc


@app.delete("/sessions/{session_id}")
async def delete_session(session_id: str):
    try:
        await service.delete_session(session_id)
        return {"success": True}
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="session not found") from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.post("/sessions/{session_id}/files")
async def upload_files(session_id: str, request: UploadFilesRequest):
    try:
        return await service.upload_files(session_id, request.files)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="session not found") from exc
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/sessions/{session_id}/files")
async def download_file(session_id: str, path: str = Query(...)):
    try:
        data = await service.download_file(session_id, path)
        filename = path.rstrip("/").split("/")[-1] or "download.bin"
        return Response(
            content=data,
            media_type="application/octet-stream",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="session not found") from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="file not found") from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.get("/sessions/{session_id}/files/list")
async def list_files(session_id: str, path: str = ".", max_depth: int = 5):
    try:
        return {"files": await service.list_files(session_id, path, max_depth)}
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="session not found") from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.get("/sessions/{session_id}/archive")
async def archive_workspace(session_id: str):
    try:
        data = await service.archive_workspace(session_id)
        return Response(
            content=data,
            media_type="application/gzip",
            headers={"Content-Disposition": f'attachment; filename="workspace-{session_id[:8]}.tar.gz"'},
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="session not found") from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.post("/sessions/{session_id}/exec")
async def exec_command(session_id: str, request: ExecRequest):
    try:
        return await service.exec(
            session_id,
            request.command,
            cwd=request.cwd,
            env=request.env,
            timeout_seconds=request.timeout_seconds,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="session not found") from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.post("/sessions/{session_id}/run")
async def run_agent(session_id: str, request: RunAgentRequest):
    try:
        run_id = await service.run_agent(
            session_id,
            command=request.command,
            cwd=request.cwd,
            env=request.env,
            timeout_seconds=request.timeout_seconds,
        )
        return RunResponse(run_id=run_id, session_id=session_id, status="queued")
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="session not found") from exc
    except Exception as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.get("/sessions/{session_id}/events")
async def stream_events(session_id: str):
    try:
        await service.sessions.get(session_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="session not found") from exc
    return StreamingResponse(event_bus.stream(session_id), media_type="text/event-stream")


@app.get("/sessions/{session_id}/trajectory")
async def get_trajectory(session_id: str, format: str = Query("json", pattern="^(json|jsonl)$")):
    try:
        await service.sessions.get(session_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="session not found") from exc
    if format == "jsonl":
        text = await service.trajectories.read_jsonl(session_id)
        return Response(content=text, media_type="application/x-ndjson")
    return JSONResponse({"records": await service.trajectories.read_records(session_id)})

