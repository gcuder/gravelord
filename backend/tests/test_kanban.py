from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

import pytest

from gravelord.board_cache import BoardCache
from gravelord.events import EventBus
from gravelord.daemon_config import DaemonDefaults
from gravelord.orchestrator import Orchestrator
from gravelord.repos import RepoRegistry
from gravelord.tracker.base import IssueRecord, TrackerConfig
from gravelord.tracker.github import BOARD_BUCKETS, derive_bucket


# --- derive_bucket --------------------------------------------------------


def test_derive_bucket_backlog_when_no_label():
    assert derive_bucket(["bug", "p1"]) == "backlog"


def test_derive_bucket_uses_state_precedence():
    # rework > in-progress
    assert derive_bucket(["gravelord/rework", "gravelord/in-progress"]) == "gravelord/rework"


def test_derive_bucket_done():
    assert derive_bucket(["gravelord/done"]) == "gravelord/done"


def test_board_buckets_constant_starts_with_backlog():
    assert BOARD_BUCKETS[0] == "backlog"
    assert len(BOARD_BUCKETS) == 6


# --- BoardCache -----------------------------------------------------------


class StubTracker:
    def __init__(self, buckets: dict[str, list[IssueRecord]]):
        self._buckets = buckets
        self.calls = 0

    async def fetch_board(self) -> dict[str, list[IssueRecord]]:
        self.calls += 1
        return self._buckets


@pytest.mark.asyncio
async def test_board_cache_hits_within_ttl():
    cache = BoardCache(ttl_seconds=60.0)
    tracker = StubTracker({"backlog": []})
    await cache.get("r1", tracker)
    await cache.get("r1", tracker)
    assert tracker.calls == 1


@pytest.mark.asyncio
async def test_board_cache_refetches_after_invalidate():
    cache = BoardCache(ttl_seconds=60.0)
    tracker = StubTracker({"backlog": []})
    await cache.get("r1", tracker)
    cache.invalidate("r1")
    await cache.get("r1", tracker)
    assert tracker.calls == 2


@pytest.mark.asyncio
async def test_board_cache_refetches_after_ttl():
    cache = BoardCache(ttl_seconds=0.01)
    tracker = StubTracker({"backlog": []})
    await cache.get("r1", tracker)
    # Override clock to simulate TTL expiry without sleeping.
    cache._now = lambda: 1e9
    await cache.get("r1", tracker)
    assert tracker.calls == 2


@pytest.mark.asyncio
async def test_board_cache_per_repo_isolation():
    cache = BoardCache(ttl_seconds=60.0)
    t1 = StubTracker({"backlog": []})
    t2 = StubTracker({"backlog": []})
    await cache.get("r1", t1)
    await cache.get("r2", t2)
    assert t1.calls == 1 and t2.calls == 1
    cache.invalidate("r1")
    await cache.get("r1", t1)
    await cache.get("r2", t2)
    assert t1.calls == 2 and t2.calls == 1


# --- Orchestrator.move smart-actions matrix -------------------------------


def _issue(identifier: str, state: str) -> IssueRecord:
    owner, rest = identifier.split("/", 1)
    name, num = rest.split("#")
    return IssueRecord(
        id=f"id-{num}",
        identifier=identifier,
        title="t",
        state=state,
        branch="b",
        labels=[state] if state.startswith("gravelord/") else [],
    )


@dataclass
class FakeTracker:
    owner: str
    name: str
    config: TrackerConfig
    issue: IssueRecord
    release_calls: list[tuple[str, str]] = field(default_factory=list)
    unlabel_calls: list[str] = field(default_factory=list)

    async def fetch_by_identifier(self, identifier: str) -> IssueRecord | None:
        return self.issue if identifier == self.issue.identifier else None

    async def release(self, issue: IssueRecord, *, to_state: str) -> None:
        self.release_calls.append((issue.identifier, to_state))

    async def unlabel(self, issue: IssueRecord) -> None:
        self.unlabel_calls.append(issue.identifier)


@dataclass
class FakeRuntime:
    config: Any
    tracker: FakeTracker
    workspace_manager: Any = None
    workflow: Any = None


class FakeRegistry:
    def __init__(self, runtimes: list[FakeRuntime]):
        self._runtimes = {rt.config.id: rt for rt in runtimes}

    def get(self, repo_id: str):
        return self._runtimes.get(repo_id)

    def all(self):
        return list(self._runtimes.values())

    def find_by_owner_repo(self, owner: str, name: str):
        for rt in self._runtimes.values():
            if rt.config.owner == owner and rt.config.name == name:
                return rt
        return None


@dataclass
class FakeRepoConfig:
    id: str
    owner: str
    name: str
    path: str = "/tmp/x"
    default_branch: str = "main"
    agent: str | None = None

    @property
    def absolute_path(self):
        from pathlib import Path

        return Path(self.path)


def _make_orchestrator(issue_state: str = "gravelord/todo", *, running: bool = False):
    tcfg = TrackerConfig(
        kind="github",
        token="t",
        owner="octo",
        repo="hello",
        default_branch="main",
    )
    issue = _issue("octo/hello#1", issue_state)
    tracker = FakeTracker(owner="octo", name="hello", config=tcfg, issue=issue)
    rcfg = FakeRepoConfig(id="hello", owner="octo", name="hello")
    runtime = FakeRuntime(config=rcfg, tracker=tracker)
    registry = FakeRegistry([runtime])
    bus = EventBus()
    orch = Orchestrator(
        registry=registry,  # type: ignore[arg-type]
        defaults=DaemonDefaults(),
        adapter_factory=lambda **kw: None,  # type: ignore[arg-type]
        events=bus,
    )

    # Stub _dispatch so we can record calls without spinning up runners.
    dispatch_calls: list[str] = []

    async def fake_dispatch(rt, iss, *, attempt):
        dispatch_calls.append(iss.identifier)

    orch._dispatch = fake_dispatch  # type: ignore[method-assign]

    if running:
        # Inject a fake RunningEntry so is_running == True.
        class _FakeRunner:
            cancelled = False

            async def cancel(self):
                self.cancelled = True

        class _FakeEntry:
            def __init__(self, iss):
                self.issue = iss
                self.runner = _FakeRunner()
                self.repo_id = "hello"

        orch.state.running[issue.id] = _FakeEntry(issue)  # type: ignore[assignment]

    return orch, tracker, dispatch_calls


@pytest.mark.asyncio
async def test_move_todo_to_in_progress_triggers_dispatch():
    orch, tracker, dispatch_calls = _make_orchestrator("gravelord/todo")
    result = await orch._handle_move("octo/hello#1", {"to": "in-progress"})
    assert result == {"moved": True, "identifier": "octo/hello#1", "to": "in-progress"}
    assert tracker.release_calls == [("octo/hello#1", "gravelord/in-progress")]
    assert dispatch_calls == ["octo/hello#1"]


@pytest.mark.asyncio
async def test_move_running_to_rework_cancels_then_releases():
    orch, tracker, dispatch_calls = _make_orchestrator(
        "gravelord/in-progress", running=True
    )
    entry = next(iter(orch.state.running.values()))
    result = await orch._handle_move("octo/hello#1", {"to": "rework"})
    assert result["moved"] is True
    assert entry.runner.cancelled is True  # type: ignore[attr-defined]
    assert tracker.release_calls == [("octo/hello#1", "rework")]
    assert dispatch_calls == []


@pytest.mark.asyncio
async def test_move_to_backlog_calls_unlabel():
    orch, tracker, _ = _make_orchestrator("gravelord/todo")
    result = await orch._handle_move("octo/hello#1", {"to": "backlog"})
    assert result["moved"] is True
    assert tracker.unlabel_calls == ["octo/hello#1"]
    assert tracker.release_calls == []


@pytest.mark.asyncio
async def test_move_done_to_todo_without_confirm_returns_409():
    orch, tracker, _ = _make_orchestrator("gravelord/done")
    result = await orch._handle_move("octo/hello#1", {"to": "todo"})
    assert result["status"] == 409
    assert result["error"] == "confirm_required"
    assert tracker.release_calls == []


@pytest.mark.asyncio
async def test_move_done_to_todo_with_confirm_succeeds():
    orch, tracker, _ = _make_orchestrator("gravelord/done")
    result = await orch._handle_move(
        "octo/hello#1", {"to": "todo", "confirm": True}
    )
    assert result["moved"] is True
    assert tracker.release_calls == [("octo/hello#1", "todo")]


@pytest.mark.asyncio
async def test_move_invalid_target_rejected():
    orch, tracker, _ = _make_orchestrator("gravelord/todo")
    result = await orch._handle_move("octo/hello#1", {"to": "garbage"})
    assert result["status"] == 400
    assert tracker.release_calls == []


@pytest.mark.asyncio
async def test_move_unknown_repo_returns_404():
    orch, _, _ = _make_orchestrator("gravelord/todo")
    result = await orch._handle_move("nope/missing#9", {"to": "todo"})
    assert result["status"] == 404


@pytest.mark.asyncio
async def test_move_in_progress_to_in_progress_is_idempotent_when_running():
    orch, tracker, dispatch_calls = _make_orchestrator(
        "gravelord/in-progress", running=True
    )
    result = await orch._handle_move("octo/hello#1", {"to": "in-progress"})
    assert result.get("already_running") is True
    assert tracker.release_calls == []
    assert dispatch_calls == []
