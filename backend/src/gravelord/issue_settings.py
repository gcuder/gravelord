"""Per-issue settings store (JSON sidecar at ~/.gravelord/issue_settings.json).

Each Kanban card may pin its preferred agent, model, and reasoning level.
Those choices are the source of truth for the next dispatch — including
retries — so they need to survive daemon restart. We use a small JSON file
instead of GitHub labels (to avoid label clutter) or issue-body front-matter
(too invasive for the daemon to silently rewrite issue descriptions).
"""
from __future__ import annotations

import asyncio
import json
import os
import tempfile
from pathlib import Path
from typing import Any, Final

import structlog
from pydantic import BaseModel

from .tracker.base import AgentKind, ReasoningLevel

log = structlog.get_logger("gravelord.issue_settings")


# Mirror of frontend/src/lib/agents.ts AGENT_OPTIONS[*].reasoning.
AGENT_REASONING: Final[dict[str, set[str]]] = {
    "claude-code": {"normal", "extended"},
    "codex": {"low", "normal", "high"},
    "opencode": {"low", "normal", "high", "extended"},
}


class UnsetType:
    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __repr__(self) -> str:
        return "UNSET"

    def __bool__(self) -> bool:
        return False


UNSET = UnsetType()


class IssueSettings(BaseModel):
    """Persistent per-issue overrides. All fields optional / nullable."""

    agent_kind: AgentKind | None = None
    model: str | None = None
    reasoning_level: ReasoningLevel | None = None


def default_settings_path() -> Path:
    return Path(os.path.expanduser("~/.gravelord/issue_settings.json"))


class IssueSettingsStore:
    """Async-safe JSON-file backed store keyed by `owner/repo#number`."""

    def __init__(self, path: Path | None = None) -> None:
        self._path = path or default_settings_path()
        self._lock = asyncio.Lock()
        self._data: dict[str, IssueSettings] = {}
        self._loaded = False

    @property
    def path(self) -> Path:
        return self._path

    async def load(self) -> None:
        async with self._lock:
            self._load_locked()

    def _load_locked(self) -> None:
        self._loaded = True
        if not self._path.exists():
            self._data = {}
            return
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            log.warning(
                "issue_settings_load_failed",
                path=str(self._path),
                error=f"{type(exc).__name__}: {exc}",
            )
            self._data = {}
            return
        issues = raw.get("issues", {}) if isinstance(raw, dict) else {}
        parsed: dict[str, IssueSettings] = {}
        for identifier, payload in issues.items():
            if not isinstance(payload, dict):
                continue
            try:
                parsed[identifier] = IssueSettings.model_validate(payload)
            except Exception as exc:
                log.warning(
                    "issue_settings_skip_invalid",
                    identifier=identifier,
                    error=str(exc),
                )
        self._data = parsed

    def get(self, identifier: str) -> IssueSettings:
        return self._data.get(identifier, IssueSettings())

    def all(self) -> dict[str, IssueSettings]:
        return dict(self._data)

    async def patch(
        self,
        identifier: str,
        *,
        agent_kind: Any = UNSET,
        model: Any = UNSET,
        reasoning_level: Any = UNSET,
    ) -> IssueSettings:
        """Update one issue's settings.

        Sentinel `UNSET` (default) means "leave this field alone". Passing
        `None` explicitly clears the field. Any other value sets it.

        Raises ValueError if `reasoning_level` isn't valid for the final
        `agent_kind`.
        """
        async with self._lock:
            current = self._data.get(identifier, IssueSettings()).model_copy()
            if agent_kind is not UNSET:
                current.agent_kind = agent_kind
            if model is not UNSET:
                current.model = model.strip() if isinstance(model, str) else model
                if current.model == "":
                    current.model = None
            if reasoning_level is not UNSET:
                current.reasoning_level = reasoning_level

            # Validate reasoning against the effective agent (if both are set).
            if current.agent_kind and current.reasoning_level:
                allowed = AGENT_REASONING.get(current.agent_kind, set())
                if current.reasoning_level not in allowed:
                    raise ValueError(
                        f"reasoning_level={current.reasoning_level!r} is not "
                        f"valid for agent_kind={current.agent_kind!r}; "
                        f"allowed: {sorted(allowed)}"
                    )

            # If every field is empty, drop the record entirely.
            if (
                current.agent_kind is None
                and current.model is None
                and current.reasoning_level is None
            ):
                self._data.pop(identifier, None)
            else:
                self._data[identifier] = current

            self._persist_locked()
            return current

    async def clear(self, identifier: str) -> None:
        async with self._lock:
            if identifier in self._data:
                self._data.pop(identifier)
                self._persist_locked()

    def _persist_locked(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": 1,
            "issues": {
                identifier: settings.model_dump(exclude_none=True)
                for identifier, settings in self._data.items()
            },
        }
        text = json.dumps(payload, indent=2, sort_keys=True)
        fd, tmp_name = tempfile.mkstemp(
            prefix=".issue_settings.",
            suffix=".json.tmp",
            dir=str(self._path.parent),
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(text)
            os.replace(tmp_name, self._path)
        except Exception:
            try:
                os.unlink(tmp_name)
            except OSError:
                pass
            raise
