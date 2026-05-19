from __future__ import annotations

from gravelord.adapters.claude_code import ClaudeCodeAdapter
from gravelord.adapters.codex import REASONING_EFFORT_MAP, CodexAdapter
from gravelord.adapters.opencode import OpenCodeAdapter


def test_claude_code_args_baseline():
    a = ClaudeCodeAdapter()
    args = a.build_args("hello")
    assert args == ["claude", "--print", "hello", "--output-format", "json"]


def test_claude_code_args_with_session_continues():
    a = ClaudeCodeAdapter()
    args = a.build_args("hi", session_id="abc")
    assert "--continue" in args


def test_claude_code_args_with_model_and_extended_thinking():
    a = ClaudeCodeAdapter(model="claude-opus-4", reasoning_level="extended")
    args = a.build_args("hi")
    assert ["--model", "claude-opus-4"] == args[args.index("--model"):args.index("--model") + 2]
    assert ["--thinking", "extended"] == args[args.index("--thinking"):args.index("--thinking") + 2]


def test_claude_code_normal_reasoning_omits_flag():
    a = ClaudeCodeAdapter(model="claude-opus-4", reasoning_level="normal")
    args = a.build_args("hi")
    assert "--thinking" not in args


def test_codex_initialize_params_baseline():
    a = CodexAdapter()
    p = a.initialize_params()
    assert p["approval_policy"] == "never"
    assert p["sandbox_policy"] == "workspace-write"
    assert "model" not in p
    assert "reasoning_effort" not in p


def test_codex_initialize_params_passes_model_and_reasoning():
    a = CodexAdapter(model="o3", reasoning_level="high")
    p = a.initialize_params()
    assert p["model"] == "o3"
    assert p["reasoning_effort"] == "high"


def test_codex_reasoning_normal_maps_to_medium():
    a = CodexAdapter(reasoning_level="normal")
    assert a.initialize_params()["reasoning_effort"] == "medium"


def test_codex_reasoning_map_completeness():
    assert REASONING_EFFORT_MAP["low"] == "low"
    assert REASONING_EFFORT_MAP["normal"] == "medium"
    assert REASONING_EFFORT_MAP["high"] == "high"
    # Codex doesn't have a literal "extended" effort, so we fold it into "high".
    assert REASONING_EFFORT_MAP["extended"] == "high"


def test_opencode_extra_args_includes_model_provider_reasoning():
    a = OpenCodeAdapter(model="gpt-4o", provider="openai", reasoning_level="high")
    extras = a._extra_args()
    assert "--model" in extras and "gpt-4o" in extras
    assert "--provider" in extras and "openai" in extras
    assert "--reasoning" in extras and "high" in extras


def test_opencode_extra_args_empty_by_default():
    assert OpenCodeAdapter()._extra_args() == []
