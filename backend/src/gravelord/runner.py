"""AgentRunner — multi-turn loop wrapping workspace + prompt + adapter."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable

import structlog

from .adapters.base import AgentAdapter, TokenUsage, TurnResult, detect_completion
from .events import EventBus
from .tracker.base import IssueRecord, ReworkContext, TrackerAdapter
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
        on_state: Callable[[RunningState], None] | None = None,
    ) -> None:
        self.issue = issue
        self.workflow = workflow
        self.adapter = adapter
        self.tracker = tracker
        self.workspace_manager = workspace_manager
        self.events = events
        self.state = RunningState(issue=issue)
        self._on_state = on_state
        self._cancelled = False

    def _notify(self) -> None:
        if self._on_state is not None:
            self._on_state(self.state)

    async def cancel(self) -> None:
        self._cancelled = True
        await self.adapter.terminate()

    async def run(self) -> RunnerOutcome:
        await self.events.publish(
            "worker_dispatched",
            issue_id=self.issue.id,
            issue_identifier=self.issue.identifier,
            branch=self.issue.branch,
        )

        # Workspace prep
        try:
            workspace = await self.workspace_manager.ensure(self.issue)
        except Exception as exc:
            await self.events.publish(
                "worker_failed",
                issue_id=self.issue.id,
                issue_identifier=self.issue.identifier,
                error=f"workspace: {exc}",
            )
            return RunnerOutcome(
                success=False, turns=0, tokens=TokenUsage(),
                last_output="", pr_url=None, error=str(exc),
                last_event_at=datetime.now(timezone.utc),
            )

        # Rework context
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
                await self.events.publish(
                    "rework_context_loaded",
                    issue_id=self.issue.id,
                    issue_identifier=self.issue.identifier,
                    comment_count=len(rework_ctx.comments),
                    pr_url=rework_ctx.pr_url,
                )

        # Prompt rendering
        try:
            base_prompt = self.workflow.render(
                issue=self.issue,
                repo={"owner": self.workflow.tracker.owner, "name": self.workflow.tracker.repo},
                attempt=None,
            )
        except Exception as exc:
            await self.events.publish(
                "worker_failed",
                issue_id=self.issue.id,
                issue_identifier=self.issue.identifier,
                error=f"prompt render: {exc}",
            )
            return RunnerOutcome(
                success=False, turns=0, tokens=TokenUsage(),
                last_output="", pr_url=None, error=str(exc),
                last_event_at=datetime.now(timezone.utc),
            )
        if rework_ctx is not None:
            base_prompt = base_prompt + "\n" + _format_rework_section(rework_ctx)

        # Turn loop
        tokens_total = TokenUsage()
        max_turns = self.workflow.agent.max_turns
        last_output = ""
        pr_url: str | None = None
        error: str | None = None
        turn_number = 0

        while turn_number < max_turns and not self._cancelled:
            turn_number += 1
            prompt = base_prompt if turn_number == 1 else CONTINUATION_PROMPT
            self.state.last_event = "turn_started"
            self.state.last_event_at = datetime.now(timezone.utc)
            self._notify()
            await self.events.publish(
                "turn_started",
                issue_id=self.issue.id,
                issue_identifier=self.issue.identifier,
                turn=turn_number,
            )

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

            await self.events.publish(
                "turn_completed",
                issue_id=self.issue.id,
                issue_identifier=self.issue.identifier,
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
                # Re-detect PR URL across cumulative output if adapter missed it.
                if pr_url is None:
                    _, pr_url = detect_completion(last_output)
                break

            # Refresh tracker — bail if no longer active.
            try:
                refreshed = await self.tracker.fetch_by_identifier(self.issue.identifier)
            except Exception:
                refreshed = None
            if refreshed is None:
                break
            self.issue = refreshed
            if refreshed.state not in ("gravelord/todo", "gravelord/rework", "gravelord/in-progress"):
                break

        await self.adapter.terminate()

        success = error is None and pr_url is not None
        if success:
            await self.events.publish(
                "worker_finished",
                issue_id=self.issue.id,
                issue_identifier=self.issue.identifier,
                turn=turn_number,
                pr_url=pr_url,
                token_usage={
                    "input": tokens_total.input_tokens,
                    "output": tokens_total.output_tokens,
                },
            )
        else:
            await self.events.publish(
                "worker_failed",
                issue_id=self.issue.id,
                issue_identifier=self.issue.identifier,
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
