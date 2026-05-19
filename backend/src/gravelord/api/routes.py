"""FastAPI routes and WebSocket fanout (multi-repo)."""
from __future__ import annotations

from typing import Any

from fastapi import (
    APIRouter,
    Body,
    HTTPException,
    Query,
    Request,
    WebSocket,
    WebSocketDisconnect,
)
from pydantic import BaseModel

from ..tracker.base import AgentKind, ReasoningLevel

router = APIRouter(prefix="/api")


def _orch(request: Request):
    return request.app.state.orchestrator


def _events(request: Request):
    return request.app.state.events


def _registry(request: Request):
    return request.app.state.registry


def _config(request: Request):
    return request.app.state.daemon_config


def _config_path(request: Request):
    return request.app.state.daemon_config_path


# --------------------------- /api/repos ---------------------------


class RegisterRepoBody(BaseModel):
    path: str
    id: str | None = None
    agent: AgentKind | None = None


@router.get("/repos")
async def list_repos(request: Request) -> dict[str, Any]:
    snap = _orch(request).snapshot()
    return {"repos": snap["repos"]}


@router.post("/repos", status_code=201)
async def register_repo(request: Request, body: RegisterRepoBody) -> dict[str, Any]:
    from ..cli_ops import add_repo_to_config

    try:
        repo_cfg = add_repo_to_config(
            path=body.path,
            repo_id=body.id,
            agent=body.agent,
            config_path=_config_path(request),
        )
    except FileNotFoundError as exc:
        raise HTTPException(status_code=400, detail={"error": "path_not_found", "message": str(exc)})
    except ValueError as exc:
        raise HTTPException(status_code=400, detail={"error": "invalid_repo", "message": str(exc)})
    # Daemon's config-watcher will pick up the file change and call
    # registry.register; return the new config row.
    return {"repo": repo_cfg.model_dump(by_alias=True)}


@router.delete("/repos/{repo_id}")
async def delete_repo(repo_id: str, request: Request) -> dict[str, Any]:
    from ..cli_ops import remove_repo_from_config

    result = await _orch(request).unregister_repo(repo_id)
    if "error" in result and result["error"] != "not_registered":
        status = result.pop("status", 500)
        raise HTTPException(
            status_code=status,
            detail={"error": result["error"], "repo_id": repo_id},
        )
    removed = remove_repo_from_config(repo_id, config_path=_config_path(request))
    if not removed and "error" in result:
        raise HTTPException(
            status_code=404, detail={"error": "not_registered", "repo_id": repo_id}
        )
    return {"removed": True, "repo_id": repo_id}


# --------------------------- /api/status ---------------------------


@router.get("/status")
async def get_status(request: Request) -> dict[str, Any]:
    return _orch(request).snapshot()


# --------------------------- /api/issues ---------------------------


def _identifier(owner: str, repo: str, number: int) -> str:
    return f"{owner}/{repo}#{number}"


@router.get("/issues/{owner}/{repo}/{number}")
async def get_issue(
    owner: str, repo: str, number: int, request: Request
) -> dict[str, Any]:
    identifier = _identifier(owner, repo, number)
    detail = _orch(request).detail(identifier)
    if detail is None:
        raise HTTPException(
            status_code=404,
            detail={"error": {"code": "issue_not_found", "message": identifier}},
        )
    return detail


@router.get("/issues/{owner}/{repo}/{number}/logs")
async def get_issue_logs(
    owner: str,
    repo: str,
    number: int,
    request: Request,
    n: int = Query(100, ge=1, le=1000),
) -> dict[str, Any]:
    identifier = _identifier(owner, repo, number)
    return {"identifier": identifier, "entries": _events(request).recent(identifier, n)}


class TriggerBody(BaseModel):
    agent_kind: AgentKind | None = None
    model: str | None = None
    reasoning_level: ReasoningLevel | None = None


@router.post("/issues/{owner}/{repo}/{number}/trigger", status_code=202)
async def trigger_issue(
    owner: str,
    repo: str,
    number: int,
    request: Request,
    body: TriggerBody | None = Body(default=None),
) -> dict[str, Any]:
    identifier = _identifier(owner, repo, number)
    payload = body.model_dump(exclude_none=True) if body is not None else None
    result = await _orch(request).trigger(identifier, payload=payload)
    if "error" in result:
        status = result.pop("status", 400)
        raise HTTPException(
            status_code=status,
            detail={"error": result["error"], "identifier": identifier},
        )
    return result


@router.post("/issues/{owner}/{repo}/{number}/kill")
async def kill_issue(
    owner: str, repo: str, number: int, request: Request
) -> dict[str, Any]:
    identifier = _identifier(owner, repo, number)
    result = await _orch(request).kill(identifier)
    if "error" in result:
        status = result.pop("status", 400)
        raise HTTPException(
            status_code=status,
            detail={"error": result["error"], "identifier": identifier},
        )
    return result


# --------------------------- /api/stream ---------------------------


async def _ws_loop(ws: WebSocket, repo_filter: str | None) -> None:
    await ws.accept()
    bus = ws.app.state.events
    q = await bus.subscribe()
    try:
        while True:
            event = await q.get()
            if repo_filter is not None and event.get("repo_id") != repo_filter:
                continue
            await ws.send_json(event)
    except WebSocketDisconnect:
        pass
    except Exception:
        pass
    finally:
        await bus.unsubscribe(q)


@router.websocket("/stream")
async def ws_stream(ws: WebSocket) -> None:
    await _ws_loop(ws, repo_filter=None)


@router.websocket("/stream/{repo_id}")
async def ws_stream_repo(ws: WebSocket, repo_id: str) -> None:
    await _ws_loop(ws, repo_filter=repo_id)
