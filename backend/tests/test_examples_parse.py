from __future__ import annotations

from pathlib import Path

import pytest

from gravelord.workflow import load_workflow

EXAMPLES = Path(__file__).resolve().parents[1] / "examples"


@pytest.mark.parametrize(
    "name",
    ["claude-code.WORKFLOW.md", "codex.WORKFLOW.md", "opencode-ollama.WORKFLOW.md"],
)
def test_example_parses(name):
    wf = load_workflow(EXAMPLES / name)
    # All examples should render with stub context.
    rendered = wf.render(
        issue=type(
            "I",
            (),
            {
                "identifier": "octo/hello#1",
                "title": "t",
                "description": "d",
                "branch": "b",
                "url": "u",
            },
        )(),
        repo={"owner": "octo", "name": "hello"},
    )
    assert "octo/hello#1" in rendered
