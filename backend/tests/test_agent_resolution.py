from __future__ import annotations

from gravelord.orchestrator import resolve_agent_kind
from gravelord.tracker.base import IssueRecord


def _issue(agent_kind: str | None = None) -> IssueRecord:
    return IssueRecord(
        id="id",
        identifier="o/r#1",
        title="t",
        state="gravelord/todo",
        branch="b",
        agent_kind=agent_kind,
    )


def test_label_wins_over_repo_and_global():
    assert (
        resolve_agent_kind(
            issue=_issue("opencode"),
            override=None,
            repo_default="codex",
            global_default="claude-code",
        )
        == "opencode"
    )


def test_override_wins_over_label():
    # The orchestrator merges trigger-time overrides onto issue.agent_kind
    # before calling _dispatch, so a fresh override == issue.agent_kind here.
    assert (
        resolve_agent_kind(
            issue=_issue("opencode"),
            override="codex",  # not actually used in this path, but documented
            repo_default=None,
            global_default="claude-code",
        )
        == "codex"
    )


def test_repo_default_when_no_label_or_override():
    assert (
        resolve_agent_kind(
            issue=_issue(None),
            override=None,
            repo_default="opencode",
            global_default="claude-code",
        )
        == "opencode"
    )


def test_global_default_as_last_resort():
    assert (
        resolve_agent_kind(
            issue=_issue(None),
            override=None,
            repo_default=None,
            global_default="claude-code",
        )
        == "claude-code"
    )
