from __future__ import annotations

from pathlib import Path

import yaml

from gravelord.daemon_config import (
    DaemonConfig,
    DaemonDefaults,
    RepoConfig,
    diff_repos,
    load_daemon_config,
    save_daemon_config,
)


def test_creates_empty_file_when_missing(tmp_path):
    p = tmp_path / "config.yaml"
    cfg = load_daemon_config(p)
    assert p.exists()
    assert cfg.port == 7777
    assert cfg.defaults.agent == "claude-code"
    assert cfg.repos == []


def test_roundtrip(tmp_path):
    p = tmp_path / "config.yaml"
    cfg = DaemonConfig(
        port=7777,
        defaults=DaemonDefaults(agent="codex", max_concurrent=5),
        repos=[
            RepoConfig(
                id="ai-backend",
                path="/tmp/ai-backend",
                owner="rocket-apps-org",
                name="ai-backend",
                default_branch="main",
            ),
        ],
    )
    save_daemon_config(cfg, p)
    again = load_daemon_config(p)
    assert again.port == 7777
    assert again.defaults.agent == "codex"
    assert again.repos[0].id == "ai-backend"
    assert again.repos[0].default_branch == "main"
    # YAML uses the camelCase alias for defaultBranch when written.
    raw = yaml.safe_load(p.read_text())
    assert raw["repos"][0]["defaultBranch"] == "main"


def test_repo_config_accepts_default_branch_alias():
    raw = {
        "id": "x",
        "path": "/tmp/x",
        "owner": "o",
        "name": "x",
        "defaultBranch": "develop",
    }
    repo = RepoConfig.model_validate(raw)
    assert repo.default_branch == "develop"


def test_diff_repos_added_removed_changed():
    r1 = RepoConfig(id="a", path="/a", owner="o", name="a", default_branch="main")
    r2 = RepoConfig(id="b", path="/b", owner="o", name="b", default_branch="main")
    r1_changed = r1.model_copy(update={"agent": "codex"})
    added, removed, changed = diff_repos([r1, r2], [r1_changed])
    assert [r.id for r in added] == []
    assert removed == ["b"]
    assert [r.id for r in changed] == ["a"]


def test_repo_absolute_path_expands_user(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    r = RepoConfig(
        id="x", path="~/project", owner="o", name="x", default_branch="main"
    )
    assert r.absolute_path == (tmp_path / "project").resolve()
