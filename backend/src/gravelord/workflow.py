"""Parse and hot-reload WORKFLOW.md.

Schema is the override schema (tracker.kind=github + agent.kind), not the spec's
linear-only schema. See plan and README.
"""
from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any, Literal

import yaml
from jinja2 import Environment, StrictUndefined, Template, TemplateError
from pydantic import BaseModel, Field, field_validator


class WorkflowError(Exception):
    pass


class MissingWorkflowFile(WorkflowError):
    pass


class WorkflowParseError(WorkflowError):
    pass


class FrontMatterNotMap(WorkflowError):
    pass


class TrackerConfig(BaseModel):
    kind: Literal["github"] = "github"
    token: str
    owner: str
    repo: str
    active_labels: list[str] = Field(default_factory=lambda: ["gravelord/todo", "gravelord/rework"])
    in_progress_label: str = "gravelord/in-progress"
    review_label: str = "gravelord/human-review"
    done_label: str = "gravelord/done"
    rework_label: str = "gravelord/rework"


class AgentConfig(BaseModel):
    kind: Literal["claude-code", "codex", "opencode"] = "claude-code"
    max_concurrent: int = 3
    max_turns: int = 20
    stall_timeout_ms: int = 300_000
    max_retry_backoff_ms: int = 300_000
    poll_interval_ms: int = 30_000

    # Agent-specific knobs (only consumed by the matching adapter)
    command: str | None = None
    mode: Literal["acp", "print"] | None = None
    model: str | None = None
    provider: str | None = None
    approval_policy: str | None = None
    sandbox_policy: str | None = None

    @field_validator("max_concurrent", "max_turns")
    @classmethod
    def _positive(cls, v: int) -> int:
        if v <= 0:
            raise ValueError("must be positive")
        return v


class WorkspaceConfig(BaseModel):
    root: str = "./gravelord_workspaces"


_VAR_PATTERN = re.compile(r"^\$([A-Z_][A-Z0-9_]*)$")


def _resolve_var(value: str) -> str:
    m = _VAR_PATTERN.match(value.strip())
    if not m:
        return value
    name = m.group(1)
    return os.environ.get(name, "")


def _expand_path(value: str) -> str:
    value = os.path.expanduser(value)
    if value.startswith("$"):
        # Path env vars like $HOME/foo handled by expandvars
        value = os.path.expandvars(value)
    return value


class WorkflowDefinition(BaseModel):
    tracker: TrackerConfig
    agent: AgentConfig = Field(default_factory=AgentConfig)
    workspace: WorkspaceConfig = Field(default_factory=WorkspaceConfig)
    prompt_body: str

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
    # find closing ---
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
    """Walk config and resolve $VAR_NAME indirection in string leaves."""
    def _walk(node):
        if isinstance(node, dict):
            return {k: _walk(v) for k, v in node.items()}
        if isinstance(node, list):
            return [_walk(v) for v in node]
        if isinstance(node, str):
            return _resolve_var(node)
        return node
    return _walk(config)


def load_workflow(path: str | Path) -> WorkflowDefinition:
    p = Path(path)
    if not p.exists():
        raise MissingWorkflowFile(f"workflow file not found: {p}")
    text = p.read_text(encoding="utf-8")
    config, body = _split_front_matter(text)
    config = _resolve_indirection(config)

    if not body:
        body = "You are working on an issue from GitHub."

    workspace_cfg = config.get("workspace", {}) or {}
    if "root" in workspace_cfg:
        workspace_cfg["root"] = _expand_path(workspace_cfg["root"])
        # Resolve relative paths relative to WORKFLOW.md's directory
        if not os.path.isabs(workspace_cfg["root"]):
            workspace_cfg["root"] = str((p.parent / workspace_cfg["root"]).resolve())

    try:
        return WorkflowDefinition(
            tracker=TrackerConfig(**(config.get("tracker") or {})),
            agent=AgentConfig(**(config.get("agent") or {})),
            workspace=WorkspaceConfig(**workspace_cfg),
            prompt_body=body,
        )
    except Exception as exc:  # pydantic ValidationError or others
        raise WorkflowParseError(f"invalid workflow config: {exc}") from exc
