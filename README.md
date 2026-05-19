# gravelord

A long-running **multi-repo** coding-agent orchestrator. Watches GitHub Issues
by label across any number of registered repositories, picks the agent
per-issue (Claude Code / Codex / OpenCode), and drives each one against its
own git workspace. Ships with a React dashboard mounted on the same FastAPI
process.

```
~/.gravelord/config.yaml ──┐
                           │   poll every 30s
                           ▼
              ┌────────────────────────┐        WS /api/stream
              │     gravelord daemon   │ ──────────────────────► browser UI
              │  (FastAPI + asyncio)   │
              └────────────────────────┘
                           │
            ┌──────────────┼──────────────┐
            ▼              ▼              ▼
       repo A tracker  repo B tracker  repo C tracker
       claude-code     codex           opencode
        adapter        adapter         adapter
```

## Monorepo

```
.
├── backend/   FastAPI daemon + adapters. Python 3.12, uv-managed.
├── frontend/  React + Vite + Tailwind + shadcn/ui dashboard.
└── Makefile   `make build`, `make dev`, `make test`.
```

## Quick start

```bash
# one-time
export GITHUB_TOKEN=ghp_xxx
cd backend && uv sync           # install Python deps (editable)
cd ../frontend && npm install   # install JS deps

# build the SPA + wheel (frontend bundles into backend/src/gravelord/static)
cd .. && make build

# register the first repo (owner / name / default branch are read from git)
cd backend && uv run gravelord add ~/path/to/your/repo

# start the daemon — serves API + SPA at http://127.0.0.1:7777
uv run gravelord
```

Or for hot-reload dev (Vite on 5173 proxying to uvicorn on 7777):

```bash
make dev
```

## CLI

```
gravelord                    # start the daemon
gravelord add <path>         # register a local git repo (writes config.yaml)
gravelord remove <repo-id>   # unregister (terminates running issues)
gravelord list               # show registered repos
gravelord daemon --port 8080 # explicit daemon start with port override
```

Repos are added by **local path**. The CLI reads the GitHub `origin` remote
to derive `owner/name` and detects the default branch from `origin/HEAD`.

## Configuration

All daemon-level config lives in `~/.gravelord/config.yaml`. Per-repo
`WORKFLOW.md` is reduced to the Jinja2 prompt template (plus an optional
`workspace.root` override).

```yaml
# ~/.gravelord/config.yaml
port: 7777
defaults:
  agent: claude-code           # fallback when an issue has no agent: label
  max_concurrent: 3            # global concurrency across ALL repos
  max_turns: 20
  stall_timeout_ms: 300000
  poll_interval_ms: 30000
  max_retry_backoff_ms: 600000
repos:
  - id: ai-backend
    path: /Users/you/workspace/ai-backend
    owner: rocket-apps-org
    name: ai-backend
    defaultBranch: main
    agent: opencode            # optional repo-level pin
```

The daemon hot-reloads `config.yaml` via watchfiles — `gravelord add` and
`gravelord remove` rewrite the file and the daemon picks up the change within
~1s.

## Per-issue agent / model / reasoning

Each issue is run by exactly one agent. The kind is resolved by fallback:

1. `agent:claude-code` / `agent:codex` / `agent:opencode` label on the issue
2. `agent_kind` in the trigger body (UI dispatch modal)
3. Repo-level `agent` in `config.yaml`
4. Global `defaults.agent`

`model` and `reasoning_level` are dispatch-time only — set via the UI
dispatch modal or `POST /api/issues/{owner}/{repo}/{number}/trigger`:

```json
{
  "agent_kind": "claude-code",
  "model": "claude-opus-4-7",
  "reasoning_level": "extended"
}
```

| Agent       | `--model` flag                                    | reasoning passed as              |
|-------------|---------------------------------------------------|----------------------------------|
| claude-code | `--model <id>`                                    | `--thinking extended` (else off) |
| codex       | session `initialize` param                        | `reasoning_effort` low/med/high  |
| opencode    | `--model <id>` (ACP + print)                      | `--reasoning <level>`            |

On daemon startup each registered repo gets these labels auto-created:

```
gravelord/todo  gravelord/in-progress  gravelord/human-review
gravelord/rework  gravelord/done
agent:claude-code  agent:codex  agent:opencode
```

## API

All under `/api`:

```
GET    /api/repos                                       list repos + counts
POST   /api/repos                          {path}       register a new repo
DELETE /api/repos/{repo_id}                             unregister (kills running)

GET    /api/status                                      global snapshot
GET    /api/issues/{owner}/{repo}/{number}              live detail
GET    /api/issues/{owner}/{repo}/{number}/logs?n=100   ring-buffer events
POST   /api/issues/{owner}/{repo}/{number}/trigger      dispatch (body optional)
POST   /api/issues/{owner}/{repo}/{number}/kill         SIGTERM + release labels

WS     /api/stream                                      all events
WS     /api/stream/{repo_id}                            events for one repo
```

The SPA is served from `/` (mounted after `/api/*` so routing always wins).

## Frontend

`frontend/` is a React + Vite + TypeScript app using Tailwind and shadcn/ui
primitives. Components:

- **Sidebar** — list of registered repos with active-run badges, add-repo dialog.
- **AllReposView** — flat feed of every active run across all repos.
- **Board** — per-repo view of running + retrying issues.
- **IssueCard** — agent kind / model / reasoning badges, turn count, token usage.
- **IssueDetail** — live log stream via WebSocket.
- **DispatchModal** — agent / model / reasoning selectors with per-agent options.

Built output (`frontend/dist`) is copied into
`backend/src/gravelord/static/` and shipped inside the Python wheel. No
browser-only globals are used at runtime — works fine inside Tauri.

## Tests

```bash
make test        # cd backend && uv run pytest
```

## Build

```bash
make build       # vite build → copy → uv build (produces dist/*.whl)
make clean       # nuke node_modules, dist, static
```
