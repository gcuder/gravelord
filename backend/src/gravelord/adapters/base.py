from __future__ import annotations

import asyncio
import re
from pathlib import Path
from typing import Protocol

from pydantic import BaseModel, Field

PR_URL_RE = re.compile(r"https?://github\.com/[^\s)\"'<>]+/pull/\d+")
HUMAN_REVIEW_SIGNAL_RE = re.compile(r"gravelord/human-review", re.IGNORECASE)


def detect_completion(output: str) -> tuple[bool, str | None]:
    """Return (is_complete, pr_url_if_matched)."""
    m = PR_URL_RE.search(output)
    if m:
        return True, m.group(0)
    if HUMAN_REVIEW_SIGNAL_RE.search(output):
        return True, None
    return False, None


class TokenUsage(BaseModel):
    input_tokens: int = 0
    output_tokens: int = 0

    @property
    def total(self) -> int:
        return self.input_tokens + self.output_tokens

    def __add__(self, other: TokenUsage) -> TokenUsage:
        return TokenUsage(
            input_tokens=self.input_tokens + other.input_tokens,
            output_tokens=self.output_tokens + other.output_tokens,
        )


class TurnResult(BaseModel):
    session_id: str
    output: str
    is_complete: bool
    token_usage: TokenUsage = Field(default_factory=TokenUsage)
    error: str | None = None
    pr_url: str | None = None


class AgentAdapter(Protocol):
    async def run_turn(
        self,
        workspace: Path,
        prompt: str,
        session_id: str | None = None,
    ) -> TurnResult: ...

    async def terminate(self) -> None: ...


class StallDetected(Exception):
    pass


async def read_with_stall(
    stream: asyncio.StreamReader,
    stall_timeout_ms: int,
    on_chunk: callable | None = None,
) -> bytes:
    """Read until EOF, resetting a stall timer on each chunk.

    Raises StallDetected when no bytes arrive within stall_timeout_ms.
    """
    chunks: list[bytes] = []
    timeout_s = stall_timeout_ms / 1000.0
    while True:
        try:
            chunk = await asyncio.wait_for(stream.read(64 * 1024), timeout=timeout_s)
        except asyncio.TimeoutError as exc:
            raise StallDetected(f"no output for {stall_timeout_ms}ms") from exc
        if not chunk:
            break
        chunks.append(chunk)
        if on_chunk is not None:
            on_chunk(chunk)
    return b"".join(chunks)
