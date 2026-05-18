from __future__ import annotations

import pytest

from gravelord.adapters.base import detect_completion
from gravelord.runner import _format_rework_section
from gravelord.tracker.base import ReviewComment, ReworkContext


def test_detect_completion_pr_url():
    is_complete, pr = detect_completion(
        "All done.\nhttps://github.com/octocat/hello/pull/42\n"
    )
    assert is_complete is True
    assert pr == "https://github.com/octocat/hello/pull/42"


def test_detect_completion_pr_url_with_trailing_punct():
    is_complete, pr = detect_completion("See https://github.com/o/r/pull/9 for details.")
    assert is_complete is True
    assert pr == "https://github.com/o/r/pull/9"


def test_detect_completion_label_signal():
    is_complete, pr = detect_completion("Setting gravelord/human-review label now")
    assert is_complete is True
    assert pr is None


def test_detect_completion_none():
    is_complete, pr = detect_completion("Still working on it.")
    assert is_complete is False
    assert pr is None


def test_format_rework_section_orders_and_labels_comments():
    ctx = ReworkContext(
        pr_url="https://github.com/o/r/pull/5",
        review_decision="changes_requested",
        comments=[
            ReviewComment(user="alice", path="a.py", line=10, body="please fix"),
            ReviewComment(user="bob", path=None, line=None, body="overall LGTM minus tests"),
        ],
    )
    out = _format_rework_section(ctx)
    assert "## Previous attempt" in out
    assert "PR: https://github.com/o/r/pull/5" in out
    assert "alice" in out and "bob" in out
    assert "a.py:10" in out
    assert "please fix" in out
