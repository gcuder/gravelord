"""FastAPI routes and WebSocket fanout."""
from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request, WebSocket, WebSocketDisconnect

router = APIRouter()


def _orch(request: Request):
    return request.app.state.orchestrator


def _events(request: Request):
    return request.app.state.events


@router.get("/status")
async def get_status(request: Request) -> dict[str, Any]:
    return _orch(request).snapshot()


@router.get("/status/{owner}/{repo}/{number}")
async def get_status_path(owner: str, repo: str, number: int, request: Request) -> dict[str, Any]:
    identifier = f"{owner}/{repo}#{number}"
    detail = _orch(request).detail(identifier)
    if detail is None:
        raise HTTPException(status_code=404, detail={"error": {"code": "issue_not_found", "message": identifier}})
    return detail


@router.get("/logs/{owner}/{repo}/{number}")
async def get_logs(
    owner: str,
    repo: str,
    number: int,
    request: Request,
    n: int = Query(100, ge=1, le=1000),
) -> dict[str, Any]:
    identifier = f"{owner}/{repo}#{number}"
    return {"identifier": identifier, "entries": _events(request).recent(identifier, n)}


@router.post("/trigger/{owner}/{repo}/{number}")
async def post_trigger(owner: str, repo: str, number: int, request: Request) -> dict[str, Any]:
    identifier = f"{owner}/{repo}#{number}"
    result = await _orch(request).trigger(identifier)
    status = result.pop("status", 202) if "error" in result else 202
    if "error" in result:
        raise HTTPException(status_code=status, detail={"error": {"code": result["error"], "message": identifier}})
    return result


@router.post("/kill/{owner}/{repo}/{number}")
async def post_kill(owner: str, repo: str, number: int, request: Request) -> dict[str, Any]:
    identifier = f"{owner}/{repo}#{number}"
    result = await _orch(request).kill(identifier)
    status = result.pop("status", 200) if "error" in result else 200
    if "error" in result:
        raise HTTPException(status_code=status, detail={"error": {"code": result["error"], "message": identifier}})
    return result


@router.websocket("/stream")
async def ws_stream(ws: WebSocket) -> None:
    await ws.accept()
    bus = ws.app.state.events
    q = await bus.subscribe()
    try:
        while True:
            event = await q.get()
            await ws.send_json(event)
    except WebSocketDisconnect:
        pass
    except Exception:
        pass
    finally:
        await bus.unsubscribe(q)
