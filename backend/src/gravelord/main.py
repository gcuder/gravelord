"""FastAPI app + lifespan + Typer CLI (multi-repo daemon)."""
from __future__ import annotations

import asyncio
import os
from contextlib import asynccontextmanager
from pathlib import Path

import structlog
import typer
from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from watchfiles import awatch

from .adapters.base import AgentAdapter
from .adapters.claude_code import ClaudeCodeAdapter
from .adapters.codex import CodexAdapter
from .adapters.opencode import OpenCodeAdapter
from .api.routes import router
from .cli_ops import (
    add_repo_to_config,
    list_repos_from_config,
    remove_repo_from_config,
)
from .daemon_config import (
    DaemonConfig,
    DaemonDefaults,
    default_config_path,
    diff_repos,
    load_daemon_config,
)
from .events import EventBus, configure_logging
from .issue_settings import IssueSettingsStore
from .orchestrator import Orchestrator
from .repos import RepoRegistry

log = structlog.get_logger("gravelord.main")


def build_adapter(
    *,
    agent_kind: str,
    model: str | None,
    reasoning_level: str | None,
    defaults: DaemonDefaults,
) -> AgentAdapter:
    if agent_kind == "claude-code":
        return ClaudeCodeAdapter(
            command="claude",
            stall_timeout_ms=defaults.stall_timeout_ms,
            model=model,
            reasoning_level=reasoning_level,
        )
    if agent_kind == "codex":
        return CodexAdapter(
            command="codex app-server",
            approval_policy="never",
            sandbox_policy="workspace-write",
            stall_timeout_ms=defaults.stall_timeout_ms,
            model=model,
            reasoning_level=reasoning_level,
        )
    if agent_kind == "opencode":
        return OpenCodeAdapter(
            command="opencode",
            mode="acp",
            stall_timeout_ms=defaults.stall_timeout_ms,
            model=model,
            reasoning_level=reasoning_level,
        )
    raise ValueError(f"unknown agent kind: {agent_kind}")


async def _watch_config(app: FastAPI, path: Path) -> None:
    bus: EventBus = app.state.events
    registry: RepoRegistry = app.state.registry
    try:
        async for _ in awatch(str(path.parent), stop_event=app.state.shutdown_event):
            if not path.exists():
                continue
            try:
                new_cfg = load_daemon_config(path)
            except Exception as exc:
                log.warning("daemon_config_reload_failed", error=str(exc))
                await bus.publish("daemon_config_reload_failed", error=str(exc))
                continue
            old_repos = [rt.config for rt in registry.all()]
            added, removed, changed = diff_repos(old_repos, new_cfg.repos)
            for repo_id in removed:
                await app.state.orchestrator.unregister_repo(repo_id)
            for repo in added:
                try:
                    await registry.register(repo)
                except Exception as exc:
                    log.warning(
                        "repo_register_failed",
                        repo_id=repo.id,
                        error=f"{type(exc).__name__}: {exc}",
                        exc_info=True,
                    )
            for repo in changed:
                await app.state.orchestrator.unregister_repo(repo.id)
                try:
                    await registry.register(repo)
                except Exception as exc:
                    log.warning(
                        "repo_reregister_failed",
                        repo_id=repo.id,
                        error=f"{type(exc).__name__}: {exc}",
                        exc_info=True,
                    )
            app.state.daemon_config = new_cfg
            await bus.publish("daemon_config_reloaded")
    except asyncio.CancelledError:
        return
    except Exception as exc:
        log.warning("config_watcher_error", error=str(exc))


@asynccontextmanager
async def lifespan(app: FastAPI):
    load_dotenv()
    configure_logging()

    cfg_path: Path = getattr(app.state, "daemon_config_path", None) or default_config_path()
    app.state.daemon_config_path = cfg_path
    cfg: DaemonConfig = load_daemon_config(cfg_path)
    app.state.daemon_config = cfg

    bus = EventBus()
    app.state.events = bus
    app.state.shutdown_event = asyncio.Event()

    registry = RepoRegistry(cfg.defaults, bus)
    app.state.registry = registry
    for repo in cfg.repos:
        try:
            await registry.register(repo)
        except Exception as exc:
            log.warning(
                "repo_register_failed",
                repo_id=repo.id,
                error=f"{type(exc).__name__}: {exc}",
                exc_info=True,
            )

    issue_settings = IssueSettingsStore()
    await issue_settings.load()
    app.state.issue_settings = issue_settings

    orchestrator = Orchestrator(
        registry=registry,
        defaults=cfg.defaults,
        adapter_factory=build_adapter,
        events=bus,
        issue_settings=issue_settings,
    )
    app.state.orchestrator = orchestrator
    await orchestrator.start()

    watch_task = asyncio.create_task(_watch_config(app, cfg_path), name="config-watch")

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
        await registry.stop_all()


def create_app() -> FastAPI:
    fa = FastAPI(title="Gravelord", lifespan=lifespan)
    fa.include_router(router)

    static_dir = Path(__file__).parent / "static"
    if static_dir.exists() and any(static_dir.iterdir()):
        fa.mount(
            "/", StaticFiles(directory=str(static_dir), html=True), name="frontend"
        )
    return fa


app = create_app()


# --------------------------- Typer CLI ---------------------------

cli_app = typer.Typer(
    name="gravelord",
    add_completion=False,
    no_args_is_help=False,
    help="Multi-repo coding-agent orchestrator. Run with no subcommand to start the daemon.",
)


def _print_repos() -> None:
    repos = list_repos_from_config()
    if not repos:
        typer.echo("(no repos registered)")
        return
    fmt = "{:<24} {:<28} {:<24} {:<10} {}"
    typer.echo(fmt.format("ID", "OWNER/NAME", "BRANCH", "AGENT", "PATH"))
    for r in repos:
        typer.echo(
            fmt.format(
                r.id[:24],
                f"{r.owner}/{r.name}"[:28],
                r.default_branch[:24],
                (r.agent or "-")[:10],
                r.path,
            )
        )


@cli_app.command("add", help="Register a local repository with the daemon.")
def cli_add(
    path: str = typer.Argument(..., help="Path to the local git repository."),
    repo_id: str | None = typer.Option(None, "--id", help="Override generated repo id."),
    agent: str | None = typer.Option(
        None, "--agent", help="Pin a repo-level agent (claude-code|codex|opencode)."
    ),
) -> None:
    repo = add_repo_to_config(path=path, repo_id=repo_id, agent=agent)
    typer.echo(f"added {repo.id} ({repo.owner}/{repo.name}) → {repo.path}")


@cli_app.command("remove", help="Unregister a repository by id.")
def cli_remove(repo_id: str = typer.Argument(...)) -> None:
    if remove_repo_from_config(repo_id):
        typer.echo(f"removed {repo_id}")
    else:
        typer.echo(f"no repo with id={repo_id}", err=True)
        raise typer.Exit(code=1)


@cli_app.command("list", help="List registered repositories.")
def cli_list() -> None:
    _print_repos()


@cli_app.command("daemon", help="Start the daemon explicitly (same as no subcommand).")
def cli_daemon(
    host: str = typer.Option(
        os.environ.get("HOST", "127.0.0.1"), "--host", help="Bind host."
    ),
    port: int | None = typer.Option(None, "--port", help="Override config port."),
) -> None:
    _run_daemon(host=host, port_override=port)


def _run_daemon(*, host: str, port_override: int | None) -> None:
    import uvicorn

    cfg = load_daemon_config()
    port = port_override or cfg.port
    uvicorn.run(
        "gravelord.main:app",
        host=host,
        port=int(os.environ.get("PORT", port)),
        reload=False,
    )


@cli_app.callback(invoke_without_command=True)
def _default(ctx: typer.Context) -> None:
    if ctx.invoked_subcommand is None:
        _run_daemon(
            host=os.environ.get("HOST", "127.0.0.1"),
            port_override=None,
        )


def cli() -> None:
    cli_app()


if __name__ == "__main__":
    cli()
