"""Global daemon config at ~/.gravelord/config.yaml.

Schema, load/save, and a small diff helper for hot-reload.
"""
from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import Iterable, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field

from .tracker.base import AgentKind


def default_config_path() -> Path:
    return Path(os.path.expanduser("~/.gravelord/config.yaml"))


class DaemonDefaults(BaseModel):
    agent: AgentKind = "claude-code"
    max_concurrent: int = 3
    max_turns: int = 20
    stall_timeout_ms: int = 300_000
    poll_interval_ms: int = 30_000
    max_retry_backoff_ms: int = 600_000


class RepoConfig(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: str
    path: str
    owner: str
    name: str
    default_branch: str = Field(default="main", alias="defaultBranch")
    agent: AgentKind | None = None

    @property
    def absolute_path(self) -> Path:
        return Path(os.path.expanduser(self.path)).resolve()


class DaemonConfig(BaseModel):
    port: int = 7777
    defaults: DaemonDefaults = Field(default_factory=DaemonDefaults)
    repos: list[RepoConfig] = Field(default_factory=list)


def load_daemon_config(path: Path | None = None) -> DaemonConfig:
    p = path or default_config_path()
    if not p.exists():
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(yaml.safe_dump(DaemonConfig().model_dump(by_alias=True)), encoding="utf-8")
        return DaemonConfig()
    raw = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    if not isinstance(raw, dict):
        raise ValueError(f"{p} must be a YAML mapping")
    return DaemonConfig.model_validate(raw)


def save_daemon_config(cfg: DaemonConfig, path: Path | None = None) -> None:
    p = path or default_config_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    data = cfg.model_dump(by_alias=True)
    yaml_text = yaml.safe_dump(data, sort_keys=False)
    # Atomic write: tmp file in same dir, then rename.
    fd, tmp_name = tempfile.mkstemp(prefix=".config.", suffix=".yaml.tmp", dir=str(p.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(yaml_text)
        os.replace(tmp_name, p)
    except Exception:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def diff_repos(
    old: Iterable[RepoConfig],
    new: Iterable[RepoConfig],
) -> tuple[list[RepoConfig], list[str], list[RepoConfig]]:
    """Return (added, removed_ids, changed). 'changed' = same id, different fields."""
    old_by_id = {r.id: r for r in old}
    new_by_id = {r.id: r for r in new}
    added = [r for rid, r in new_by_id.items() if rid not in old_by_id]
    removed = [rid for rid in old_by_id if rid not in new_by_id]
    changed: list[RepoConfig] = []
    for rid, new_r in new_by_id.items():
        if rid in old_by_id and old_by_id[rid].model_dump() != new_r.model_dump():
            changed.append(new_r)
    return added, removed, changed
