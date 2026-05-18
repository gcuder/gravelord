# gravelord (backend)

Long-running orchestrator daemon. Polls GitHub Issues by label, dispatches
coding-agent subprocesses against per-issue git workspaces, surfaces progress
via FastAPI HTTP + WebSocket.

Implementation of the [OpenAI Symphony spec](https://github.com/openai/symphony/blob/main/SPEC.md)
with GitHub Issues as the tracker and three pluggable adapters (Claude Code,
Codex, OpenCode).

## Run

```bash
cd backend
uv sync
cp .env.example .env  # fill in GITHUB_TOKEN
cp examples/claude-code.WORKFLOW.md WORKFLOW.md  # set owner/repo
uv run uvicorn gravelord.main:app --port 8000
```

## Layout

```
backend/
├── src/gravelord/        # package code (src layout)
│   ├── main.py           # FastAPI app + lifespan + CLI
│   ├── orchestrator.py   # poll/dispatch/retry/reconcile loop
│   ├── runner.py         # per-issue multi-turn driver
│   ├── workflow.py       # WORKFLOW.md parser + hot reload
│   ├── workspace.py      # per-issue git workspaces
│   ├── events.py         # event bus + structlog JSON
│   ├── adapters/         # claude_code / codex / opencode
│   ├── tracker/          # github tracker
│   └── api/              # FastAPI routes + WebSocket
├── tests/                # focused unit tests
├── examples/             # WORKFLOW.md examples per adapter
└── WORKFLOW.md           # local working copy (gitignored in real use)
```

## Tests

```bash
cd backend
uv run pytest
```

## State machine (GitHub labels)

| Label                   | Meaning                              | Set by         |
|-------------------------|--------------------------------------|----------------|
| `gravelord/todo`        | Eligible for dispatch                | human          |
| `gravelord/in-progress` | Claimed by orchestrator              | gravelord      |
| `gravelord/human-review`| Agent finished, PR open              | agent          |
| `gravelord/rework`      | Human wants changes                  | human          |
| `gravelord/done`        | Terminal, workspace cleaned          | human on merge |

## Endpoints

- `GET  /status` — orchestrator + running/retrying agents
- `GET  /status/{owner}/{repo}/{number}` — per-issue debug detail
- `GET  /logs/{owner}/{repo}/{number}?n=100` — last N log lines for the issue
- `POST /trigger/{owner}/{repo}/{number}` — manual dispatch
- `POST /kill/{owner}/{repo}/{number}` — SIGTERM agent, requeue as rework
- `WS   /stream` — real-time event broadcast
