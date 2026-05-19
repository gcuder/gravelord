"""Multi-repo orchestrator: poll registered repos, dispatch, retry, reconcile.

A single asyncio task owns all mutations to RuntimeState. API handlers post
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
from .daemon_config import DaemonDefaults
from .events import EventBus
from .repos import RepoRegistry, RepoRuntime
from .runner import AgentRunner, RunnerOutcome, RunningState
from .tracker.base import IssueRecord

log = structlog.get_logger("gravelord.orchestrator")


AdapterFactory = Callable[..., AgentAdapter]


@dataclass
class RunningEntry:
    repo_id: str
    issue: IssueRecord
    runner: AgentRunner
    task: asyncio.Task
    started_at: datetime
    retry_attempt: int
    state: RunningState
    agent_kind: str
    model: str | None = None
    reasoning_level: str | None = None


@dataclass
class RetryEntry:
    repo_id: str
    identifier: str
    issue_id: str
    attempt: int
    due_at_monotonic: float
    error: str | None
    payload: dict | None
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
    kind: str  # "trigger" | "kill" | "unregister_repo"
    identifier: str
    future: asyncio.Future
    payload: dict | None = None


def compute_backoff_ms(attempt: int, max_backoff_ms: int) -> int:
    base = 10_000 * (2 ** max(0, attempt - 1))
    capped = min(base, max_backoff_ms)
    jitter = int(capped * 0.1 * (2 * random.random() - 1))
    return max(1_000, capped + jitter)


def parse_identifier(identifier: str) -> tuple[str, str, int] | None:
    if "#" not in identifier:
        return None
    head, num = identifier.split("#", 1)
    if "/" not in head:
        return None
    owner, name = head.split("/", 1)
    try:
        number = int(num)
    except ValueError:
        return None
    return owner, name, number


def resolve_agent_kind(
    *,
    issue: IssueRecord,
    override: str | None,
    repo_default: str | None,
    global_default: str,
) -> str:
    """Fallback chain: trigger override → issue.agent_kind (label) → repo → global."""
    return override or issue.agent_kind or repo_default or global_default


class Orchestrator:
    def __init__(
        self,
        *,
        registry: RepoRegistry,
        defaults: DaemonDefaults,
        adapter_factory: AdapterFactory,
        events: EventBus,
    ) -> None:
        self._registry = registry
        self._defaults = defaults
        self._adapter_factory = adapter_factory
        self._events = events
        self.state = RuntimeState()
        self._commands: asyncio.Queue[Command] = asyncio.Queue()
        self._main_task: asyncio.Task | None = None
        self._stop_event = asyncio.Event()
        self._immediate_tick = asyncio.Event()

    @property
    def defaults(self) -> DaemonDefaults:
        return self._defaults

    @property
    def registry(self) -> RepoRegistry:
        return self._registry

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

    async def trigger(self, identifier: str, payload: dict | None = None) -> dict:
        return await self._submit_command("trigger", identifier, payload=payload)

    async def kill(self, identifier: str) -> dict:
        return await self._submit_command("kill", identifier)

    async def unregister_repo(self, repo_id: str) -> dict:
        return await self._submit_command("unregister_repo", repo_id)

    async def _submit_command(
        self, kind: str, identifier: str, *, payload: dict | None = None
    ) -> dict:
        fut: asyncio.Future = asyncio.get_running_loop().create_future()
        await self._commands.put(
            Command(kind=kind, identifier=identifier, payload=payload, future=fut)
        )
        self._immediate_tick.set()
        return await fut

    # ---- main loop ----

    async def _run(self) -> None:
        try:
            await self._startup_cleanup()
            await self._events.publish("orchestrator_started")

            while not self._stop_event.is_set():
                await self._tick()
                interval_s = self._defaults.poll_interval_ms / 1000.0
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

    async def _startup_cleanup(self) -> None:
        for runtime in self._registry.all():
            try:
                terminal = await runtime.tracker.fetch_terminal()
                for iss in terminal:
                    await runtime.workspace_manager.cleanup(iss)
            except Exception as exc:
                log.warning(
                    "startup_terminal_cleanup_failed",
                    repo_id=runtime.config.id,
                    error=str(exc),
                )

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
                    result = await self._handle_trigger(cmd.identifier, cmd.payload)
                elif cmd.kind == "kill":
                    result = await self._handle_kill(cmd.identifier)
                elif cmd.kind == "unregister_repo":
                    result = await self._handle_unregister_repo(cmd.identifier)
                else:
                    result = {"error": f"unknown command {cmd.kind}"}
                if not cmd.future.done():
                    cmd.future.set_result(result)
            except Exception as exc:
                if not cmd.future.done():
                    cmd.future.set_exception(exc)

    def _find_repo_for(self, identifier: str) -> RepoRuntime | None:
        parsed = parse_identifier(identifier)
        if parsed is None:
            return None
        owner, name, _ = parsed
        return self._registry.find_by_owner_repo(owner, name)

    async def _handle_trigger(self, identifier: str, payload: dict | None) -> dict:
        if identifier in self._running_by_identifier():
            return {"error": "already_running", "status": 409}
        runtime = self._find_repo_for(identifier)
        if runtime is None:
            return {"error": "repo_not_registered", "status": 404}
        issue = await runtime.tracker.fetch_by_identifier(identifier)
        if issue is None:
            return {"error": "not_found", "status": 404}
        # Apply trigger-time overrides.
        override_kind = (payload or {}).get("agent_kind")
        override_model = (payload or {}).get("model")
        override_reasoning = (payload or {}).get("reasoning_level")
        if override_kind:
            issue.agent_kind = override_kind
        if override_model:
            issue.model = override_model
        if override_reasoning:
            issue.reasoning_level = override_reasoning
        await runtime.tracker.release(
            issue, to_state=runtime.tracker.config.in_progress_label
        )
        await self._dispatch(runtime, issue, attempt=None)
        return {"queued": True, "identifier": identifier}

    async def _handle_kill(self, identifier: str) -> dict:
        entry = self._running_by_identifier().get(identifier)
        if entry is None:
            return {"error": "not_running", "status": 404}
        runtime = self._registry.get(entry.repo_id)
        await entry.runner.cancel()
        if runtime is not None:
            await runtime.tracker.release(
                entry.issue, to_state=runtime.tracker.config.rework_label
            )
        return {"killed": True, "identifier": identifier}

    async def _handle_unregister_repo(self, repo_id: str) -> dict:
        runtime = self._registry.get(repo_id)
        if runtime is None:
            return {"error": "not_registered", "status": 404}
        # Terminate running issues in this repo; release labels to in-progress.
        for entry in list(self.state.running.values()):
            if entry.repo_id != repo_id:
                continue
            await entry.runner.cancel()
            try:
                await runtime.tracker.release(
                    entry.issue,
                    to_state=runtime.tracker.config.in_progress_label,
                )
            except Exception as exc:
                log.warning(
                    "unregister_release_failed",
                    repo_id=repo_id,
                    identifier=entry.issue.identifier,
                    error=str(exc),
                )
        # Drop retry entries for this repo.
        for issue_id, retry in list(self.state.retry_attempts.items()):
            if retry.repo_id != repo_id:
                continue
            if retry.handle is not None:
                retry.handle.cancel()
            self.state.retry_attempts.pop(issue_id, None)
            self.state.claimed.discard(issue_id)
        await self._registry.unregister(repo_id)
        return {"unregistered": True, "repo_id": repo_id}

    def _running_by_identifier(self) -> dict[str, RunningEntry]:
        return {entry.issue.identifier: entry for entry in self.state.running.values()}

    # ---- reconciliation ----

    async def _reconcile(self) -> None:
        now = time.monotonic()
        stall_ms = self._defaults.stall_timeout_ms
        if stall_ms > 0:
            for entry in list(self.state.running.values()):
                elapsed = (
                    datetime.now(timezone.utc) - entry.state.last_event_at
                ).total_seconds() * 1000
                if elapsed > stall_ms:
                    log.warning(
                        "stall_detected",
                        identifier=entry.issue.identifier,
                        elapsed_ms=int(elapsed),
                    )
                    await self._events.publish(
                        "stall_detected",
                        repo_id=entry.repo_id,
                        issue_id=entry.issue.id,
                        issue_identifier=entry.issue.identifier,
                        elapsed_ms=int(elapsed),
                    )
                    await entry.runner.cancel()

        # Per-repo tracker refresh — terminate runs whose label moved.
        by_repo: dict[str, list[RunningEntry]] = {}
        for entry in self.state.running.values():
            by_repo.setdefault(entry.repo_id, []).append(entry)
        for repo_id, entries in by_repo.items():
            runtime = self._registry.get(repo_id)
            if runtime is None:
                continue
            identifiers = [e.issue.identifier for e in entries]
            try:
                refreshed = await runtime.tracker.refresh(identifiers)
            except Exception as exc:
                log.warning(
                    "reconcile_refresh_failed", repo_id=repo_id, error=str(exc)
                )
                refreshed = []
            by_ident = {r.identifier: r for r in refreshed}
            for entry in entries:
                latest = by_ident.get(entry.issue.identifier)
                if latest is None:
                    continue
                latest.agent_kind = latest.agent_kind or entry.issue.agent_kind
                latest.model = latest.model or entry.issue.model
                latest.reasoning_level = (
                    latest.reasoning_level or entry.issue.reasoning_level
                )
                entry.issue = latest
                state = latest.state
                if state == runtime.tracker.config.done_label:
                    await entry.runner.cancel()
                    await runtime.workspace_manager.cleanup(latest)
                    await self._events.publish(
                        "label_changed",
                        repo_id=repo_id,
                        issue_id=latest.id,
                        issue_identifier=latest.identifier,
                        new_state=state,
                    )
        _ = now

    # ---- dispatch ----

    async def _dispatch_cycle(self) -> None:
        max_conc = self._defaults.max_concurrent
        slots = max(max_conc - len(self.state.running), 0)
        if slots <= 0:
            return

        # Gather candidates from every registered repo.
        all_candidates: list[tuple[RepoRuntime, IssueRecord]] = []
        for runtime in self._registry.all():
            try:
                candidates = await runtime.tracker.fetch_candidates()
            except Exception as exc:
                log.warning(
                    "fetch_candidates_failed",
                    repo_id=runtime.config.id,
                    error=str(exc),
                )
                continue
            for issue in candidates:
                all_candidates.append((runtime, issue))

        all_candidates = self._sort_candidates(all_candidates)
        for runtime, issue in all_candidates:
            if slots <= 0:
                break
            if issue.id in self.state.running:
                continue
            if issue.id in self.state.claimed:
                continue
            claimed = await runtime.tracker.claim(issue)
            if not claimed:
                continue
            await self._dispatch(runtime, issue, attempt=None)
            slots -= 1

    @staticmethod
    def _sort_candidates(
        entries: list[tuple[RepoRuntime, IssueRecord]],
    ) -> list[tuple[RepoRuntime, IssueRecord]]:
        return sorted(
            entries,
            key=lambda pair: (
                pair[1].priority if pair[1].priority is not None else 999,
                pair[1].created_at.timestamp() if pair[1].created_at else float("inf"),
                pair[1].identifier,
            ),
        )

    async def _dispatch(
        self, runtime: RepoRuntime, issue: IssueRecord, *, attempt: int | None
    ) -> None:
        agent_kind = resolve_agent_kind(
            issue=issue,
            override=None,  # already merged into issue by _handle_trigger
            repo_default=runtime.config.agent,
            global_default=self._defaults.agent,
        )
        adapter = self._adapter_factory(
            agent_kind=agent_kind,
            model=issue.model,
            reasoning_level=issue.reasoning_level,
            defaults=self._defaults,
        )
        runner = AgentRunner(
            issue=issue,
            workflow=runtime.workflow,
            adapter=adapter,
            tracker=runtime.tracker,
            workspace_manager=runtime.workspace_manager,
            events=self._events,
            repo_id=runtime.config.id,
            max_turns=self._defaults.max_turns,
            on_state=lambda s: None,
        )
        started_at = datetime.now(timezone.utc)
        task = asyncio.create_task(self._run_and_complete(issue.id, runner))
        entry = RunningEntry(
            repo_id=runtime.config.id,
            issue=issue,
            runner=runner,
            task=task,
            started_at=started_at,
            retry_attempt=attempt or 0,
            state=runner.state,
            agent_kind=agent_kind,
            model=issue.model,
            reasoning_level=issue.reasoning_level,
        )
        self.state.running[issue.id] = entry
        self.state.claimed.add(issue.id)
        self.state.retry_attempts.pop(issue.id, None)
        await self._events.publish(
            "worker_dispatched_resolved",
            repo_id=runtime.config.id,
            issue_id=issue.id,
            issue_identifier=issue.identifier,
            agent_kind=agent_kind,
            model=issue.model,
            reasoning_level=issue.reasoning_level,
        )

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

        runtime = self._registry.get(entry.repo_id)

        if outcome.success:
            if runtime is not None:
                try:
                    await runtime.tracker.release(
                        entry.issue,
                        to_state=runtime.tracker.config.review_label,
                    )
                except Exception:
                    pass
            self.state.completed.add(issue_id)
            self.state.claimed.discard(issue_id)
            return

        if runtime is None:
            # Repo was unregistered while running; nothing to retry against.
            self.state.claimed.discard(issue_id)
            return

        attempt = entry.retry_attempt + 1
        await self._schedule_retry(
            entry.repo_id,
            entry.issue,
            attempt=attempt,
            error=outcome.error,
            payload={
                "agent_kind": entry.agent_kind,
                "model": entry.model,
                "reasoning_level": entry.reasoning_level,
            },
        )

    async def _schedule_retry(
        self,
        repo_id: str,
        issue: IssueRecord,
        *,
        attempt: int,
        error: str | None,
        payload: dict | None,
    ) -> None:
        delay_ms = compute_backoff_ms(attempt, self._defaults.max_retry_backoff_ms)
        due_at = time.monotonic() + delay_ms / 1000.0
        existing = self.state.retry_attempts.pop(issue.id, None)
        if existing is not None and existing.handle is not None:
            existing.handle.cancel()
        retry = RetryEntry(
            repo_id=repo_id,
            identifier=issue.identifier,
            issue_id=issue.id,
            attempt=attempt,
            due_at_monotonic=due_at,
            error=error,
            payload=payload,
        )
        loop = asyncio.get_running_loop()
        retry.handle = loop.call_later(
            delay_ms / 1000.0,
            lambda: asyncio.create_task(self._fire_retry(issue.id)),
        )
        self.state.retry_attempts[issue.id] = retry
        await self._events.publish(
            "worker_retrying",
            repo_id=repo_id,
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
        runtime = self._registry.get(retry.repo_id)
        if runtime is None:
            self.state.claimed.discard(issue_id)
            return
        try:
            issue = await runtime.tracker.fetch_by_identifier(retry.identifier)
        except Exception as exc:
            await self._schedule_retry(
                retry.repo_id,
                IssueRecord(
                    id=issue_id,
                    identifier=retry.identifier,
                    title="",
                    state="unknown",
                    branch="",
                ),
                attempt=retry.attempt + 1,
                error=f"retry fetch failed: {exc}",
                payload=retry.payload,
            )
            return
        if issue is None:
            self.state.claimed.discard(issue_id)
            return
        if issue.state not in (
            runtime.tracker.config.in_progress_label,
            *runtime.tracker.config.active_labels,
        ):
            self.state.claimed.discard(issue_id)
            return
        if len(self.state.running) >= self._defaults.max_concurrent:
            await self._schedule_retry(
                retry.repo_id,
                issue,
                attempt=retry.attempt + 1,
                error="no available orchestrator slots",
                payload=retry.payload,
            )
            return
        # Carry retry payload onto issue.
        if retry.payload:
            issue.agent_kind = issue.agent_kind or retry.payload.get("agent_kind")
            issue.model = issue.model or retry.payload.get("model")
            issue.reasoning_level = (
                issue.reasoning_level or retry.payload.get("reasoning_level")
            )
        await self._dispatch(runtime, issue, attempt=retry.attempt)

    # ---- status surface ----

    def snapshot(self) -> dict:
        per_repo: dict[str, dict] = {}
        for rt in self._registry.all():
            per_repo[rt.config.id] = {
                "id": rt.config.id,
                "owner": rt.config.owner,
                "name": rt.config.name,
                "path": str(rt.config.absolute_path),
                "default_branch": rt.config.default_branch,
                "agent": rt.config.agent,
                "running": 0,
                "retrying": 0,
            }
        for entry in self.state.running.values():
            if entry.repo_id in per_repo:
                per_repo[entry.repo_id]["running"] += 1
        for retry in self.state.retry_attempts.values():
            if retry.repo_id in per_repo:
                per_repo[retry.repo_id]["retrying"] += 1

        return {
            "started_at": self.state.started_at.isoformat(),
            "uptime_seconds": (
                datetime.now(timezone.utc) - self.state.started_at
            ).total_seconds(),
            "max_concurrent": self._defaults.max_concurrent,
            "concurrency_used": len(self.state.running),
            "counts": {
                "running": len(self.state.running),
                "retrying": len(self.state.retry_attempts),
                "completed": len(self.state.completed),
            },
            "repos": list(per_repo.values()),
            "running": [
                {
                    "repo_id": e.repo_id,
                    "issue_id": e.issue.id,
                    "issue_identifier": e.issue.identifier,
                    "state": e.issue.state,
                    "agent_kind": e.agent_kind,
                    "model": e.model,
                    "reasoning_level": e.reasoning_level,
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
                    "repo_id": r.repo_id,
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
                    "repo_id": entry.repo_id,
                    "issue_identifier": identifier,
                    "issue_id": entry.issue.id,
                    "status": "running",
                    "agent_kind": entry.agent_kind,
                    "model": entry.model,
                    "reasoning_level": entry.reasoning_level,
                    "workspace": {
                        "path": str(
                            self._registry.get(entry.repo_id).workspace_manager.root
                            / entry.issue.identifier
                        )
                        if self._registry.get(entry.repo_id) is not None
                        else None
                    },
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
                    "repo_id": retry.repo_id,
                    "issue_identifier": identifier,
                    "issue_id": retry.issue_id,
                    "status": "retrying",
                    "running": None,
                    "retry": {
                        "attempt": retry.attempt,
                        "due_in_seconds": max(
                            0.0, retry.due_at_monotonic - time.monotonic()
                        ),
                        "error": retry.error,
                    },
                    "last_error": retry.error,
                }
        return None
