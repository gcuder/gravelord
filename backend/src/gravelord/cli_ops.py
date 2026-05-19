"""Shared add/remove logic for the CLI and the /api/repos endpoint.

Both ultimately mutate ~/.gravelord/config.yaml; the daemon's watchfiles
listener picks the change up and (un)registers repo runtimes.
"""
from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

from .daemon_config import (
    DaemonConfig,
    RepoConfig,
    default_config_path,
    load_daemon_config,
    save_daemon_config,
)
from .tracker.base import AgentKind


_GH_SSH = re.compile(r"git@github\.com:([^/]+)/([^/.]+?)(?:\.git)?$")
_GH_HTTPS = re.compile(r"https?://github\.com/([^/]+)/([^/.]+?)(?:\.git)?$")
_SLUG_BAD = re.compile(r"[^a-z0-9._-]+")


def _git(cwd: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", *args],
        cwd=str(cwd),
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        raise ValueError(
            f"git {' '.join(args)} failed in {cwd}: {proc.stderr.strip()}"
        )
    return proc.stdout.strip()


def parse_remote(url: str) -> tuple[str, str]:
    for pattern in (_GH_SSH, _GH_HTTPS):
        m = pattern.match(url.strip())
        if m:
            return m.group(1), m.group(2)
    raise ValueError(f"could not parse GitHub remote: {url}")


def detect_default_branch(repo_path: Path) -> str:
    try:
        out = _git(repo_path, "symbolic-ref", "--short", "refs/remotes/origin/HEAD")
        if out.startswith("origin/"):
            return out.split("/", 1)[1]
        return out
    except ValueError:
        # Fallback: try main, then master.
        for guess in ("main", "master"):
            try:
                _git(repo_path, "rev-parse", "--verify", f"refs/remotes/origin/{guess}")
                return guess
            except ValueError:
                continue
        return "main"


def slugify_id(name: str) -> str:
    s = _SLUG_BAD.sub("-", name.lower()).strip("-")
    return s or "repo"


def derive_repo_fields(repo_path: Path) -> tuple[str, str, str]:
    """Returns (owner, name, default_branch) by querying git."""
    if not repo_path.exists():
        raise FileNotFoundError(f"path does not exist: {repo_path}")
    if not (repo_path / ".git").exists():
        raise ValueError(f"not a git repository: {repo_path}")
    try:
        remote = _git(repo_path, "remote", "get-url", "origin")
    except ValueError as exc:
        raise ValueError(f"no origin remote in {repo_path}: {exc}") from exc
    owner, name = parse_remote(remote)
    default_branch = detect_default_branch(repo_path)
    return owner, name, default_branch


def _ensure_unique_id(cfg: DaemonConfig, base: str) -> str:
    existing = {r.id for r in cfg.repos}
    if base not in existing:
        return base
    n = 2
    while f"{base}-{n}" in existing:
        n += 1
    return f"{base}-{n}"


def add_repo_to_config(
    *,
    path: str,
    repo_id: str | None = None,
    agent: AgentKind | None = None,
    config_path: Path | None = None,
) -> RepoConfig:
    abs_path = Path(os.path.expanduser(path)).resolve()
    owner, name, default_branch = derive_repo_fields(abs_path)
    cfg_path = config_path or default_config_path()
    cfg = load_daemon_config(cfg_path)

    # Reject duplicates on (owner, name) so the same repo doesn't get listed twice.
    for existing in cfg.repos:
        if existing.owner == owner and existing.name == name:
            raise ValueError(
                f"repo {owner}/{name} already registered as id={existing.id}"
            )

    chosen_id = _ensure_unique_id(cfg, repo_id or slugify_id(name))
    new_repo = RepoConfig(
        id=chosen_id,
        path=str(abs_path),
        owner=owner,
        name=name,
        default_branch=default_branch,
        agent=agent,
    )
    cfg.repos.append(new_repo)
    save_daemon_config(cfg, cfg_path)
    return new_repo


def remove_repo_from_config(
    repo_id: str, *, config_path: Path | None = None
) -> bool:
    cfg_path = config_path or default_config_path()
    cfg = load_daemon_config(cfg_path)
    before = len(cfg.repos)
    cfg.repos = [r for r in cfg.repos if r.id != repo_id]
    if len(cfg.repos) == before:
        return False
    save_daemon_config(cfg, cfg_path)
    return True


def list_repos_from_config(*, config_path: Path | None = None) -> list[RepoConfig]:
    cfg = load_daemon_config(config_path or default_config_path())
    return cfg.repos
