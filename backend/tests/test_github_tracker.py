from __future__ import annotations

from gravelord.tracker.github import derive_agent_kind, derive_state, slugify_branch


def test_slug_basic():
    assert slugify_branch(42, "Fix login bug") == "gravelord/42-fix-login-bug"


def test_slug_special_chars():
    assert slugify_branch(7, "Add OAuth! (Google + GitHub)") == "gravelord/7-add-oauth-google-github"


def test_slug_max_60():
    s = slugify_branch(123, "A" * 200)
    assert len(s) <= 60
    assert s.startswith("gravelord/123-")


def test_slug_trailing_hyphen_trim():
    s = slugify_branch(9, "ABC--    --DEF")
    assert s == "gravelord/9-abc-def"


def test_derive_state_precedence_rework_over_in_progress():
    # rework wins over in-progress (a human re-opened it)
    assert derive_state(["gravelord/rework", "gravelord/in-progress"]) == "gravelord/rework"


def test_derive_state_in_progress_over_human_review():
    assert derive_state(["gravelord/in-progress", "gravelord/human-review"]) == "gravelord/in-progress"


def test_derive_state_none_when_no_labels():
    assert derive_state(["bug", "p1"]) is None


def test_derive_state_done():
    assert derive_state(["gravelord/done"]) == "gravelord/done"


def test_derive_agent_kind_claude_code():
    assert derive_agent_kind(["agent:claude-code", "bug"]) == "claude-code"


def test_derive_agent_kind_codex():
    assert derive_agent_kind(["agent:codex"]) == "codex"


def test_derive_agent_kind_opencode_case_insensitive():
    assert derive_agent_kind(["Agent:OpenCode"]) == "opencode"


def test_derive_agent_kind_none_when_no_label():
    assert derive_agent_kind(["bug", "p1"]) is None
