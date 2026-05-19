"""Event bus, ring buffer, structlog config.

Single fanout bus shared by orchestrator/runner producers and WebSocket consumers.
Per-issue ring buffer powers GET /logs/{identifier}.
"""
from __future__ import annotations

import asyncio
from collections import defaultdict, deque
from datetime import datetime, timezone
from typing import Any

import structlog


def configure_logging() -> None:
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(20),  # INFO
        cache_logger_on_first_use=True,
    )


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


class EventBus:
    """Async fanout queue. Each subscriber gets their own asyncio.Queue."""

    def __init__(self, *, history_per_issue: int = 500) -> None:
        self._subscribers: set[asyncio.Queue[dict]] = set()
        self._lock = asyncio.Lock()
        self._issue_logs: dict[str, deque[dict]] = defaultdict(
            lambda: deque(maxlen=history_per_issue)
        )
        self._log = structlog.get_logger("gravelord.events")

    async def subscribe(self) -> asyncio.Queue[dict]:
        q: asyncio.Queue[dict] = asyncio.Queue(maxsize=1024)
        async with self._lock:
            self._subscribers.add(q)
        return q

    async def unsubscribe(self, q: asyncio.Queue[dict]) -> None:
        async with self._lock:
            self._subscribers.discard(q)

    async def publish(
        self,
        event: str,
        *,
        repo_id: str | None = None,
        issue_id: str | None = None,
        issue_identifier: str | None = None,
        **data: Any,
    ) -> None:
        payload = {
            "event": event,
            "repo_id": repo_id,
            "issue_id": issue_id,
            "issue_identifier": issue_identifier,
            "timestamp": now_iso(),
            "data": data,
        }
        if issue_identifier:
            self._issue_logs[issue_identifier].append(payload)
        self._log.info(
            event, repo_id=repo_id, issue_identifier=issue_identifier, **data
        )
        async with self._lock:
            stale: list[asyncio.Queue[dict]] = []
            for q in self._subscribers:
                try:
                    q.put_nowait(payload)
                except asyncio.QueueFull:
                    stale.append(q)
            for q in stale:
                self._subscribers.discard(q)

    def recent(self, identifier: str, n: int = 100) -> list[dict]:
        buf = self._issue_logs.get(identifier)
        if not buf:
            return []
        return list(buf)[-n:]
