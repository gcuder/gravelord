from __future__ import annotations

import pytest

from gravelord.workflow import (
    FrontMatterNotMap,
    MissingWorkflowFile,
    WorkflowError,
    WorkflowParseError,
    load_workflow,
)


BARE_TEMPLATE = """Hello {{ issue.identifier }} ({{ repo.owner }}/{{ repo.name }})
"""


WORKSPACE_OVERRIDE_TEMPLATE = """---
workspace:
  root: ./tmp
---

Hello {{ issue.identifier }} ({{ repo.owner }}/{{ repo.name }})
"""


def test_load_bare_template(tmp_path):
    f = tmp_path / "WORKFLOW.md"
    f.write_text(BARE_TEMPLATE)
    wf = load_workflow(f)
    rendered = wf.render(
        issue=type("I", (), {"identifier": "octocat/hello#1"})(),
        repo={"owner": "octocat", "name": "hello"},
    )
    assert "octocat/hello#1" in rendered
    # No workspace override → root is None; WorkspaceManager fills in the default
    # at register time using the repo path.
    assert wf.workspace.root is None


def test_workspace_override_resolved_against_workflow_dir(tmp_path):
    f = tmp_path / "WORKFLOW.md"
    f.write_text(WORKSPACE_OVERRIDE_TEMPLATE)
    wf = load_workflow(f)
    assert wf.workspace.root is not None
    assert wf.workspace.root.endswith("tmp")


def test_missing_file(tmp_path):
    with pytest.raises(MissingWorkflowFile):
        load_workflow(tmp_path / "does-not-exist.md")


def test_unterminated_front_matter(tmp_path):
    f = tmp_path / "WORKFLOW.md"
    f.write_text("---\nworkspace:\n  root: ./tmp\n\nbody")
    with pytest.raises(WorkflowParseError):
        load_workflow(f)


def test_front_matter_not_a_map(tmp_path):
    f = tmp_path / "WORKFLOW.md"
    f.write_text("---\n- a\n- b\n---\nbody\n")
    with pytest.raises(FrontMatterNotMap):
        load_workflow(f)


def test_strict_undefined_raises(tmp_path):
    body = "Hello {{ nope }}"
    f = tmp_path / "WORKFLOW.md"
    f.write_text(body)
    wf = load_workflow(f)
    with pytest.raises(WorkflowError):
        wf.render(issue=object(), repo={"owner": "a", "name": "b"})
