"""Per-issue git workspaces, rooted inside each repo.

Default workspace root is `<repo.path>/.gravelord_workspaces/`. A repo's
WORKFLOW.md may override via `workspace.root`. Default branch comes from
the daemon's RepoConfig (no probing origin/main → origin/master).

Invariants:
  1. Coding agent runs only inside workspace_path.
  2. workspace_path stays inside workspace_root.
  3. Directory name = sanitized identifier ([A-Za-z0-9._-] only).
"""
from __future__ import annotations

import asyncio
import re
import shutil
from dataclasses import dataclass
from pathlib import Path

import structlog

from .tracker.base import IssueRecord, TrackerConfig
from .workflow import WorkspaceConfig

log = structlog.get_logger("gravelord.workspace")

_SAFE_KEY = re.compile(r"[^A-Za-z0-9._-]")

DEFAULT_WORKSPACE_DIR = ".gravelord_workspaces"


def sanitize_key(identifier: str) -> str:
    return _SAFE_KEY.sub("_", identifier)


@dataclass
class Workspace:
    path: Path
    key: str
    created_now: bool


class WorkspaceError(Exception):
    pass


class WorkspaceManager:
    def __init__(
        self,
        tracker_cfg: TrackerConfig,
        workspace_cfg: WorkspaceConfig,
        *,
        repo_path: Path,
    ) -> None:
        self._tracker = tracker_cfg
        self._repo_path = Path(repo_path).expanduser().resolve()
        root = workspace_cfg.root
        if root:
            resolved = Path(root).expanduser()
            if not resolved.is_absolute():
                resolved = (self._repo_path / resolved).resolve()
            self._root = resolved.resolve()
        else:
            self._root = (self._repo_path / DEFAULT_WORKSPACE_DIR).resolve()
        self._root.mkdir(parents=True, exist_ok=True)

    @property
    def root(self) -> Path:
        return self._root

    @property
    def repo_path(self) -> Path:
        return self._repo_path

    def _path_for(self, identifier: str) -> tuple[Path, str]:
        key = sanitize_key(identifier)
        path = (self._root / key).resolve()
        if self._root not in path.parents and path != self._root:
            raise WorkspaceError(f"workspace path escapes root: {path}")
        return path, key

    async def ensure(self, issue: IssueRecord) -> Workspace:
        path, key = self._path_for(issue.identifier)
        created_now = False
        if not path.exists():
            await self._git_clone(path)
            created_now = True
        await self._checkout_branch(path, issue.branch)
        return Workspace(path=path, key=key, created_now=created_now)

    async def cleanup(self, issue: IssueRecord) -> None:
        path, _ = self._path_for(issue.identifier)
        if not path.exists():
            return
        await asyncio.get_running_loop().run_in_executor(
            None, shutil.rmtree, str(path), True
        )
        log.info("workspace_cleaned", identifier=issue.identifier, path=str(path))

    async def _git_clone(self, dest: Path) -> None:
        token = self._tracker.token
        url = (
            f"https://x-access-token:{token}@github.com/"
            f"{self._tracker.owner}/{self._tracker.repo}.git"
        )
        dest.parent.mkdir(parents=True, exist_ok=True)
        await self._run_git("clone", "--depth=50", url, str(dest), cwd=dest.parent)
        await self._run_git("config", "user.email", "gravelord@example.invalid", cwd=dest)
        await self._run_git("config", "user.name", "Gravelord", cwd=dest)

    async def _checkout_branch(self, repo: Path, branch: str) -> None:
        await self._run_git("fetch", "origin", cwd=repo, check=False)

        remote_branch = await self._run_git(
            "rev-parse", "--verify", "--quiet", f"refs/remotes/origin/{branch}",
            cwd=repo, check=False, quiet=True,
        )
        if remote_branch:
            await self._run_git(
                "checkout", "-f", "-B", branch, f"origin/{branch}",
                cwd=repo, check=False,
            )
            return

        default_ref = f"origin/{self._tracker.default_branch}"
        ok = await self._run_git(
            "checkout", "-f", "-B", branch, default_ref,
            cwd=repo, check=False, quiet=True,
        )
        if ok:
            return
        await self._run_git("checkout", "-f", "-B", branch, cwd=repo, check=False)

    async def _run_git(
        self, *args: str, cwd: Path, check: bool = True, quiet: bool = False,
    ) -> bool:
        proc = await asyncio.create_subprocess_exec(
            "git",
            *args,
            cwd=str(cwd),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        out, err = await proc.communicate()
        ok = proc.returncode == 0
        if not ok and check:
            raise WorkspaceError(
                f"git {args[0]} failed (exit {proc.returncode}): "
                f"{err.decode(errors='replace')[:500]}"
            )
        if not ok and not quiet:
            log.warning(
                "git_command_failed",
                args=args,
                exit=proc.returncode,
                stderr=err.decode(errors="replace")[:200],
            )
        return ok
