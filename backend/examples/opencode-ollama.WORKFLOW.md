---
tracker:
  kind: github
  token: $GITHUB_TOKEN
  owner: my-org
  repo: my-repo
  active_labels:
    - gravelord/todo
    - gravelord/rework

agent:
  kind: opencode
  mode: acp
  provider: ollama
  model: gemma3:27b
  max_concurrent: 1
  max_turns: 30
  stall_timeout_ms: 600000

workspace:
  root: ./gravelord_workspaces
---

You are an autonomous coding agent working on a GitHub issue.

Repository: {{ repo.owner }}/{{ repo.name }}
Issue: {{ issue.identifier }} — {{ issue.title }}
Branch: {{ issue.branch }}
Issue URL: {{ issue.url }}

## Task
{{ issue.description }}

## Workflow
1. Read AGENTS.md or CLAUDE.md in this repo for project conventions.
2. Create and checkout branch: {{ issue.branch }}
3. Implement the changes needed to resolve the issue.
4. Ensure existing tests pass. Add tests for new behaviour.
5. Commit your changes with a clear commit message.
6. Push the branch and open a pull request against main.
7. Set the gravelord/human-review label on the issue.

When complete, output the PR URL on its own line.
