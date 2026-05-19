"""Parse and hot-reload WORKFLOW.md.

Post-refactor schema: WORKFLOW.md is per-repo and contains only the Jinja2
prompt template (plus an optional `workspace.root` override). Tracker / agent
config lives in ~/.gravelord/config.yaml; agent kind / model / reasoning are
chosen per-issue at dispatch time.
"""
from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

import yaml
from jinja2 import Environment, StrictUndefined, Template, TemplateError
from pydantic import BaseModel, Field


class WorkflowError(Exception):
    pass


class MissingWorkflowFile(WorkflowError):
    pass


class WorkflowParseError(WorkflowError):
    pass


class FrontMatterNotMap(WorkflowError):
    pass


class WorkspaceConfig(BaseModel):
    root: str | None = None  # resolved by WorkspaceManager against repo path


_VAR_PATTERN = re.compile(r"^\$([A-Z_][A-Z0-9_]*)$")


def _resolve_var(value: str) -> str:
    m = _VAR_PATTERN.match(value.strip())
    if not m:
        return value
    return os.environ.get(m.group(1), "")


def _expand_path(value: str) -> str:
    value = os.path.expanduser(value)
    if value.startswith("$"):
        value = os.path.expandvars(value)
    return value


class WorkflowDefinition(BaseModel):
    workspace: WorkspaceConfig = Field(default_factory=WorkspaceConfig)
    prompt_body: str
    base_dir: str  # WORKFLOW.md's directory (absolute)

    @property
    def template(self) -> Template:
        env = Environment(undefined=StrictUndefined, autoescape=False)
        return env.from_string(self.prompt_body)

    def render(self, **context: Any) -> str:
        try:
            return self.template.render(**context)
        except TemplateError as exc:
            raise WorkflowError(f"template render error: {exc}") from exc


def _split_front_matter(text: str) -> tuple[dict, str]:
    if not text.startswith("---"):
        return {}, text.strip()
    lines = text.splitlines(keepends=True)
    if lines[0].strip() != "---":
        return {}, text.strip()
    end_idx = None
    for i, line in enumerate(lines[1:], start=1):
        if line.strip() == "---":
            end_idx = i
            break
    if end_idx is None:
        raise WorkflowParseError("front matter not terminated by '---'")
    yaml_text = "".join(lines[1:end_idx])
    body = "".join(lines[end_idx + 1 :]).strip()
    try:
        config = yaml.safe_load(yaml_text) or {}
    except yaml.YAMLError as exc:
        raise WorkflowParseError(f"invalid YAML front matter: {exc}") from exc
    if not isinstance(config, dict):
        raise FrontMatterNotMap("front matter must be a YAML map")
    return config, body


def _resolve_indirection(config: dict) -> dict:
    def _walk(node):
        if isinstance(node, dict):
            return {k: _walk(v) for k, v in node.items()}
        if isinstance(node, list):
            return [_walk(v) for v in node]
        if isinstance(node, str):
            return _resolve_var(node)
        return node
    return _walk(config)


DEFAULT_PROMPT_BODY = (
    "You are working on a GitHub issue.\n\n"
    "Issue: {{ issue.identifier }} — {{ issue.title }}\n"
    "{% if issue.description %}\n{{ issue.description }}\n{% endif %}\n"
)


def load_workflow(path: str | Path) -> WorkflowDefinition:
    p = Path(path)
    if not p.exists():
        raise MissingWorkflowFile(f"workflow file not found: {p}")
    text = p.read_text(encoding="utf-8")
    config, body = _split_front_matter(text)
    config = _resolve_indirection(config)

    if not body:
        body = DEFAULT_PROMPT_BODY

    workspace_cfg = config.get("workspace", {}) or {}
    if "root" in workspace_cfg and workspace_cfg["root"]:
        workspace_cfg["root"] = _expand_path(workspace_cfg["root"])
        if not os.path.isabs(workspace_cfg["root"]):
            workspace_cfg["root"] = str((p.parent / workspace_cfg["root"]).resolve())

    try:
        return WorkflowDefinition(
            workspace=WorkspaceConfig(**workspace_cfg),
            prompt_body=body,
            base_dir=str(p.parent.resolve()),
        )
    except Exception as exc:
        raise WorkflowParseError(f"invalid workflow config: {exc}") from exc
