"""OpenCode adapter — ACP (nd-JSON over stdio) primary, `--print` fallback.

OpenCode owns provider/model auth via `opencode auth login`. We pass model and
provider flags through when configured.
"""
from __future__ import annotations

import asyncio
import json
import signal
import uuid
from pathlib import Path

import structlog

from .base import AgentAdapter, TokenUsage, TurnResult, detect_completion

log = structlog.get_logger("gravelord.adapters.opencode")


class OpenCodeAdapter(AgentAdapter):
    def __init__(
        self,
        *,
        command: str = "opencode",
        mode: str = "acp",
        model: str | None = None,
        provider: str | None = None,
        reasoning_level: str | None = None,
        stall_timeout_ms: int = 300_000,
    ) -> None:
        self._command = command
        self._mode = mode
        self._model = model
        self._provider = provider
        self._reasoning_level = reasoning_level
        self._stall_timeout_ms = stall_timeout_ms
        self._proc: asyncio.subprocess.Process | None = None
        self._workspace: Path | None = None
        self._session_uuid: str | None = None

    def _extra_args(self) -> list[str]:
        extras: list[str] = []
        if self._model:
            extras += ["--model", self._model]
        if self._provider:
            extras += ["--provider", self._provider]
        if self._reasoning_level:
            extras += ["--reasoning", self._reasoning_level]
        return extras

    async def run_turn(
        self,
        workspace: Path,
        prompt: str,
        session_id: str | None = None,
    ) -> TurnResult:
        if self._mode == "print":
            return await self._run_print(workspace, prompt, session_id)
        # ACP primary; fall back if startup fails.
        try:
            return await self._run_acp(workspace, prompt, session_id)
        except (FileNotFoundError, RuntimeError) as exc:
            log.warning("opencode_acp_failed_falling_back_to_print", error=str(exc))
            self._mode = "print"
            return await self._run_print(workspace, prompt, session_id)

    async def _ensure_acp_proc(self, workspace: Path) -> asyncio.subprocess.Process:
        if self._proc is not None and self._proc.returncode is None:
            return self._proc
        args = [self._command, "acp", "--cwd", str(workspace)] + self._extra_args()
        self._proc = await asyncio.create_subprocess_exec(
            *args,
            cwd=str(workspace),
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        self._workspace = workspace
        return self._proc

    async def _run_acp(self, workspace: Path, prompt: str, session_id: str | None) -> TurnResult:
        proc = await self._ensure_acp_proc(workspace)
        assert proc.stdin is not None and proc.stdout is not None

        if self._session_uuid is None:
            self._session_uuid = session_id or str(uuid.uuid4())

        msg = {
            "jsonrpc": "2.0",
            "id": str(uuid.uuid4()),
            "method": "session.message",
            "params": {
                "session_id": self._session_uuid,
                "role": "user",
                "content": prompt,
            },
        }
        proc.stdin.write((json.dumps(msg) + "\n").encode())
        await proc.stdin.drain()

        output_chunks: list[str] = []
        token_usage = TokenUsage()
        stall_s = self._stall_timeout_ms / 1000.0

        while True:
            try:
                line = await asyncio.wait_for(proc.stdout.readline(), timeout=stall_s)
            except asyncio.TimeoutError:
                await self.terminate()
                return TurnResult(
                    session_id=self._session_uuid,
                    output="".join(output_chunks),
                    is_complete=False,
                    token_usage=token_usage,
                    error=f"stall: no ACP output for {self._stall_timeout_ms}ms",
                )
            if not line:
                break
            text = line.decode(errors="replace").strip()
            if not text:
                continue
            try:
                evt = json.loads(text)
            except json.JSONDecodeError:
                continue
            event_kind = evt.get("method") or evt.get("event") or evt.get("type") or ""
            params = evt.get("params") or evt.get("data") or evt
            if "assistant" in event_kind or "message" in event_kind:
                content = params.get("content") or params.get("text") or ""
                if isinstance(content, str):
                    output_chunks.append(content)
            elif "usage" in event_kind:
                token_usage = TokenUsage(
                    input_tokens=int(params.get("input_tokens", token_usage.input_tokens) or 0),
                    output_tokens=int(params.get("output_tokens", token_usage.output_tokens) or 0),
                )
            elif "turn" in event_kind and ("complete" in event_kind or "done" in event_kind):
                break
            elif "error" in event_kind:
                err_msg = params.get("message") or params.get("error") or "opencode error"
                return TurnResult(
                    session_id=self._session_uuid,
                    output="".join(output_chunks),
                    is_complete=False,
                    token_usage=token_usage,
                    error=str(err_msg),
                )

        full = "".join(output_chunks)
        is_complete, pr_url = detect_completion(full)
        return TurnResult(
            session_id=self._session_uuid,
            output=full,
            is_complete=is_complete,
            token_usage=token_usage,
            pr_url=pr_url,
        )

    async def _run_print(self, workspace: Path, prompt: str, session_id: str | None) -> TurnResult:
        args = (
            [self._command, "-p", prompt, "-f", "json", "-q", "--cwd", str(workspace)]
            + self._extra_args()
        )
        proc = await asyncio.create_subprocess_exec(
            *args,
            cwd=str(workspace),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(), timeout=self._stall_timeout_ms / 1000.0
            )
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            return TurnResult(
                session_id=session_id or "",
                output="",
                is_complete=False,
                error=f"opencode print timed out at {self._stall_timeout_ms}ms",
            )
        text = stdout.decode(errors="replace")
        parsed: dict = {}
        try:
            parsed = json.loads(text) if text.strip() else {}
        except json.JSONDecodeError:
            parsed = {"output": text}
        out = parsed.get("output") or parsed.get("result") or parsed.get("text") or text
        if not isinstance(out, str):
            out = json.dumps(out)
        usage = parsed.get("usage") or {}
        token_usage = TokenUsage(
            input_tokens=int(usage.get("input_tokens", 0) or 0),
            output_tokens=int(usage.get("output_tokens", 0) or 0),
        )
        is_complete, pr_url = detect_completion(out)
        error = None
        if proc.returncode and proc.returncode != 0:
            error = f"opencode exit {proc.returncode}: {stderr.decode(errors='replace')[:300]}"
        return TurnResult(
            session_id=session_id or str(uuid.uuid4()),
            output=out,
            is_complete=is_complete,
            token_usage=token_usage,
            pr_url=pr_url,
            error=error if not is_complete else None,
        )

    async def terminate(self) -> None:
        if self._proc is None:
            return
        if self._proc.returncode is None:
            try:
                self._proc.send_signal(signal.SIGTERM)
                await asyncio.wait_for(self._proc.wait(), timeout=5.0)
            except asyncio.TimeoutError:
                self._proc.kill()
                await self._proc.wait()
            except ProcessLookupError:
                pass
        self._proc = None
        self._session_uuid = None
