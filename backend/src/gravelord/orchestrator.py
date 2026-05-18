"""Gravelord orchestrator: poll, dispatch, retry, reconcile.

Single asyncio task owns all mutations to RuntimeState. API handlers post
commands via a queue rather than touching state directly.
"""
from __future__ import annotations

import asyncio
import random
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable

import structlog

from .adapters.base import AgentAdapter, TokenUsage
from .events import EventBus
from .runner import AgentRunner, RunnerOutcome, RunningState
from .tracker.base import IssueRecord, TrackerAdapter
from .workflow import WorkflowDefinition
from .workspace import WorkspaceManager

log = structlog.get_logger("gravelord.orchestrator")


CONTINUATION_DELAY_MS = 1_000


@dataclass
class RunningEntry:
    issue: IssueRecord
    runner: AgentRunner
    task: asyncio.Task
    started_at: datetime
    retry_attempt: int
    state: RunningState


@dataclass
class RetryEntry:
    identifier: str
    issue_id: str
    attempt: int
    due_at_monotonic: float
    error: str | None
    handle: asyncio.TimerHandle | None = None


@dataclass
class RuntimeState:
    started_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    running: dict[str, RunningEntry] = field(default_factory=dict)
    claimed: set[str] = field(default_factory=set)
    retry_attempts: dict[str, RetryEntry] = field(default_factory=dict)
    completed: set[str] = field(default_factory=set)
    totals: TokenUsage = field(default_factory=TokenUsage)
    seconds_running: float = 0.0


@dataclass
class Command:
    kind: str
    identifier: str
    future: asyncio.Future


def compute_backoff_ms(attempt: int, max_backoff_ms: int) -> int:
    base = 10_000 * (2 ** max(0, attempt - 1))
    capped = min(base, max_backoff_ms)
    jitter = int(capped * 0.1 * (2 * random.random() - 1))
    return max(1_000, capped + jitter)


AdapterFactory = Callable[[WorkflowDefinition], AgentAdapter]


class Orchestrator:
    def __init__(
        self,
        *,
        workflow_provider: Callable[[], WorkflowDefinition],
        tracker: TrackerAdapter,
        workspace_manager: WorkspaceManager,
        adapter_factory: AdapterFactory,
        events: EventBus,
    ) -> None:
        self._workflow_provider = workflow_provider
        self._tracker = tracker
        self._workspace_manager = workspace_manager
        self._adapter_factory = adapter_factory
        self._events = events
        self.state = RuntimeState()
        self._commands: asyncio.Queue[Command] = asyncio.Queue()
        self._main_task: asyncio.Task | None = None
        self._stop_event = asyncio.Event()
        self._immediate_tick = asyncio.Event()

    @property
    def workflow(self) -> WorkflowDefinition:
        return self._workflow_provider()

    async def start(self) -> None:
        self._main_task = asyncio.create_task(self._run(), name="orchestrator")

    async def stop(self) -> None:
        self._stop_event.set()
        self._immediate_tick.set()
        for entry in list(self.state.running.values()):
            await entry.runner.cancel()
        if self._main_task is not None:
            self._main_task.cancel()
            try:
                await self._main_task
            except (asyncio.CancelledError, Exception):
                pass

    # ---- public command surface (called by API) ----

    async def trigger(self, identifier: str) -> dict:
        return await self._submit_command("trigger", identifier)

    async def kill(self, identifier: str) -> dict:
        return await self._submit_command("kill", identifier)

    async def _submit_command(self, kind: str, identifier: str) -> dict:
        fut: asyncio.Future = asyncio.get_running_loop().create_future()
        await self._commands.put(Command(kind=kind, identifier=identifier, future=fut))
        self._immediate_tick.set()
        return await fut

    # ---- main loop ----

    async def _run(self) -> None:
        try:
            # Startup terminal workspace cleanup
            try:
                terminal = await self._tracker.fetch_terminal()
                for iss in terminal:
                    await self._workspace_manager.cleanup(iss)
            except Exception as exc:
                log.warning("startup_terminal_cleanup_failed", error=str(exc))

            await self._events.publish("orchestrator_started")

            while not self._stop_event.is_set():
                await self._tick()
                interval_s = self.workflow.agent.poll_interval_ms / 1000.0
                try:
                    await asyncio.wait_for(self._immediate_tick.wait(), timeout=interval_s)
                except asyncio.TimeoutError:
                    pass
                self._immediate_tick.clear()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log.exception("orchestrator_crashed", error=str(exc))
            raise

    async def _tick(self) -> None:
        await self._drain_commands()
        await self._reconcile()
        await self._dispatch_cycle()

    async def _drain_commands(self) -> None:
        while True:
            try:
                cmd = self._commands.get_nowait()
            except asyncio.QueueEmpty:
                break
            try:
                if cmd.kind == "trigger":
                    result = await self._handle_trigger(cmd.identifier)
                elif cmd.kind == "kill":
                    result = await self._handle_kill(cmd.identifier)
                else:
                    result = {"error": f"unknown command {cmd.kind}"}
                if not cmd.future.done():
                    cmd.future.set_result(result)
            except Exception as exc:
                if not cmd.future.done():
                    cmd.future.set_exception(exc)

    async def _handle_trigger(self, identifier: str) -> dict:
        if identifier in self._running_by_identifier():
            return {"error": "already_running", "status": 409}
        issue = await self._tracker.fetch_by_identifier(identifier)
        if issue is None:
            return {"error": "not_found", "status": 404}
        # Mark in-progress (best-effort)
        await self._tracker.release(issue, to_state=self.workflow.tracker.in_progress_label)
        await self._dispatch(issue, attempt=None)
        return {"queued": True, "identifier": identifier}

    async def _handle_kill(self, identifier: str) -> dict:
        entry = self._running_by_identifier().get(identifier)
        if entry is None:
            return {"error": "not_running", "status": 404}
        await entry.runner.cancel()
        await self._tracker.release(entry.issue, to_state=self.workflow.tracker.rework_label)
        return {"killed": True, "identifier": identifier}

    def _running_by_identifier(self) -> dict[str, RunningEntry]:
        return {entry.issue.identifier: entry for entry in self.state.running.values()}

    # ---- reconciliation ----

    async def _reconcile(self) -> None:
        now = time.monotonic()
        stall_ms = self.workflow.agent.stall_timeout_ms
        if stall_ms > 0:
            for entry in list(self.state.running.values()):
                elapsed = (datetime.now(timezone.utc) - entry.state.last_event_at).total_seconds() * 1000
                if elapsed > stall_ms:
                    log.warning(
                        "stall_detected",
                        identifier=entry.issue.identifier,
                        elapsed_ms=int(elapsed),
                    )
                    await self._events.publish(
                        "stall_detected",
                        issue_id=entry.issue.id,
                        issue_identifier=entry.issue.identifier,
                        elapsed_ms=int(elapsed),
                    )
                    await entry.runner.cancel()

        # Tracker state refresh — terminate runs whose label moved
        identifiers = [e.issue.identifier for e in self.state.running.values()]
        if identifiers:
            try:
                refreshed = await self._tracker.refresh(identifiers)
            except Exception as exc:
                log.warning("reconcile_refresh_failed", error=str(exc))
                refreshed = []
            by_ident = {r.identifier: r for r in refreshed}
            for entry in list(self.state.running.values()):
                latest = by_ident.get(entry.issue.identifier)
                if latest is None:
                    continue
                entry.issue = latest
                state = latest.state
                if state == self.workflow.tracker.done_label:
                    await entry.runner.cancel()
                    await self._workspace_manager.cleanup(latest)
                    await self._events.publish(
                        "label_changed",
                        issue_id=latest.id,
                        issue_identifier=latest.identifier,
                        new_state=state,
                    )
                elif state in (self.workflow.tracker.review_label,):
                    # agent itself moved label — let runner finish naturally
                    pass

        _ = now

    # ---- dispatch ----

    async def _dispatch_cycle(self) -> None:
        max_conc = self.workflow.agent.max_concurrent
        slots = max(max_conc - len(self.state.running), 0)
        if slots <= 0:
            return

        try:
            candidates = await self._tracker.fetch_candidates()
        except Exception as exc:
            log.warning("fetch_candidates_failed", error=str(exc))
            return

        candidates = self._sort_candidates(candidates)
        for issue in candidates:
            if slots <= 0:
                break
            if issue.id in self.state.running:
                continue
            if issue.id in self.state.claimed:
                continue
            claimed = await self._tracker.claim(issue)
            if not claimed:
                continue
            await self._dispatch(issue, attempt=None)
            slots -= 1

    @staticmethod
    def _sort_candidates(issues: list[IssueRecord]) -> list[IssueRecord]:
        return sorted(
            issues,
            key=lambda i: (
                i.priority if i.priority is not None else 999,
                i.created_at.timestamp() if i.created_at else float("inf"),
                i.identifier,
            ),
        )

    async def _dispatch(self, issue: IssueRecord, *, attempt: int | None) -> None:
        adapter = self._adapter_factory(self.workflow)
        runner = AgentRunner(
            issue=issue,
            workflow=self.workflow,
            adapter=adapter,
            tracker=self._tracker,
            workspace_manager=self._workspace_manager,
            events=self._events,
            on_state=lambda s: None,
        )
        started_at = datetime.now(timezone.utc)
        task = asyncio.create_task(self._run_and_complete(issue.id, runner))
        entry = RunningEntry(
            issue=issue,
            runner=runner,
            task=task,
            started_at=started_at,
            retry_attempt=attempt or 0,
            state=runner.state,
        )
        self.state.running[issue.id] = entry
        self.state.claimed.add(issue.id)
        self.state.retry_attempts.pop(issue.id, None)

    async def _run_and_complete(self, issue_id: str, runner: AgentRunner) -> None:
        try:
            outcome: RunnerOutcome = await runner.run()
        except Exception as exc:
            log.exception("runner_crashed", error=str(exc))
            outcome = RunnerOutcome(
                success=False, turns=0, tokens=TokenUsage(),
                last_output="", pr_url=None, error=str(exc),
                last_event_at=datetime.now(timezone.utc),
            )
        await self._on_runner_done(issue_id, outcome)

    async def _on_runner_done(self, issue_id: str, outcome: RunnerOutcome) -> None:
        entry = self.state.running.pop(issue_id, None)
        if entry is None:
            return
        self.state.totals = self.state.totals + outcome.tokens
        self.state.seconds_running += (
            datetime.now(timezone.utc) - entry.started_at
        ).total_seconds()

        if outcome.success:
            try:
                await self._tracker.release(entry.issue, to_state=self.workflow.tracker.review_label)
            except Exception:
                pass
            self.state.completed.add(issue_id)
            self.state.claimed.discard(issue_id)
            return

        # Otherwise schedule a retry — failure-driven backoff.
        attempt = entry.retry_attempt + 1
        await self._schedule_retry(entry.issue, attempt=attempt, error=outcome.error)

    async def _schedule_retry(self, issue: IssueRecord, *, attempt: int, error: str | None) -> None:
        delay_ms = compute_backoff_ms(attempt, self.workflow.agent.max_retry_backoff_ms)
        due_at = time.monotonic() + delay_ms / 1000.0
        existing = self.state.retry_attempts.pop(issue.id, None)
        if existing is not None and existing.handle is not None:
            existing.handle.cancel()
        retry = RetryEntry(
            identifier=issue.identifier,
            issue_id=issue.id,
            attempt=attempt,
            due_at_monotonic=due_at,
            error=error,
        )
        loop = asyncio.get_running_loop()
        retry.handle = loop.call_later(delay_ms / 1000.0, lambda: asyncio.create_task(self._fire_retry(issue.id)))
        self.state.retry_attempts[issue.id] = retry
        await self._events.publish(
            "worker_retrying",
            issue_id=issue.id,
            issue_identifier=issue.identifier,
            attempt=attempt,
            delay_ms=delay_ms,
            error=error,
        )

    async def _fire_retry(self, issue_id: str) -> None:
        retry = self.state.retry_attempts.pop(issue_id, None)
        if retry is None:
            return
        try:
            issue = await self._tracker.fetch_by_identifier(retry.identifier)
        except Exception as exc:
            await self._schedule_retry(
                IssueRecord(id=issue_id, identifier=retry.identifier, title="", state="unknown", branch=""),
                attempt=retry.attempt + 1,
                error=f"retry fetch failed: {exc}",
            )
            return
        if issue is None:
            self.state.claimed.discard(issue_id)
            return
        if issue.state not in (
            self.workflow.tracker.in_progress_label,
            *self.workflow.tracker.active_labels,
        ):
            self.state.claimed.discard(issue_id)
            return
        if len(self.state.running) >= self.workflow.agent.max_concurrent:
            await self._schedule_retry(issue, attempt=retry.attempt + 1, error="no available orchestrator slots")
            return
        await self._dispatch(issue, attempt=retry.attempt)

    # ---- status surface ----

    def snapshot(self) -> dict:
        return {
            "started_at": self.state.started_at.isoformat(),
            "uptime_seconds": (datetime.now(timezone.utc) - self.state.started_at).total_seconds(),
            "max_concurrent": self.workflow.agent.max_concurrent,
            "concurrency_used": len(self.state.running),
            "counts": {
                "running": len(self.state.running),
                "retrying": len(self.state.retry_attempts),
                "completed": len(self.state.completed),
            },
            "running": [
                {
                    "issue_id": e.issue.id,
                    "issue_identifier": e.issue.identifier,
                    "state": e.issue.state,
                    "session_id": e.state.session_id,
                    "turn_count": e.state.turn_count,
                    "last_event": e.state.last_event,
                    "last_event_at": e.state.last_event_at.isoformat(),
                    "started_at": e.started_at.isoformat(),
                    "tokens": {
                        "input_tokens": e.state.tokens.input_tokens,
                        "output_tokens": e.state.tokens.output_tokens,
                        "total_tokens": e.state.tokens.total,
                    },
                }
                for e in self.state.running.values()
            ],
            "retrying": [
                {
                    "issue_id": r.issue_id,
                    "issue_identifier": r.identifier,
                    "attempt": r.attempt,
                    "due_in_seconds": max(0.0, r.due_at_monotonic - time.monotonic()),
                    "error": r.error,
                }
                for r in self.state.retry_attempts.values()
            ],
            "totals": {
                "input_tokens": self.state.totals.input_tokens,
                "output_tokens": self.state.totals.output_tokens,
                "total_tokens": self.state.totals.total,
                "seconds_running": self.state.seconds_running,
            },
        }

    def detail(self, identifier: str) -> dict | None:
        for entry in self.state.running.values():
            if entry.issue.identifier == identifier:
                return {
                    "issue_identifier": identifier,
                    "issue_id": entry.issue.id,
                    "status": "running",
                    "workspace": {"path": str(self._workspace_manager.root / entry.issue.identifier)},
                    "running": {
                        "session_id": entry.state.session_id,
                        "turn_count": entry.state.turn_count,
                        "state": entry.issue.state,
                        "started_at": entry.started_at.isoformat(),
                        "last_event": entry.state.last_event,
                        "last_message": entry.state.last_message,
                        "last_event_at": entry.state.last_event_at.isoformat(),
                        "tokens": {
                            "input_tokens": entry.state.tokens.input_tokens,
                            "output_tokens": entry.state.tokens.output_tokens,
                            "total_tokens": entry.state.tokens.total,
                        },
                    },
                    "retry": None,
                    "last_error": None,
                }
        for retry in self.state.retry_attempts.values():
            if retry.identifier == identifier:
                return {
                    "issue_identifier": identifier,
                    "issue_id": retry.issue_id,
                    "status": "retrying",
                    "running": None,
                    "retry": {
                        "attempt": retry.attempt,
                        "due_in_seconds": max(0.0, retry.due_at_monotonic - time.monotonic()),
                        "error": retry.error,
                    },
                    "last_error": retry.error,
                }
        return None
