from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from gravelord.daemon_config import DaemonDefaults
from gravelord.events import EventBus
from gravelord.issue_settings import (
    UNSET,
    IssueSettings,
    IssueSettingsStore,
)
from gravelord.orchestrator import Orchestrator
from gravelord.tracker.base import IssueRecord, TrackerConfig


# --- IssueSettingsStore ---------------------------------------------------


def _store_path(tmp_path: Path) -> Path:
    return tmp_path / "issue_settings.json"


async def test_store_empty_when_file_absent(tmp_path: Path):
    store = IssueSettingsStore(_store_path(tmp_path))
    await store.load()
    assert store.get("octo/hello#1") == IssueSettings()


async def test_store_patch_persists_and_reloads(tmp_path: Path):
    path = _store_path(tmp_path)
    store = IssueSettingsStore(path)
    await store.load()

    result = await store.patch(
        "octo/hello#1",
        agent_kind="codex",
        model="gpt-5.5",
        reasoning_level="high",
    )
    assert result.agent_kind == "codex"
    assert result.model == "gpt-5.5"
    assert result.reasoning_level == "high"

    # New instance reads from disk.
    fresh = IssueSettingsStore(path)
    await fresh.load()
    assert fresh.get("octo/hello#1").agent_kind == "codex"
    assert fresh.get("octo/hello#1").model == "gpt-5.5"
    assert fresh.get("octo/hello#1").reasoning_level == "high"


async def test_store_patch_unset_leaves_field(tmp_path: Path):
    store = IssueSettingsStore(_store_path(tmp_path))
    await store.load()
    await store.patch("octo/hello#1", agent_kind="codex", model="gpt-5.5")
    # Patching only reasoning shouldn't touch agent_kind or model.
    await store.patch("octo/hello#1", reasoning_level="high")
    settings = store.get("octo/hello#1")
    assert settings.agent_kind == "codex"
    assert settings.model == "gpt-5.5"
    assert settings.reasoning_level == "high"


async def test_store_patch_none_clears_field(tmp_path: Path):
    store = IssueSettingsStore(_store_path(tmp_path))
    await store.load()
    await store.patch(
        "octo/hello#1", agent_kind="codex", model="gpt-5.5", reasoning_level="high"
    )
    await store.patch("octo/hello#1", model=None)
    settings = store.get("octo/hello#1")
    assert settings.agent_kind == "codex"
    assert settings.model is None
    assert settings.reasoning_level == "high"


async def test_store_clears_record_when_all_fields_empty(tmp_path: Path):
    path = _store_path(tmp_path)
    store = IssueSettingsStore(path)
    await store.load()
    await store.patch("octo/hello#1", agent_kind="codex")
    await store.patch("octo/hello#1", agent_kind=None)
    raw = json.loads(path.read_text())
    assert "octo/hello#1" not in raw["issues"]


async def test_store_rejects_invalid_reasoning(tmp_path: Path):
    store = IssueSettingsStore(_store_path(tmp_path))
    await store.load()
    # claude-code only allows normal/extended.
    with pytest.raises(ValueError):
        await store.patch(
            "octo/hello#1", agent_kind="claude-code", reasoning_level="high"
        )


async def test_store_strip_empty_model_to_none(tmp_path: Path):
    store = IssueSettingsStore(_store_path(tmp_path))
    await store.load()
    await store.patch("octo/hello#1", agent_kind="codex", model="   ")
    assert store.get("octo/hello#1").model is None


async def test_store_corrupt_json_loads_empty(tmp_path: Path):
    path = _store_path(tmp_path)
    path.write_text("not-json{{{", encoding="utf-8")
    store = IssueSettingsStore(path)
    await store.load()
    assert store.get("anything") == IssueSettings()


async def test_store_skip_invalid_entries(tmp_path: Path):
    path = _store_path(tmp_path)
    path.write_text(
        json.dumps(
            {
                "version": 1,
                "issues": {
                    "octo/hello#1": {"agent_kind": "codex"},
                    "octo/hello#2": "not-a-dict",
                    "octo/hello#3": {"agent_kind": "not-a-real-agent"},
                },
            }
        ),
        encoding="utf-8",
    )
    store = IssueSettingsStore(path)
    await store.load()
    assert store.get("octo/hello#1").agent_kind == "codex"
    assert store.get("octo/hello#2") == IssueSettings()
    assert store.get("octo/hello#3") == IssueSettings()


# --- Orchestrator integration --------------------------------------------


def _issue(identifier: str, state: str = "gravelord/todo") -> IssueRecord:
    owner, rest = identifier.split("/", 1)
    name, num = rest.split("#")
    return IssueRecord(
        id=f"id-{num}",
        identifier=identifier,
        title="t",
        state=state,
        branch="b",
    )


@dataclass
class FakeTracker:
    owner: str
    name: str
    config: TrackerConfig
    issue: IssueRecord
    release_calls: list[tuple[str, str]] = field(default_factory=list)

    async def fetch_by_identifier(self, identifier: str) -> IssueRecord | None:
        if identifier != self.issue.identifier:
            return None
        return self.issue.model_copy()

    async def release(self, issue: IssueRecord, *, to_state: str) -> None:
        self.release_calls.append((issue.identifier, to_state))


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
        from pathlib import Path as _P

        return _P(self.path)


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


def _make_orchestrator(tmp_path: Path):
    tcfg = TrackerConfig(
        kind="github",
        token="t",
        owner="octo",
        repo="hello",
        default_branch="main",
    )
    issue = _issue("octo/hello#1", "gravelord/todo")
    tracker = FakeTracker(owner="octo", name="hello", config=tcfg, issue=issue)
    runtime = FakeRuntime(
        config=FakeRepoConfig(id="hello", owner="octo", name="hello"),
        tracker=tracker,
    )
    registry = FakeRegistry([runtime])
    store = IssueSettingsStore(_store_path(tmp_path))
    bus = EventBus()
    orch = Orchestrator(
        registry=registry,  # type: ignore[arg-type]
        defaults=DaemonDefaults(),
        adapter_factory=lambda **kw: None,  # type: ignore[arg-type]
        events=bus,
        issue_settings=store,
    )

    dispatched: list[IssueRecord] = []

    async def fake_dispatch(rt, iss, *, attempt, override=None):
        # Replicate the real `_dispatch` overlay order so tests can assert
        # which settings would have been passed to the adapter.
        orch._apply_saved_settings(iss)
        if override:
            if override.get("agent_kind"):
                iss.agent_kind = override["agent_kind"]
            if override.get("model"):
                iss.model = override["model"]
            if override.get("reasoning_level"):
                iss.reasoning_level = override["reasoning_level"]
        dispatched.append(iss)

    orch._dispatch = fake_dispatch  # type: ignore[method-assign]
    return orch, store, tracker, dispatched


async def test_trigger_applies_saved_settings(tmp_path: Path):
    orch, store, tracker, dispatched = _make_orchestrator(tmp_path)
    await store.load()
    await store.patch(
        "octo/hello#1",
        agent_kind="codex",
        model="gpt-5.5",
        reasoning_level="high",
    )
    result = await orch._handle_trigger("octo/hello#1", None)
    assert result == {"queued": True, "identifier": "octo/hello#1"}
    assert len(dispatched) == 1
    iss = dispatched[0]
    assert iss.agent_kind == "codex"
    assert iss.model == "gpt-5.5"
    assert iss.reasoning_level == "high"


async def test_trigger_payload_overrides_saved_settings(tmp_path: Path):
    orch, store, _, dispatched = _make_orchestrator(tmp_path)
    await store.load()
    await store.patch("octo/hello#1", agent_kind="codex", model="gpt-5.5")
    await orch._handle_trigger(
        "octo/hello#1",
        {"agent_kind": "opencode", "model": "anthropic/claude-sonnet-4-6"},
    )
    iss = dispatched[0]
    assert iss.agent_kind == "opencode"
    assert iss.model == "anthropic/claude-sonnet-4-6"


async def test_move_to_in_progress_applies_saved_settings(tmp_path: Path):
    orch, store, tracker, dispatched = _make_orchestrator(tmp_path)
    await store.load()
    await store.patch("octo/hello#1", agent_kind="codex", model="gpt-5.5")
    result = await orch._handle_move("octo/hello#1", {"to": "in-progress"})
    assert result["moved"] is True
    iss = dispatched[0]
    assert iss.agent_kind == "codex"
    assert iss.model == "gpt-5.5"


async def test_saved_settings_overlay_skipped_when_none(tmp_path: Path):
    orch, store, tracker, dispatched = _make_orchestrator(tmp_path)
    await store.load()
    # Issue ships from tracker with no saved settings — agent_kind stays None
    # so resolve_agent_kind falls back to repo/global defaults.
    await orch._handle_trigger("octo/hello#1", None)
    iss = dispatched[0]
    assert iss.agent_kind is None
    assert iss.model is None
    assert iss.reasoning_level is None


# --- API routes ----------------------------------------------------------


@pytest.fixture
def api_client(tmp_path: Path, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from gravelord.api.routes import router
    from gravelord.events import EventBus

    app = FastAPI()
    app.include_router(router)

    store = IssueSettingsStore(_store_path(tmp_path))

    # The route handlers reach into app.state for the store and registry.
    app.state.issue_settings = store
    app.state.events = EventBus()

    # Minimal registry / orchestrator stubs for board route tests below.
    class _StubRegistry:
        def all(self):
            return []

        def get(self, repo_id):
            return None

    class _StubOrch:
        class _BC:
            async def get(self, *_a, **_kw):
                return {}

        board_cache = _BC()

        def snapshot(self):
            return {"repos": []}

        def detail(self, _identifier):
            return None

    app.state.registry = _StubRegistry()
    app.state.orchestrator = _StubOrch()

    # `load()` is sync-safe in the test thread because TestClient pumps the
    # event loop, but the store starts empty either way.
    import asyncio as _aio

    _aio.get_event_loop().run_until_complete(store.load())
    return TestClient(app), store


def test_route_get_settings_empty(api_client):
    client, _ = api_client
    r = client.get("/api/issues/octo/hello/1/settings")
    assert r.status_code == 200
    assert r.json() == {
        "agent_kind": None,
        "model": None,
        "reasoning_level": None,
    }


def test_route_patch_sets_and_clears(api_client):
    client, store = api_client
    r = client.patch(
        "/api/issues/octo/hello/1/settings",
        json={"agent_kind": "codex", "model": "gpt-5.5", "reasoning_level": "high"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body == {
        "agent_kind": "codex",
        "model": "gpt-5.5",
        "reasoning_level": "high",
    }
    # Clear model only.
    r = client.patch(
        "/api/issues/octo/hello/1/settings",
        json={"model": None},
    )
    assert r.status_code == 200
    assert r.json()["model"] is None
    assert r.json()["agent_kind"] == "codex"


def test_route_patch_rejects_unknown_field(api_client):
    client, _ = api_client
    r = client.patch(
        "/api/issues/octo/hello/1/settings",
        json={"junk": "x"},
    )
    assert r.status_code == 422
    assert r.json()["detail"]["error"] == "unknown_fields"


def test_route_patch_rejects_invalid_reasoning(api_client):
    client, _ = api_client
    r = client.patch(
        "/api/issues/octo/hello/1/settings",
        json={"agent_kind": "claude-code", "reasoning_level": "high"},
    )
    assert r.status_code == 422
    assert r.json()["detail"]["error"] == "invalid_settings"


def test_route_patch_only_subset_omits_unmentioned(api_client):
    client, store = api_client
    client.patch(
        "/api/issues/octo/hello/1/settings",
        json={"agent_kind": "codex", "model": "gpt-5.5"},
    ).raise_for_status()
    r = client.patch(
        "/api/issues/octo/hello/1/settings",
        json={"reasoning_level": "low"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["agent_kind"] == "codex"
    assert body["model"] == "gpt-5.5"
    assert body["reasoning_level"] == "low"


# --- UNSET sentinel ------------------------------------------------------


def test_unset_sentinel_is_falsy_and_singleton():
    from gravelord.issue_settings import UnsetType

    assert UNSET is UnsetType()
    assert bool(UNSET) is False
    assert repr(UNSET) == "UNSET"
