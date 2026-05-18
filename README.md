# gravelord

Coding-agent orchestrator. Polls GitHub Issues by label, dispatches Claude Code
/ Codex / OpenCode subprocesses against per-issue git workspaces, and exposes a
FastAPI HTTP + WebSocket API.

Implementation of the [OpenAI Symphony spec](https://github.com/openai/symphony/blob/main/SPEC.md)
with GitHub Issues as the tracker.

## Monorepo

```
.
├── backend/   Python 3.12 / FastAPI orchestrator service. See backend/README.md.
└── webapp/    React dashboard. Not yet implemented.
```

## Quick start (backend)

```bash
cd backend
uv sync
cp .env.example .env  # fill in GITHUB_TOKEN
cp examples/claude-code.WORKFLOW.md WORKFLOW.md  # set owner/repo
uv run uvicorn gravelord.main:app --port 8000
```

See [`backend/README.md`](backend/README.md) for full details (state machine,
endpoints, examples).
