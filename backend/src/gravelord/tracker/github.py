"""GitHub Issues tracker. Label-driven state machine.

PyGithub is synchronous; we run its calls in a default executor to keep the
event loop responsive.
"""
from __future__ import annotations

import asyncio
import re
from typing import get_args

from github import Github, GithubException
from github.Issue import Issue
from github.Repository import Repository

from .base import (
    AgentKind,
    BlockerRef,
    IssueRecord,
    ReviewComment,
    ReworkContext,
    TrackerAdapter,
    TrackerConfig,
)

GRAVELORD_LABEL_DEFINITIONS: list[tuple[str, str]] = [
    ("gravelord/todo", "0E8A16"),
    ("gravelord/in-progress", "1D76DB"),
    ("gravelord/human-review", "FBCA04"),
    ("gravelord/rework", "D93F0B"),
    ("gravelord/done", "5319E7"),
]

AGENT_LABEL_DEFINITIONS: list[tuple[str, str]] = [
    ("agent:claude-code", "0075ca"),
    ("agent:codex", "e4e669"),
    ("agent:opencode", "f9d0c4"),
]

LABEL_DEFINITIONS: list[tuple[str, str]] = (
    GRAVELORD_LABEL_DEFINITIONS + AGENT_LABEL_DEFINITIONS
)

# Precedence: when an issue has multiple gravelord/* labels, derived state is the
# first match in this order. Active labels go first so that rework/todo isn't
# masked by a stale in-progress label.
STATE_PRECEDENCE = [
    "gravelord/rework",
    "gravelord/todo",
    "gravelord/in-progress",
    "gravelord/human-review",
    "gravelord/done",
]

_AGENT_KINDS: tuple[str, ...] = get_args(AgentKind)


def slugify_branch(number: int, title: str, *, max_len: int = 60) -> str:
    base = f"gravelord/{number}-"
    slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")
    remaining = max_len - len(base)
    if remaining <= 0:
        return f"gravelord/{number}"
    return base + slug[:remaining].rstrip("-")


def derive_state(labels: list[str]) -> str | None:
    lower = {l.lower() for l in labels}
    for state in STATE_PRECEDENCE:
        if state in lower:
            return state
    return None


def derive_agent_kind(labels: list[str]) -> str | None:
    lower = {l.lower() for l in labels}
    for kind in _AGENT_KINDS:
        if f"agent:{kind}" in lower:
            return kind
    return None


class GitHubTracker(TrackerAdapter):
    def __init__(self, config: TrackerConfig) -> None:
        self._cfg = config
        self._gh = Github(login_or_token=config.token, per_page=100)
        self._repo: Repository | None = None
        self._lock = asyncio.Lock()

    @property
    def owner(self) -> str:
        return self._cfg.owner

    @property
    def repo_name(self) -> str:
        return self._cfg.repo

    @property
    def config(self) -> TrackerConfig:
        return self._cfg

    async def _run(self, fn, *args, **kwargs):
        return await asyncio.get_running_loop().run_in_executor(
            None, lambda: fn(*args, **kwargs)
        )

    async def _get_repo(self) -> Repository:
        if self._repo is None:
            self._repo = await self._run(
                self._gh.get_repo, f"{self._cfg.owner}/{self._cfg.repo}"
            )
        return self._repo

    async def ensure_labels(self) -> None:
        repo = await self._get_repo()
        for name, color in LABEL_DEFINITIONS:
            try:
                await self._run(repo.create_label, name=name, color=color)
            except GithubException as exc:
                if exc.status not in (422, 409):
                    raise

    def _to_record(self, issue: Issue) -> IssueRecord:
        labels = [l.name for l in issue.labels]
        state = derive_state(labels) or "unlabelled"
        branch = slugify_branch(issue.number, issue.title)
        identifier = f"{self._cfg.owner}/{self._cfg.repo}#{issue.number}"
        return IssueRecord(
            id=issue.node_id,
            identifier=identifier,
            title=issue.title,
            description=issue.body,
            state=state,
            url=issue.html_url,
            branch=branch,
            priority=None,
            created_at=issue.created_at,
            updated_at=issue.updated_at,
            labels=[l.lower() for l in labels],
            blocked_by=[],
            agent_kind=derive_agent_kind(labels),
        )

    async def fetch_candidates(self) -> list[IssueRecord]:
        repo = await self._get_repo()
        seen: dict[str, IssueRecord] = {}
        for label in self._cfg.active_labels:
            try:
                issues = await self._run(
                    lambda l=label: list(repo.get_issues(state="open", labels=[l]))
                )
            except GithubException as exc:
                if exc.status == 404:
                    continue
                raise
            for raw in issues:
                if raw.pull_request is not None:
                    continue
                rec = self._to_record(raw)
                seen.setdefault(rec.id, rec)
        return list(seen.values())

    async def fetch_by_identifier(self, identifier: str) -> IssueRecord | None:
        if "#" not in identifier:
            return None
        try:
            number = int(identifier.split("#", 1)[1])
        except ValueError:
            return None
        repo = await self._get_repo()
        try:
            raw = await self._run(repo.get_issue, number)
        except GithubException as exc:
            if exc.status == 404:
                return None
            raise
        return self._to_record(raw)

    async def refresh(self, identifiers: list[str]) -> list[IssueRecord]:
        out: list[IssueRecord] = []
        for ident in identifiers:
            rec = await self.fetch_by_identifier(ident)
            if rec is not None:
                out.append(rec)
        return out

    async def fetch_terminal(self) -> list[IssueRecord]:
        repo = await self._get_repo()
        try:
            issues = await self._run(
                lambda: list(repo.get_issues(state="all", labels=[self._cfg.done_label]))
            )
        except GithubException as exc:
            if exc.status == 404:
                return []
            raise
        return [self._to_record(i) for i in issues if i.pull_request is None]

    async def claim(self, issue: IssueRecord) -> bool:
        """Atomic todo|rework -> in-progress. Returns False if already claimed."""
        repo = await self._get_repo()
        number = int(issue.identifier.split("#", 1)[1])
        try:
            raw = await self._run(repo.get_issue, number)
        except GithubException:
            return False

        active = [l for l in self._cfg.active_labels]
        current_lower = {l.name.lower() for l in raw.labels}
        active_present = next((l for l in active if l.lower() in current_lower), None)
        if active_present is None:
            return False

        try:
            await self._run(raw.remove_from_labels, active_present)
        except GithubException as exc:
            if exc.status == 404:
                return False
            raise
        try:
            await self._run(raw.add_to_labels, self._cfg.in_progress_label)
        except GithubException as exc:
            if exc.status == 422:
                return False
            raise
        return True

    async def release(self, issue: IssueRecord, *, to_state: str) -> None:
        repo = await self._get_repo()
        number = int(issue.identifier.split("#", 1)[1])
        try:
            raw = await self._run(repo.get_issue, number)
        except GithubException:
            return
        new_label = {
            "rework": self._cfg.rework_label,
            "review": self._cfg.review_label,
            "todo": "gravelord/todo",
            "done": self._cfg.done_label,
            "in-progress": self._cfg.in_progress_label,
        }.get(to_state, to_state)
        for l in list(raw.labels):
            if l.name.lower().startswith("gravelord/") and l.name != new_label:
                try:
                    await self._run(raw.remove_from_labels, l.name)
                except GithubException:
                    pass
        try:
            await self._run(raw.add_to_labels, new_label)
        except GithubException:
            pass

    async def fetch_rework_context(self, issue: IssueRecord) -> ReworkContext | None:
        repo = await self._get_repo()
        head = f"{self._cfg.owner}:{issue.branch}"
        try:
            pulls = await self._run(
                lambda: list(repo.get_pulls(state="all", head=head, sort="created", direction="desc"))
            )
        except GithubException:
            return None
        if not pulls:
            return None
        pr = pulls[0]

        review_comments_raw = await self._run(lambda: list(pr.get_review_comments()))
        issue_comments_raw = await self._run(lambda: list(pr.get_issue_comments()))
        reviews_raw = await self._run(lambda: list(pr.get_reviews()))

        comments: list[ReviewComment] = []
        for rc in review_comments_raw:
            comments.append(
                ReviewComment(
                    user=rc.user.login if rc.user else "unknown",
                    path=rc.path,
                    line=rc.line or rc.original_line,
                    body=rc.body or "",
                    created_at=rc.created_at,
                )
            )
        for ic in issue_comments_raw:
            comments.append(
                ReviewComment(
                    user=ic.user.login if ic.user else "unknown",
                    path=None,
                    line=None,
                    body=ic.body or "",
                    created_at=ic.created_at,
                )
            )
        comments.sort(key=lambda c: c.created_at or 0)

        decision: str | None = None
        for rv in reviews_raw:
            if rv.state in ("APPROVED", "CHANGES_REQUESTED", "COMMENTED"):
                decision = rv.state.lower()
        if decision is None:
            decision = "changes requested"

        return ReworkContext(pr_url=pr.html_url, review_decision=decision, comments=comments)
