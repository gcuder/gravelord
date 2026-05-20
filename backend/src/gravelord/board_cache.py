"""Async TTL cache around GitHubTracker.fetch_board.

Each repo gets its own slot keyed by repo_id. A per-repo asyncio.Lock
prevents thundering-herd refetches when multiple API callers race.
"""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass

from .tracker.base import IssueRecord
from .tracker.github import GitHubTracker


@dataclass
class _Entry:
    fetched_at: float
    buckets: dict[str, list[IssueRecord]]


class BoardCache:
    def __init__(self, *, ttl_seconds: float = 30.0) -> None:
        self._ttl = ttl_seconds
        self._entries: dict[str, _Entry] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        self._now = time.monotonic  # injectable for tests

    def _lock_for(self, repo_id: str) -> asyncio.Lock:
        lock = self._locks.get(repo_id)
        if lock is None:
            lock = asyncio.Lock()
            self._locks[repo_id] = lock
        return lock

    def peek(self, repo_id: str) -> dict[str, list[IssueRecord]] | None:
        entry = self._entries.get(repo_id)
        if entry is None:
            return None
        if self._now() - entry.fetched_at > self._ttl:
            return None
        return entry.buckets

    async def get(
        self, repo_id: str, tracker: GitHubTracker
    ) -> dict[str, list[IssueRecord]]:
        cached = self.peek(repo_id)
        if cached is not None:
            return cached
        async with self._lock_for(repo_id):
            cached = self.peek(repo_id)
            if cached is not None:
                return cached
            buckets = await tracker.fetch_board()
            self._entries[repo_id] = _Entry(
                fetched_at=self._now(), buckets=buckets
            )
            return buckets

    def invalidate(self, repo_id: str) -> None:
        self._entries.pop(repo_id, None)

    def clear(self) -> None:
        self._entries.clear()
