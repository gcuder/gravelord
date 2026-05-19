"""AgentRunner — multi-turn loop wrapping workspace + prompt + adapter."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable

import structlog

from .adapters.base import AgentAdapter, TokenUsage, TurnResult, detect_completion
from .events import EventBus
from .tracker.base import IssueRecord, ReworkContext, TrackerAdapter
from .tracker.github import GitHubTracker
from .workflow import WorkflowDefinition
from .workspace import WorkspaceManager

log = structlog.get_logger("gravelord.runner")


@dataclass
class RunnerOutcome:
    success: bool
    turns: int
    tokens: TokenUsage
    last_output: str
    pr_url: str | None
    error: str | None
    last_event_at: datetime


@dataclass
class RunningState:
    issue: IssueRecord
    session_id: str | None = None
    turn_count: int = 0
    last_event: str = "starting"
    last_event_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    last_message: str = ""
    tokens: TokenUsage = field(default_factory=TokenUsage)


CONTINUATION_PROMPT = (
    "Continue the task from where you left off. If you have already opened a "
    "pull request and applied the gravelord/human-review label, simply output "
    "the PR URL again to confirm completion. Otherwise, keep working until the "
    "PR is open and labelled."
)


def _format_rework_section(ctx: ReworkContext) -> str:
    lines = [
        "",
        "## Previous attempt",
        f"PR: {ctx.pr_url}",
        f"Status: {ctx.review_decision or 'changes requested'}",
        "",
        "## Review feedback to address",
    ]
    for c in ctx.comments:
        loc = c.path or "general comment"
        if c.line is not None:
            loc = f"{c.path}:{c.line}"
        lines.append("---")
        lines.append(f"(commenter: {c.user}, file: {loc})")
        lines.append(c.body)
    lines.append("---")
    return "\n".join(lines)


class AgentRunner:
    def __init__(
        self,
        *,
        issue: IssueRecord,
        workflow: WorkflowDefinition,
        adapter: AgentAdapter,
        tracker: TrackerAdapter,
        workspace_manager: WorkspaceManager,
        events: EventBus,
        repo_id: str,
        max_turns: int,
        on_state: Callable[[RunningState], None] | None = None,
    ) -> None:
        self.issue = issue
        self.workflow = workflow
        self.adapter = adapter
        self.tracker = tracker
        self.workspace_manager = workspace_manager
        self.events = events
        self.repo_id = repo_id
        self.max_turns = max_turns
        self.state = RunningState(issue=issue)
        self._on_state = on_state
        self._cancelled = False

    def _notify(self) -> None:
        if self._on_state is not None:
            self._on_state(self.state)

    async def _publish(self, event: str, **data) -> None:
        await self.events.publish(
            event,
            repo_id=self.repo_id,
            issue_id=self.issue.id,
            issue_identifier=self.issue.identifier,
            **data,
        )

    async def cancel(self) -> None:
        self._cancelled = True
        await self.adapter.terminate()

    def _repo_context(self) -> dict[str, str]:
        if isinstance(self.tracker, GitHubTracker):
            return {"owner": self.tracker.owner, "name": self.tracker.repo_name}
        return {"owner": "", "name": ""}

    async def run(self) -> RunnerOutcome:
        await self._publish("worker_dispatched", branch=self.issue.branch)

        try:
            workspace = await self.workspace_manager.ensure(self.issue)
        except Exception as exc:
            await self._publish("worker_failed", error=f"workspace: {exc}")
            return RunnerOutcome(
                success=False, turns=0, tokens=TokenUsage(),
                last_output="", pr_url=None, error=str(exc),
                last_event_at=datetime.now(timezone.utc),
            )

        rework_ctx: ReworkContext | None = None
        if self.issue.state == "gravelord/rework":
            try:
                rework_ctx = await self.tracker.fetch_rework_context(self.issue)
            except Exception as exc:
                log.warning("rework_context_fetch_failed", error=str(exc))
            if rework_ctx is not None:
                self.issue.pr_url = rework_ctx.pr_url
                self.issue.review_decision = rework_ctx.review_decision
                self.issue.rework_context = rework_ctx
                await self._publish(
                    "rework_context_loaded",
                    comment_count=len(rework_ctx.comments),
                    pr_url=rework_ctx.pr_url,
                )

        try:
            base_prompt = self.workflow.render(
                issue=self.issue,
                repo=self._repo_context(),
                attempt=None,
            )
        except Exception as exc:
            await self._publish("worker_failed", error=f"prompt render: {exc}")
            return RunnerOutcome(
                success=False, turns=0, tokens=TokenUsage(),
                last_output="", pr_url=None, error=str(exc),
                last_event_at=datetime.now(timezone.utc),
            )
        if rework_ctx is not None:
            base_prompt = base_prompt + "\n" + _format_rework_section(rework_ctx)

        tokens_total = TokenUsage()
        last_output = ""
        pr_url: str | None = None
        error: str | None = None
        turn_number = 0

        while turn_number < self.max_turns and not self._cancelled:
            turn_number += 1
            prompt = base_prompt if turn_number == 1 else CONTINUATION_PROMPT
            self.state.last_event = "turn_started"
            self.state.last_event_at = datetime.now(timezone.utc)
            self._notify()
            await self._publish("turn_started", turn=turn_number)

            try:
                result: TurnResult = await self.adapter.run_turn(
                    workspace.path, prompt, session_id=self.state.session_id
                )
            except Exception as exc:
                error = f"adapter error: {exc}"
                break

            self.state.session_id = result.session_id or self.state.session_id
            self.state.turn_count = turn_number
            self.state.last_event = "turn_completed"
            self.state.last_event_at = datetime.now(timezone.utc)
            self.state.last_message = result.output[:500]
            self.state.tokens = self.state.tokens + result.token_usage
            tokens_total = tokens_total + result.token_usage
            last_output = result.output
            self._notify()

            await self._publish(
                "turn_completed",
                turn=turn_number,
                token_usage={
                    "input": result.token_usage.input_tokens,
                    "output": result.token_usage.output_tokens,
                },
                session_id=result.session_id,
            )

            if result.error and not result.is_complete:
                error = result.error
                break

            if result.is_complete:
                pr_url = result.pr_url
                if pr_url is None:
                    _, pr_url = detect_completion(last_output)
                break

            try:
                refreshed = await self.tracker.fetch_by_identifier(self.issue.identifier)
            except Exception:
                refreshed = None
            if refreshed is None:
                break
            self.issue = refreshed
            if refreshed.state not in (
                "gravelord/todo",
                "gravelord/rework",
                "gravelord/in-progress",
            ):
                break

        await self.adapter.terminate()

        success = error is None and pr_url is not None
        if success:
            await self._publish(
                "worker_finished",
                turn=turn_number,
                pr_url=pr_url,
                token_usage={
                    "input": tokens_total.input_tokens,
                    "output": tokens_total.output_tokens,
                },
            )
        else:
            await self._publish(
                "worker_failed",
                turn=turn_number,
                error=error or "max_turns reached without PR",
            )

        return RunnerOutcome(
            success=success,
            turns=turn_number,
            tokens=tokens_total,
            last_output=last_output,
            pr_url=pr_url,
            error=error if not success else None,
            last_event_at=self.state.last_event_at,
        )
