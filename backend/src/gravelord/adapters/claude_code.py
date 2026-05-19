"""Claude Code adapter — `claude --print` subprocess per turn.

Auth: host's Claude Code subscription (no API key managed here).
"""
from __future__ import annotations

import asyncio
import json
import signal
from pathlib import Path

import structlog

from .base import (
    AgentAdapter,
    StallDetected,
    TokenUsage,
    TurnResult,
    detect_completion,
    read_with_stall,
)

log = structlog.get_logger("gravelord.adapters.claude_code")


class ClaudeCodeAdapter(AgentAdapter):
    def __init__(
        self,
        *,
        command: str = "claude",
        stall_timeout_ms: int = 300_000,
        model: str | None = None,
        reasoning_level: str | None = None,
    ) -> None:
        self._command = command
        self._stall_timeout_ms = stall_timeout_ms
        self._model = model
        self._reasoning_level = reasoning_level
        self._proc: asyncio.subprocess.Process | None = None

    def build_args(self, prompt: str, session_id: str | None = None) -> list[str]:
        args = [self._command, "--print", prompt, "--output-format", "json"]
        if session_id is not None:
            args.append("--continue")
        if self._model:
            args += ["--model", self._model]
        if self._reasoning_level == "extended":
            args += ["--thinking", "extended"]
        return args

    async def run_turn(
        self,
        workspace: Path,
        prompt: str,
        session_id: str | None = None,
    ) -> TurnResult:
        args = self.build_args(prompt, session_id)

        self._proc = await asyncio.create_subprocess_exec(
            *args,
            cwd=str(workspace),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        assert self._proc.stdout is not None
        try:
            raw = await read_with_stall(self._proc.stdout, self._stall_timeout_ms)
        except StallDetected as exc:
            await self.terminate()
            return TurnResult(
                session_id=session_id or "",
                output="",
                is_complete=False,
                error=str(exc),
            )
        await self._proc.wait()
        stderr = (await self._proc.stderr.read()).decode(errors="replace") if self._proc.stderr else ""

        out_text = raw.decode(errors="replace")
        parsed: dict = {}
        try:
            parsed = json.loads(out_text) if out_text.strip() else {}
        except json.JSONDecodeError:
            # Some claude versions emit one JSON object per line; take the last.
            for line in reversed(out_text.splitlines()):
                line = line.strip()
                if not line:
                    continue
                try:
                    parsed = json.loads(line)
                    break
                except json.JSONDecodeError:
                    continue

        new_session_id = parsed.get("session_id") or session_id or ""
        content_text = self._extract_text(parsed)
        usage = parsed.get("usage") or {}
        token_usage = TokenUsage(
            input_tokens=int(usage.get("input_tokens", 0) or 0),
            output_tokens=int(usage.get("output_tokens", 0) or 0),
        )

        error: str | None = None
        if self._proc.returncode and self._proc.returncode != 0:
            error = f"claude exit {self._proc.returncode}: {stderr[:300]}"

        is_complete, pr_url = detect_completion(content_text)
        if error and not is_complete:
            return TurnResult(
                session_id=new_session_id,
                output=content_text,
                is_complete=False,
                token_usage=token_usage,
                error=error,
            )
        return TurnResult(
            session_id=new_session_id,
            output=content_text,
            is_complete=is_complete,
            token_usage=token_usage,
            pr_url=pr_url,
            error=None,
        )

    @staticmethod
    def _extract_text(parsed: dict) -> str:
        if not parsed:
            return ""
        # Common shapes: {"result": "..."} or {"content": [{"type":"text","text":"..."}]}
        if isinstance(parsed.get("result"), str):
            return parsed["result"]
        content = parsed.get("content") or parsed.get("messages") or []
        texts: list[str] = []
        if isinstance(content, list):
            for block in content:
                if isinstance(block, dict):
                    if isinstance(block.get("text"), str):
                        texts.append(block["text"])
                    elif isinstance(block.get("content"), str):
                        texts.append(block["content"])
        return "\n".join(texts) if texts else ""

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
