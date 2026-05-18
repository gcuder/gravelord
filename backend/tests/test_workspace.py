from __future__ import annotations

import pytest

from gravelord.workflow import TrackerConfig, WorkspaceConfig
from gravelord.workspace import WorkspaceManager, sanitize_key


def test_sanitize_key_basic():
    assert sanitize_key("octocat/hello#42") == "octocat_hello_42"


def test_sanitize_key_passthrough_safe_chars():
    assert sanitize_key("ABC-123.def_456") == "ABC-123.def_456"


def test_workspace_root_normalized(tmp_path):
    wm = WorkspaceManager(
        TrackerConfig(token="t", owner="o", repo="r"),
        WorkspaceConfig(root=str(tmp_path / "workspaces")),
    )
    assert wm.root.is_absolute()
    assert wm.root.exists()


def test_path_for_stays_within_root(tmp_path):
    wm = WorkspaceManager(
        TrackerConfig(token="t", owner="o", repo="r"),
        WorkspaceConfig(root=str(tmp_path / "ws")),
    )
    path, key = wm._path_for("octocat/hello#42")
    assert wm.root in path.parents
    assert key == "octocat_hello_42"


def test_path_for_blocks_traversal(tmp_path):
    wm = WorkspaceManager(
        TrackerConfig(token="t", owner="o", repo="r"),
        WorkspaceConfig(root=str(tmp_path / "ws")),
    )
    # Slashes are sanitized to underscores so traversal segments cannot escape root.
    path, key = wm._path_for("../../etc/passwd")
    assert wm.root in path.parents
    # No path separators in the sanitized key — every traversal char became "_".
    assert "/" not in key
    # Resolved path is still under root.
    assert str(path).startswith(str(wm.root))
