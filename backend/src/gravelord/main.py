"""FastAPI entrypoint + lifespan + workflow watcher."""
from __future__ import annotations

import asyncio
import os
import sys
from contextlib import asynccontextmanager
from pathlib import Path

import structlog
from dotenv import load_dotenv
from fastapi import FastAPI
from watchfiles import awatch

from .adapters.base import AgentAdapter
from .adapters.claude_code import ClaudeCodeAdapter
from .adapters.codex import CodexAdapter
from .adapters.opencode import OpenCodeAdapter
from .api.routes import router
from .events import EventBus, configure_logging
from .orchestrator import Orchestrator
from .tracker.github import GitHubTracker
from .workflow import WorkflowDefinition, load_workflow
from .workspace import WorkspaceManager

log = structlog.get_logger("gravelord.main")


def build_adapter(workflow: WorkflowDefinition) -> AgentAdapter:
    kind = workflow.agent.kind
    if kind == "claude-code":
        return ClaudeCodeAdapter(
            command=workflow.agent.command or "claude",
            stall_timeout_ms=workflow.agent.stall_timeout_ms,
        )
    if kind == "codex":
        return CodexAdapter(
            command=workflow.agent.command or "codex app-server",
            approval_policy=workflow.agent.approval_policy or "never",
            sandbox_policy=workflow.agent.sandbox_policy or "workspace-write",
            stall_timeout_ms=workflow.agent.stall_timeout_ms,
        )
    if kind == "opencode":
        return OpenCodeAdapter(
            command=workflow.agent.command or "opencode",
            mode=workflow.agent.mode or "acp",
            model=workflow.agent.model,
            provider=workflow.agent.provider,
            stall_timeout_ms=workflow.agent.stall_timeout_ms,
        )
    raise ValueError(f"unknown agent.kind: {kind}")


def _workflow_path() -> Path:
    raw = os.environ.get("SYMPHONY_WORKFLOW") or "./WORKFLOW.md"
    return Path(raw).resolve()


async def _watch_workflow(app: FastAPI, path: Path) -> None:
    bus: EventBus = app.state.events
    try:
        async for _ in awatch(str(path), stop_event=app.state.shutdown_event):
            try:
                new_workflow = load_workflow(path)
            except Exception as exc:
                log.warning("workflow_reload_failed", error=str(exc))
                await bus.publish("config_reloaded", ok=False, error=str(exc))
                continue
            app.state.workflow = new_workflow
            await bus.publish("config_reloaded", ok=True)
            log.info("workflow_reloaded")
    except asyncio.CancelledError:
        return
    except Exception as exc:
        log.warning("workflow_watcher_error", error=str(exc))


@asynccontextmanager
async def lifespan(app: FastAPI):
    load_dotenv()
    configure_logging()

    path = _workflow_path()
    workflow = load_workflow(path)
    app.state.workflow = workflow

    bus = EventBus()
    app.state.events = bus
    app.state.shutdown_event = asyncio.Event()

    tracker = GitHubTracker(workflow.tracker)
    await tracker.ensure_labels()
    app.state.tracker = tracker

    workspace_manager = WorkspaceManager(workflow.tracker, workflow.workspace)
    app.state.workspace_manager = workspace_manager

    orchestrator = Orchestrator(
        workflow_provider=lambda: app.state.workflow,
        tracker=tracker,
        workspace_manager=workspace_manager,
        adapter_factory=build_adapter,
        events=bus,
    )
    app.state.orchestrator = orchestrator
    await orchestrator.start()

    watch_task = asyncio.create_task(_watch_workflow(app, path), name="workflow-watch")

    try:
        yield
    finally:
        app.state.shutdown_event.set()
        watch_task.cancel()
        try:
            await watch_task
        except (asyncio.CancelledError, Exception):
            pass
        await orchestrator.stop()


app = FastAPI(title="Gravelord", lifespan=lifespan)
app.include_router(router)


def cli() -> None:
    """Console entry point: `gravelord [path-to-WORKFLOW.md]`."""
    if len(sys.argv) > 1:
        os.environ["SYMPHONY_WORKFLOW"] = sys.argv[1]
    import uvicorn

    uvicorn.run(
        "gravelord.main:app",
        host=os.environ.get("HOST", "0.0.0.0"),
        port=int(os.environ.get("PORT", "8000")),
        reload=False,
    )


if __name__ == "__main__":
    cli()
