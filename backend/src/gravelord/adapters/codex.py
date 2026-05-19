"""Codex app-server adapter — JSON-RPC 2.0 over stdio.

This is a best-effort interpretation of spec §10. The targeted Codex
app-server protocol is the source of truth; method names defined below are
isolated as constants so they can be adjusted once verified against
`codex app-server generate-json-schema --out <dir>`.
"""
from __future__ import annotations

import asyncio
import json
import shlex
import signal
import uuid
from pathlib import Path
from typing import Any

import structlog

from .base import AgentAdapter, TokenUsage, TurnResult, detect_completion

log = structlog.get_logger("gravelord.adapters.codex")

# --- Protocol method names (adjust to match installed Codex app-server) ---
METHOD_INITIALIZE = "initialize"
METHOD_THREAD_START = "thread.start"
METHOD_TURN_START = "turn.start"
METHOD_SHUTDOWN = "shutdown"

# Notifications we listen for on stdout
NOTIF_TURN_EVENT = "turn.event"
NOTIF_TURN_COMPLETED = "turn.completed"
NOTIF_TURN_FAILED = "turn.failed"
NOTIF_USAGE = "thread.tokenUsage.updated"


REASONING_EFFORT_MAP: dict[str, str] = {
    "low": "low",
    "normal": "medium",
    "high": "high",
    "extended": "high",
}


class CodexAdapter(AgentAdapter):
    def __init__(
        self,
        *,
        command: str = "codex app-server",
        approval_policy: str = "never",
        sandbox_policy: str = "workspace-write",
        stall_timeout_ms: int = 300_000,
        read_timeout_ms: int = 5_000,
        model: str | None = None,
        reasoning_level: str | None = None,
    ) -> None:
        self._command = command
        self._approval_policy = approval_policy
        self._sandbox_policy = sandbox_policy
        self._stall_timeout_ms = stall_timeout_ms
        self._read_timeout_ms = read_timeout_ms
        self._model = model
        self._reasoning_level = reasoning_level
        self._proc: asyncio.subprocess.Process | None = None
        self._thread_id: str | None = None
        self._next_id = 0
        self._pending: dict[Any, asyncio.Future] = {}
        self._reader_task: asyncio.Task | None = None
        self._notif_queue: asyncio.Queue[dict] = asyncio.Queue()

    def initialize_params(self) -> dict[str, Any]:
        params: dict[str, Any] = {
            "approval_policy": self._approval_policy,
            "sandbox_policy": self._sandbox_policy,
        }
        if self._model:
            params["model"] = self._model
        if self._reasoning_level:
            params["reasoning_effort"] = REASONING_EFFORT_MAP.get(
                self._reasoning_level, "medium"
            )
        return params

    def _id(self) -> int:
        self._next_id += 1
        return self._next_id

    async def _spawn(self, workspace: Path) -> None:
        if self._proc is not None and self._proc.returncode is None:
            return
        argv = shlex.split(self._command)
        self._proc = await asyncio.create_subprocess_exec(
            "bash",
            "-lc",
            self._command,
            cwd=str(workspace),
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _ = argv  # quiet
        self._reader_task = asyncio.create_task(self._read_loop())
        await self._request(METHOD_INITIALIZE, self.initialize_params())

    async def _read_loop(self) -> None:
        assert self._proc is not None and self._proc.stdout is not None
        stream = self._proc.stdout
        while True:
            line = await stream.readline()
            if not line:
                break
            text = line.decode(errors="replace").strip()
            if not text:
                continue
            try:
                msg = json.loads(text)
            except json.JSONDecodeError:
                continue
            if "id" in msg and ("result" in msg or "error" in msg):
                fut = self._pending.pop(msg["id"], None)
                if fut and not fut.done():
                    fut.set_result(msg)
            else:
                await self._notif_queue.put(msg)

    async def _request(self, method: str, params: dict | None = None) -> dict:
        assert self._proc is not None and self._proc.stdin is not None
        msg_id = self._id()
        fut: asyncio.Future = asyncio.get_running_loop().create_future()
        self._pending[msg_id] = fut
        payload = {"jsonrpc": "2.0", "id": msg_id, "method": method}
        if params is not None:
            payload["params"] = params
        self._proc.stdin.write((json.dumps(payload) + "\n").encode())
        await self._proc.stdin.drain()
        return await asyncio.wait_for(fut, timeout=self._read_timeout_ms / 1000.0)

    async def _send_notification(self, method: str, params: dict | None = None) -> None:
        assert self._proc is not None and self._proc.stdin is not None
        payload = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            payload["params"] = params
        self._proc.stdin.write((json.dumps(payload) + "\n").encode())
        await self._proc.stdin.drain()

    async def run_turn(
        self,
        workspace: Path,
        prompt: str,
        session_id: str | None = None,
    ) -> TurnResult:
        try:
            await self._spawn(workspace)
        except FileNotFoundError as exc:
            return TurnResult(
                session_id=session_id or "",
                output="",
                is_complete=False,
                error=f"codex_not_found: {exc}",
            )

        if self._thread_id is None:
            resp = await self._request(
                METHOD_THREAD_START,
                {"cwd": str(workspace), "approval_policy": self._approval_policy,
                 "sandbox_policy": self._sandbox_policy},
            )
            self._thread_id = (
                (resp.get("result") or {}).get("thread_id")
                or (resp.get("result") or {}).get("threadId")
                or str(uuid.uuid4())
            )

        turn_resp = await self._request(
            METHOD_TURN_START,
            {"thread_id": self._thread_id, "prompt": prompt, "cwd": str(workspace)},
        )
        turn_id = (
            (turn_resp.get("result") or {}).get("turn_id")
            or (turn_resp.get("result") or {}).get("turnId")
            or str(uuid.uuid4())
        )

        output_chunks: list[str] = []
        token_usage = TokenUsage()
        error: str | None = None
        is_complete = False
        stall_s = self._stall_timeout_ms / 1000.0

        while True:
            try:
                notif = await asyncio.wait_for(self._notif_queue.get(), timeout=stall_s)
            except asyncio.TimeoutError:
                error = f"stall: no codex events for {self._stall_timeout_ms}ms"
                await self.terminate()
                break
            method = notif.get("method", "")
            params = notif.get("params") or {}
            if method == NOTIF_TURN_EVENT:
                delta = params.get("text") or params.get("delta") or ""
                if isinstance(delta, str):
                    output_chunks.append(delta)
            elif method == NOTIF_USAGE:
                token_usage = TokenUsage(
                    input_tokens=int(params.get("input_tokens", 0) or 0),
                    output_tokens=int(params.get("output_tokens", 0) or 0),
                )
            elif method == NOTIF_TURN_COMPLETED:
                break
            elif method == NOTIF_TURN_FAILED:
                error = params.get("error") or "turn_failed"
                break

        full = "".join(output_chunks)
        complete_now, pr_url = detect_completion(full)
        is_complete = complete_now and error is None
        return TurnResult(
            session_id=f"{self._thread_id}-{turn_id}",
            output=full,
            is_complete=is_complete,
            token_usage=token_usage,
            pr_url=pr_url,
            error=error,
        )

    async def terminate(self) -> None:
        if self._proc is None:
            return
        try:
            if self._proc.returncode is None:
                try:
                    await asyncio.wait_for(self._send_notification(METHOD_SHUTDOWN), timeout=2.0)
                except Exception:
                    pass
                self._proc.send_signal(signal.SIGTERM)
                try:
                    await asyncio.wait_for(self._proc.wait(), timeout=5.0)
                except asyncio.TimeoutError:
                    self._proc.kill()
                    await self._proc.wait()
        except ProcessLookupError:
            pass
        if self._reader_task and not self._reader_task.done():
            self._reader_task.cancel()
        self._proc = None
        self._thread_id = None
