from __future__ import annotations

from gravelord.tracker.base import TrackerConfig
from gravelord.workflow import WorkspaceConfig
from gravelord.workspace import WorkspaceManager, sanitize_key


def test_sanitize_key_basic():
    assert sanitize_key("octocat/hello#42") == "octocat_hello_42"


def test_sanitize_key_passthrough_safe_chars():
    assert sanitize_key("ABC-123.def_456") == "ABC-123.def_456"


def test_workspace_root_default_inside_repo(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    wm = WorkspaceManager(
        TrackerConfig(token="t", owner="o", repo="r"),
        WorkspaceConfig(),
        repo_path=repo,
    )
    assert wm.root == (repo / ".gravelord_workspaces").resolve()
    assert wm.root.exists()


def test_workspace_root_override(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    override = tmp_path / "elsewhere" / "ws"
    wm = WorkspaceManager(
        TrackerConfig(token="t", owner="o", repo="r"),
        WorkspaceConfig(root=str(override)),
        repo_path=repo,
    )
    assert wm.root == override.resolve()
    assert wm.root.exists()


def test_path_for_stays_within_root(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    wm = WorkspaceManager(
        TrackerConfig(token="t", owner="o", repo="r"),
        WorkspaceConfig(),
        repo_path=repo,
    )
    path, key = wm._path_for("octocat/hello#42")
    assert wm.root in path.parents
    assert key == "octocat_hello_42"


def test_path_for_blocks_traversal(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    wm = WorkspaceManager(
        TrackerConfig(token="t", owner="o", repo="r"),
        WorkspaceConfig(),
        repo_path=repo,
    )
    path, key = wm._path_for("../../etc/passwd")
    assert wm.root in path.parents
    assert "/" not in key
    assert str(path).startswith(str(wm.root))
