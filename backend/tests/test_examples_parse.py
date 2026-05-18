from __future__ import annotations

from pathlib import Path

import pytest

from gravelord.workflow import load_workflow

EXAMPLES = Path(__file__).resolve().parents[1] / "examples"


@pytest.mark.parametrize("name", ["claude-code.WORKFLOW.md", "codex.WORKFLOW.md", "opencode-ollama.WORKFLOW.md"])
def test_example_parses(monkeypatch, name):
    monkeypatch.setenv("GITHUB_TOKEN", "ghp_test_token")
    wf = load_workflow(EXAMPLES / name)
    assert wf.tracker.kind == "github"
    assert wf.tracker.token == "ghp_test_token"
    assert wf.agent.kind in {"claude-code", "codex", "opencode"}
