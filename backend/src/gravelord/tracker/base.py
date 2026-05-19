from __future__ import annotations

from datetime import datetime
from typing import Literal, Protocol

from pydantic import BaseModel, Field


AgentKind = Literal["claude-code", "codex", "opencode"]
ReasoningLevel = Literal["low", "normal", "high", "extended"]


class TrackerConfig(BaseModel):
    """Synthesized in-memory from RepoConfig at register time. Labels and
    other state-machine knobs are hardcoded defaults (overridable via
    constructor for tests)."""

    kind: Literal["github"] = "github"
    token: str
    owner: str
    repo: str
    default_branch: str = "main"
    active_labels: list[str] = Field(
        default_factory=lambda: ["gravelord/todo", "gravelord/rework"]
    )
    in_progress_label: str = "gravelord/in-progress"
    review_label: str = "gravelord/human-review"
    done_label: str = "gravelord/done"
    rework_label: str = "gravelord/rework"


class BlockerRef(BaseModel):
    id: str | None = None
    identifier: str | None = None
    state: str | None = None


class ReviewComment(BaseModel):
    user: str
    path: str | None = None
    line: int | None = None
    body: str
    created_at: datetime | None = None


class ReworkContext(BaseModel):
    pr_url: str
    review_decision: str | None = None
    comments: list[ReviewComment] = Field(default_factory=list)


class IssueRecord(BaseModel):
    id: str
    identifier: str
    title: str
    description: str | None = None
    state: str
    url: str | None = None
    branch: str
    priority: int | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None
    labels: list[str] = Field(default_factory=list)
    blocked_by: list[BlockerRef] = Field(default_factory=list)

    pr_url: str | None = None
    review_decision: str | None = None
    rework_context: ReworkContext | None = None

    # Per-dispatch agent selection (label → trigger override → repo → defaults).
    agent_kind: AgentKind | None = None
    model: str | None = None
    reasoning_level: ReasoningLevel | None = None


class TrackerAdapter(Protocol):
    async def fetch_candidates(self) -> list[IssueRecord]: ...

    async def refresh(self, identifiers: list[str]) -> list[IssueRecord]: ...

    async def fetch_by_identifier(self, identifier: str) -> IssueRecord | None: ...

    async def fetch_terminal(self) -> list[IssueRecord]: ...

    async def claim(self, issue: IssueRecord) -> bool: ...

    async def release(self, issue: IssueRecord, *, to_state: str) -> None: ...

    async def fetch_rework_context(self, issue: IssueRecord) -> ReworkContext | None: ...
