"""Repo registry — wraps a RepoConfig + its tracker, workspace manager, and
per-repo WORKFLOW.md template (with hot-reload of that one file).

The orchestrator polls registered RepoRuntimes; the lifespan watcher on
~/.gravelord/config.yaml drives register/unregister.
"""
from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

import structlog
from watchfiles import awatch

from .daemon_config import DaemonDefaults, RepoConfig
from .events import EventBus
from .tracker.base import TrackerConfig
from .tracker.github import GitHubTracker
from .workflow import WorkflowDefinition, WorkspaceConfig, load_workflow
from .workspace import WorkspaceManager

log = structlog.get_logger("gravelord.repos")


def _github_token() -> str:
    return os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN") or ""


def workflow_path_for(repo: RepoConfig) -> Path:
    return repo.absolute_path / "WORKFLOW.md"


def _default_workflow_definition(repo: RepoConfig) -> WorkflowDefinition:
    return WorkflowDefinition(
        workspace=WorkspaceConfig(),
        prompt_body=(
            "You are working on a GitHub issue in {{ repo.owner }}/{{ repo.name }}.\n\n"
            "Issue: {{ issue.identifier }} — {{ issue.title }}\n"
            "{% if issue.description %}\n{{ issue.description }}\n{% endif %}\n"
        ),
        base_dir=str(repo.absolute_path),
    )


def _load_workflow_or_default(repo: RepoConfig) -> WorkflowDefinition:
    wf_path = workflow_path_for(repo)
    if wf_path.exists():
        return load_workflow(wf_path)
    return _default_workflow_definition(repo)


def build_tracker_config(repo: RepoConfig) -> TrackerConfig:
    return TrackerConfig(
        kind="github",
        token=_github_token(),
        owner=repo.owner,
        repo=repo.name,
        default_branch=repo.default_branch,
    )


@dataclass
class RepoRuntime:
    config: RepoConfig
    workflow: WorkflowDefinition
    tracker: GitHubTracker
    workspace_manager: WorkspaceManager
    workflow_watch_task: asyncio.Task | None = field(default=None, repr=False)


class RepoRegistry:
    def __init__(self, defaults: DaemonDefaults, events: EventBus) -> None:
        self._defaults = defaults
        self._events = events
        self._runtimes: dict[str, RepoRuntime] = {}
        self._lock = asyncio.Lock()

    @property
    def defaults(self) -> DaemonDefaults:
        return self._defaults

    def get(self, repo_id: str) -> RepoRuntime | None:
        return self._runtimes.get(repo_id)

    def all(self) -> list[RepoRuntime]:
        return list(self._runtimes.values())

    def find_by_owner_repo(self, owner: str, name: str) -> RepoRuntime | None:
        for rt in self._runtimes.values():
            if rt.config.owner == owner and rt.config.name == name:
                return rt
        return None

    async def register(self, cfg: RepoConfig) -> RepoRuntime:
        async with self._lock:
            if cfg.id in self._runtimes:
                return self._runtimes[cfg.id]
            repo_path = cfg.absolute_path
            if not repo_path.exists():
                raise FileNotFoundError(
                    f"repo path does not exist: {repo_path} (repo id={cfg.id})"
                )
            tracker_cfg = build_tracker_config(cfg)
            tracker = GitHubTracker(tracker_cfg)
            try:
                await tracker.ensure_labels()
            except Exception as exc:
                log.warning(
                    "ensure_labels_failed",
                    repo_id=cfg.id,
                    error=f"{type(exc).__name__}: {exc}",
                )
            workflow = _load_workflow_or_default(cfg)
            ws_manager = WorkspaceManager(
                tracker_cfg,
                workflow.workspace,
                repo_path=cfg.absolute_path,
            )
            runtime = RepoRuntime(
                config=cfg,
                workflow=workflow,
                tracker=tracker,
                workspace_manager=ws_manager,
            )
            runtime.workflow_watch_task = asyncio.create_task(
                self._watch_workflow(runtime),
                name=f"workflow-watch:{cfg.id}",
            )
            self._runtimes[cfg.id] = runtime
            await self._events.publish(
                "repo_registered",
                repo_id=cfg.id,
                owner=cfg.owner,
                name=cfg.name,
            )
            log.info("repo_registered", repo_id=cfg.id, owner=cfg.owner, name=cfg.name)
            return runtime

    async def unregister(self, repo_id: str) -> RepoRuntime | None:
        async with self._lock:
            runtime = self._runtimes.pop(repo_id, None)
        if runtime is None:
            return None
        task = runtime.workflow_watch_task
        if task is not None and not task.done():
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass
        await self._events.publish("repo_unregistered", repo_id=repo_id)
        log.info("repo_unregistered", repo_id=repo_id)
        return runtime

    async def stop_all(self) -> None:
        for repo_id in list(self._runtimes.keys()):
            await self.unregister(repo_id)

    async def _watch_workflow(self, runtime: RepoRuntime) -> None:
        wf_path = workflow_path_for(runtime.config)
        try:
            async for _ in awatch(str(wf_path.parent), recursive=False):
                if not wf_path.exists():
                    continue
                try:
                    new_wf = load_workflow(wf_path)
                except Exception as exc:
                    log.warning(
                        "workflow_reload_failed",
                        repo_id=runtime.config.id,
                        error=str(exc),
                    )
                    await self._events.publish(
                        "workflow_reload_failed",
                        repo_id=runtime.config.id,
                        error=str(exc),
                    )
                    continue
                runtime.workflow = new_wf
                # Workspace root may have changed; rebuild the manager.
                runtime.workspace_manager = WorkspaceManager(
                    runtime.tracker.config,
                    new_wf.workspace,
                    repo_path=runtime.config.absolute_path,
                )
                await self._events.publish(
                    "workflow_reloaded",
                    repo_id=runtime.config.id,
                )
        except asyncio.CancelledError:
            return
        except Exception as exc:
            log.warning(
                "workflow_watcher_error",
                repo_id=runtime.config.id,
                error=str(exc),
            )


def reconcile_repos(
    registry: RepoRegistry,
    desired: Iterable[RepoConfig],
) -> tuple[list[RepoConfig], list[str], list[RepoConfig]]:
    """Returns (added, removed_ids, changed) by comparing the registry to a
    desired set."""
    current = [rt.config for rt in registry.all()]
    from .daemon_config import diff_repos

    return diff_repos(current, desired)
