from __future__ import annotations

import os

import pytest

from gravelord.workflow import (
    FrontMatterNotMap,
    MissingWorkflowFile,
    WorkflowParseError,
    load_workflow,
)


WORKFLOW_TEMPLATE = """---
tracker:
  kind: github
  token: $TEST_GH_TOKEN
  owner: octocat
  repo: hello

agent:
  kind: claude-code
  max_concurrent: 5
  max_turns: 7

workspace:
  root: ./tmp
---

Hello {{ issue.identifier }} ({{ repo.owner }}/{{ repo.name }})
"""


def test_load_basic(tmp_path, monkeypatch):
    monkeypatch.setenv("TEST_GH_TOKEN", "tok-abc")
    f = tmp_path / "WORKFLOW.md"
    f.write_text(WORKFLOW_TEMPLATE)
    wf = load_workflow(f)
    assert wf.tracker.owner == "octocat"
    assert wf.tracker.token == "tok-abc"
    assert wf.agent.max_concurrent == 5
    assert wf.agent.max_turns == 7
    # workspace.root resolved relative to WORKFLOW.md dir
    assert wf.workspace.root.endswith("tmp")
    rendered = wf.render(
        issue=type("I", (), {"identifier": "octocat/hello#1"})(),
        repo={"owner": "octocat", "name": "hello"},
    )
    assert "octocat/hello#1" in rendered


def test_missing_file(tmp_path):
    with pytest.raises(MissingWorkflowFile):
        load_workflow(tmp_path / "does-not-exist.md")


def test_unterminated_front_matter(tmp_path):
    f = tmp_path / "WORKFLOW.md"
    f.write_text("---\ntracker:\n  kind: github\n\nbody")
    with pytest.raises(WorkflowParseError):
        load_workflow(f)


def test_front_matter_not_a_map(tmp_path):
    f = tmp_path / "WORKFLOW.md"
    f.write_text("---\n- a\n- b\n---\nbody\n")
    with pytest.raises(FrontMatterNotMap):
        load_workflow(f)


def test_strict_undefined_raises(tmp_path, monkeypatch):
    monkeypatch.setenv("TEST_GH_TOKEN", "tok")
    body = "Hello {{ nope }}"
    text = "---\ntracker:\n  kind: github\n  token: $TEST_GH_TOKEN\n  owner: a\n  repo: b\n---\n" + body
    f = tmp_path / "WORKFLOW.md"
    f.write_text(text)
    wf = load_workflow(f)
    from gravelord.workflow import WorkflowError

    with pytest.raises(WorkflowError):
        wf.render(issue=object(), repo={"owner": "a", "name": "b"})


def test_var_resolution_empty_when_missing(tmp_path, monkeypatch):
    monkeypatch.delenv("TEST_GH_TOKEN_UNSET", raising=False)
    text = "---\ntracker:\n  kind: github\n  token: $TEST_GH_TOKEN_UNSET\n  owner: a\n  repo: b\n---\nbody\n"
    f = tmp_path / "WORKFLOW.md"
    f.write_text(text)
    wf = load_workflow(f)
    assert wf.tracker.token == ""
