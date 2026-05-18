from __future__ import annotations

from datetime import datetime
from typing import Protocol

from pydantic import BaseModel, Field


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


class TrackerAdapter(Protocol):
    async def fetch_candidates(self) -> list[IssueRecord]: ...

    async def refresh(self, identifiers: list[str]) -> list[IssueRecord]: ...

    async def fetch_by_identifier(self, identifier: str) -> IssueRecord | None: ...

    async def fetch_terminal(self) -> list[IssueRecord]: ...

    async def claim(self, issue: IssueRecord) -> bool: ...

    async def release(self, issue: IssueRecord, *, to_state: str) -> None: ...

    async def fetch_rework_context(self, issue: IssueRecord) -> ReworkContext | None: ...
