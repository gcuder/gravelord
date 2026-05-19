# gravelord (backend)

Multi-repo coding-agent orchestrator daemon. Polls registered GitHub repos by
label, dispatches Claude Code / Codex / OpenCode subprocesses against
per-issue git workspaces, exposes a FastAPI HTTP + WebSocket API, and serves
the bundled SPA from `/`.

See the [top-level README](../README.md) for the user-facing quick start.

## Run

```bash
cd backend
uv sync
export GITHUB_TOKEN=ghp_xxx          # required for the GitHub adapter
uv run gravelord add ~/path/to/repo  # register a repo
uv run gravelord                     # start the daemon (http://127.0.0.1:7777)
```

`uv run gravelord --help` lists `add` / `remove` / `list` / `daemon`.

## Layout

```
backend/
├── src/gravelord/
│   ├── main.py            # FastAPI app + Typer CLI + lifespan + static mount
│   ├── daemon_config.py   # ~/.gravelord/config.yaml schema + load/save/diff
│   ├── cli_ops.py         # add/remove/list shared by CLI and /api/repos
│   ├── repos.py           # RepoRegistry / RepoRuntime (per-repo tracker + WORKFLOW)
│   ├── orchestrator.py    # multi-repo poll / dispatch / retry / reconcile
│   ├── runner.py          # per-issue multi-turn driver
│   ├── workflow.py        # WORKFLOW.md parser (template-only post-refactor)
│   ├── workspace.py       # per-issue git workspaces in <repo>/.gravelord_workspaces
│   ├── events.py          # async fanout bus + per-issue ring buffer
│   ├── adapters/          # claude_code / codex / opencode
│   ├── tracker/           # GitHubTracker, TrackerConfig, IssueRecord
│   ├── api/routes.py      # /api/* REST + WS
│   └── static/            # bundled frontend (populated by `make build`)
├── tests/                 # focused unit tests
└── examples/              # WORKFLOW.md templates per adapter
```

## Tests

```bash
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
| `agent:claude-code`     | Run with the Claude Code adapter     | human (or UI)  |
| `agent:codex`           | Run with the Codex adapter           | human (or UI)  |
| `agent:opencode`        | Run with the OpenCode adapter        | human (or UI)  |

All labels are created automatically on each registered repo at daemon startup.

## Agent / model / reasoning resolution

Per-issue fallback chain (first non-null wins):

1. `agent:*` label on the issue
2. `agent_kind` in the trigger body
3. `repos[*].agent` in `~/.gravelord/config.yaml`
4. `defaults.agent` in `~/.gravelord/config.yaml`

`model` and `reasoning_level` come only from the trigger body (UI dispatch
modal). They are *not* read from labels or `WORKFLOW.md`.

## API

```
GET    /api/repos                                      list repos + counts
POST   /api/repos                          {path}      register a repo
DELETE /api/repos/{repo_id}                            unregister (cancels running)

GET    /api/status                                     global snapshot
GET    /api/issues/{owner}/{repo}/{number}             live detail
GET    /api/issues/{owner}/{repo}/{number}/logs?n=100  ring-buffer events
POST   /api/issues/{owner}/{repo}/{number}/trigger     dispatch (optional body)
POST   /api/issues/{owner}/{repo}/{number}/kill        SIGTERM + release labels

WS     /api/stream                                     all events
WS     /api/stream/{repo_id}                           events for one repo
```

Trigger body (all fields optional):

```json
{
  "agent_kind": "claude-code | codex | opencode",
  "model": "<adapter-specific id>",
  "reasoning_level": "low | normal | high | extended"
}
```

## WORKFLOW.md

Per-repo, lives at `<repo>/WORKFLOW.md`. Now a Jinja2 prompt template with
optional front matter for workspace overrides only:

```yaml
---
workspace:
  root: ./.gravelord_workspaces   # optional; default = <repo>/.gravelord_workspaces
---

You are working on {{ repo.owner }}/{{ repo.name }}.
Issue: {{ issue.identifier }} — {{ issue.title }}
{{ issue.description }}
```

Tracker / agent / concurrency config moved to `~/.gravelord/config.yaml` —
no `agent.kind` lives in `WORKFLOW.md` anymore.
